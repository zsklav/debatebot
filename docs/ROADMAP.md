# DebateBot v2: build plan

## Current direction: post-interview Review Council

The current implementation lives at `/review`, alongside the original DebateBot
and local Claim CI. Interviews happen elsewhere; this module reviews completed
transcripts. See [the scoped implementation plan](INTERVIEW_PLATFORM_PLAN.md).

Implemented: labelled-text ingestion and source preview, up to three rubric
criteria, isolated per-run Qdrant retrieval, LangChain adapters, a LangGraph
two-reviewer/adjudicator workflow, exact quote validation, anonymous/shuffled
reviewer input to the judge, partial reports, and owner-scoped saved results.
Live adapters support explicit Groq/OpenRouter routes. Distinct declared model
families are required; this is configuration, not evidence of neutral assessment.
The synthetic demo uses scripted outputs and zero external model requests.

Verified: 170 Python tests (one optional embedding smoke skipped), 16 frontend
tests, and a real local-browser synthetic run saved/reopened through PostgreSQL.
No live multi-model assessment has been run. The call budget is at most three
requests per new live run, with successful identical results reused for ten
minutes within one account and configuration.

Next: configure and smoke-test the chosen live routes on synthetic examples;
build a held-out, human-labelled assessment set and compare against a single
model with an equal budget; add finding-level human corrections and report
deletion/retention controls. Document/audio import and extra challenge rounds
are later work. Keep this local prototype out of real employment decisions.

## Earlier DebateBot phases (historical progress)

Status: Phase 0 complete for the offline baseline. Safe conversation updates and exact-quote validation
implemented; model selection is centralized and all four roles are configurable.
Phase 1 ingestion and local semantic retrieval are implemented. All 43 tests pass,
including Qdrant storage/retrieval checks. Real CPU embeddings indexed two sourced
summaries into four chunks and generated saved development evaluation reports.
Live generation now has a three-case smoke review; browser appearance was checked
with fixtures rather than live generation.
Phase 2 evidence assessment now connects to weakness analysis through an optional
API endpoint. Phase 3 now has a bounded native tool-calling research loop and
/api/research, now orchestrated through actual LangGraph StateGraph nodes;
Latest Python run: 105 checks with PostgreSQL integration enabled, zero failures,
one optional cached-model smoke skipped
(previously passed). Three live Groq smoke cases also completed and were reviewed.
Broader live quality evaluation
remains planned. PostgreSQL now persists API sessions and evidence/research results;
source-ingestion records are still pending. The original UI now includes an Evidence
Lab for both modes; seven frontend tests pass alongside the Python suite. Accounts,
revocable cookie sessions, and owner-scoped API storage are implemented. Next.js,
streaming, deployment, and live quality measurement remain outstanding.

## Product goal

Turn an argument into an evidence-backed critique: identify a load-bearing
weakness, research its factual claims, show relevant passages, distinguish
facts from reasoning, and suggest a defensible revision.

The first release supports a curated corpus about remote work and productivity.
Keep the existing personas, standalone analysis, and CLI working.

## Proposed stack

| Layer | Choice | Reason |
| --- | --- | --- |
| UI | Next.js + TypeScript | Evidence cards and live research activity |
| API and AI logic | Python + FastAPI | Extend existing working code |
| Agent orchestration | LangGraph, introduced in phase 3 | Explicit state and bounded research loops |
| Retrieval | Qdrant | Embeddings and source metadata |
| Application storage | PostgreSQL | Documents, debates, runs, and results |
| AI observability | Langfuse | Inspect model and tool calls |
| Verification | pytest + custom evaluation runner | Software correctness and AI quality |

These are provisional choices. Before adding dependencies, verify their current
APIs and compatibility. Confirm WorkWise's stack before optimizing for stack
diversity. Retain the current Groq integration initially; make model selection
configurable rather than requiring a new provider.

## Phase 0: establish a reliable baseline

First slice completed: conversation state is committed only after a nonblank
reply, quoted claims must occur exactly in the input, and malformed/ungrounded
analysis retries within the existing budget. Verification uses standard-library
unittest (no extra test dependency). Model configuration separation is also
complete, including import-safe evaluation and injected test clients. API route
and CLI integration tests now cover the main flows using mocked provider replies.
The API still returns a generic 500 on provider failures; friendly error mapping
and CLI recovery from provider exceptions remain reliability follow-ups.

- Record existing behavior and preserve the original ten examples as development
  cases, not evidence of held-out accuracy.
- Validate that the quoted weakest claim occurs in the submitted argument.
- Ensure failed model calls do not corrupt conversation history.
- Separate model configuration from business logic and enable mocked model calls.
- Add targeted tests for validation, retries, and conversation state.

Done when: existing flows still work and these failure paths pass offline tests.
Learning: guide modules 1 and 2.

