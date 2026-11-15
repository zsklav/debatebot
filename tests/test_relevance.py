"""Relevance abstention and cost controls; no live LLM or embedding downloads."""

from pathlib import Path
import unittest
from unittest.mock import Mock

from evidence import assess_claim, analyze_argument
from relevance import RelevantLocalIndex, screen
from research import research_claim
from test_research import decision, tool
from test_baseline import completion, analysis


def passage():
    return {"chunk_id":"hybrid", "title":"Hybrid schedules, retention and performance",
            "text":Path("corpus/hybrid_retention.md").read_text(encoding="utf-8")}


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.raw = Mock()
        self.raw.search.return_value = [passage()]
        self.index = RelevantLocalIndex(self.raw)
        self.client = Mock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create

    def test_nearest_but_unrelated_matches_are_rejected_even_with_high_score(self):
        hit = {**passage(), "score": .99}
        for query in ["CEO not smart", "Does remote work lower blood pressure?",
                      "How does hybrid school attendance affect examination results?", "hybrid work"]:
            with self.subTest(query=query):
                self.assertEqual(screen(query, [hit]), [])

    def test_covered_outcomes_and_common_synonyms_are_not_blocked(self):
        for query in ["Did hybrid work reduce quitting?", "Does working from home improve employee retention?",
                      "Did hybrid employees receive worse performance reviews?", "Did resignations decrease?"]:
            with self.subTest(query=query):
                self.assertEqual(len(screen(query, [passage()])), 1)

    def test_unrelated_research_stops_before_planner_with_zero_llm_calls(self):
        result = research_claim(self.client, self.index, "CEO not smart")
        self.assertEqual(result["termination_reason"], "no_relevant_evidence")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["assessment"]["verdict"], "insufficient_evidence")
        self.assertEqual(result["rejected_candidate_count"], 1)
        self.assertEqual(result["read_passages"], [])
        self.create.assert_not_called()
        self.raw.search.assert_called_once()

    def test_direct_assessment_abstains_without_paying_to_assess_irrelevant_text(self):
        result = assess_claim(self.client, self.index, "CEO not smart")
        self.assertEqual(result["evidence_status"], "no_relevant_evidence")
        self.assertEqual(result["citations"], [])
        self.create.assert_not_called()

    def test_selected_weak_claim_is_screened_separately_from_rhetorical_diagnosis(self):
        self.create.return_value = completion(analysis("CEO not smart"))
        result = analyze_argument(self.client, self.index, "CEO not smart")
        self.assertIn("weakness", result)
        self.assertEqual(result["evidence"]["verdict"], "insufficient_evidence")
        self.create.assert_called_once()  # Diagnosis only; no assessment call.

    def test_two_empty_agent_searches_stop_before_third_planning_call(self):
        self.raw.search.side_effect = [[passage()], [], []]  # Preflight + two attempts.
        self.create.side_effect = [decision(tool("search_evidence", {"query":"retention"}, str(i)))
                                   for i in range(2)]
        result = research_claim(self.client, self.index, "Hybrid work reduces quitting")
        self.assertEqual(result["termination_reason"], "no_relevant_evidence")
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(result["assessment"]["citations"], [])

    def test_agent_query_cannot_replace_the_original_claim_relevance_target(self):
        scoped = self.index.for_claim("CEO not smart")
        self.assertEqual(scoped.search("hybrid retention", limit=2), [])

    def test_web_mode_is_not_blocked_by_local_keyword_policy(self):
        self.create.return_value = decision()
        web = Mock()
        result = research_claim(self.client, web, "CEO intelligence", source_mode="web")
        self.assertEqual(result["source_mode"], "web")
        self.create.assert_called_once()
