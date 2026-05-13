"""
Day 6: The eval harness for find_hole.

Runs find_hole on every test case in benchmark.py, then uses a SEPARATE
LLM-as-judge call to compare the actual analysis against the intended
weakness. Returns HIT / PARTIAL / MISS for each, plus a summary.

Why this matters: production Gen AI engineers don't ship features by
vibes-checking — they ship them by *measuring*. This file is the measurement
infrastructure for one feature. Real systems have dozens of these.

Usage:
    .venv/bin/python eval_findhole.py
"""

import json
import os
import sys
import time
from typing import Literal, Optional

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel, ValidationError

from benchmark import BENCHMARK
from find_hole import find_hole, ArgumentWeakness

load_dotenv()

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY not found.")
    sys.exit(1)

client = Groq(api_key=api_key)
JUDGE_MODEL = "llama-3.3-70b-versatile"


class JudgeVerdict(BaseModel):
    verdict: Literal["HIT", "PARTIAL", "MISS"]
    reasoning: str


JUDGE_SYSTEM_PROMPT = """You are an evaluator for an argument-analysis tool. The user will give you three things:
1. ARGUMENT: the original argument that was analyzed
2. EXPECTED WEAKNESS: what a test designer says is the load-bearing weakness
3. ACTUAL ANALYSIS: what the tool produced (its quoted weakest_claim + diagnosis)

Your job: determine whether the tool correctly identified the SAME conceptual weakness as expected. Different wording is fine — the same conceptual category is what matters.

Return ONLY this JSON (no commentary, no code fences):
{
  "verdict": "HIT" | "PARTIAL" | "MISS",
  "reasoning": "<one sentence explanation>"
}

VERDICT KEY:
- HIT: the actual analysis identifies the same conceptual weakness as expected (even if worded differently)
- PARTIAL: the actual analysis touches the right area but focuses on a secondary issue or misses the core point
- MISS: the actual analysis identifies a totally different weakness, or is completely wrong

Be strict. Do not give HIT for a vaguely-related answer. Reserve PARTIAL for cases where the tool clearly engaged with the right area but didn't nail it."""


def judge(argument: str, expected: str, actual: ArgumentWeakness) -> JudgeVerdict:
    """Use an LLM to compare expected vs actual weakness."""
    user_msg = (
        f"ARGUMENT:\n{argument}\n\n"
        f"EXPECTED WEAKNESS:\n{expected}\n\n"
        f"ACTUAL ANALYSIS:\n"
        f"  weakest_claim: \"{actual.weakest_claim}\"\n"
        f"  diagnosis: {actual.why_weak}\n\n"
        f"Verdict?"
    )
    for attempt in range(2):
        response = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        try:
            return JudgeVerdict(**json.loads(raw))
        except (json.JSONDecodeError, ValidationError, KeyError):
            continue
    return JudgeVerdict(verdict="MISS", reasoning="Judge produced invalid output after retries.")


def run_eval() -> None:
    results: list[dict] = []
    print()
    print("=" * 78)
    print(f"  Running find_hole eval on {len(BENCHMARK)} cases")
    print("=" * 78)
    print()

    start = time.time()
    for case in BENCHMARK:
        case_id = case["id"]
        category = case["category"]
        argument = case["argument"]
        expected = case["intended_weakness"]

        print(f"[{case_id:>2}/10] {category:<25s} ", end="", flush=True)
        try:
            actual = find_hole(client, argument)
            verdict = judge(argument, expected, actual)
        except Exception as e:
            print(f"ERROR: {e}")
            results.append(
                {"id": case_id, "category": category, "verdict": "ERROR",
                 "reasoning": str(e), "actual_quote": "", "actual_diagnosis": ""}
            )
            continue

        symbol = {"HIT": "[HIT]    ", "PARTIAL": "[PARTIAL]", "MISS": "[MISS]   "}[verdict.verdict]
        print(f"{symbol}  {verdict.reasoning}")
        results.append(
            {
                "id": case_id,
                "category": category,
                "verdict": verdict.verdict,
                "reasoning": verdict.reasoning,
                "actual_quote": actual.weakest_claim,
                "actual_diagnosis": actual.why_weak,
            }
        )

    elapsed = time.time() - start
    summarize(results, elapsed)


def summarize(results: list[dict], elapsed: float) -> None:
    hits = sum(1 for r in results if r["verdict"] == "HIT")
    partials = sum(1 for r in results if r["verdict"] == "PARTIAL")
    misses = sum(1 for r in results if r["verdict"] == "MISS")
    errors = sum(1 for r in results if r["verdict"] == "ERROR")
    total = len(results)

    print()
    print("=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    print(f"  Total cases:  {total}")
    print(f"  HIT:          {hits}  ({100*hits//max(total,1)}%)")
    print(f"  PARTIAL:      {partials}")
    print(f"  MISS:         {misses}")
    if errors:
        print(f"  ERROR:        {errors}")
    print(f"  Elapsed:      {elapsed:.1f}s")
    print()
    print("  By category:")
    by_cat: dict[str, list[str]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["verdict"])
    for cat, verdicts in by_cat.items():
        marks = " ".join(
            "+" if v == "HIT" else "~" if v == "PARTIAL" else "X" if v == "MISS" else "?"
            for v in verdicts
        )
        print(f"    {cat:<25s} {marks}")
    print()
    print("  Failed/partial cases (read these to find the bug):")
    for r in results:
        if r["verdict"] in ("MISS", "PARTIAL", "ERROR"):
            print(f"    #{r['id']} ({r['category']}) — {r['verdict']}")
            print(f"       expected: see benchmark.py")
            print(f"       actual quote:    \"{r['actual_quote']}\"")
            print(f"       actual diagnosis: {r['actual_diagnosis']}")
            print(f"       judge said: {r['reasoning']}")
            print()
    print("=" * 78)


if __name__ == "__main__":
    run_eval()
