"""Known-scenario collector contracts. Provider responses are scripted in tests."""

from contextlib import nullcontext, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from tools.claimci import Claim
from tools.claimci_collect import collect_live_citations, collect_retrieval, collect_validation, finalize, main
from test_baseline import completion
from test_evidence import output

ROOT = Path(__file__).resolve().parent.parent


class CollectorTests(unittest.TestCase):
    def test_real_python_socket_attempt_is_blocked_and_worker_state_does_not_leak(self):
        script = '''
import json, socket
from tools.claimci_network import PythonNetworkObserver, NetworkAttemptBlocked
observer = PythonNetworkObserver()
observer.install()
try:
    with observer.case('connection'):
        socket.create_connection(('127.0.0.1', 9), timeout=1)
except NetworkAttemptBlocked:
    pass
assert observer.current is None
print(json.dumps(list(observer.rows.values())))
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual(row["external_requests"], 1)
        self.assertFalse(row["observation_complete"])
        self.assertEqual(row["scope"], "python_socket_events")
        self.assertNotIn("127.0.0.1", result.stdout)

    def test_validation_probes_are_labelled_fixtures_and_not_response_samples(self):
        result = collect_validation()
        self.assertTrue(result["fixture_only"])
        self.assertEqual(result["collection_status"], "complete")
        self.assertTrue(all(row["passed"] for row in result["probe_cases"]))
        self.assertNotIn("response_cases", result)

    def test_failed_retrieval_cannot_become_a_complete_zero_network_observation(self):
        claim = Claim(id="local-retrieval", text="No Python socket attempts", kind="data_locality",
                      paths=["retrieval.py"], required_cases=["cached-query", "missing-evidence"],
                      network_scope="python_socket_events")
        def register(observer):
            observer.ready = True  # Unit fake only; registration itself is tested in a child above.
        with patch("tools.claimci_collect.metadata", return_value={"collection_status":"complete", "source_files":{}}), \
                patch("tools.claimci_network.PythonNetworkObserver.install", register), \
                patch("app.evidence.runtime.open_evidence_index", side_effect=RuntimeError("private local path")):
            result = collect_retrieval(claim)
        self.assertEqual(result["collection_status"], "incomplete")
        self.assertTrue(all(not row["observation_complete"] for row in result["network_cases"]))
        self.assertNotIn("private local path", json.dumps(result))

    def test_source_changes_during_capture_invalidate_the_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source.py").write_text("old", encoding="utf-8")
            original = hashlib.sha256((root / "source.py").read_bytes()).hexdigest()
            (root / "source.py").write_text("new", encoding="utf-8")
            data = {"source_files":{"source.py":original}, "collection_status":"complete",
                    "network_cases":[{"observation_complete":True}]}
            with patch("tools.claimci_collect.ROOT", root):
                result = finalize(data)
            self.assertEqual(result["collection_status"], "source_changed_during_collection")
            self.assertFalse(result["network_cases"][0]["observation_complete"])

    def test_live_collection_requires_explicit_quota_opt_in(self):
        with patch("tools.claimci_collect.subprocess.run") as launch, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(["--profile", "live-citations"])
            self.assertEqual(raised.exception.code, 2)
            launch.assert_not_called()

    def test_live_path_has_one_request_budget_and_no_hidden_repair_retries(self):
        claim = Claim(id="evidence-citations", text="Exact quote", kind="citations",
                      paths=["evidence.py"], required_cases=["supported-retention"])
        provider = Mock()
        provider.chat.completions.create.return_value = completion(output())
        connection = MagicMock()
        connection.__enter__.return_value = provider
        index = Mock()
        index.search.return_value = [{"chunk_id":"c1", "text":"Quitting decreased in this trial."}]
        for hits, expected_count, status in [(index.search.return_value, 1, "complete"), ([], 0, "no_supported_response")]:
            provider.reset_mock()
            index.search.return_value = hits
            with patch.dict(os.environ, {"GROQ_API_KEY":"offline-test-key"}, clear=True), \
                    patch("dotenv.load_dotenv"), \
                    patch("groq.Groq", return_value=connection) as factory, \
                    patch("app.evidence.runtime.open_evidence_index", return_value=nullcontext(index)), \
                    patch("tools.claimci_collect.metadata", side_effect=lambda _: {"source_files":{}, "collection_status":"complete"}):
                result = collect_live_citations(claim)
            self.assertEqual(result["model_requests_attempted"], expected_count)
            self.assertEqual(provider.chat.completions.create.call_count, expected_count)
            self.assertEqual(result["collection_status"], status)
            self.assertEqual(factory.call_args.kwargs["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
