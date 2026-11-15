"""Read-only checks of declared repository claims. Never executes repository code."""

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path, PureWindowsPath
import re
import subprocess
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    text: str = Field(min_length=1, max_length=1000)
    kind: Literal["data_locality", "citations", "benchmark"]
    paths: list[str] = Field(min_length=1, max_length=100)
    artifact: str | None = None
    required_cases: list[str] = Field(default_factory=list, max_length=100)
    metric: str | None = None
    unit: str | None = None
    operator: Literal[">=", "<="] = ">="
    target: float | None = Field(default=None, allow_inf_nan=False)
    min_samples: int = Field(default=1, ge=1)
    network_scope: Literal["external_requests", "python_socket_events"] = "external_requests"

    @model_validator(mode="after")
    def check_contract(self):
        if not self.text.strip():
            raise ValueError("Claim text cannot be blank")
        if len(set(self.paths)) != len(self.paths):
            raise ValueError("Duplicate dependency paths")
        if self.kind in {"data_locality", "citations"} and (
                not self.required_cases or len(set(self.required_cases)) != len(self.required_cases)):
            raise ValueError("Declare unique required_cases for network/citation checks")
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", name) for name in self.required_cases):
            raise ValueError("Case IDs must be short identifiers")
        if self.kind == "benchmark" and (not self.metric or not self.unit or self.target is None):
            raise ValueError("Benchmark claims require metric, unit and target")
        return self


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    claims: list[Claim] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_claims(self):
        if len({c.id for c in self.claims}) != len(self.claims):
            raise ValueError("Claim IDs must be unique")
        return self


