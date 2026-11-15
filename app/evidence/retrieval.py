"""Inspectable, local semantic retrieval. No answer generation or Groq calls."""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from app.evidence.ingestion import Chunk, Document, chunk_document, load_document, stable_id


class Embedder(Protocol):
    model_id: str
    dimension: int

    def passages(self, texts: list[str]) -> list[list[float]]: ...
    def query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """Pinned English CPU baseline; download happens only on explicit creation."""

    dimension = 384
    model_name = "BAAI/bge-small-en-v1.5"

    def __init__(self, cache_dir: Path, *, offline: bool = False):
        from fastembed import TextEmbedding

        self.encoder = TextEmbedding(model_name=self.model_name, cache_dir=str(cache_dir),
                                     threads=2, providers=["CPUExecutionProvider"],
                                     local_files_only=offline)
        # Pin the actual downloaded artifacts as well as the package/model name.
        # Upstream model files can change under the same repository name.
        model_dir = Path(self.encoder.model._model_dir)
        digest = hashlib.sha256()
        for name in ("model_optimized.onnx", "tokenizer.json", "tokenizer_config.json",
                     "special_tokens_map.json", "config.json"):
            digest.update(name.encode("utf-8"))
            with (model_dir / name).open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        self.artifact_hash = digest.hexdigest()
        self.model_id = f"fastembed-{version('fastembed')}:{self.model_name}:v1:{self.artifact_hash}"

    def _validate_text(self, text: str) -> None:
        if not text.strip():
            raise ValueError("Embedding text must not be blank")
        # Version-specific adapter: FastEmbed 0.9.1's pinned BGE ONNX backend
        # exposes its tokenizer. Overflow identifies truncation before inference.
        if self.encoder.model.tokenizer.encode(text).overflowing:
            raise ValueError("Text exceeds the embedding context; use smaller chunks or query")

    def passages(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            self._validate_text(text)
        return [vector.tolist() for vector in self.encoder.passage_embed(texts, batch_size=16)]

    def query(self, text: str) -> list[float]:
        self._validate_text(text)
        return next(iter(self.encoder.query_embed(text))).tolist()


@dataclass(frozen=True)
class CorpusSource:
    document: Document
    metadata: dict


def load_corpus(manifest_path: Path) -> list[CorpusSource]:
    """Load only explicitly listed local sources; URLs are metadata, never fetched."""
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not manifest.get("sources"):
        raise ValueError("Manifest requires version 1 and a nonempty sources list")
    sources = []
    seen = set()
    for entry in manifest["sources"]:
        source_id = entry["source_id"].strip()
        if source_id in seen:
            raise ValueError(f"Duplicate source_id: {source_id}")
        seen.add(source_id)
        path = (manifest_path.parent / entry["path"]).resolve()
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError("Source paths must stay within the manifest directory")
        document = load_document(path, source_id=source_id, title=entry.get("title"),
                                 source_url=entry.get("source_url"), source_date=entry.get("source_date"))
        metadata = {key: entry[key] for key in (
            "authors", "license", "license_url", "representation", "verified_on") if key in entry}
        sources.append(CorpusSource(document, metadata))
    return sources


def point_id(chunk_id: str) -> str:
    # Qdrant point IDs accept UUIDs, not arbitrary 64-character hashes.
    return str(uuid5(NAMESPACE_URL, "debatebot:" + chunk_id))


class EvidenceIndex:
    def __init__(self, client, embedder: Embedder):
        self.client = client
        self.embedder = embedder
        # A different embedding model/library configuration gets its own space.
        self.collection = "evidence_" + stable_id(embedder.model_id, str(embedder.dimension))[:24]

    def _vector(self, vector: list[float]) -> list[float]:
        if (len(vector) != self.embedder.dimension or
                not all(math.isfinite(v) for v in vector) or not any(vector)):
            raise ValueError("Invalid embedding dimension, nonfinite value, or zero vector")
        return vector

    def _ensure_collection(self) -> None:
        from qdrant_client import models

        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=self.embedder.dimension, distance=models.Distance.COSINE))
        config = self.client.get_collection(self.collection).config.params.vectors
        if config.size != self.embedder.dimension or config.distance != models.Distance.COSINE:
            raise ValueError("Collection vector configuration does not match embedder")

    def index_source(self, source: CorpusSource, *, chunk_words: int = 80,
                     overlap_words: int = 16) -> list[Chunk]:
        from qdrant_client import models

        document = source.document
        chunks = chunk_document(document, chunk_words=chunk_words, overlap_words=overlap_words)
        if not chunks:
            raise ValueError("Cannot index an empty source")
        vectors = self.embedder.passages([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("Embedding count must match chunk count")
        points = []
        for chunk, vector in zip(chunks, vectors):
            payload = {**source.metadata, **asdict(chunk), "source_id": document.source_id,
                       "title": document.title, "source_url": document.source_url,
                       "source_date": document.source_date, "embedding_model": self.embedder.model_id,
                       "chunking": {"algorithm": "word-window-v1", "chunk_words": chunk_words,
                                    "overlap_words": overlap_words}}
            points.append(models.PointStruct(id=point_id(chunk.chunk_id),
                                             vector=self._vector(vector), payload=payload))
        self._ensure_collection()
        self.client.upsert(self.collection, points=points, wait=True)
        # Upsert first: embedding/upsert failure must not delete the old source.
        # Cleanup is not transactional; a crash can temporarily leave both revisions.
        self.client.delete(self.collection, points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document.document_id))],
            must_not=[models.HasIdCondition(has_id=[point.id for point in points])],
        )), wait=True)
        return chunks

    def search(self, query: str, *, limit: int = 3) -> list[dict]:
        if not query.strip() or not 1 <= limit <= 20:
            raise ValueError("Query must be nonblank and limit between 1 and 20")
        if not self.client.collection_exists(self.collection):
            return []
        vector = self._vector(self.embedder.query(query))
        points = self.client.query_points(self.collection, query=vector, limit=limit,
                                          with_payload=True, with_vectors=False).points
        return [{"score": point.score, **point.payload} for point in points]


