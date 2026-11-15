"""Exercise real API routes and CLI flows, with only the provider mocked."""

import importlib
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout, nullcontext
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from test_baseline import analysis, completion


class InterfaceTests(unittest.TestCase):
    def test_repeated_analysis_reuses_result_but_new_turn_invalidates_it(self):
        client = self.api()
        sid = self.create_session(client)
        create = self.provider.chat.completions.create
        create.return_value = completion("Why?")
        client.post(f"/api/debate/{sid}/turn", json={"message":"A claim"})
        with patch.object(sys.modules["web.api"], "find_hole") as finding:
            finding.return_value.model_dump.return_value = {"weakest_claim":"A claim"}
            for _ in range(2):
                self.assertEqual(client.post(f"/api/debate/{sid}/findhole").status_code, 200)
            finding.assert_called_once()
            client.post(f"/api/debate/{sid}/turn", json={"message":"A revised claim"})
            self.assertEqual(client.post(f"/api/debate/{sid}/findhole").status_code, 200)
            self.assertEqual(finding.call_count, 2)
        self.assertEqual(create.call_count, 2)

    def test_web_research_uses_web_backend_without_opening_local_index(self):
        client = self.api()
        module = sys.modules["web.api"]
        web = Mock(); web.provider = "test-provider"
        with patch.object(module,"WebEvidence",return_value=web), patch.object(module,"open_evidence_index") as opening, patch.object(
                module,"research_claim",return_value={"status":"partial","source_mode":"web","assessment":None}) as researching:
            response = client.post("/api/research",json={"claim":"A general claim","source":"web"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["search_provider"],"test-provider")
        opening.assert_not_called()
        self.assertEqual(researching.call_args.kwargs["source_mode"],"web")

    def test_missing_web_provider_has_distinct_setup_error(self):
        client = self.api()
        module = sys.modules["web.api"]
        with patch.object(module,"WebEvidence",side_effect=module.WebUnavailable("missing")):
            response = client.post("/api/research",json={"claim":"A claim","source":"web"})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()["code"],"web_unavailable")

    def test_saved_run_can_be_reopened_and_storage_errors_are_not_model_errors(self):
        client = self.api()
        module = sys.modules["web.api"]
        index = Mock()
        with patch.object(module, "open_evidence_index", return_value=nullcontext(index)), patch.object(
                module, "research_claim", return_value={"status": "partial", "assessment": None}):
            response = client.post("/api/research", json={"claim": "A claim"})
        self.assertEqual(response.status_code, 200)
        run_id = response.json()["run_id"]
        saved = client.get(f"/api/runs/{run_id}")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["result"], response.json())
        self.assertEqual(client.get("/api/runs/missing").status_code, 404)
        from storage import StorageUnavailable
        with patch.object(module, "open_evidence_index", return_value=nullcontext(index)), patch.object(
                module, "research_claim", return_value={"status": "partial"}), patch.object(
                module.repository, "save_run", side_effect=StorageUnavailable("private DSN")):
            failed = client.post("/api/research", json={"claim": "A claim"})
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn("private DSN", failed.text)

    def test_stale_turn_returns_conflict_without_overwriting_storage(self):
        client = self.api()
        module = sys.modules["web.api"]
        sid = self.create_session(client)
        self.provider.chat.completions.create.return_value = completion("Why?")
        from storage import SessionConflict
        with patch.object(module.repository, "update_session", side_effect=SessionConflict()):
            response = client.post(f"/api/debate/{sid}/turn", json={"message": "A claim"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(client.get(f"/api/debate/{sid}").json()["turn_count"], 0)

    def test_research_api_exposes_budget_termination_without_false_verdict(self):
        client = self.api()
        module = sys.modules["web.api"]
        index = Mock()
        self.provider.with_options.return_value = self.provider
        from test_research import decision, tool
        self.provider.chat.completions.create.side_effect = [
            decision(tool("read_passage", {"chunk_id": "unknown"}, str(i))) for i in range(4)]
        with patch.object(module, "open_evidence_index", return_value=nullcontext(index)):
            response = client.post("/api/research", json={"claim": "A claim"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "partial")
        self.assertEqual(response.json()["termination_reason"], "step_limit")
        self.assertIsNone(response.json()["assessment"])

    def test_research_api_rejects_invalid_inputs_before_loading_index(self):
        client = self.api()
        with patch.object(sys.modules["web.api"], "open_evidence_index") as opening:
            self.assertEqual(client.post("/api/research", json={"claim": " "}).status_code, 400)
            self.assertEqual(client.post("/api/research", json={"claim": "x" * 4001}).status_code, 422)
            opening.assert_not_called()

    def test_evidence_api_combines_real_analysis_and_citation_validation(self):
        client = self.api()
        module = sys.modules["web.api"]
        index = Mock()
        index.search.return_value = [{"chunk_id": "c1", "text": "Quitting decreased.",
                                     "source_url": "https://example.org/study"}]
        assessment = {"verdict": "insufficient_evidence", "explanation": "This finding does not establish always.",
                      "suggested_revision": "Limit the claim to the studied population.", "citations": []}
        self.provider.chat.completions.create.side_effect = [completion(analysis()), completion(json.dumps(assessment))]
        with patch.object(module, "open_evidence_index", return_value=nullcontext(index)):
            response = client.post("/api/findhole/evidence", json={"argument": "Remote work always improves productivity."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["evidence"]["verdict"], "insufficient_evidence")
        self.assertIn("weakness", response.json())
        index.search.assert_called_once_with("Remote work always improves productivity.", limit=3)

    def test_evidence_api_rejects_invalid_input_before_opening_index(self):
        client = self.api()
        with patch.object(sys.modules["web.api"], "open_evidence_index") as opening:
            for payload, status in (({"argument": " "}, 400), ({"argument": "x" * 4001}, 422),
                                    ({"argument": "A claim", "limit": 6}, 422)):
                self.assertEqual(client.post("/api/findhole/evidence", json=payload).status_code, status)
            opening.assert_not_called()
        self.provider.chat.completions.create.assert_not_called()

    def test_evidence_api_missing_index_is_actionable(self):
        client = self.api()
        module = sys.modules["web.api"]
        with patch.object(module, "open_evidence_index", side_effect=module.EvidenceUnavailable("private path")):
            response = client.post("/api/findhole/evidence", json={"argument": "A claim"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private path", response.text)
        self.provider.chat.completions.create.assert_not_called()

    def test_evidence_api_invalid_generation_returns_error_not_verdict(self):
        client = self.api()
        index = Mock()
        index.search.return_value = [{"chunk_id": "c1", "text": "A passage"}]
        self.provider.chat.completions.create.side_effect = [completion(analysis()), completion("bad json"), completion("bad json")]
        with patch.object(sys.modules["web.api"], "open_evidence_index", return_value=nullcontext(index)):
            response = client.post("/api/findhole/evidence", json={"argument": "Remote work always improves productivity."})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("verdict", response.json())

    def setUp(self):
        self.provider = Mock()
        self.environment = patch.dict(os.environ, {
            "GROQ_API_KEY": "offline-test-key",
            "DEBATEBOT_MODEL": "offline-model",
            "DEBATEBOT_AUTH_REQUIRED": "0",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.dotenv = patch("dotenv.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        self.factory = patch("groq.Groq", return_value=self.provider)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def load_entrypoint(self, name):
        # Restore module cache after each test to avoid leaking fake credentials
        # or mocked clients to other tests in the same Python process.
        previous = sys.modules.pop(name, None)

        def restore():
            sys.modules.pop(name, None)
            if previous is not None:
                sys.modules[name] = previous

        self.addCleanup(restore)
        return importlib.import_module(name)

    def api(self):
        module = self.load_entrypoint("web.api")
        client = TestClient(module.app, raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client

    def create_session(self, client):
        response = client.post("/api/debate", json={"persona_key": "socratic"})
        self.assertEqual(response.status_code, 200)
        return response.json()["session_id"]

    def test_api_serves_ui_personas_and_rejects_invalid_inputs(self):
        client = self.api()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(len(client.get("/api/personas").json()["personas"]), 3)
        self.assertEqual(client.get("/api/health").json()["status"], "ok")
        self.assertEqual(client.post("/api/debate", json={"persona_key": "missing"}).status_code, 400)
        self.assertEqual(client.get("/api/debate/missing").status_code, 404)
        sid = self.create_session(client)
        self.assertEqual(client.post(f"/api/debate/{sid}/turn", json={"message": " "}).status_code, 400)
        self.assertEqual(client.post(f"/api/debate/{sid}/score").status_code, 400)
        self.assertEqual(client.post(f"/api/debate/{sid}/findhole").status_code, 400)
        self.assertEqual(client.post("/api/findhole", json={"argument": " "}).status_code, 400)
        self.provider.chat.completions.create.assert_not_called()

    def test_api_debate_analysis_and_scoring(self):
        client = self.api()
        sid = self.create_session(client)
        claim = "Remote work always improves productivity."
        create = self.provider.chat.completions.create
        create.return_value = completion("What evidence supports always?")
        response = client.post(f"/api/debate/{sid}/turn", json={"message": claim})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["turn_count"], 1)
        self.assertEqual(response.json()["total_tokens"], 12)
        self.assertIn(claim, client.get(f"/api/debate/{sid}").json()["transcript"])
        create.return_value = completion(analysis())
        for path, body in ((f"/api/debate/{sid}/findhole", None),
                           ("/api/findhole", {"argument": claim})):
            response = client.post(path, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["weakest_claim"], claim)
        payload = {axis: {"score": 5, "rationale": "Needs support."} for axis in
                   ("logical_coherence", "evidence_quality", "opposition_handling", "clarity")}
        payload.update(overall=5, summary="Needs evidence.")
        create.return_value = completion(json.dumps(payload))
        response = client.post(f"/api/debate/{sid}/score")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overall"], 5)

    def test_api_failed_turn_preserves_session_for_retry(self):
        client = self.api()
        sid = self.create_session(client)
        create = self.provider.chat.completions.create
        create.side_effect = TimeoutError("simulated failure")
        response = client.post(f"/api/debate/{sid}/turn", json={"message": "A claim"})
        self.assertEqual(response.status_code, 500)
        stored = client.get(f"/api/debate/{sid}").json()
        self.assertEqual(stored["turn_count"], 0)
        self.assertEqual(stored["transcript"], "")
        create.side_effect = None
        create.return_value = completion("Why?")
        self.assertEqual(client.post(f"/api/debate/{sid}/turn", json={"message": "A claim"}).status_code, 200)
        self.assertEqual(client.get(f"/api/debate/{sid}").json()["turn_count"], 1)

    def test_cli_multiturn_and_commands(self):
        cli = self.load_entrypoint("chat")
        self.provider.chat.completions.create.return_value = completion("Why?")
        output = io.StringIO()
        with patch("builtins.input", side_effect=["1", "A claim", "/tokens", "/transcript", "/exit"]), redirect_stdout(output):
            cli.main()
        self.assertIn("YOU: A claim", output.getvalue())
        self.assertIn("12 tokens", output.getvalue())
        self.assertIn("Debate ended after 1 turn(s).", output.getvalue())

    def test_cli_standalone_and_compare(self):
        cli = self.load_entrypoint("chat")
        create = self.provider.chat.completions.create
        create.return_value = completion(analysis())
        output = io.StringIO()
        with patch("builtins.input", side_effect=["5", "Remote work always improves productivity.", ""]), redirect_stdout(output):
            cli.main()
        self.assertIn("WEAKEST CLAIM", output.getvalue())
        create.reset_mock()
        create.return_value = completion("Why?")
        with patch("builtins.input", side_effect=["4", "A claim"]), redirect_stdout(io.StringIO()):
            cli.main()
        self.assertEqual(create.call_count, 3)