class Files:
    """Explicit relative files only; refuse secrets, traversal and large inputs."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.cache = {}
        self.bytes = 0

    def path(self, name):
        path = Path(name)
        if not name or path.is_absolute() or PureWindowsPath(name).is_absolute() or ":" in name:
            raise ValueError("Use a repository-relative path")
        parts = re.split(r"[/\\]", name.lower())
        if any(p in {"..", ".git", ".venv", ".data", ".aws", ".codex", ".agents", "node_modules"}
               or p.startswith(".env") or p.endswith((".pem", ".key")) for p in parts):
            raise ValueError("Sensitive directories and traversal are excluded")
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path escapes the repository")
        if resolved != (self.root / path).absolute():
            raise ValueError("Input symlinks are excluded")
        return resolved

    def read(self, name):
        path = self.path(name)
        key = path.relative_to(self.root).as_posix()
        if key not in self.cache:
            if len(self.cache) >= 256:
                raise ValueError("Too many input files")
            if not path.is_file():
                self.cache[key] = None
            else:
                if path.stat().st_size > 524288:
                    raise ValueError("Input file exceeds 512 KiB")
                raw = path.read_bytes()
                self.bytes += len(raw)
                if self.bytes > 20 * 1024 * 1024:
                    raise ValueError("Input budget exceeds 20 MiB")
                self.cache[key] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                   "text": raw.decode("utf-8-sig")}
        return self.cache[key]


def read_json(text):
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise ValueError("Duplicate JSON keys")
            data[key] = value
        return data
    def invalid(value):
        raise ValueError("Nonfinite JSON number")
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite JSON number")
        return number
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)


def external_calls(files, paths):
    """Recognized Python calls only. A syntax match is NOT a runtime trace."""
    providers = {"groq.Groq", "groq.AsyncGroq", "openai.OpenAI", "openai.AsyncOpenAI",
                 "anthropic.Anthropic", "anthropic.AsyncAnthropic"}
    requests = {f"{package}.{method}" for package in ("requests", "httpx")
                for method in ("get", "post", "put", "patch", "delete", "request", "stream")}
    requests |= {"urllib.request.urlopen", "urllib.request.urlretrieve", "socket.create_connection"}
    evidence = []
    for name in paths:
        entry = files.read(name)
        if entry is None or Path(name).suffix != ".py":
            continue
        try:
            tree = ast.parse(entry["text"])
        except SyntaxError:
            continue
        aliases = {}
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(item, ast.ImportFrom) and item.module and not item.level:
                for alias in item.names:
                    aliases[alias.asname or alias.name] = f"{item.module}.{alias.name}"
        def symbol(item):
            if isinstance(item, ast.Name):
                return aliases.get(item.id, item.id)
            if isinstance(item, ast.Attribute):
                parent = symbol(item.value)
                return f"{parent}.{item.attr}" if parent else ""
            return ""
        for item in ast.walk(tree):
            if isinstance(item, ast.Call):
                name_of_call = symbol(item.func)
                if name_of_call in providers | requests and len(evidence) < 50:
                    evidence.append({"path":name, "line":item.lineno, "sha256":entry["sha256"],
                                     "message":f"Potential external API path: {name_of_call}. Static match; execution/data flow not established."})
    return evidence[:50]


def artifact_for(claim, files):
    if not claim.artifact:
        return None, "No observation artifact configured."
    entry = files.read(claim.artifact)
    if entry is None:
        return None, "Observation artifact is missing."
    artifact = read_json(entry["text"])
    if not isinstance(artifact, dict) or type(artifact.get("version")) is not int or artifact["version"] != 1:
        return None, "Unsupported artifact format."
    if artifact.get("fixture_only") is not False:
        return None, "Artifact is a fixture or has not been declared a real observation."
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(artifact.get("recorded_commit", ""))):
        return None, "Artifact must identify its recorded commit."
    try:
        recorded = datetime.fromisoformat(artifact["generated_at"].replace("Z", "+00:00"))
        if recorded.tzinfo is None or recorded > datetime.now(timezone.utc):
            return None, "Artifact timestamp must include a timezone and cannot be in the future."
    except (KeyError, ValueError, TypeError, AttributeError):
        return None, "Artifact timestamp is missing or invalid."
    fingerprints = artifact.get("source_files")
    if not isinstance(fingerprints, dict) or set(fingerprints) != set(claim.paths):
        return None, "Artifact must fingerprint exactly the declared dependency files."
    for name in claim.paths:
        current = files.read(name)
        if current is None or fingerprints[name] != current["sha256"]:
            return None, f"Artifact is stale or its dependency is missing: {name}"
    return artifact, None


def required_observations(claim, artifact, key):
    observations = artifact.get(key)
    if not isinstance(observations, list) or not observations or len(observations) > 100:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or
           not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", row["id"]) for row in observations):
        return None
    by_id = {row["id"]:row for row in observations}
    if len(by_id) != len(observations) or not set(claim.required_cases).issubset(by_id):
        return None
    return [by_id[key] for key in claim.required_cases]


def evaluate(claim, files):
    evidence = external_calls(files, claim.paths) if claim.kind == "data_locality" else []
    artifact, error = artifact_for(claim, files)
    if error:
        return "unverified", error, evidence
    evidence.append({"path":claim.artifact, "sha256":files.read(claim.artifact)["sha256"],
                     "message":f"Observation artifact from {artifact['generated_at']}, recorded commit {artifact['recorded_commit']}. Self-reported provenance; not independently attested."})
    if claim.kind == "data_locality":
        rows = required_observations(claim, artifact, "network_cases")
        if not rows or any(not isinstance(row.get("external_requests"), int) or
                           isinstance(row.get("external_requests"), bool) or row["external_requests"] < 0 for row in rows):
            return "unverified", "Required network observations are missing/incomplete.", evidence
        monitor = artifact.get("monitor")
        if claim.network_scope == "python_socket_events" and (
                not isinstance(monitor, dict) or monitor.get("scope") != "python_socket_events" or
                monitor.get("hook_registered") is not True or
                any(row.get("scope") != "python_socket_events" for row in rows)):
            return "unverified", "Python socket instrumentation scope/registration is missing.", evidence
        if claim.network_scope == "python_socket_events":
            for row in rows:
                events = row.get("events")
                if (not isinstance(events, dict) or any(type(value) is not int or value < 0 for value in events.values()) or
                        sum(events.values()) != row["external_requests"]):
                    return "unverified", "Python event counts are missing or inconsistent.", evidence
        offending = [row["id"] for row in rows if row["external_requests"]]
        if offending:
            label = "Python network attempts" if claim.network_scope == "python_socket_events" else "External requests"
            return "contradicted", label + " were recorded in cases: " + ", ".join(offending), evidence
        if any(row.get("observation_complete") is not True for row in rows):
            return "unverified", "A zero count from incomplete observations cannot prove absence.", evidence
        if claim.network_scope == "python_socket_events":
            return "supported", "Zero Python socket/DNS events in the declared operations. Native-code I/O and operations outside those windows are not observed; this is not proof of full data locality.", evidence
        return "supported", "No external requests reported in the declared cases only. Not proof of universal data locality.", evidence
    if claim.kind == "citations":
        if artifact.get("collection_status", "complete") != "complete":
            return "unverified", "Live response collection was incomplete; no response guarantee is established.", evidence
        rows = required_observations(claim, artifact, "response_cases")
        if not rows:
            return "unverified", "Required response cases are missing.", evidence
        for row in rows:
            response = row.get("response")
            if not isinstance(response, dict):
                return "unverified", "A recorded response is missing or malformed.", evidence
            citations = response.get("citations")
            if not isinstance(citations, list) or not citations:
                return "contradicted", f"Case {row['id']} contains no citations.", evidence
            passages = response.get("retrieved_passages", [])
            if not isinstance(passages, list) or any(not isinstance(p, dict) or not isinstance(p.get("text"), str) or not isinstance(p.get("chunk_id"), str) for p in passages):
                return "unverified", "Recorded source passages are missing or malformed.", evidence
            texts = {p["chunk_id"]:p["text"] for p in passages}
            if not texts or len(texts) != len(passages):
                return "unverified", "Source passage snapshots are missing or ambiguous.", evidence
            for citation in citations:
                if not isinstance(citation, dict):
                    return "contradicted", f"Case {row['id']} contains a malformed citation.", evidence
                quote_text = citation.get("quote")
                chunk_id = citation.get("chunk_id")
                if not isinstance(chunk_id, str) or not isinstance(quote_text, str) or not quote_text.strip() or quote_text not in texts.get(chunk_id, ""):
                    return "contradicted", f"Case {row['id']} has a citation not grounded in its recorded passages.", evidence
        return "supported", "All declared response cases contain exact quotes from recorded passages. Entailment and source authenticity are not established.", evidence
    benchmark = artifact.get("benchmark")
    if not isinstance(benchmark, dict) or benchmark.get("metric") != claim.metric or benchmark.get("unit") != claim.unit:
        return "unverified", "Benchmark metric/unit does not match the claim.", evidence
    value, samples = benchmark.get("value"), benchmark.get("sample_count")
    if (type(value) not in {int, float} or type(samples) is not int or samples < claim.min_samples or
            type(benchmark.get("duration_seconds")) not in {int, float} or benchmark["duration_seconds"] <= 0 or
            not isinstance(benchmark.get("environment"), str) or not benchmark["environment"].strip() or
            not isinstance(benchmark.get("method"), str) or not benchmark["method"].strip()):
        return "unverified", "Benchmark needs sufficient samples, duration, environment and method.", evidence
    supported = value >= claim.target if claim.operator == ">=" else value <= claim.target
    return ("supported" if supported else "contradicted",
            f"Recorded {claim.metric}: {value} {claim.unit}; required {claim.operator} {claim.target}. Applies only to the recorded method/environment.", evidence)


def commit_sha(root):
    result = subprocess.run(["git", "-C", str(root), f"--work-tree={root}", "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=5)
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


def source_state(root):
    result = subprocess.run(["git", "-C", str(root), f"--work-tree={root}", "status", "--porcelain"],
                            capture_output=True, text=True, timeout=5)
    return "unknown" if result.returncode else "dirty" if result.stdout.strip() else "clean"


def audit(root, registry_path, previous=None):
    files = Files(root)
    source = files.read(registry_path)
    if source is None:
        raise ValueError("Claim registry not found")
    registry = Registry.model_validate(read_json(source["text"]))
    checker_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    old_rows = previous.get("claims") if isinstance(previous, dict) else None
    old = {row.get("id"):row for row in old_rows if isinstance(row, dict) and isinstance(row.get("id"), str)} if isinstance(old_rows, list) else {}
    results = []
    for claim in registry.claims:
        dependencies = {}
        for name in [*claim.paths, *([claim.artifact] if claim.artifact else [])]:
            item = files.read(name)
            dependencies[name] = item["sha256"] if item else None
        fingerprint = hashlib.sha256(json.dumps({"checker":checker_hash, "claim":claim.model_dump(),
            "dependencies":dependencies}, sort_keys=True).encode()).hexdigest()
        # Previous reports are local optimization inputs, not a security authority.
        # Unverified results always recompute; successful artifacts are revalidated.
        status, explanation, evidence = evaluate(claim, files)
        prior = old.get(claim.id, {})
        unchanged = prior.get("fingerprint") == fingerprint
        results.append({"id":claim.id, "text":claim.text, "kind":claim.kind, "status":status,
                        "explanation":explanation, "evidence":evidence, "dependencies":dependencies,
                        "fingerprint":fingerprint, "changed":not unchanged,
                        "previous_status":prior.get("status"),
                        "regression":prior.get("status") == "supported" and status != "supported"})
    return {"version":1, "commit":commit_sha(files.root), "source_state":source_state(files.root),
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "registry":registry_path, "claims":results,
            "summary":{key:sum(row["status"] == key for row in results) for key in ("supported", "contradicted", "unverified")},
            "scope":"Read-only checker, declared files/cases only. The checker executes no repository code or tests; separate collectors may do so. Artifact observations are self-reported, not independently attested. Supported is not a universal guarantee."}


def md(value):
    value = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
    for char in ("\\", "|", "`", "[", "]", "*", "_"):
        value = value.replace(char, "\\" + char)
    return value


def markdown(report, repo_url=None):
    if repo_url:
        url = urlsplit(repo_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or
                not re.fullmatch(r"[A-Za-z0-9.-]+", url.netloc) or
                not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", url.path)):
            raise ValueError("repo-url must be an HTTPS repository URL without credentials/query")
    lines = ["# Claim CI", "", f"Commit: `{report['commit'] or 'unavailable'}`", "", report["scope"], "",
             "| Claim | Status | Finding |", "|---|---|---|"]
    if report["source_state"] != "clean":
        lines.insert(5, "Working tree is dirty or not a Git checkout. Commit links are withheld; findings describe the local snapshot.")
    for row in report["claims"]:
        lines.append(f"| {md(row['text'])} | {row['status']} | {md(row['explanation'])} |")
    for row in report["claims"]:
        lines += ["", f"## {row['id']}", "", f"Changed: {row['changed']} · Regression: {row['regression']}"]
        for finding in row["evidence"]:
            label = finding["path"] + (f":{finding['line']}" if finding.get("line") else "")
            ref = md(label)
            if repo_url and report["commit"] and report["source_state"] == "clean":
                suffix = f"#L{finding['line']}" if finding.get("line") else ""
                ref = f"[{md(label)}]({repo_url.rstrip('/')}/blob/{report['commit']}/{quote(finding['path'], safe='/')}{suffix})"
            lines.append(f"- {ref}: {md(finding['message'])}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--claims", default=".claimci.json")
    parser.add_argument("--output", default=".data/claimci")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--repo-url")
    parser.add_argument("--fail-on-contradicted", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.previous and args.previous.stat().st_size > 1048576:
            raise ValueError("Previous report exceeds 1 MiB")
        previous = read_json(args.previous.read_text(encoding="utf-8")) if args.previous else None
        report = audit(args.root, args.claims, previous)
        rendered = markdown(report, args.repo_url)
        output = (args.root / args.output).resolve()
        if not output.is_relative_to(args.root.resolve()) or any(part in {".git", ".aws", ".venv"} for part in output.relative_to(args.root.resolve()).parts):
            raise ValueError("Output must stay in a non-sensitive repository directory")
        output.mkdir(parents=True, exist_ok=True)
        for filename, content in [("report.json", json.dumps(report, indent=2) + "\n"), ("report.md", rendered)]:
            target = output / filename
            if target.is_symlink():
                raise ValueError("Output files must not be symlinks")
            target.write_text(content, encoding="utf-8")
        print(json.dumps(report["summary"]))
        print(f"Reports written to {output}")
        return 1 if args.fail_on_contradicted and report["summary"]["contradicted"] else 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Claim CI setup failed: {type(error).__name__}. Check registry, paths and artifact format.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
