"""Compare the Review Council with its single-reviewer baselines on labelled cases.

Without --live this only validates the dataset and prints the request plan (zero
model calls). Baselines are not extra requests: each reviewer already receives the
same frozen evidence and prompt, so a reviewer's own output is the single-model
baseline. The council costs up to three requests per case; a baseline costs one.

Counts are reported as fractions of a small development set. They are not
accuracy, fairness or hiring-quality claims.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Literal

from pydantic import Field, model_validator

from app.core.paths import ROOT
from app.review.models import Criterion, ReviewInput, StrictModel

Status = Literal["demonstrated", "needs_verification", "not_assessed"]
SYSTEMS = ("reviewer_1", "reviewer_2", "council")
STOP_STATUSES = ("401", "402", "403", "429")


class Expected(StrictModel):
    criterion_id: str
    status: Status
    also_acceptable: list[Status] = Field(default_factory=list)
    must_quote: list[str] = Field(default_factory=list, max_length=3)


class Case(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    label_status: Literal["draft", "author_reviewed", "human_reviewed"]
    note: str = Field(max_length=300)
    job_description: str
    transcript: str
    resume: str = ""
    criteria: list[Criterion]
    expected: list[Expected]

    @model_validator(mode="after")
    def labels_match_criteria(self):
        if sorted(e.criterion_id for e in self.expected) != sorted(c.id for c in self.criteria):
            raise ValueError("Every criterion needs exactly one expected label")
        return self

    def review_input(self):
        return ReviewInput(transcript=self.transcript, job_description=self.job_description,
                           resume=self.resume, criteria=self.criteria)


def load_cases(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [Case.model_validate(item) for item in data["cases"]]
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    for case in cases:
        case.review_input()  # Same limits as the application.
    return cases


def system_findings(report):
    """Map each system to its findings, or None when it produced nothing valid."""
    found = dict.fromkeys(SYSTEMS)
    if report.get("final"):
        found["council"] = report["final"]["findings"]
    for review in report.get("reviews", []):
        key = review["reviewer_id"].casefold().replace(" ", "_")
        if key in found:
            found[key] = review["findings"]
    return found


def score_findings(case, findings):
    if findings is None:
        return None
    by_id = {f["criterion_id"]: f for f in findings}
    rows = []
    for expected in case.expected:
        finding = by_id.get(expected.criterion_id)
        predicted = finding["status"] if finding else None
        quotes = [c["quote"] for c in finding["evidence"]] if finding else []
        rows.append({
            "criterion_id": expected.criterion_id, "expected": expected.status, "predicted": predicted,
            "match": predicted in {expected.status, *expected.also_acceptable},
            "overclaim": predicted == "demonstrated" and "demonstrated" not in {expected.status, *expected.also_acceptable},
            "evidence_expected": len(expected.must_quote),
            "evidence_hit": sum(any(wanted in quote for quote in quotes) for wanted in expected.must_quote),
        })
    return rows


def rescore(case_results, cases):
    """Re-apply the current labels to saved predictions, so edited labels need no new requests."""
    by_id = {case.id: case for case in cases}
    updated = {}
    for case_id, result in case_results.items():
        case = by_id.get(case_id)
        if case is None:
            continue  # Case removed from the dataset since the run.
        scores = {}
        for system, rows in result["scores"].items():
            if rows is None:
                scores[system] = None
                continue
            expected = {e.criterion_id: e for e in case.expected}
            scores[system] = [{**row, "expected": expected[row["criterion_id"]].status,
                               "match": row["predicted"] in {expected[row["criterion_id"]].status, *expected[row["criterion_id"]].also_acceptable},
                               "overclaim": row["predicted"] == "demonstrated" and "demonstrated" not in
                               {expected[row["criterion_id"]].status, *expected[row["criterion_id"]].also_acceptable}}
                              for row in rows]
        updated[case_id] = {**result, "scores": scores, "expected_ids": [e.criterion_id for e in case.expected],
                            "label_status": case.label_status}
    return updated


def summarize(case_results):
    """Aggregate saved case results into per-system counts."""
    summary = {}
    for system in SYSTEMS:
        rows = [row for result in case_results.values() for row in (result["scores"].get(system) or [])]
        total = sum(len(result["expected_ids"]) for result in case_results.values())
        summary[system] = {
            "cases_with_result": sum(1 for r in case_results.values() if r["scores"].get(system) is not None),
            "cases_total": len(case_results),
            "criteria_total": total, "criteria_predicted": len(rows),
            "match": sum(r["match"] for r in rows), "overclaim": sum(r["overclaim"] for r in rows),
            # Strict counts only the primary label, so it also works on older saved results.
            "strict": sum(r["predicted"] == r["expected"] for r in rows),
            "evidence_expected": sum(r["evidence_expected"] for r in rows),
            "evidence_hit": sum(r["evidence_hit"] for r in rows),
        }
    pairs = [(r["scores"]["reviewer_1"], r["scores"]["reviewer_2"]) for r in case_results.values()
             if r["scores"].get("reviewer_1") and r["scores"].get("reviewer_2")]
    both = [(a, b) for left, right in pairs for a, b in zip(left, right)]
    summary["reviewer_agreement"] = {"criteria_compared": len(both),
                                     "same_status": sum(a["predicted"] == b["predicted"] for a, b in both)}
    summary["model_requests"] = sum(r["model_requests"] for r in case_results.values())
    # Runs saved before this field existed have no key, so they are counted as unrecorded.
    recorded = [r for r in case_results.values() if r["status"] == "complete" and "resolution_adjusted" in r]
    summary["judge_labels"] = {
        "complete_councils": sum(r["status"] == "complete" for r in case_results.values()),
        "recorded": len(recorded),
        "changed_by_code": sum(len(r["resolution_adjusted"]) for r in recorded),
    }
    return summary


def disagreements(case_results):
    """Criteria where the systems did not all give the same status."""
    found = []
    for case_id, result in case_results.items():
        by_system = {s: {r["criterion_id"]: r for r in rows} for s, rows in result["scores"].items() if rows}
        for criterion in result["expected_ids"]:
            statuses = {s: rows[criterion]["predicted"] for s, rows in by_system.items() if criterion in rows}
            if len(set(statuses.values())) > 1:
                expected = next(iter(by_system.values()))[criterion]["expected"]
                found.append({"case": case_id, "criterion": criterion, "expected": expected, **statuses})
    return found


def should_stop(report):
    kinds = " ".join((report.get("audit") or {}).get("failure_kinds", {}).values())
    return any(f" {code}" in kinds for code in STOP_STATUSES)


def run_case(case, adapters, evaluate):
    started = time.monotonic()
    report = evaluate(case.review_input(), adapters)
    scores = {system: score_findings(case, found) for system, found in system_findings(report).items()}
    audit = report.get("audit") or {}
    return {
        "label_status": case.label_status, "status": report["status"], "expected_ids": [e.criterion_id for e in case.expected],
        "scores": scores, "model_requests": audit.get("model_requests", 0),
        "failure_kinds": audit.get("failure_kinds", {}), "resolution_adjusted": audit.get("resolution_adjusted", []),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "stopped": should_stop(report),
    }


def plan(cases, done, rerun):
    todo = [c for c in cases if c.id not in done or c.id in rerun]
    return todo, 3 * len(todo)


def print_summary(summary):
    print("system        results  lenient  strict  overclaim  evidence")
    for system in SYSTEMS:
        s = summary[system]
        print(f"{system:<13} {s['cases_with_result']}/{s['cases_total']:<6} {s['match']}/{s['criteria_total']:<6} "
              f"{s['strict']}/{s['criteria_total']:<5} {s['overclaim']:<10} {s['evidence_hit']}/{s['evidence_expected']}")
    a = summary["reviewer_agreement"]
    print(f"reviewer agreement: {a['same_status']}/{a['criteria_compared']} criteria; model requests used: {summary['model_requests']}")
    j = summary["judge_labels"]
    print(f"judge resolution labels changed by code: {j['changed_by_code']} "
          f"(recorded for {j['recorded']} of {j['complete_councils']} complete councils)")


def print_disagreements(case_results):
    rows = disagreements(case_results)
    print(f"\nCriteria where systems differed: {len(rows)}")
    for row in rows:
        print(f"  {row['case']} / {row['criterion']}: expected={row['expected']} "
              + " ".join(f"{s}={row[s]}" for s in SYSTEMS if s in row))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=ROOT / "eval/review_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".data/review_eval.json")
    parser.add_argument("--live", action="store_true", help="Send cases to the configured free models")
    parser.add_argument("--max-requests", type=int, default=9, help="Hard cap on model requests for this invocation")
    parser.add_argument("--rerun", nargs="*", default=[], help="Case IDs to run again")
    parser.add_argument("--report", action="store_true", help="Summarize saved results; no model request")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    saved = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {"cases": {}}
    if args.report:
        if saved["cases"]:
            current = rescore(saved["cases"], cases)
            print_summary(summarize(current))
            print_disagreements(current)
            print("Scored with the current labels; label status counts: "
                  + ", ".join(f"{s}={sum(r['label_status'] == s for r in current.values())}"
                              for s in ("draft", "author_reviewed", "human_reviewed")))
        else:
            print("No saved results.")
        return
    todo, worst_case = plan(cases, saved["cases"], set(args.rerun))
    drafts = sum(c.label_status == "draft" for c in cases)
    print(f"{len(cases)} cases ({drafts} with draft labels); {len(todo)} to run; up to {worst_case} model requests.")
    if not args.live:
        print("Dry run: no model request sent. Add --live to run.")
        return
    if worst_case > args.max_requests:
        todo = todo[: args.max_requests // 3]
        print(f"Capped at {len(todo)} cases by --max-requests {args.max_requests}.")
    if not todo:
        if saved["cases"]:
            print_summary(summarize(saved["cases"]))
        return

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from app.review.council import evaluate
    from app.review.providers import ModelAdapter, configuration

    routes = configuration()  # Enforces the free-only policy before any request.
    for case in todo:
        result = run_case(case, [ModelAdapter(route) for route in routes], evaluate)
        saved["cases"][case.id] = result
        saved["recorded_at"] = datetime.now(timezone.utc).isoformat()
        saved["models"] = [route.public() for route in routes]
        saved["kind"] = "development_evaluation_not_accuracy_claim"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        print(f"{case.id}: {result['status']} ({result['model_requests']} requests) {result['failure_kinds'] or ''}")
        if result["stopped"]:
            print("Stopped early: provider limit or auth error. Wait before running again.")
            break
    print_summary(summarize(saved["cases"]))
    print_disagreements(saved["cases"])


if __name__ == "__main__":
    main()
