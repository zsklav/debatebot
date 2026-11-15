"""Check source identity, exact spans, overlap, and ingestion failure paths."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from app.evidence.ingestion import chunk_document, load_document, main


class IngestionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "source.md"

    def document(self, text="one two three\nfour five six\nseven eight"):
        self.path.write_bytes(text.encode("utf-8"))
        return load_document(self.path, source_id="test-source", source_url="https://example.org/study")

    def test_exact_spans_overlap_and_coverage(self):
        document = self.document()
        chunks = chunk_document(document, chunk_words=4, overlap_words=1)
        self.assertEqual([c.text for c in chunks],
                         ["one two three\nfour", "four five six\nseven", "seven eight"])
        self.assertEqual([(c.start_line, c.end_line) for c in chunks], [(1, 2), (2, 3), (3, 3)])
        for chunk in chunks:
            self.assertEqual(chunk.text, document.text[chunk.start_char:chunk.end_char])
        self.assertEqual(chunks[-1].end_char, len(document.text))
        self.assertTrue(all(c.word_count <= 4 for c in chunks))

    def test_repeat_ingestion_is_stable_and_path_independent(self):
        document = self.document()
        moved = self.path.with_name("moved.txt")
        moved.write_bytes(self.path.read_bytes())
        same_source = load_document(moved, source_id="test-source")
        self.assertEqual(document.document_id, same_source.document_id)
        self.assertEqual(chunk_document(document), chunk_document(same_source))

    def test_revisions_change_chunks_but_preserve_source_identity(self):
        first = self.document("first revision")
        second = self.document("second revision")
        self.assertEqual(first.document_id, second.document_id)
        self.assertNotEqual(first.content_hash, second.content_hash)
        self.assertNotEqual(chunk_document(first)[0].chunk_id, chunk_document(second)[0].chunk_id)

    def test_settings_and_distinct_sources_have_distinct_chunk_ids(self):
        document = self.document()
        other = load_document(self.path, source_id="different-source")
        self.assertNotEqual(document.document_id, other.document_id)
        self.assertNotEqual(chunk_document(document)[0].chunk_id, chunk_document(other)[0].chunk_id)
        self.assertNotEqual(chunk_document(document)[0].chunk_id,
                            chunk_document(document, chunk_words=4, overlap_words=0)[0].chunk_id)

    def test_no_redundant_tail_and_zero_overlap(self):
        document = self.document("a b c d e f")
        self.assertEqual(len(chunk_document(document, chunk_words=4, overlap_words=2)), 2)
        chunks = chunk_document(document, chunk_words=3, overlap_words=0)
        self.assertEqual([c.text for c in chunks], ["a b c", "d e f"])
        self.assertEqual(len(chunk_document(document)), 1)

    def test_unicode_bom_and_crlf_are_traceable(self):
        self.path.write_bytes(b"\xef\xbb\xbf" + "  caf\u00e9\r\n\u0939\u093f\u0902\u0926\u0940  ".encode("utf-8"))
        document = load_document(self.path, source_id="unicode")
        chunk = chunk_document(document)[0]
        self.assertIn("\r\n", chunk.text)
        self.assertEqual((chunk.start_line, chunk.end_line), (1, 2))
        self.assertEqual(chunk.text, document.text[chunk.start_char:chunk.end_char])

    def test_invalid_documents_and_parameters(self):
        document = self.document()
        for size, overlap in ((0, 0), (3, -1), (3, 3), (3, 4)):
            with self.subTest(size=size, overlap=overlap), self.assertRaises(ValueError):
                chunk_document(document, chunk_words=size, overlap_words=overlap)
        with self.assertRaises(ValueError):
            load_document(self.path, source_id=" ")
        with self.assertRaises(ValueError):
            load_document(self.path.with_suffix(".pdf"), source_id="test")
        self.path.write_text(" \n ", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_document(self.path, source_id="test")
        self.path.write_bytes(b"\xff\xff")
        with self.assertRaises(UnicodeError):
            load_document(self.path, source_id="test")

    def test_cli_outputs_inspectable_json_and_reports_errors(self):
        self.document()
        output = io.StringIO()
        with redirect_stdout(output):
            main([str(self.path), "--source-id", "cli-source", "--chunk-words", "4", "--overlap-words", "1"])
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["document"]["source_id"], "cli-source")
        self.assertEqual(len(payload["chunks"]), 3)
        self.assertNotIn("text", payload["document"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main([str(self.path), "--source-id", "test", "--chunk-words", "0"])
        self.assertEqual(error.exception.code, 2)
