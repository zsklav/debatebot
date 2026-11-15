"""Claim CI uses declared artifacts, never an LLM verdict or repository execution."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.claimci import Files, Registry, audit, main, markdown, read_json


class ClaimCITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "service.py"
        self.source.write_text("raise RuntimeError('must never execute repository code')\n", encoding="utf-8")

    def claim(self, kind="citations", **changes):
        base = {"id":"answer-citations", "text":"Answers cite retrieved text.", "kind":kind,
                "paths":["service.py"], "artifact":"observations.json", "required_cases":["normal"]}
        if kind == "benchmark":
            base.update(required_cases=[], metric="requests_per_second", unit="req/s", target=100, min_samples=100)
        return {**base, **changes}

    def registry(self, *claims):
        (self.root / "claims.json").write_text(json.dumps({"version":1, "claims":list(claims)}), encoding="utf-8")

    def artifact(self, **changes):
        data = {"version":1, "fixture_only":False, "generated_at":"2000-01-01T00:00:00Z",
                "recorded_commit":"a" * 40, "source_files":{"service.py":hashlib.sha256(self.source.read_bytes()).hexdigest()},
                "response_cases":[{"id":"normal", "response":{"citations":[{"chunk_id":"c1", "quote":"Known text"}],
                                   "retrieved_passages":[{"chunk_id":"c1", "text":"Known text from a source."}]}}]}
        data.update(changes)
        (self.root / "observations.json").write_text(json.dumps(data), encoding="utf-8")

    def report(self, previous=None):
        return audit(self.root, "claims.json", previous)

    def test_citation_artifact_support_is_explicitly_scoped(self):
        self.registry(self.claim())
        self.artifact()
        result = self.report()
        self.assertEqual(result["summary"]["supported"], 1)
        self.assertIn("Entailment", result["claims"][0]["explanation"])
        self.assertIn("checker executes no repository code", result["scope"])

    def test_missing_citation_is_a_reproducible_counterexample(self):
        self.registry(self.claim())
        self.artifact(response_cases=[{"id":"normal", "response":{"reply":"Uncited answer"}}])
        row = self.report()["claims"][0]
        self.assertEqual(row["status"], "contradicted")
        self.assertIn("normal", row["explanation"])

    def test_ungrounded_quote_fails_but_missing_source_snapshot_is_unverified(self):
        self.registry(self.claim())
        for passages, expected in [([], "unverified"), ([{"chunk_id":"c1", "text":"Different words"}], "contradicted")]:
            self.artifact(response_cases=[{"id":"normal", "response":{"citations":[{"chunk_id":"c1", "quote":"Invented text"}],
                                                                         "retrieved_passages":passages}}])
            self.assertEqual(self.report()["claims"][0]["status"], expected)

    def test_missing_or_fixture_artifact_never_passes(self):
        self.registry(self.claim())
        self.assertEqual(self.report()["claims"][0]["status"], "unverified")
        self.artifact(fixture_only=True)
        self.assertEqual(self.report()["claims"][0]["status"], "unverified")

    def test_case_coverage_is_not_inferred_from_one_successful_example(self):
        self.registry(self.claim(required_cases=["normal", "fallback"]))
        self.artifact()
        self.assertEqual(self.report()["claims"][0]["status"], "unverified")

    def test_static_network_paths_are_review_signals_not_leak_proofs(self):
        self.source.write_text("from groq import Groq as Model\nclient = Model()\n", encoding="utf-8")
        self.registry(self.claim("data_locality"))
        row = self.report()["claims"][0]
        self.assertEqual(row["status"], "unverified")
        self.assertEqual(row["evidence"][0]["line"], 2)
        self.assertIn("groq.Groq", row["evidence"][0]["message"])
        self.assertIn("Static match", row["evidence"][0]["message"])

    def test_network_observation_detects_counterexample_and_incomplete_traces(self):
        self.registry(self.claim("data_locality"))
        for complete, calls, expected in [(True, 1, "contradicted"), (True, 0, "supported"), (False, 0, "unverified"), (False, 1, "contradicted")]:
            self.artifact(network_cases=[{"id":"normal", "observation_complete":complete, "external_requests":calls}])
            self.assertEqual(self.report()["claims"][0]["status"], expected)

    def test_python_event_observations_cannot_be_used_as_full_network_trace(self):
        self.registry(self.claim("data_locality", network_scope="python_socket_events"))
        row = {"id":"normal", "observation_complete":True, "external_requests":0,
               "scope":"python_socket_events", "events":{}}
        self.artifact(network_cases=[row])
        self.assertEqual(self.report()["claims"][0]["status"], "unverified")
        self.artifact(network_cases=[row], monitor={"scope":"python_socket_events", "hook_registered":True})
        result = self.report()["claims"][0]
        self.assertEqual(result["status"], "supported")
        self.assertIn("Native-code", result["explanation"])
        self.artifact(network_cases=[{**row, "external_requests":1}],
                      monitor={"scope":"python_socket_events", "hook_registered":True})
        self.assertEqual(self.report()["claims"][0]["status"], "unverified")

    def test_benchmark_units_samples_and_actual_threshold_matter(self):
        self.registry(self.claim("benchmark"))
        benchmark = {"metric":"requests_per_second", "value":120, "unit":"req/s", "sample_count":500,
                     "duration_seconds":30, "environment":"isolated test", "method":"fixed load"}
        for changes, expected in [({}, "supported"), ({"value":99}, "contradicted"),
                                  ({"unit":"concurrent users"}, "unverified"), ({"sample_count":1}, "unverified")]:
            self.artifact(benchmark={**benchmark, **changes})
            self.assertEqual(self.report()["claims"][0]["status"], expected)

    def test_dependency_change_marks_previously_supported_claim_for_review(self):
        self.registry(self.claim())
        self.artifact()
        previous = self.report()
        unchanged = self.report(previous)["claims"][0]
        self.assertFalse(unchanged["changed"])
        self.source.write_text("# new response implementation\n", encoding="utf-8")
        current = self.report(previous)["claims"][0]
        self.assertTrue(current["changed"])
        self.assertTrue(current["regression"])
        self.assertEqual(current["status"], "unverified")
        self.assertIn("stale", current["explanation"])

    def test_paths_cannot_escape_or_read_secrets(self):
        files = Files(self.root)
        for path in ["../outside", ".env", ".env.production", ".git/config", ".data/postgres-dev.password", "C:/secret", "/etc/passwd"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                files.read(path)

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for raw in ['{"a":1,"a":2}', '{"value":NaN}', '{"value":Infinity}', '{"value":1e9999}']:
            with self.assertRaises(ValueError):
                read_json(raw)

    def test_registry_rejects_duplicate_claim_ids_and_incomplete_contracts(self):
        for claims in [[self.claim(), self.claim()], [self.claim(required_cases=[])], [self.claim("benchmark", target=None)]]:
            with self.assertRaises(ValueError):
                Registry.model_validate({"version":1, "claims":claims})

    def test_dirty_snapshot_does_not_link_evidence_to_an_unrelated_commit(self):
        self.registry(self.claim())
        self.artifact()
        with patch("tools.claimci.commit_sha", return_value="a" * 40), patch("tools.claimci.source_state", return_value="dirty"):
            report = self.report()
        report["claims"][0]["text"] = '<script>bad</script> [click](javascript:x) | misleading'
        rendered = markdown(report, "https://github.com/example/repository")
        self.assertNotIn("/blob/", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("links are withheld", rendered)

    def test_clean_snapshot_has_commit_pinned_links_and_cli_reports(self):
        self.registry(self.claim())
        self.artifact()
        with patch("tools.claimci.commit_sha", return_value="a" * 40), patch("tools.claimci.source_state", return_value="clean"):
            report = self.report()
        self.assertIn("/blob/" + "a" * 40, markdown(report, "https://github.com/example/repository"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root",str(self.root), "--claims","claims.json", "--output","reports"]), 0)
        self.assertTrue((self.root / "reports/report.json").is_file())
        self.artifact(response_cases=[{"id":"normal", "response":{}}])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root",str(self.root), "--claims","claims.json", "--fail-on-contradicted"]), 1)


if __name__ == "__main__":
    unittest.main()
