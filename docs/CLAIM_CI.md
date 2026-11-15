# Claim CI: first working slice

Claim CI checks a declared claim registry against explicit repository files and
recorded observations. It produces JSON and Markdown without calling an LLM,
importing the target code, running its tests, installing its dependencies or
fetching URLs. The existing DebateBot chat, RAG, Qdrant and LangGraph research
features remain separate and unchanged.

This first slice provides the evidence contract for a future agentic reviewer.
A separate known-scenario collector, `tools/claimci_collect.py`, executes selected
DebateBot retrieval/validation functions in a child process. It does **not**
provide generic repository execution, automatic README claim discovery, a GitHub
App, repository cloning, an agent that runs benchmarks, a web dashboard, or a
validated advantage over a general-purpose assistant.

## Run

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m tools.claimci
```

Outputs: `.data/claimci/report.json` and `.data/claimci/report.md`. These are
ignored by Git. The supplied `.claimci.json` registers two narrowly scoped
claims about local retrieval and exact citation quotes. Before collection, both claims are unverified. After the default offline
collector succeeds, the scoped Python-network observation can be supported;
the live citation claim remains unverified until explicitly collected. Existing
unit tests do not automatically become real network observations or production
response samples.

Useful options:

```powershell
# Review another explicit local registry.
python -m tools.claimci --root . --claims .claimci.json

# Compare dependency fingerprints and statuses with a previous report.
# The previous report is read before the output is overwritten.
python -m tools.claimci --previous .data/claimci/report.json

# Attach commit-pinned source links only for a clean Git checkout.
python -m tools.claimci --repo-url https://github.com/zsklav/debatebot

# Opt into failing on a recorded counterexample. Default mode is informational.
python -m tools.claimci --fail-on-contradicted
```

Exit codes: `0` report written; `1` a contradicted claim in the optional failure
mode; `2` invalid configuration/input. **Unverified is never a passing proof**,
even though informational CI does not fail the job for it.

## Supported check types

| Kind | Evidence inspected | Scope of the result |
|---|---|---|
| `data_locality` | Recognized Python external-call paths plus declared network observation cases | Static calls are review signals only. A recorded external request contradicts locality for that case. A zero count supports only the observed cases; completeness is asserted by the artifact producer. |
| `citations` | Required recorded response cases, citations and passage snapshots | Missing citations or unmatched quotes are counterexamples. Successful exact quotation does not establish entailment, source authenticity, or universal coverage. |
| `benchmark` | Matching metric/unit, samples, duration, environment, method and threshold | Compares reported measurements under those conditions. Does not run the load test, validate its methodology or turn throughput into concurrent-user capacity. |

Statuses are `supported`, `contradicted`, `unverified`. Read the explanation
and declared scope with every status. Artifacts are self-reported inputs, not
independently attested measurements. This tool cannot detect dishonest producers.

## Registry format

Claims are explicit; the checker does not guess what the author meant.

```json
{
  "version": 1,
  "claims": [
    {
      "id": "answer-citations",
      "text": "The required answer cases quote retrieved passages exactly.",
      "kind": "citations",
      "paths": ["service.py"],
      "artifact": "claimci-observations/citations.json",
      "required_cases": ["normal", "fallback"]
    }
  ]
}
```

Paths must be explicit, relative files. Directories, globs, traversal, symlinks,
secret locations such as `.env`, `.git`, `.aws`, `.data` and `.venv`, files over
512 KiB, and total input above 20 MiB are excluded. At most 50 claims, 100
dependencies per claim and 256 distinct inputs are supported. Static call
findings are capped at 50 per claim. The static adapter covers Python and a
small set of known network/provider functions; it is not whole-program analysis.

## Observation artifact format

Use the known-scenario collector described below, or a separately reviewed
collector for another system. Artifacts are still self-reported, not signed
proofs. Never
rename synthetic tests to make them look like production measurements. The
following is a **format template**, not a valid artifact or benchmark result:

```json
{
  "version": 1,
  "fixture_only": false,
  "recorded_commit": "REPLACE_WITH_RECORDED_COMMIT_SHA",
  "generated_at": "REPLACE_WITH_TIMEZONE_AWARE_TIMESTAMP",
  "source_files": {"service.py": "REPLACE_WITH_SHA256_OF_FILE_BYTES"},
  "response_cases": [
    {
      "id": "normal",
      "response": {
        "citations": [{"chunk_id": "c1", "quote": "Observed passage"}],
        "retrieved_passages": [{"chunk_id": "c1", "text": "Observed passage from the source snapshot."}]
      }
    }
  ]
}
```

The artifact's `source_files` must contain exactly the registry's dependency
paths and match their current SHA-256 hashes. Missing dependencies, stale
hashes, fixture-only artifacts, missing cases or invalid metadata abstain.
Declare all relevant runtime dependencies; omitted files cannot be checked.
The recorded commit is provenance metadata, not a cryptographic attestation.

Additional payloads for the other check types:

```json
{
  "network_cases": [
    {"id": "normal", "observation_complete": true, "external_requests": 0}
  ],
  "benchmark": {
    "metric": "requests_per_second",
    "value": 120,
    "unit": "req/s",
    "sample_count": 1000,
    "duration_seconds": 30,
    "environment": "Describe actual hardware and test setup",
    "method": "Describe actual load and measurement procedure"
  }
}
```

Those numbers are illustrative, **not DebateBot performance claims**. Benchmark
registry entries additionally need `metric`, `unit`, `target`, `operator` (`>=`
or `<=`) and optionally `min_samples`. Network and citation entries require
`required_cases`. All payloads still need the common metadata above.

## Change tracking and CI

`--previous` reports which claims' definitions/dependencies changed, their old
statuses, and whether a previously supported claim now needs review. Checks
currently re-evaluate every declared claim; **selective agent execution and
cross-run CI caching are not implemented**. No prior report is trusted as proof
of the current status.

`.github/workflows/claimci.yml` runs on pushes, pull requests and manual
dispatch. It installs only Pydantic, runs clearly labelled validator probes, reads the
registry and writes the Markdown
to the [GitHub Actions job summary](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#adding-a-job-summary).
Permissions are `contents: read`; there are no LLM secrets, repository writes,
PR comments or `pull_request_target` events. The checker never executes target
code. The separate validation collector does execute the known local validator
functions; it is not a runner for arbitrary repositories.
The workflow is informational until a user explicitly enables gating.
It has been parsed locally; the newly added workflow has not yet been pushed
or observed running on GitHub.

Commit links are emitted only for a clean checkout. For dirty local files the
report gives the baseline HEAD, file hashes and a warning rather than linking
uncommitted observations to the wrong source version.

## Next integration boundary

A bounded LangGraph reviewer can later use this report to request missing
observations or investigate claim dependencies. LangChain/Qdrant can retrieve
candidate code/document passages. Neither retrieval nor an LLM's confidence
should override missing measurements or promote a static suspicion into a
verified runtime finding.


## Known-scenario collector

```powershell
# Cached embedding/Qdrant operations plus scripted validator probes. No Groq call.
python -m tools.claimci_collect

