"""Offline regression tests: no provider calls, credentials, or judge required."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from debate import Debate
from find_hole import find_hole


def completion(content, tokens=12):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(total_tokens=tokens),
    )


def analysis(quote="Remote work always improves productivity.", **overrides):
    data = dict(
        weakest_claim=quote,
        why_weak="The absolute claim exceeds the evidence.",
        the_attack="Results can vary between occupations.",
        how_to_defend="Limit the claim to the studied population.",
        severity=7,
    )
    data.update(overrides)
    return json.dumps(data)


class DebateTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.create = self.client.chat.completions.create
        self.debate = Debate(self.client, "socratic")

    def test_success_sends_history_and_commits_complete_turns(self):
        self.create.side_effect = [completion("Why?"), completion("What evidence?")]
        self.debate.respond("Remote work helps.")
        self.debate.respond("It reduces commuting.")
        sent = self.create.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in sent],
                         ["system", "user", "assistant", "user"])
        self.assertEqual(self.debate.turn_count(), 2)
        self.assertEqual(self.debate.total_tokens, 24)
        self.assertIn("YOU: It reduces commuting.", self.debate.transcript())
        # Saved request lists must not gain the subsequently generated reply.
        self.assertEqual(len(self.create.call_args_list[0].kwargs["messages"]), 2)

    def test_failure_after_success_preserves_state_and_retry_is_clean(self):
        self.create.return_value = completion("Why?")
        self.debate.respond("First claim")
        before = [dict(m) for m in self.debate.messages]
        self.create.side_effect = TimeoutError("provider timeout")
        with self.assertRaises(TimeoutError):
            self.debate.respond("Second claim")
        self.assertEqual(self.debate.messages, before)
        self.assertEqual(self.debate.total_tokens, 12)
        self.create.side_effect = None
        self.debate.respond("Second claim")
        self.assertEqual(sum(m["content"] == "Second claim" for m in self.debate.messages), 1)

    def test_empty_replies_do_not_commit(self):
        for reply in (None, "", "   "):
            with self.subTest(reply=reply):
                self.create.return_value = completion(reply)
                with self.assertRaises(ValueError):
                    self.debate.respond("A claim")
                self.assertEqual(self.debate.turn_count(), 0)
                self.assertEqual(self.debate.total_tokens, 0)

    def test_missing_usage_accepts_reply(self):
        response = completion("Why?")
        response.usage = None
        self.create.return_value = response
        self.debate.respond("A claim")
        self.assertEqual(self.debate.turn_count(), 1)
        self.assertEqual(self.debate.total_tokens, 0)

    def test_blank_input_never_calls_provider(self):
        with self.assertRaises(ValueError):
            self.debate.respond("  ")
        self.create.assert_not_called()

    def test_long_debate_bounds_context_without_losing_transcript(self):
        for i in range(20):
            self.create.return_value = completion(f"reply {i}: " + "a" * 900)
            self.debate.respond(f"claim {i}: " + "b" * 900)
        sent = self.create.call_args.kwargs["messages"]
        contents = [m["content"] for m in sent]
        self.assertTrue(contents[1].startswith("claim 0:"))
        self.assertTrue(any(text.startswith("claim 18:") for text in contents))
        self.assertFalse(any(text.startswith("claim 5:") for text in contents))
        self.assertLessEqual(len(sent), 12)  # System + opening pair + 4 pairs + current input.
        self.assertEqual(len(self.debate.messages), 41)
        self.assertIn("claim 5:", self.debate.transcript())
        self.assertEqual(self.create.call_count, 20)  # No summarization calls.
        self.assertEqual(self.create.call_args.kwargs["max_completion_tokens"], 768)

    def test_large_historical_turn_does_not_break_context_budget(self):
        self.debate.messages += [{"role":"user", "content":"u" * 30000},
                                 {"role":"assistant", "content":"a" * 30000}]
        sent = self.debate.request_messages("Current claim")
        self.assertLess(sum(len(m["content"]) for m in sent[1:]), 1700)
        self.assertEqual(sent[-1]["content"], "Current claim")
        self.assertEqual(len(self.debate.messages[1]["content"]), 30000)

    def test_oversized_message_never_calls_provider(self):
        with self.assertRaises(ValueError):
            self.debate.respond("x" * 4001)
        self.create.assert_not_called()


class FindHoleTests(unittest.TestCase):
    argument = "Remote work always improves productivity. Commuting takes time."

    def setUp(self):
        self.client = Mock()
        self.create = self.client.chat.completions.create

    def test_exact_quote_is_accepted(self):
        self.create.return_value = completion(analysis())
        result = find_hole(self.client, self.argument)
        self.assertIn(result.weakest_claim, self.argument)
        self.create.assert_called_once()

    def test_invalid_outputs_retry_then_recover(self):
        for invalid in ("not json", "[]", "{}", None,
                        analysis("Invented claim"), analysis(""), analysis(" "),
                        analysis(severity=11)):
            with self.subTest(invalid=invalid):
                self.create.reset_mock()
                self.create.side_effect = [completion(invalid), completion(analysis())]
                result = find_hole(self.client, self.argument)
                self.assertEqual(result.severity, 7)
                self.assertEqual(self.create.call_count, 2)

    def test_invalid_quote_exhausts_retry_budget(self):
        self.create.return_value = completion(analysis("Invented claim"))
        with self.assertRaisesRegex(RuntimeError, "after 2 attempt"):
            find_hole(self.client, self.argument)
        self.assertEqual(self.create.call_count, 2)

    def test_zero_retries_means_one_attempt(self):
        self.create.return_value = completion("invalid json")
        with self.assertRaisesRegex(RuntimeError, "after 1 attempt"):
            find_hole(self.client, self.argument, max_retries=0)
        self.create.assert_called_once()

    def test_invalid_inputs_never_call_provider(self):
        for argument, retries in ((" ", 1), (self.argument, -1)):
            with self.subTest(argument=argument, retries=retries):
                with self.assertRaises(ValueError):
                    find_hole(self.client, argument, max_retries=retries)
        self.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
