"""Evaluation harness contracts. Fake reports only; no model or network calls."""

import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from tools import review_eval

CASES = Path(__file__).resolve().parents[1] / "eval" / "review_cases.json"


def finding(criterion, status, quote=None):
    return {"criterion_id": criterion, "status": status, "evidence": [{"source_id": "s", "quote": quote}] if quote else []}


class ReviewEvalTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case.id: case for case in review_eval.load_cases(CASES)}

    def test_dataset_is_valid_and_never_claims_independent_review(self):
        self.assertGreaterEqual(len(self.cases), 6)
        # No second, independent reviewer exists, so human_reviewed must not appear.
        self.assertTrue(all(case.label_status in {"draft", "author_reviewed"} for case in self.cases.values()))

    def test_missing_label_is_rejected(self):
        data = self.cases["caching-limited-admission"].model_dump()
        data["expected"] = []
        with self.assertRaises(ValueError):
            review_eval.Case.model_validate(data)

    def test_score_counts_match_overclaim_and_evidence(self):
        case = self.cases["caching-limited-admission"]  # primary needs_verification
        rows = review_eval.score_findings(case, [finding("caching", "needs_verification", "I added a 60-second TTL")])
        self.assertTrue(rows[0]["match"])
        self.assertFalse(rows[0]["overclaim"])
        self.assertEqual((rows[0]["evidence_hit"], rows[0]["evidence_expected"]), (1, 1))
        rows = review_eval.score_findings(self.cases["caching-injection-in-answer"], [finding("caching", "demonstrated", "I used a cache once")])
        self.assertFalse(rows[0]["match"])
        self.assertTrue(rows[0]["overclaim"])

    def test_also_acceptable_status_matches(self):
        case = self.cases["caching-off-topic"]
        rows = review_eval.score_findings(case, [finding("caching", "needs_verification")])
        self.assertTrue(rows[0]["match"])
        self.assertFalse(rows[0]["overclaim"])

    def test_no_result_is_not_scored_as_wrong(self):
        self.assertIsNone(review_eval.score_findings(self.cases["caching-off-topic"], None))

    def test_partial_report_scores_only_valid_reviewer(self):
        report = {"status": "partial", "final": None, "errors": ["reviewer_1_request_or_validation_failed"],
                  "reviews": [{"reviewer_id": "Reviewer 2", "findings": [finding("caching", "not_assessed")]}],
                  "audit": {"model_requests": 2, "failure_kinds": {"reviewer_1": "ReviewProviderError <- HTTPStatusError 429"}}}
        result = review_eval.run_case(self.cases["caching-off-topic"], [], lambda payload, adapters: report)
        self.assertIsNone(result["scores"]["reviewer_1"])
        self.assertIsNone(result["scores"]["council"])
        self.assertTrue(result["scores"]["reviewer_2"][0]["match"])
        self.assertTrue(result["stopped"])
        summary = review_eval.summarize({"caching-off-topic": result})
        self.assertEqual(summary["council"]["cases_with_result"], 0)
        self.assertEqual(summary["reviewer_agreement"]["criteria_compared"], 0)
        self.assertEqual(summary["model_requests"], 2)

    def test_strict_score_and_disagreements_show_what_lenient_matching_hides(self):
        case = self.cases["caching-off-topic"]  # primary not_assessed, needs_verification also accepted
        report = {"status": "complete", "errors": [], "audit": {"model_requests": 3},
                  "reviews": [{"reviewer_id": "Reviewer 1", "findings": [finding("caching", "not_assessed")]},
                              {"reviewer_id": "Reviewer 2", "findings": [finding("caching", "needs_verification")]}],
                  "final": {"findings": [finding("caching", "needs_verification")]}}
        result = review_eval.run_case(case, [], lambda payload, adapters: report)
        summary = review_eval.summarize({case.id: result})
        self.assertEqual(summary["reviewer_2"]["match"], 1)
        self.assertEqual(summary["reviewer_2"]["strict"], 0)
        self.assertEqual(summary["reviewer_1"]["strict"], 1)
        rows = review_eval.disagreements({case.id: result})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reviewer_1"], "not_assessed")

    def test_edited_labels_rescore_saved_predictions_without_new_requests(self):
        case = self.cases["caching-limited-admission"]  # primary needs_verification, nothing else accepted
        saved = {case.id: {"label_status": "draft", "status": "complete", "expected_ids": ["caching"], "model_requests": 3,
                           "failure_kinds": {}, "elapsed_seconds": 1, "stopped": False,
                           "scores": {"reviewer_1": [{"criterion_id": "caching", "expected": "demonstrated",
                                                      "predicted": "demonstrated", "match": True, "overclaim": False,
                                                      "evidence_expected": 1, "evidence_hit": 1}],
                                      "reviewer_2": None, "council": None}}}
        updated = review_eval.rescore(saved, [case])
        row = updated[case.id]["scores"]["reviewer_1"][0]
        self.assertEqual((row["expected"], row["match"], row["overclaim"]), ("needs_verification", False, True))
        self.assertEqual(updated[case.id]["label_status"], case.label_status)
        self.assertIsNone(updated[case.id]["scores"]["council"])

    def test_judge_label_changes_are_counted_only_where_recorded(self):
        case = self.cases["caching-off-topic"]
        def report(adjusted):
            audit = {"model_requests": 3}
            if adjusted is not None:
                audit["resolution_adjusted"] = adjusted
            return {"status": "complete", "errors": [], "audit": audit, "reviews": [],
                    "final": {"findings": [finding("caching", "not_assessed")]}}
        change = [{"criterion_id": "caching", "from": "unresolved", "to": "agreed"}]
        new = review_eval.run_case(case, [], lambda payload, adapters: report(change))
        old = review_eval.run_case(case, [], lambda payload, adapters: report(None))
        old.pop("resolution_adjusted")  # Results saved before the field existed.
        summary = review_eval.summarize({"new": new, "old": old})
        self.assertEqual(summary["judge_labels"], {"complete_councils": 2, "recorded": 1, "changed_by_code": 1})

    def test_resume_is_passed_to_the_review_input(self):
        self.assertTrue(self.cases["hard-resume-claim-only"].review_input().resume)
        self.assertGreaterEqual(len(self.cases), 9)

    def test_dry_run_writes_nothing_and_reports_worst_case(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                review_eval.main(["--output", str(output)])
            self.assertFalse(output.exists())
            self.assertIn("Dry run", buffer.getvalue())
            self.assertIn(f"up to {3 * len(self.cases)} model requests", buffer.getvalue())

    def test_finished_cases_are_skipped_unless_rerun(self):
        cases = list(self.cases.values())
        todo, worst = review_eval.plan(cases, {cases[0].id: {}}, set())
        self.assertEqual((len(todo), worst), (len(cases) - 1, 3 * (len(cases) - 1)))
        todo, _ = review_eval.plan(cases, {cases[0].id: {}}, {cases[0].id})
        self.assertEqual(len(todo), len(cases))


if __name__ == "__main__":
    unittest.main()
