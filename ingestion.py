"""Local text ingestion and exact-span word chunking; no model or database needed."""

import argparse
from bisect import bisect_left
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re


def stable_id(*parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class Document:
    document_id: str
    source_id: str
    content_hash: str
    title: str
    source_url: str | None
    source_date: str | None
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    content_hash: str
    index: int
    text: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    word_count: int


def load_document(
    path: Path, *, source_id: str, title: str | None = None,
    source_url: str | None = None, source_date: str | None = None,
) -> Document:
    """Read UTF-8 text, preserving line endings. Source identity is caller-owned.

    Assign a unique source_id per source, independent of its local file path.
    Offsets refer to decoded text (after any UTF-8 BOM), not original file bytes.
    Markdown is kept as text; this is not a Markdown renderer or PDF extractor.
    """
    path = Path(path)
    if path.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("Only .txt and .md files are supported")
    if not source_id.strip():
        raise ValueError("source_id must not be blank")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        text = handle.read()
    if not text.strip():
        raise ValueError("Document must contain nonblank text")
    return Document(
        document_id=stable_id("document-v1", source_id.strip()),
        source_id=source_id.strip(),
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        title=title.strip() if title and title.strip() else path.stem,
        source_url=source_url,
        source_date=source_date,
        text=text,
    )


def chunk_document(document: Document, *, chunk_words: int = 160,
                   overlap_words: int = 32) -> list[Chunk]:
    """Return overlapping windows with exact character spans and line locations.

    Words are non-whitespace spans, not model tokens. This first baseline may
    split sentences or Markdown sections; measure retrieval before tuning it.
    """
    if chunk_words < 1 or not 0 <= overlap_words < chunk_words:
        raise ValueError("Require chunk_words > 0 and 0 <= overlap_words < chunk_words")
    words = list(re.finditer(r"\S+", document.text))
    newlines = [match.start() for match in re.finditer("\n", document.text)]
    chunks = []
    start_word = 0
    while start_word < len(words):
        end_word = min(start_word + chunk_words, len(words))
        start_char = words[start_word].start()
        end_char = words[end_word - 1].end()
        chunks.append(Chunk(
            chunk_id=stable_id("word-window-v1", document.document_id,
                               document.content_hash, str(chunk_words),
                               str(overlap_words), str(start_char), str(end_char)),
            document_id=document.document_id,
            content_hash=document.content_hash,
            index=len(chunks),
            text=document.text[start_char:end_char],
            start_char=start_char,
            end_char=end_char,
            start_line=bisect_left(newlines, start_char) + 1,
            end_line=bisect_left(newlines, end_char - 1) + 1,
            word_count=end_word - start_word,
        ))
        if end_word == len(words):
            break
        start_word = end_word - overlap_words
    return chunks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--title")
    parser.add_argument("--source-url")
    parser.add_argument("--source-date", help="Publication date metadata, if known")
    parser.add_argument("--chunk-words", type=int, default=160)
    parser.add_argument("--overlap-words", type=int, default=32)
    args = parser.parse_args(argv)
    try:
        document = load_document(args.path, source_id=args.source_id, title=args.title,
                                 source_url=args.source_url, source_date=args.source_date)
        chunks = chunk_document(document, chunk_words=args.chunk_words,
                                overlap_words=args.overlap_words)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    metadata = asdict(document)
    del metadata["text"]
    print(json.dumps({"document": metadata, "chunking": {
        "algorithm": "word-window-v1", "chunk_words": args.chunk_words,
        "overlap_words": args.overlap_words,
    }, "chunks": [asdict(chunk) for chunk in chunks]}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
