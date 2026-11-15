"""Scripted native tool calls test control flow, not live agent intelligence."""

import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from app.evidence.research import research_claim
from groq import APITimeoutError
from test_baseline import completion
from test_evidence import output


def tool(name, args, identifier):
    return NS(id=identifier, function=NS(name=name, arguments=json.dumps(args)))


def decision(*calls):
    return NS(choices=[NS(message=NS(content="Finished" if not calls else None, tool_calls=list(calls)))])


class ResearchTests(unittest.TestCase):
    def test_web_reads_fetch_sources_and_assess_only_fetched_text(self):
        self.index.search.return_value = [{"chunk_id":"c1","text":"Search snippet only"}]
        self.index.read_passage.return_value = {"chunk_id":"c1","text":"Quitting decreased.", "source_url":"https://example.org/study"}
        self.create.side_effect = [decision(tool("search_evidence", {"query":"retention"}, "1")),
            decision(tool("read_passage", {"chunk_id":"c1"}, "2")),decision(),completion(output())]
        result = research_claim(self.client,self.index,"A claim",source_mode="web")
        self.assertEqual(result["source_mode"],"web")
        self.assertEqual(result["assessment"]["verdict"],"supported")
        self.index.read_passage.assert_called_once()
        self.assertEqual(result["read_passages"][0]["text"],"Quitting decreased.")

    def test_unavailable_provider_stops_without_fake_evidence_verdict(self):
        from app.evidence.web_retrieval import WebUnavailable
        self.index.search.side_effect = WebUnavailable("provider unavailable")
        self.create.side_effect = [decision(tool("search_evidence", {"query":"retention"}, "1")),decision()]
        result = research_claim(self.client,self.index,"A claim",source_mode="web")
        self.assertEqual(result["termination_reason"],"tool_error")
        self.assertIsNone(result["assessment"])

    def test_truncated_model_response_is_partial_not_completion(self):
        response = decision()
        response.choices[0].finish_reason = "length"
        self.create.return_value = response
        result = research_claim(self.client, self.index, "A claim")
        self.assertEqual(result["termination_reason"], "output_limit")
        self.assertIsNone(result["assessment"])

    def test_provider_timeout_returns_partial_evidence(self):
        self.create.side_effect = APITimeoutError(request=Mock())
        result = research_claim(self.client, self.index, "A claim")
        self.assertEqual(result["termination_reason"], "provider_timeout")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["assessment"])

    def setUp(self):
        self.client = Mock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        self.index = Mock()
        self.index.search.return_value = [{"chunk_id": "c1", "text": "Quitting decreased in this trial.",
                                          "source_url": "https://example.org/study"}]

    def test_search_read_finish_assesses_only_inspected_passages(self):
        self.create.side_effect = [decision(tool("search_evidence", {"query": "retention"}, "1")),
            decision(tool("read_passage", {"chunk_id": "c1"}, "2")), decision(), completion(output())]
        result = research_claim(self.client, self.index, "Did quitting decrease?")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["assessment"]["verdict"], "supported")
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual([event["tool"] for event in result["events"]], ["search_evidence", "read_passage"])

    def test_query_reformulation_after_empty_results(self):
        self.index.search.side_effect = [[], self.index.search.return_value]
        self.create.side_effect = [decision(tool("search_evidence", {"query": "first"}, "1")),
            decision(tool("search_evidence", {"query": "second"}, "2")),
            decision(tool("read_passage", {"chunk_id": "c1"}, "3")), decision(), completion(output())]
        result = research_claim(self.client, self.index, "A claim")
        self.assertEqual(result["searches"], 2)
        self.assertEqual(result["status"], "complete")

    def test_unread_previews_cannot_become_final_evidence(self):
        self.create.side_effect = [decision(tool("search_evidence", {"query": "retention"}, "1")), decision()]
        result = research_claim(self.client, self.index, "A claim")
        self.assertEqual(result["assessment"]["verdict"], "insufficient_evidence")
        self.assertEqual(result["assessment"]["evidence_status"], "candidates_not_inspected")
        self.assertIn("Search returned candidate passages", result["assessment"]["explanation"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["inspected_count"], 0)
        self.assertEqual(self.create.call_count, 2)

    def test_no_results_and_no_search_are_distinguished(self):
        self.create.side_effect = [decision()]
        result = research_claim(self.client, self.index, "A claim")
        self.assertEqual(result["assessment"]["evidence_status"], "not_searched")
        self.index.search.return_value = []
        self.create.side_effect = [decision(tool("search_evidence", {"query": "unknown"}, "1")), decision()]
        result = research_claim(self.client, self.index, "A claim")
        self.assertEqual(result["assessment"]["evidence_status"], "no_candidates")

    def test_step_exhaustion_returns_partial_without_verdict(self):
        self.create.return_value = decision(tool("search_evidence", {"query": "retention"}, "1"))
        result = research_claim(self.client, self.index, "A claim", max_steps=1)
        self.assertEqual(result["termination_reason"], "step_limit")
        self.assertIsNone(result["assessment"])

    def test_parallel_calls_cannot_exceed_tool_budget(self):
        self.create.return_value = decision(tool("search_evidence", {"query": "first"}, "1"),
                                             tool("search_evidence", {"query": "second"}, "2"))
        result = research_claim(self.client, self.index, "A claim", max_tool_calls=1)
        self.assertEqual(result["termination_reason"], "tool_limit")
        self.assertEqual(self.index.search.call_count, 1)

    def test_unknown_tools_and_unseen_ids_are_not_executed(self):
        self.create.side_effect = [decision(tool("execute_code", {"code": "malicious"}, "1")),
            decision(tool("read_passage", {"chunk_id": "unknown"}, "2")), decision()]
        result = research_claim(self.client, self.index, "A claim")
        self.assertTrue(all("error" in event["output"] for event in result["events"]))
        self.index.search.assert_not_called()

    def test_search_error_can_be_followed_by_reformulation(self):
        self.index.search.side_effect = RuntimeError("private storage path")
        self.create.side_effect = [decision(tool("search_evidence", {"query": "retention"}, "1")), decision()]
        result = research_claim(self.client, self.index, "A claim")
        self.assertNotIn("private storage path", json.dumps(result))
        self.assertIn("error", result["events"][0]["output"])

    def test_deadline_before_request_prevents_provider_call(self):
        clock = Mock(side_effect=[0, 50, 51])
        result = research_claim(self.client, self.index, "A claim", clock=clock)
        self.assertEqual(result["termination_reason"], "deadline")
        self.create.assert_not_called()

    def test_three_search_limit_is_enforced_by_code(self):
        self.create.side_effect = [decision(tool("search_evidence", {"query": str(i)}, str(i))) for i in range(4)] + [decision()]
        result = research_claim(self.client, self.index, "A claim", max_steps=5)
        self.assertEqual(self.index.search.call_count, 3)
        self.assertIn("error", result["events"][-1]["output"])

    def test_invalid_budgets_and_blank_input_fail_before_provider(self):
        for claim, kwargs in ((" ", {}), ("x" * 4001, {}), ("a", {"max_steps": 0}),
                              ("a", {"max_tool_calls": 11}), ("a", {"max_seconds": 0})):
            with self.assertRaises(ValueError):
                research_claim(self.client, self.index, claim, **kwargs)
        self.create.assert_not_called()