## Phase 1: build retrieval independently

Implemented: app/evidence/ingestion.py loads UTF-8 text/Markdown, tracks source
metadata and revision hashes, and creates exact-span word windows with configurable
overlap. A local CLI prints inspectable JSON. The included example is fictional,
not evidence. app/evidence/retrieval.py now embeds a two-study, attributed summary corpus with
FastEmbed/BGE and persists vectors plus source metadata in local Qdrant. Stable
upserts and source-scoped stale-chunk cleanup are tested; cleanup is not atomic.
Eight development queries expose misses and missing-evidence behavior. See
eval_results/README.md. PostgreSQL ingestion records, a larger corpus, and stronger
human relevance labels remain before calling this whole phase complete.

- Collect a small, legally usable corpus with contrasting findings, dates,
  provenance, and known relevant passages. Save source URLs and extraction details.
- Implement text/Markdown ingestion first; add PDF parsing later.
- Chunk documents, assign stable document/chunk IDs, embed and index in Qdrant.
- Store source metadata and ingestion records in PostgreSQL.
- Provide a retrieval command returning passages and metadata without an LLM.
- Annotate queries and relevant passages; measure retrieval recall at k.

Done when: ingestion is repeatable without duplicate chunks, sources are traceable,
and retrieval performance is recorded on annotated queries.
Learning: guide modules 3 and 4.

## Phase 2: ship evidence-backed Find the Hole

First slice: app/evidence/assessment.py assesses a user-supplied claim against Qdrant passages.
It validates exact quotes, supplied IDs, verdict/citation consistency, and uses
retriever-owned source metadata. Empty retrieval abstains without model calls.
The CLI is runnable with a real API key, but no live generative assessment was
run. analyze_argument() now combines the analyzer and assessor; the optional
/api/findhole/evidence endpoint uses lazy cached embeddings and serialized local
Qdrant access. Original routes stay unchanged. Semantic citation
support, answer contamination, and appropriate abstention require live/human evals.

Flow: argument -> weakness analysis -> checkable factual claim -> retrieval ->
evidence assessment -> critique and suggested repair.

- Separate logical weaknesses, empirical claims, and opinions.
- Return supported, contradicted, mixed, or insufficient evidence assessments
  relative to the indexed corpus; do not present these as universal truth labels.
- Each assessment cites existing chunk IDs and relevant passages.
- Validate IDs and quoted text in code; assess whether a passage actually supports
  the claim through annotated evaluation and human review.
- Treat retrieved text as untrusted data and add adversarial fixtures.
- Keep prompt-only mode for comparison.

Done when: a user can inspect the actual evidence, invalid citations are rejected,
and no-evidence cases produce an honest limitation.
Learning: guide modules 5 and 6.

## Phase 3: add the bounded research agent

Web-source prototype added as an explicit source mode. It supports a configured
Tavily provider or optional best-effort DDGS/Bing and bounded public-HTTPS page
reads before assessment. URL/IP/redirect boundaries and provider adapters are
tested offline. DDGS live checks were intermittent; the Tavily key is missing,
so reliable live web flow remains blocked on provider setup/verification. This
does not replace the local Qdrant baseline. See README for limits and data flow.

Implemented first with an explicit Python loop in research.py. The model chooses
search_evidence/read_passage via Groq function calls. Search previews alone cannot
be cited: final assessment sees only opened passages. Defaults: four planner calls,
six tools, three searches, five reads, 45-second cooperative deadline. A separate
final assessment call runs only after normal completion. Partial runs retain events
without verdicts. Tests cover reformulation, failures, tool allowlists, and limits.
The initial loop has now been migrated to LangGraph 1.2.22 with typed per-run
state, decide/tools/assess nodes, conditional transitions, and a recursion guard.
Graph-route and state-isolation tests pass, as does a real cached-embedding/Qdrant
smoke with scripted generation. Live adaptive behavior and comparative quality
remain unmeasured beyond a three-case live smoke. A local API key is now configured
and ignored by Git. Groq model availability required migrating the default to
openai/gpt-oss-20b. See eval_results/live_smoke_review.md. The endpoint
holds the existing single-process local-store lock; no checkpoint persistence exists.

- Tools: search_evidence(query), read_passage(chunk_id).
- Let the model choose a query, inspect results, reformulate, or stop.
- Start with at most three retrieval calls and two passages per iteration;
  make these limits configurable and enforce them outside model prompts.
- Enforce a run deadline, output token limits, and bounded retries.
- Record tool inputs, outputs, errors, and termination reasons.
- Use LangGraph for explicit state and conditional transitions once the simple
  retrieval workflow works. Add web search only in a later iteration.

Done when: traces demonstrate useful query adaptation, tool failures are handled,
and budget exhaustion ends with a partial or insufficient-evidence result.
Learning: guide module 7.

