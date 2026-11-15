"""Collect known DebateBot observations in a separate worker; offline by default."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import os
import platform
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from tools.claimci import Files, Registry, commit_sha, read_json, source_state

from app.core.paths import ROOT
ARTIFACT_DIR = "claimci-observations"
LIMITATIONS = ("Python socket/DNS audit events only, during named worker operations. "
               "Not OS-level isolation; native-code I/O and work outside those operations are not covered. "
               "Counts represent attempted events, not completed HTTP requests. No addresses/payloads recorded.")


def runtime_versions():
    packages = {}
    for name in ("pydantic", "fastembed", "qdrant-client", "onnxruntime", "groq"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            pass
    return {"python":platform.python_version(), "platform":platform.system(), "packages":packages}


def metadata(paths, *, fixture_only=False):
    files = Files(ROOT)
    hashes = {}
    for name in paths:
        entry = files.read(name)
        if entry is None:
            raise ValueError("A configured dependency is missing")
        hashes[name] = entry["sha256"]
    return {"version":1, "fixture_only":fixture_only, "recorded_commit":commit_sha(ROOT),
            "source_state":source_state(ROOT), "generated_at":datetime.now(timezone.utc).isoformat(),
            "run_id":str(uuid4()), "collector":"debatebot-known-scenarios-v1",
            "source_files":hashes, "runtime":runtime_versions(), "collection_status":"complete"}


def finalize(data):
    files = Files(ROOT)
    for name, fingerprint in data["source_files"].items():
        entry = files.read(name)
        if entry is None or fingerprint != entry["sha256"]:
            data["collection_status"] = "source_changed_during_collection"
            for row in data.get("network_cases", []):
                row["observation_complete"] = False
            break
    data["completed_at"] = datetime.now(timezone.utc).isoformat()
    return data


def save(name, data):
    files = Files(ROOT)
    path = files.path(f"{ARTIFACT_DIR}/{name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode()) > 524288:
        raise ValueError("Collected artifact exceeds the checker input budget")
    temporary = path.with_suffix(".tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise ValueError("Artifact outputs cannot be symlinks")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def collect_retrieval(claim):
    from tools.claimci_network import PythonNetworkObserver

    data = metadata(claim.paths)
    data["monitor"] = {"scope":"python_socket_events", "limitations":LIMITATIONS}
    observer = PythonNetworkObserver()
    observer.install()
    data["monitor"]["hook_registered"] = observer.ready
    # The guard covers package/model initialization as well as both queries.
    # Missing models are an incomplete observation, never a download opportunity.
    try:
        with observer.case("cached-query") as row:
            from app.evidence.runtime import open_evidence_index
            manager = open_evidence_index()
            with manager as index:
                hits = index.search("A two-day hybrid schedule reduced quitting in the studied workforce.", limit=3)
                row["accepted_passages"] = len(hits)
                data["embedding_model"] = index.index.embedder.model_id
    except (Exception, SystemExit) as error:
        data["collection_status"] = "incomplete"
        data["error_type"] = type(error).__name__
    if data["collection_status"] == "complete":
        try:
            with observer.case("missing-evidence") as row:
                from app.evidence.runtime import open_evidence_index
                with open_evidence_index() as index:
                    row["accepted_passages"] = len(index.search("CEO not smart", limit=3))
        except (Exception, SystemExit) as error:
            data["collection_status"] = "incomplete"
            data["error_type"] = type(error).__name__
    for identifier in claim.required_cases:
        if identifier not in observer.rows:
            observer.rows[identifier] = {"id":identifier, "scope":"python_socket_events",
                                         "external_requests":0, "events":{}, "observation_complete":False}
    data["network_cases"] = list(observer.rows.values())
    return finalize(data)


def collect_validation():
    from app.evidence.assessment import Assessment, validate_assessment

    paths = ["app/evidence/assessment.py", "app/core/model_config.py", "tools/claimci_collect.py", "corpus/hybrid_retention.md"]
    data = metadata(paths, fixture_only=True)
    data["scope"] = "Scripted validator probes over a real local source; not model-generated answers, an HTTP endpoint test, or production response observations."
    text = (ROOT / "corpus/hybrid_retention.md").read_text(encoding="utf-8")
    probes = [("exact-quote", "c1", "Quitting decreased by one-third", True),
              ("invented-quote", "c1", "A sentence that is absent from this source.", False),
              ("unknown-passage", "unknown", "Quitting decreased by one-third", False)]
    data["probe_cases"] = []
    for name, chunk_id, quotation, expected_accept in probes:
        result = Assessment(verdict="supported", explanation="SCRIPTED VALIDATOR PROBE ONLY",
                            suggested_revision="Read the original study.",
                            citations=[{"chunk_id":chunk_id, "quote":quotation, "relation":"supports"}])
        accepted = True
        try:
            validate_assessment(result, [{"chunk_id":"c1", "text":text}])
        except ValueError:
            accepted = False
        data["probe_cases"].append({"id":name, "expected_accept":expected_accept,
                                   "observed_accept":accepted, "passed":accepted == expected_accept})
    if not all(row["passed"] for row in data["probe_cases"]):
        data["collection_status"] = "probe_failed"
    return finalize(data)


def collect_live_citations(claim):
    """Opt-in: one direct evidence assessment, no automatic repair/network retries."""
    from dotenv import load_dotenv
    from groq import Groq
    from app.evidence.assessment import assess_claim
    from app.evidence.runtime import open_evidence_index
    from app.core.model_config import get_model

    data = metadata(claim.paths)
    data["scope"] = "One live direct evidence.assess_claim invocation over local passages; not a web API route or a comprehensive evaluation."
    data["response_cases"] = []
    data["model_requests_attempted"] = 0
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GROQ_API_KEY")
    if not key or key == "your_groq_api_key_here":
        data["collection_status"] = "missing_api_key"
        return finalize(data)
    data["model"] = get_model("evidence")
    try:
        with open_evidence_index() as index:
            with Groq(api_key=key, max_retries=0, timeout=30) as client:
                def create(**request):
                    if data["model_requests_attempted"] >= 1:
                        raise RuntimeError("Live collector request budget exhausted")
                    data["model_requests_attempted"] += 1
                    return client.chat.completions.create(**request)
                counted = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
                result = assess_claim(counted, index,
                    "A two-day hybrid schedule reduced quitting in the studied workforce.",
                    limit=3, max_retries=0)
                if result["verdict"] != "supported":
                    data["collection_status"] = "no_supported_response"
                else:
                    data["response_cases"] = [{"id":"supported-retention", "response":result}]
    except (Exception, SystemExit) as error:
        data["collection_status"] = "incomplete"
        data["error_type"] = type(error).__name__
    return finalize(data)


def worker(profile):
    # Set these before importing embedding/retrieval packages. Existing cached
    # resources are used; downloads are a separate explicit setup operation.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    registry_file = Files(ROOT).read(".claimci.json")
    if registry_file is None:
        raise ValueError("Claim registry missing")
    registry = Registry.model_validate(read_json(registry_file["text"]))
    claims = {claim.id:claim for claim in registry.claims}
    results = []
    if profile in {"retrieval", "offline"}:
        data = collect_retrieval(claims["local-retrieval"])
        save("local-retrieval.json", data)
        results.append(("local-retrieval.json", data))
    if profile in {"validation", "offline"}:
        data = collect_validation()
        save("validation-probes.json", data)
        results.append(("validation-probes.json", data))
    if profile == "live-citations":
        data = collect_live_citations(claims["evidence-citations"])
        save("citations.json", data)
        results.append(("citations.json", data))
    for name, data in results:
        print(f"{name}: {data['collection_status']}; fixture_only={data['fixture_only']}")
    return 0 if all(data["collection_status"] == "complete" for _, data in results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["offline", "retrieval", "validation", "live-citations"], default="offline")
    parser.add_argument("--allow-live-llm", action="store_true", help="Allow the live-citations profile to use up to one Groq request")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.profile == "live-citations" and not args.allow_live_llm:
        parser.error("live-citations requires --allow-live-llm; it consumes Groq quota")
    if args.worker:
        try:
            return worker(args.profile)
        except (Exception, SystemExit) as error:
            print(f"Collection setup failed: {type(error).__name__}; no proof is implied.")
            return 2
    command = [sys.executable, "-m", "tools.claimci_collect", "--worker", "--profile", args.profile]
    if args.allow_live_llm:
        command.append("--allow-live-llm")
    try:
        return subprocess.run(command, cwd=ROOT, timeout=120).returncode
    except subprocess.TimeoutExpired:
        print("Collector worker timed out; use only completed, fresh observations.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