def evaluate(index: EvidenceIndex, sources: list[CorpusSource], cases_path: Path,
             *, limit: int = 3, chunk_words: int = 80, overlap_words: int = 16) -> dict:
    """Development passage recall; no-evidence cases are shown, not scored as hits."""
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if cases.get("split") != "development" or not cases.get("cases"):
        raise ValueError("Expected a nonempty development case file")
    by_id = {source.document.source_id: source.document for source in sources}
    rows = []
    recalls = []
    for case in cases["cases"]:
        relevant = set()
        if case["source_id"] is not None:
            document = by_id[case["source_id"]]
            relevant = {chunk.chunk_id for chunk in chunk_document(document,
                        chunk_words=chunk_words, overlap_words=overlap_words)
                        if case["passage"] in chunk.text}
            if not relevant:
                raise ValueError(f"Annotated passage not present in a chunk: {case['id']}")
        hits = index.search(case["query"], limit=limit)
        recall = len(relevant.intersection(hit["chunk_id"] for hit in hits)) / len(relevant) if relevant else None
        if recall is not None:
            recalls.append(recall)
        rows.append({"id": case["id"], "query": case["query"], "recall_at_k": recall,
                     "relevant_chunk_ids": sorted(relevant),
                     "retrieved": [{"chunk_id": h["chunk_id"], "source_id": h["source_id"],
                                    "score": h["score"]} for h in hits]})
    return {"split": "development", "recorded_at": datetime.now(timezone.utc).isoformat(),
            "embedding_model": index.embedder.model_id,
            "dimension": index.embedder.dimension, "k": limit,
            "sources": [{"source_id": source.document.source_id,
                         "content_hash": source.document.content_hash} for source in sources],
            "chunk_words": chunk_words, "overlap_words": overlap_words,
            "answerable_cases": len(recalls), "no_evidence_cases": len(rows) - len(recalls),
            "mean_passage_recall_at_k": sum(recalls) / len(recalls) if recalls else None,
            "evidence_hit_rate_at_k": sum(recall > 0 for recall in recalls) / len(recalls) if recalls else None,
            "metric_definition": "Recall counts every chunk containing the annotated substring, including overlapping duplicates. Hit rate counts queries retrieving at least one such chunk.",
            "warning": "Tiny curated development set; not held-out accuracy or factual verification. Nearest results are not truth judgments.",
            "cases": rows}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["index", "search", "evaluate"])
    parser.add_argument("--manifest", type=Path, default=Path("corpus/manifest.json"))
    parser.add_argument("--storage", type=Path, default=Path(".data/qdrant"))
    parser.add_argument("--cache", type=Path, default=Path(".data/models"))
    parser.add_argument("--offline", action="store_true", help="Use cached model files without downloading")
    parser.add_argument("--output", type=Path, help="Also save JSON output as UTF-8")
    parser.add_argument("--cases", type=Path, default=Path("corpus/retrieval_cases.json"))
    parser.add_argument("--query")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--chunk-words", type=int, default=80)
    parser.add_argument("--overlap-words", type=int, default=16)
    args = parser.parse_args(argv)
    if args.command == "search" and (not args.query or not args.query.strip()):
        parser.error("search requires a nonblank --query")
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    if args.chunk_words < 1 or not 0 <= args.overlap_words < args.chunk_words:
        parser.error("Require chunk-words > 0 and 0 <= overlap-words < chunk-words")
    # Validate local data before downloading a model or creating storage.
    sources = load_corpus(args.manifest)
    from qdrant_client import QdrantClient

    embedder = LocalEmbedder(args.cache, offline=args.offline)
    args.storage.mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=str(args.storage))
    try:
        index = EvidenceIndex(client, embedder)
        if args.command == "index":
            count = sum(len(index.index_source(source, chunk_words=args.chunk_words,
                            overlap_words=args.overlap_words)) for source in sources)
            result = {"indexed_sources": len(sources), "chunks": count,
                      "collection": index.collection, "embedding_model": embedder.model_id}
        elif args.command == "search":
            result = {"query": args.query, "results": index.search(args.query, limit=args.limit)}
        else:
            result = evaluate(index, sources, args.cases, limit=args.limit,
                              chunk_words=args.chunk_words, overlap_words=args.overlap_words)
        rendered = json.dumps(result, ensure_ascii=True, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        client.close()


if __name__ == "__main__":
    main()