## Phase 4: make the experience inspectable

CI workflow configured for push/PR/manual runs: Python 3.12/3.13, ephemeral PostgreSQL
17, offline application/security/graph tests, dependency checks, and Node 22 frontend
contracts. No API credentials or embedding downloads. YAML/local equivalents are
verified; hosted GitHub runs and the Linux matrix remain unverified until push.
Docker build exclusions now protect ignored local data and credentials.

Persistence slice implemented: optional Psycopg/PostgreSQL repository, additive
migration 001, revision-checked session updates, saved analysis results, reopen API,
last-debate browser restore, and run-ID reopen controls. A separate local PostgreSQL
17 cluster runs on localhost:55432 with generated ignored credentials. Real SQL
tests verified saved state and reopening through a fresh API instance with mocked
generation. Authentication/ownership are now implemented (migration 002); run-list
UI and graph checkpoints remain pending.

First UI slice completed in the existing vanilla-JS page: fixed and research mode
selection, source/citation cards, retrieved passages, completed tool events, partial
runs, loading states, and error recovery. Fixture browser checks verified these
flows at a narrow viewport and inert rendering of an HTML payload. This is not
Next.js and does not stream events. Reuse the API contracts during the planned
frontend migration. No live-model browser run was performed.

- Build the Next.js interface around the existing standalone analysis flow first.
- Show argument, weakness, research events, evidence cards, and proposed repair.
- Stream status events via SSE; keep provider keys exclusively on the backend.
- Persist runs and sessions, with clear failure and retry states.
- Integrate Langfuse; avoid logging secrets and full private documents by default.
- Add CI for offline tests and a reproducible local setup.

Done when: a fresh checkout can run the documented demo, stored runs can be
reopened, and failures have visible, useful states.
Learning: guide module 8.

## Phase 5: evaluate and publish the engineering story

- Start with 50 human-reviewed cases: 30 development and 20 held-out test cases.
  Group related arguments and document variants together to reduce split leakage.
- Include supported, contradicted, mixed, missing-evidence, opinion, and injection
  cases. Keep retrieval labels separate from argument-quality labels.
- Compare prompt-only, fixed retrieval, and adaptive retrieval under recorded
  model/version/settings and equivalent input conditions.
- Report sample counts, retrieval recall, verdict accuracy, citation correctness,
  appropriate abstention, failures, p50/p95 latency, and token usage.
- Use repeated runs where feasible; show variability. Track provider pricing and
  date if estimating monetary cost; tokens alone are not a cost estimate.
- Human-review a sample of automated judgments. Another model is not ground truth.
- Freeze test cases before tuning; if tuned against them, retire them to development.

Done when: saved results support an honest comparison, including regressions and
limitations. Resume bullets contain only implemented features and measured results.
Learning: guide module 9.

## Scope boundaries

Defer MCP, voice, multi-agent teams, fine-tuning, knowledge graphs, arbitrary URL
fetching, and Kubernetes. Add hybrid retrieval or reranking after measured misses
show a need. This plan is milestone-based; set dates after the first working slice
reveals the learning and implementation pace.

## Next implementation session

The accepted product experiment is **Claim CI**: connect technical claims with
repository evidence and track whether changes invalidate that evidence. Keep
the existing debate, LangGraph/RAG and Qdrant implementation intact.

The first local slice is implemented: a declared claim registry, read-only
Python call-path inspection, case/benchmark artifact checks, dependency hashes,
status transitions, JSON/Markdown reports and an informational GitHub Actions
workflow. See `docs/CLAIM_CI.md`. A separate known-scenario worker now collects
real cached retrieval observations under Python socket-event instrumentation
and fixture-labelled citation-validator probes. The last local offline run
supports the narrow zero-monitored-Python-events claim; live response coverage
still abstains. Explicit live citation collection is available with a one-call
opt-in and has not been run in this implementation session.

Next integrate bounded agent investigation and repository-passage retrieval,
and evaluate known claim regressions against a general repository assistant.
Broader collectors, native/OS-level network observation and signed CI provenance
remain outside this local worker's scope.
Automatic claim extraction, selective agent reruns, a GitHub App and a Claim CI
web view remain pending. Do not describe those as implemented or assert a
reason-to-switch before comparison with a repository-aware general assistant.

Authentication/ownership and PostgreSQL session/run persistence are implemented.
The frontend uses local vanilla JavaScript. Wider mobile checks, hosted
deployment, inference quotas and dependable live web-search verification remain
separate follow-ups. The two-study corpus is a development sample, not broad
research coverage or a held-out benchmark.

## Reference material

- [Building effective agents](https://www.anthropic.com/research/building-effective-agents)
- [Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)

Read alongside [docs/AI_LEARNING_GUIDE.md](AI_LEARNING_GUIDE.md).
