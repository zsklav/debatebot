"""Verify configuration precedence and the actual model sent by each role."""

import json
import os
import unittest
from unittest.mock import Mock, patch

from app.debate.engine import Debate
from tools.eval_findhole import judge
from app.debate.find_hole import ArgumentWeakness, find_hole
from app.core.model_config import DEFAULT_MODEL, ROLE_ENV, get_model
from app.debate.scorer import score_argument
from test_baseline import analysis, completion


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        # Never depend on local credentials or model settings while testing.
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_original_default_for_every_role(self):
        for role in ROLE_ENV:
            self.assertEqual(get_model(role), DEFAULT_MODEL)

    def test_shared_role_and_explicit_precedence(self):
        os.environ["DEBATEBOT_MODEL"] = "shared-test-model"
        self.assertEqual(get_model("debate"), "shared-test-model")
        os.environ["DEBATEBOT_JUDGE_MODEL"] = "judge-test-model"
        self.assertEqual(get_model("judge"), "judge-test-model")
        self.assertEqual(get_model("judge", "explicit-model"), "explicit-model")
        self.assertEqual(get_model("debate"), "shared-test-model")

    def test_environment_is_read_after_import(self):
        self.assertEqual(get_model("debate"), DEFAULT_MODEL)
        os.environ["DEBATEBOT_MODEL"] = " changed-model "
        self.assertEqual(get_model("debate"), "changed-model")

    def test_blank_settings_fail_early(self):
        with self.assertRaises(ValueError):
            get_model("debate", " ")
        os.environ["DEBATEBOT_MODEL"] = ""
        with self.assertRaises(ValueError):
            get_model("judge")
        os.environ["DEBATEBOT_JUDGE_MODEL"] = "valid-model"
        self.assertEqual(get_model("judge"), "valid-model")

    def test_debate_passes_selected_model_and_freezes_it_per_session(self):
        client = Mock()
        client.chat.completions.create.return_value = completion("Why?")
        os.environ["DEBATEBOT_DEBATE_MODEL"] = "debate-test-model"
        debate = Debate(client, "socratic")
        os.environ["DEBATEBOT_DEBATE_MODEL"] = "new-model"
        debate.respond("A claim")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"],
                         "debate-test-model")
        self.assertEqual(Debate(client, "socratic", model="override").model, "override")

    def test_analyzer_passes_role_model_and_override(self):
        client = Mock()
        client.chat.completions.create.return_value = completion(analysis())
        os.environ["DEBATEBOT_FINDHOLE_MODEL"] = "analysis-test-model"
        find_hole(client, "Remote work always improves productivity.")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"],
                         "analysis-test-model")
        find_hole(client, "Remote work always improves productivity.", model="override")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "override")

    def test_scorer_passes_role_model_and_override(self):
        client = Mock()
        payload = {axis: {"score": 5, "rationale": "Limited support."} for axis in
                   ("logical_coherence", "evidence_quality", "opposition_handling", "clarity")}
        payload.update(overall=5, summary="Needs evidence.")
        client.chat.completions.create.return_value = completion(json.dumps(payload))
        os.environ["DEBATEBOT_SCORER_MODEL"] = "scorer-test-model"
        score_argument(client, "YOU: A claim")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"],
                         "scorer-test-model")
        score_argument(client, "YOU: A claim", model="override")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "override")

    def test_judge_uses_injected_client_without_api_key(self):
        client = Mock()
        client.chat.completions.create.return_value = completion(
            json.dumps({"verdict": "HIT", "reasoning": "Same weakness."}))
        actual = ArgumentWeakness.model_validate_json(analysis())
        os.environ["DEBATEBOT_JUDGE_MODEL"] = "judge-test-model"
        result = judge("An argument", "Overgeneralization", actual, client=client)
        self.assertEqual(result.verdict, "HIT")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"],
                         "judge-test-model")
        judge("An argument", "Overgeneralization", actual, client=client, model="override")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "override")
