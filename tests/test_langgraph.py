"""Verify actual graph execution, plus an opt-in cached-model integration smoke."""

import json
import os
import unittest
from unittest.mock import Mock

from app.evidence.research import build_research_graph, research_claim
from test_baseline import completion
from test_evidence import output
from test_research import decision, tool


class LangGraphTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.with_options.return_value = self.client
        self.index = Mock()
        self.index.search.return_value = [{"chunk_id": "c1", "text": "Quitting decreased."}]

    def test_graph_has_separate_nodes_and_conditional_loop(self):
        graph = build_research_graph(self.client, self.index).get_graph()
        self.assertTrue({"decide", "tools", "assess", "__start__", "__end__"}.issubset(graph.nodes))
        edges = {(edge.source, edge.target) for edge in graph.edges}
        self.assertIn(("tools", "decide"), edges)
        self.assertIn(("decide", "assess"), edges)
        self.assertIn(("decide", "tools"), edges)

    def test_nodes_execute_in_order_and_invocations_are_isolated(self):
        self.client.chat.completions.create.side_effect = [
            decision(tool("search_evidence", {"query": "retention"}, "1")),
            decision(tool("read_passage", {"chunk_id": "c1"}, "2")),
            decision(), completion(output())]
        result = research_claim(self.client, self.index, "Did quitting decrease?")
        self.assertEqual(result["orchestrator"], "langgraph")
        self.assertEqual(result["node_history"], ["decide", "tools", "decide", "tools", "decide", "assess"])
        self.client.chat.completions.create.side_effect = [decision()]
        another = research_claim(self.client, self.index, "An unrelated claim")
        self.assertEqual(another["events"], [])
        self.assertEqual(another["read_passages"], [])
        self.assertEqual(another["node_history"], ["decide", "assess"])

    def test_budget_termination_skips_assessment_node(self):
        self.client.chat.completions.create.return_value = decision(
            tool("search_evidence", {"query": "retention"}, "1"))
        result = research_claim(self.client, self.index, "A claim", max_steps=1)
        self.assertEqual(result["node_history"], ["decide", "tools"])
        self.assertIsNone(result["assessment"])

    @unittest.skipUnless(os.environ.get("RUN_LOCAL_EMBEDDING_SMOKE") == "1",
                         "Opt in to real cached embeddings/Qdrant with RUN_LOCAL_EMBEDDING_SMOKE=1")
    def test_cached_model_qdrant_and_graph_with_scripted_generation(self):
        from app.evidence.runtime import open_evidence_index

        stage = 0
        def scripted_response(**request):
            nonlocal stage
            stage += 1
            if stage == 1:
                return decision(tool("search_evidence", {"query": "hybrid retention randomized trial"}, "search-1"))
            if stage == 2:
                found = json.loads(request["messages"][-1]["content"])["results"]
                self.assertTrue(found)
                return decision(tool("read_passage", {"chunk_id": found[0]["chunk_id"]}, "read-1"))
            if stage == 3:
                return decision()
            passages = json.loads(request["messages"][-1]["content"])["passages"]
            return completion(json.dumps({"verdict": "supported",
                "explanation": "SCRIPTED TEST ONLY: validates graph and citation plumbing, not factual judgment.",
                "suggested_revision": "Inspect the original study before drawing conclusions.",
                "citations": [{"chunk_id": passages[0]["chunk_id"],
                               "quote": passages[0]["text"][:80], "relation": "supports"}]}))

        self.client.chat.completions.create.side_effect = scripted_response
        with open_evidence_index() as index:
            result = research_claim(self.client, index, "Hybrid schedules affect retention", max_seconds=120)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["orchestrator"], "langgraph")
        self.assertEqual(len(result["assessment"]["citations"]), 1)
        self.assertTrue(result["assessment"]["citations"][0]["source"]["source_url"].startswith("https://"))
