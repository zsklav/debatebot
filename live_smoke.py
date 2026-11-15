"""Small opt-in live Groq check. Consumes API quota; not an accuracy benchmark."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv

from evidence import assess_claim
from evidence_runtime import ROOT, open_evidence_index
from model_config import get_model
from research import research_claim


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".data/live_smoke.json")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key or key == "your_groq_api_key_here":
        parser.error("Add a valid GROQ_API_KEY to your local .env first. Never paste it in chat.")
    from groq import Groq

    client = Groq(api_key=key, timeout=30, max_retries=0)
    checks = [
        ("fixed_retention", "fixed", "A two-day hybrid schedule reduced quitting in the studied Chinese technology workforce."),
        ("missing_evidence", "fixed", "Working from home lowers blood pressure."),
        ("agent_retention", "research", "Does a two-day hybrid schedule reduce quitting in the studied Chinese technology workforce?"),
    ]
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(),
              "kind": "live_smoke_not_accuracy_benchmark", "langgraph_version": version("langgraph"),
              "models": {role: get_model(role) for role in ("evidence", "research")},
              "checks": [], "review_required": True}
    try:
        # Fail on missing local resources before consuming model quota.
        with open_evidence_index() as index:
            report["embedding_model"] = index.embedder.model_id
            for identifier, mode, claim in checks:
                started = time.monotonic()
                row = {"id": identifier, "mode": mode, "claim": claim}
                try:
                    result = (assess_claim(client, index, claim, max_retries=0) if mode == "fixed"
                              else research_claim(client, index, claim))
                    row.update(executed=True, result=result)
                except Exception as error:
                    # Exception messages may include provider details; retain only type.
                    row.update(executed=False, error_type=type(error).__name__)
                row["elapsed_seconds"] = time.monotonic() - started
                report["checks"].append(row)
                print(f"{identifier}: {'response received' if row['executed'] else row['error_type']}")
                if row.get("error_type") in {"AuthenticationError", "PermissionDeniedError", "RateLimitError", "NotFoundError"}:
                    report["stopped_early"] = row["error_type"]
                    break
    finally:
        client.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"Report saved to {args.output}. Review evidence scope and citation meaning manually.")
    if any(not row["executed"] for row in report["checks"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
