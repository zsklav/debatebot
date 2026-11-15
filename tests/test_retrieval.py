"""Real local Qdrant with deterministic test vectors; no model downloads in tests."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stderr

try:
    from qdrant_client import QdrantClient
except ModuleNotFoundError:
    QdrantClient = None

from ingestion import load_document
from retrieval import CorpusSource, EvidenceIndex, LocalEmbedder, evaluate, load_corpus, main


class TestEmbedder:
    model_id = "deterministic-test-vectors-v1"
    dimension = 3

    def query(self, text):
        return [float(text.lower().count("cat")), float(text.lower().count("dog")), 0.1]

    def passages(self, texts):
        return [self.query(text) for text in texts]


@unittest.skipUnless(QdrantClient, "Install requirements-retrieval.txt for Qdrant tests")
class RetrievalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = QdrantClient(":memory:")
        self.addCleanup(self.client.close)
        self.embedder = TestEmbedder()
        self.index = EvidenceIndex(self.client, self.embedder)

    def source(self, source_id="cats", text="Cats are curious. Cats like climbing."):
        path = self.root / f"{source_id}.md"
        path.write_text(text, encoding="utf-8")
        return CorpusSource(load_document(path, source_id=source_id,
                            source_url="https://example.org/" + source_id),
                            {"representation": "test fixture", "license": "test-only"})

    def count(self):
        return self.client.count(self.index.collection, exact=True).count

    def test_real_search_preserves_provenance_and_exact_spans(self):
        source = self.source()
        chunks = self.index.index_source(source)
        self.index.index_source(self.source("dogs", "Dogs bark. Dogs run."))
        hit = self.index.search("cat", limit=1)[0]
        self.assertEqual(hit["chunk_id"], chunks[0].chunk_id)
        self.assertEqual(hit["source_url"], "https://example.org/cats")
        self.assertEqual(hit["representation"], "test fixture")
        self.assertEqual(hit["text"], source.document.text[hit["start_char"]:hit["end_char"]])
        self.assertGreater(hit["score"], 0.9)

    def test_rerun_and_rechunk_remove_stale_points_only_for_target_source(self):
        cats = self.source()
        self.index.index_source(cats, chunk_words=3, overlap_words=1)
        self.index.index_source(self.source("dogs", "Dogs bark."))
        self.index.index_source(cats, chunk_words=3, overlap_words=1)
        before = self.count()
        self.assertGreater(before, 2)
        self.index.index_source(cats)
        self.assertEqual(self.count(), 2)
        revised = self.source(text="Cats sleep.")
        self.index.index_source(revised)
        self.assertEqual(self.count(), 2)
        hit = self.index.search("cat", limit=1)[0]
        self.assertEqual(hit["content_hash"], revised.document.content_hash)
        self.assertEqual(hit["text"], "Cats sleep.")

    def test_failed_embedding_does_not_delete_previous_revision(self):
        self.index.index_source(self.source())
        revised = self.source(text="Cats changed.")
        for vectors in ([], [[1.0]], [[float("nan"), 1.0, 0.1]], [[0.0, 0.0, 0.0]]):
            with self.subTest(vectors=vectors), patch.object(self.embedder, "passages", return_value=vectors):
                with self.assertRaises(ValueError):
                    self.index.index_source(revised)
                self.assertEqual(self.count(), 1)
                self.assertNotEqual(self.index.search("cat")[0]["content_hash"], revised.document.content_hash)

    def test_upsert_failure_keeps_previous_revision(self):
        original = self.source()
        self.index.index_source(original)
        with patch.object(self.client, "upsert", side_effect=RuntimeError("storage error")):
            with self.assertRaises(RuntimeError):
                self.index.index_source(self.source(text="Cats changed."))
        self.assertEqual(self.index.search("cat")[0]["content_hash"], original.document.content_hash)

    def test_model_spaces_are_separate_and_missing_index_is_empty(self):
        self.assertEqual(self.index.search("cat"), [])
        self.index.index_source(self.source())
        other_embedder = TestEmbedder()
        other_embedder.model_id = "different-model-same-dimension"
        other = EvidenceIndex(self.client, other_embedder)
        self.assertNotEqual(self.index.collection, other.collection)
        self.assertEqual(other.search("cat"), [])

    def test_search_input_validation(self):
        for query, limit in ((" ", 3), ("cat", 0), ("cat", 21)):
            with self.assertRaises(ValueError):
                self.index.search(query, limit=limit)

    def test_persistent_index_reopens_without_reindexing(self):
        storage = self.root / "qdrant"
        client = QdrantClient(path=str(storage))
        try:
            index = EvidenceIndex(client, self.embedder)
            index.index_source(self.source())
        finally:
            client.close()
        client = QdrantClient(path=str(storage))
        try:
            hits = EvidenceIndex(client, self.embedder).search("cat", limit=1)
            self.assertEqual(hits[0]["source_id"], "cats")
        finally:
            client.close()

    def test_development_recall_and_no_evidence_are_separate(self):
        sources = [self.source(), self.source("dogs", "Dogs bark.")]
        for source in sources:
            self.index.index_source(source)
        cases = self.root / "cases.json"
        cases.write_text(json.dumps({"split": "development", "cases": [
            {"id": "cats", "query": "cat", "source_id": "cats", "passage": "Cats are curious"},
            {"id": "missing", "query": "fish", "source_id": None, "passage": None},
        ]}), encoding="utf-8")
        report = evaluate(self.index, sources, cases, limit=1)
        self.assertEqual(report["mean_passage_recall_at_k"], 1.0)
        self.assertEqual(report["evidence_hit_rate_at_k"], 1.0)
        self.assertEqual(report["no_evidence_cases"], 1)
        self.assertIsNone(report["cases"][1]["recall_at_k"])
        self.assertTrue(report["cases"][1]["retrieved"])


class CorpusTests(unittest.TestCase):
    def test_embedding_guard_rejects_blank_and_truncated_inputs_before_inference(self):
        embedder = object.__new__(LocalEmbedder)
        embedder.encoder = Mock()
        embedder.encoder.model.tokenizer.encode.return_value.overflowing = ["overflow"]
        for text in (" ", "too many tokens"):
            with self.assertRaises(ValueError):
                embedder.passages([text])
            with self.assertRaises(ValueError):
                embedder.query(text)
        embedder.encoder.passage_embed.assert_not_called()
        embedder.encoder.query_embed.assert_not_called()

    def test_real_manifest_includes_only_attributed_sources(self):
        root = Path(__file__).resolve().parent.parent
        sources = load_corpus(root / "corpus/manifest.json")
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(source.metadata["license"] == "CC BY 4.0" for source in sources))
        self.assertTrue(all("editorial summary" in source.metadata["representation"] for source in sources))

    def test_manifest_rejects_duplicate_sources_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "text.md").write_text("A source", encoding="utf-8")
            path = root / "manifest.json"
            entry = {"source_id": "same", "path": "text.md"}
            for entries in ([entry, entry], [{"source_id": "escape", "path": "../outside.md"}]):
                path.write_text(json.dumps({"version": 1, "sources": entries}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_corpus(path)

    def test_cli_rejects_invalid_search_before_model_loading(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["search"])
        self.assertEqual(error.exception.code, 2)