# Only validator probes: requires Pydantic, no embedding model or provider.
python -m tools.claimci_collect --profile validation

# Explicit quota opt-in: up to ONE direct Groq assessment. Not run by default/CI.
python -m tools.claimci_collect --profile live-citations --allow-live-llm

# Inspect artifacts and refresh the claim report.
python -m tools.claimci --previous .data/claimci/report.json
```

The default `offline` profile needs the existing cached embedding model and
indexed corpus. It does not download missing models or silently switch to a
provider. Missing resources produce incomplete observations, not a supported
zero-network finding. Artifacts are written atomically under ignored
`claimci-observations/` and excluded from Docker builds. File hashes are captured
before execution and rechecked afterwards; changes invalidate the observation.

Profiles and scope:

- `retrieval`: runs the actual cached-query and missing-evidence operations.
  Python audit hooks count/block documented socket/DNS events only during those
  operations, including import/model initialization. No addresses or payloads
  are stored. The claim explicitly uses `network_scope: python_socket_events`.
  A missing/incomplete observer cannot prove a zero count; a recorded attempt
  is a counterexample even if it interrupts the operation. Native-code I/O,
  operations outside the observation window, bypasses and full-system data
  locality are **not covered**. This uses Python's
  [audit events](https://docs.python.org/3/library/audit_events.html) and
  [audit hooks](https://docs.python.org/3/library/sys.html#sys.addaudithook),
  which are explicitly not an OS sandbox. Only this trusted repository's known
  functions are run; this is not safe execution of arbitrary uploaded code.
- `validation`: exercises the real citation validator with exact, invented and
  unknown-passage quotations. The inputs are scripted, so the artifact is
  **fixture_only: true** and is not a production/model response sample.
- `live-citations`: explicit opt-in, one direct `assess_claim` invocation over
  the local index. JSON repair and SDK retries are disabled; actual request
  attempts are counted. A refused/insufficient response or failed request leaves
  the required supported-response case unverified. This is not an HTTP endpoint
  test or a held-out accuracy evaluation. It reads the ignored local Groq key
  only for this profile; credentials are not recorded.

Artifacts include a run ID, start/completion times, dependency hashes, baseline
commit, working-tree state and package/Python versions. Runtime metadata is
recorded context, not an independently verified deployment environment. On a
failed/timeout run, inspect exit status and artifact timestamps; an older artifact
must not be mistaken for a new observation. Artifacts are not signed or protected
against a dishonest producer. The collector's audit hook is confined to its child
process so it does not alter the running app or test runner.

The GitHub job runs only `validation`, without keys/downloads. A fresh runner
therefore still abstains on real retrieval/live-response claims unless separate
valid observations are provided. The workflow has not yet been observed hosted.
