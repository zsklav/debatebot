"""Retrieval recall and status precision on the labelled review cases.

Recall: for each must_quote line in the labels, is a source containing it among the
pieces sent to the reviewers? This runs the real local retrieval (BGE + Qdrant) and
makes zero model requests.

Precision: from saved eval results (.data/review_eval.json), when a system said
"demonstrated", how often did the label accept "demonstrated"? Also zero requests.

Both use a tiny, author-labelled development set. They are not accuracy claims.
"""

import argparse
import json
from pathlib import Path

from app.core.paths import ROOT
from tools.review_eval import SYSTEMS, load_cases, rescore


def recall_rows(case, encoder=None):
    """One row per must_quote line: was its source retrieved?"""
    from app.review.evidence import prepare, retrieve

    payload = case.review_input()
    sources = prepare(payload)
    selected = retrieve(payload, sources, encoder)
    selected_text = [s.text for s in selected if s.kind == "transcript" and s.speaker == "Candidate"]
    rows = []
    for expected in case.expected:
        for wanted in expected.must_quote:
            in_transcript = any(wanted in s.text for s in sources)
            rows.append({"case": case.id, "criterion": expected.criterion_id,
                         "hit": any(wanted in text for text in selected_text),
                         "in_transcript": in_transcript,
                         "selected": len(selected), "candidate_pieces": sum(
                             s.kind == "transcript" and s.speaker == "Candidate" for s in sources)})
    return rows


def precision(case_results):
    """Per system: how many 'demonstrated' calls the labels accept."""
    out = {}
    for system in SYSTEMS:
        rows = [r for result in case_results.values() for r in (result["scores"].get(system) or [])]
        said = [r for r in rows if r["predicted"] == "demonstrated"]
        out[system] = {"said_demonstrated": len(said),
                       "label_accepts": sum(not r["overclaim"] for r in said),
                       "labelled_demonstrated": sum(r["expected"] == "demonstrated" for r in rows),
                       "found_demonstrated": sum(r["expected"] == "demonstrated" and r["predicted"] == "demonstrated"
                                                 for r in rows)}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=ROOT / "eval/review_cases.json")
    parser.add_argument("--saved", type=Path, default=ROOT / ".data/review_eval.json")
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)

    rows = [row for case in cases for row in recall_rows(case)]
    hits = sum(row["hit"] for row in rows)
    print(f"Retrieval recall: {hits}/{len(rows)} labelled quotes reached the reviewers (0 model requests)")
    for row in rows:
        print(f"  {'hit ' if row['hit'] else 'MISS'} {row['case']} / {row['criterion']}"
              f" (candidate pieces {row['candidate_pieces']}, sent {row['selected']})"
              + ("" if row["in_transcript"] else "  [quote not found in transcript: label error]"))

    if args.saved.exists():
        saved = json.loads(args.saved.read_text(encoding="utf-8"))
        current = rescore(saved.get("cases", {}), cases)
        print("\nFrom saved results, scored with current labels (0 model requests):")
        print("system        said 'demonstrated'  label accepts  found of labelled 'demonstrated'")
        for system, p in precision(current).items():
            print(f"{system:<13} {p['said_demonstrated']:<20} {p['label_accepts']:<14} "
                  f"{p['found_demonstrated']}/{p['labelled_demonstrated']}")
    else:
        print("\nNo saved eval results; precision skipped.")


if __name__ == "__main__":
    main()
