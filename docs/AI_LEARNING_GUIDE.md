# Learn AI by building DebateBot

This is a separate learning track for the v2 roadmap. Existing docs/LEARNINGS.md stays
as the history of v1. Modules below are planned lessons, not completed work.

## How we will work

For each milestone: explain the concept in plain language, trace one concrete
example, implement a small piece, inspect its output, and discuss a failure case.
Then you explain the design back in your own words. Keep changes small enough
that you can understand and defend them without memorizing framework names.

After each module, append what you built, commands you ran, observed results,
mistakes, and questions to the learning log at the end of this file.

## 1. Understand the existing LLM application

Learn tokens, context windows, system/user/assistant messages, temperature,
inference, and structured output. Inference means using an already trained model
to generate an answer; this project does not initially train a model.

Read app/debate/engine.py and find_hole.py. A session resends conversation messages; it does
not change model weights. Temperature changes sampling behavior, not factual
reliability. Even low-temperature output is not a guarantee of determinism.

Exercise: draw the exact request for a second debate turn and explain why context
and token usage grow. Inspect a saved or mocked response without exposing keys.

Checkpoint: explain how a chat application remembers earlier turns.

## 2. Build reliable boundaries around model output

Learn JSON parsing, Pydantic validation, semantic checks, retries, timeouts, and
state updates. A well-shaped JSON object can still contain a fabricated quote.

Exercise: feed a schema-valid response containing a quote absent from the input.
Reject it in application code. Simulate an API failure and verify that the
conversation remains valid.

Checkpoint: explain the difference between valid structure and correct content.

## 3. Understand embeddings and retrieval

An embedding represents text as numbers used to compare similarity. Retrieval
selects candidate passages; similarity is not evidence that a claim is true.
Learn document IDs, chunk IDs, metadata, cosine similarity, and top-k retrieval.

Exercise: compare searches for paraphrases and exact names/numbers. Inspect the
returned passages, not just their similarity scores.

Checkpoint: explain why keyword and vector search can fail on different queries.

## 4. Build an ingestion pipeline

Learn extraction, chunk size, overlap, provenance, deduplication, indexing, and
re-indexing when the embedding model or chunking strategy changes.

Example: a study's conclusion can be misleading if its limitations are placed
in a different chunk. Chunking changes what the answer generator can see.

Exercise: try two chunking configurations on the same annotated queries. Record
recall at k: relevant annotated passages retrieved divided by all annotated
relevant passages. Do not assume larger chunks always help.

Checkpoint: trace one source document to a retrieved passage and its citation.

## 5. Build RAG and honest citations

RAG means retrieval-augmented generation: retrieve evidence and include it in the
model's input before requesting an answer. This does not teach the model new
weights and does not eliminate hallucinations.

Learn the difference between retrieval relevance, citation validity, and citation
support. An existing source ID does not prove the cited text supports the answer.
Treat opinions and logical fallacies differently from factual claims.

Exercise: test a claim with supporting evidence, one with conflicting evidence,
and one absent from the corpus. Inspect every citation and its surrounding context.

Checkpoint: explain why 'insufficient evidence in our corpus' does not mean false.

## 6. Understand retrieved prompt injection

A document may contain text such as 'ignore previous instructions'. That text is
source material, not authority to control tools or application behavior.

Learn instruction/data separation, least-privilege tools, output validation, and
adversarial testing. Prompt wording alone is not a complete defense.

Exercise: index a harmless test document with malicious instructions. Verify that
it cannot grant new tools, change run limits, or introduce fabricated citations.

Checkpoint: identify which protections are enforced by code rather than prompts.

## 7. Understand tools, workflows, and agents

A workflow follows predefined steps. An agent lets a model choose actions based
on intermediate results. Tools are application functions with validated inputs
and outputs. LangGraph helps express execution state; it does not make answers
correct on its own.

Learn state transitions, tool schemas, query reformulation, termination conditions,
deadlines, and error recovery. Store evidence and tool events, not private
chain-of-thought. Multiple personas are not automatically multiple agents.

Exercise: inspect one run that succeeds on its first search, one that needs a new
query, and one that stops at its budget. Compare with fixed retrieval.

Checkpoint: justify when the extra agent loop is worth its latency and tokens.

## 8. Understand AI application operations

Learn API/UI boundaries, SSE events, durable state, traces, token accounting,
request latency, and redaction. Model calls are only one part of a reliable app.

Exercise: trace a failed request across the UI, API, retriever, and model call.
Reopen a stored run after restarting the backend. Keep secrets out of browser code
and traces.

Checkpoint: explain how you would distinguish retrieval failure from generation
failure using the recorded run.

## 9. Evaluate before claiming improvement

Learn development/test splits, leakage, baselines, ablations, human annotation,
LLM-judge bias, repeated runs, and small-sample uncertainty.

Use separate metrics for retrieval, evidence judgments, citations, abstention,
latency, and usage. A good-looking final answer can hide poor retrieval.

Exercise: compare prompt-only, fixed RAG, and agentic RAG. Record regressions as
well as gains. Manually review automated grades and disagreements.

Checkpoint: answer 'How do you know the agent helped?' using saved results rather
than framework names or impressions.

## Final interview exercise

Explain the project in two minutes, then defend these decisions:

1. Why retrieval instead of fine-tuning for this use case?
2. Why this chunking strategy and embedding model?
3. When does the system abstain?
4. Why introduce an agent instead of always retrieving once?
5. How do you validate citations and measure their support?
6. What failure remains, and what evidence would justify the next feature?

## Learning log

### Session 1: reliable model boundaries (modules 1 and 2)

Implemented and checked the first baseline slice. Review these concepts before
marking the lessons understood:

- **Conversation memory:** the app sends a list of messages on every request.
  Saving a user message before the API succeeds used to leave an unfinished turn
  after a timeout. app/debate/engine.py now builds a temporary request and saves both messages
  only after receiving a nonblank reply. This is not a concurrency solution;
  simultaneous requests to the same session still need coordination later.
- **Structure versus grounding:** Pydantic can verify types and severity bounds,
  but a schema-valid quote can still be invented. app/debate/find_hole.py now also checks
  that the quote is nonblank and occurs exactly in the submitted input. Changes
  to punctuation or wording are rejected intentionally.
- **Bounded retries:** invalid JSON, invalid schema, and invalid quotes consume
  the same retry budget. max_retries=1 allows two attempts total. Exhaustion raises
  an error instead of returning an ungrounded result.
- **Mocking:** tests replace the client with controlled responses and failures.
  They exercise real validation and state logic without keys, network calls, or
  model charges. Ten offline tests pass; they do not measure argument quality.

Run from the repository directory on Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Files changed: app/debate/engine.py, app/debate/find_hole.py, tests/test_baseline.py.

Limits: an exact quote does not prove the diagnosis is correct. In a transcript,
this check alone does not distinguish a user's sentence from a bot's sentence;
the existing prompt still handles attribution. Structured speaker attribution is
a later improvement. No live Groq calls or web/CLI interactive sessions were run.

Explain back: Why can a perfectly valid JSON response still be wrong? What would
happen if we saved the user's message and then the API timed out?

### Session 2: configuration versus application logic

Implemented app/core/model_config.py and connected debate, weakness analysis, argument
scoring, and the evaluation judge. All 18 offline tests pass, including the
original 10 regression tests. No live provider calls were made.

**Concept:** application logic answers 'what should happen?' Configuration answers
'which model should perform it?' We can change a judge model without changing
the judging rubric or retrieval code.

Selection order:

1. Explicit `model=` argument in Python.
2. Role-specific environment variable, such as `DEBATEBOT_JUDGE_MODEL`.
3. Shared `DEBATEBOT_MODEL` environment variable.
4. Original default model.

Environment variables are process settings; `.env` is a local file that our entry
points load into those settings. The resolver reads settings at call time, rather
than module import, because CLI/API entry points load `.env` after importing
business modules. A debate stores its selected model when the session is created.
Restart the app after changing `.env`; configuration does not silently migrate
an existing conversation to another model.

**Dependency injection:** judge() now receives its client as an explicit argument.
Previously importing tools/eval_findhole.py required credentials and constructed a live
client. Credentials and client creation now happen only when run_eval() executes.
Tests pass a mock client and can verify the selected model without any API key.

These settings do not add another provider, validate model availability, or
guarantee that a different model gives better judgments. The same default model
still serves all roles unless changed. A different judge is an experiment to
evaluate, not a substitute for human annotations.

Example (PowerShell, fake IDs for illustration only):

```powershell
$env:DEBATEBOT_MODEL = 'shared-model-id'
$env:DEBATEBOT_JUDGE_MODEL = 'judge-model-id'
```

The analyzer would select `shared-model-id`; the judge would select `judge-model-id`.
Use actual compatible Groq IDs for a live run. Never put API keys in tracked files.

Run the same test command from session 1. Read tests/test_model_config.py to see
how isolated environment settings and mock clients verify precedence and routing.

Explain back: If the shared model is A, the judge setting is B, and a judge call
passes `model='C'`, which model is used, and why?

### Session 3: integration tests and the completed offline baseline

Added tests/test_interfaces.py. All 23 tests pass, with five new integration
tests covering real API routes and CLI functions while replacing the Groq client
with a mock. No live model requests were made.

**Unit versus integration tests:** a unit test isolates a function or class. An
integration test checks whether pieces work together. Here a turn request crosses
HTTP request validation, session lookup, Debate.respond(), and response serialization.
Only the external provider is replaced. CLI tests simulate typed input and capture
printed output, including persona comparison and standalone weakness analysis.

We verified creating a session, sending a turn, retrieving its transcript, scoring,
weakness analysis, invalid inputs, and retrying after a failed request. The failed
request still returns HTTP 500; the important baseline invariant is that it leaves
no half-written conversation behind. User-friendly failure messages remain future
work. These tests do not verify browser rendering or live model quality.

**Debugging lesson:** the first API test run hung before reaching the route.
A Python traceback showed the Windows event loop blocked creating its local
socket pair inside the sandbox. Running the same tests with the needed local
socket access passed. Diagnose which layer failed before changing application code.
Two blocked diagnostic runs were stopped afterward.

The installed Starlette TestClient emits a deprecation warning about httpx.
It does not fail the tests; dependency compatibility cleanup is still pending.

Phase 0 is complete for this offline baseline. Next: ingestion and chunking,
which prepare documents for retrieval. Retrieval and generation will be tested
separately so a fluent answer cannot hide poor source selection.

### Session 4: document ingestion and chunking

Built app/evidence/ingestion.py, a fictional demo document, and eight ingestion tests. The
CLI demo produced three overlapping chunks at 40 words per chunk and 8 words
of overlap. No embeddings or model calls are involved yet.

**Ingestion** means bringing source text into a consistent application structure.
We read UTF-8 text/Markdown and keep title, source ID, optional URL/date, and a
content hash. Markdown syntax is retained; PDF extraction is not implemented.

**Chunking** divides that text into passages that can later be retrieved. For
example, with size 4 and overlap 1:

```text
Document: one two three four five six seven eight
Chunk 1:  one two three four
Chunk 2:                four five six seven
Chunk 3:                              seven eight
```

Overlap gives a later passage some preceding context, but also duplicates text
and increases embedding/storage work. Fixed word windows can split sentences or
separate a finding from its limitations. These are baseline settings, not an
optimized retrieval strategy. A word count is not an LLM token count.

**Traceability:** each chunk stores start/end character offsets and line numbers.
The exact text equals document.text[start_char:end_char]. End offsets are
exclusive. Offsets count decoded Unicode characters after any UTF-8 BOM, not
file bytes; line endings are preserved.

**Identity and revisions:** a document ID comes from the caller's stable source
ID. A content hash distinguishes revisions. Chunk IDs include document identity,
revision, chunking configuration, and span. Rerunning identical inputs therefore
produces identical IDs without depending on the file's local path. Reusing the
same source ID for unrelated documents would be a caller error.

Stable IDs make future index updates possible; no database is connected yet.
Deleting old revisions and handling duplicate sources still need explicit index
logic. Supplied source URLs and dates are metadata, not proof of authenticity.

Run the inspection example from README.md. Read each returned passage and locate
it in examples/chunking_demo.md. That file is deliberately fictional and must
never be used as evidence for real remote-work claims.

Explain back: Why might a chunk containing a study's conclusion be misleading
if its limitations appear only in the next chunk?

### Session 5: embeddings, Qdrant, and a measured retrieval baseline

Built app/evidence/retrieval.py, requirements/retrieval.txt, a two-source corpus with attribution,
12 retrieval/corpus tests, and saved development evaluation reports. All 43 tests
pass. Real-model indexing and query runs also succeeded on CPU; no Groq calls or
paid inference APIs were used.

**Embedding:** a learned encoder turns text into numbers. We use the English
BAAI/bge-small-en-v1.5 model through FastEmbed, producing 384-dimensional vectors.
An embedding model is different from the model generating debate replies. It
encodes text for comparison instead of producing a written answer.

**Vector retrieval:** Qdrant stores passage vectors and payloads. A query is
encoded in the same vector space, then cosine similarity ranks the passages.
Payloads retain exact text, source links, dates, authors, license, representation,
line locations, revision, and chunk IDs. Similarity measures resemblance, not
factual correctness or the probability that a claim is true.

Pipeline:

```text
Source summary -> chunks -> embedding encoder -> Qdrant vectors + metadata
User query ----------------> same encoder ----> similarity search -> passages
```

This implements retrieval, not complete RAG. Generation is a later step in which
a model receives retrieved evidence and is asked to produce a grounded result.

**Why another identity?** Chunk IDs identify text spans. Model identity identifies
the vector space. Even two models with 384 dimensions can produce incompatible
vectors. We include FastEmbed version, model name, and downloaded artifact hash
in collection identity. The adapter rejects inputs exceeding its tokenizer limit
before inference, because silent truncation would hide text from the encoder.

**Storage behavior:** local Qdrant persists to .data/qdrant without Docker. A repeated
ingestion upserts the same point IDs. Changing content or chunking replaces the
indexed source's old points after the new points are written. This is not an atomic
transaction. A failed cleanup or crash can leave mixed revisions until rerun.
Removing a source from the manifest does not remove it from an old store.

**Real evaluation result:** on six answerable development queries, top-1 found
annotated evidence for four; top-3 found it for all six. There are only four
candidate chunks, so the latter result is not impressive accuracy evidence.
Mean chunk recall at 1 was 0.5833; one annotation appears in two overlapping
chunks, and retrieving one counts as half recall but a successful evidence hit.
These are deliberately different metrics.

Both unanswerable questions also retrieved passages. One asked about examination
results in hybrid schooling; an office-innovation passage received a higher
similarity than some useful results for other queries. Scores across questions
are not calibrated confidence. We have not selected an abstention threshold.

One resignation query returned the wrong source first. Do not hide that miss:
it gives us a concrete reason to investigate query rewriting, hybrid retrieval,
or reranking after building a larger annotated set.

**Corpus provenance:** corpus/*.md contains short editorial adaptations of two
CC BY 4.0 articles, with authors, source links, dates, license links, and changes
recorded in corpus/manifest.json and corpus/README.md. Local line numbers refer to
our summaries, not original paper pages. This distinction must remain visible in
future citations.

**Testing distinction:** normal tests use deterministic fake embeddings against
real local Qdrant; they check indexing and search logic, not semantic model quality.
Separate cached real-model runs verified 384-dimensional output, long-input
rejection, search, and the saved evaluation. Optional Qdrant tests skip if its
dependency is not installed. The installed test client still emits its earlier
deprecation warning, and Qdrant emitted a SQLite ResourceWarning during persistence
tests despite explicit client.close(); both remain dependency follow-ups.

Try the retrieval commands in README.md. Compare the passages for an answerable
question with 'Does remote work lower blood pressure?' Inspect the source scope.

Explain back: If Qdrant returns a passage with a high similarity score, why can't
we automatically label the user's claim supported?

### Session 6: from retrieval to evidence assessment

Built app/evidence/assessment.py, the optional evidence-model setting, a CLI, and 10 tests.
All 53 tests pass. Assessment generation is mocked; live Groq assessment quality
has not been checked. This first slice accepts one explicit claim, rather than
automatically picking a factual claim from the weakness analyzer.

Flow: claim -> local retrieval -> model assessment -> validation -> report.
The model receives retrieved text in a JSON data message, separate from its system
instructions. It has no tools. Its job is to distinguish supported, contradicted,
mixed, insufficient evidence, and not-checkable claims. An opinion such as 'I
prefer working from home' is different from an empirical productivity claim.

**Citation validity versus entailment:** an exact quote can exist in a genuine
source and still fail to support the model's conclusion. Code verifies IDs,
nonblank quotes, exact text, duplicates, and verdict/relation consistency. Human
annotations and later evaluations must verify meaning, population scope, and
whether the explanation or suggested revision invents facts.

**Provenance ownership:** generated citations contain IDs and quotes. Source URLs,
authors, licenses, and local line references are copied from retrieval afterward.
The model is not trusted to invent source metadata. Our current sources are
editorial adaptations, which stays explicit in returned citations.

**Abstention versus failure:** zero returned passages yields insufficient evidence
without a model call. A model may also abstain when passages are irrelevant.
Malformed JSON or fabricated citations are different: retry within a budget,
then raise an operational error. Do not call a broken generation 'no evidence'.

**Prompt injection limits:** malicious text cannot grant this assessor executable
tools because none are provided. It can still influence the generated answer.
Tests confirm data-message placement and invalid-citation rejection, not reliable
resistance to malicious text. We need live adversarial evaluation before claiming
that property.

Try the README assessment command only when ready to use Groq API quota. It loads
cached local embeddings; generation requires your key. The earlier retrieval
commands remain key-free. The current API and Find the Hole are unchanged.

Explain back: if a model quotes a real passage but draws the wrong conclusion,
which of our checks will catch it, and what evaluation is still needed?

### Session 7: compose the workflow and expose it through an API

Added analyze_argument() and POST /api/findhole/evidence. All 59 tests pass.
Six new checks cover workflow composition, invalid requests, missing setup,
and failed generation through real API routing with mocked external resources.

The workflow first identifies an exact quoted weakness, then assesses that quote
using retrieved passages. We return the reasoning diagnosis separately from the
evidence result: a logically weak argument can contain a supported fact, and a
plausible argument can lack empirical support. A quoted sentence containing many
claims still needs more granular claim extraction later.

The API imports no embedding model at startup. An optional resource factory loads
cached weights on demand and opens local Qdrant only when needed. It closes the
client afterward and serializes access with a process-local lock. Cached real-model
retrieval through this factory was smoke-checked without a generative API call.

This lock does not coordinate multiple processes. Run one API worker, and avoid
concurrent indexing with local mode. The lock currently covers the entire combined
request, so generation limits throughput; a server-based index is a later upgrade.

API errors distinguish bad input (400/422), missing retrieval setup (503), and
provider/output failure (502). A failure is not a factual verdict. Original
prompt-only endpoints and the current UI are unchanged. No live combined-model
quality measurement or semantic prompt-injection evaluation has been run.

This is still a fixed workflow, not an agent: the application always performs
analysis then one retrieval/assessment step. An agent will choose whether to search
again based on what it found, within enforced limits.

### Session 8: a bounded tool-calling agent

Built app/evidence/research.py and POST /api/research. All 72 tests pass, including 11 agent
tests and two API checks. Model decisions are scripted in tests, not sampled from
a live model. There is no evidence yet that the agent improves retrieval quality.

**What changed:** the fixed workflow retrieves once. Here the model can request a
search, observe its results, request a different query, read a passage, or stop.
The application executes only the two allowlisted tools with validated arguments.
An assistant tool call includes an ID; its tool result must use the matching ID
before another model request. See app/evidence/research.py for the actual message sequence.

**Two boundaries:** search exposes previews; read_passage opens known IDs. Final
assessment receives only opened passages. This makes the selected evidence
inspectable rather than silently giving the assessor every search result.

**Budgets live in code:** four planner steps, six total tool executions, three
searches, five distinct reads, and a 45-second cooperative deadline. Invalid tool
calls also consume the total tool budget. Each search returns at most two results.
Provider timeouts use remaining time and SDK retries are disabled. Local blocking
work is not forcibly cancelled: checks stop additional work after the deadline.
The final assessment is a separate bounded call without validation retries.

**Completion versus partial results:** when budgets run out, we return available
events/passages and no verdict. A normal finish with no inspected evidence gets
insufficient evidence. We do not pretend a stopped run completed factual research.
Provider timeouts also return partial results; other provider/output failures are
API errors. Source contamination can still affect choices despite tool limits.

**Framework choice:** this is a plain Python loop using native function calls,
not LangGraph. Understanding messages, tools, state, and stopping conditions first
will make a later framework migration explainable. Do not claim LangGraph on a
resume until it is actually integrated and tested.

**Trace:** returned events record tool inputs and outputs, not hidden reasoning.
There is no persistent trace storage or private-source redaction yet. The API
still serializes local Qdrant access and requires one worker. UI and real-model
comparisons remain future work.

Explain back: if a model repeatedly asks for another search, what actually stops
it: our system prompt, or the counters and deadline in application code?

### Session 9: evidence UI and honest run states

Added Evidence Lab to the existing page with mode selection, loading states,
weakness/evidence sections, source cards, and completed research activity. Inputs
stay available after failures and submissions are disabled while a run is active.
The UI displays partial research as partial, without manufacturing a verdict.

This is vanilla JavaScript served by FastAPI, not Next.js. Tool events appear after
completion, not over a live stream. Those differences remain explicit in the
roadmap. A backend capability is not a finished user feature until someone can
understand its outputs, limits, and failures in the interface.

**Untrusted output:** source and model text is inserted through textContent rather
than innerHTML. Citation links are restricted to credential-free HTTP(S) URLs.
In a browser fixture check, an HTML image payload appeared literally as text and
created zero image elements. This checks that rendering path, not every potential
web security issue. Legacy inline error messages were also changed to text nodes.

**Verification:** all 72 Python tests and four Node renderer/URL tests pass.
The Node tests use a minimal DOM contract; actual browser checks separately
verified completed citations, partial research, preserved input after a setup
error, retry, and the HTML payload at the available narrow viewport. No live LLM
calls were made. tests/ui_preview.py is an explicitly fake local server, not the
production app. A screenshot of fixture results is saved in the ignored .data
folder. The preview server was stopped after verification.

**Still pending:** Next.js migration, live generative quality checks, desktop
breakpoint checks, accessibility review, SSE events, persistent runs, and deployment.
The current local corpus and single-worker storage limitations still apply.

Explain back: why should an agent's 'step limit reached' state look different from
a completed assessment that found insufficient evidence?

### Session 10: migrate real orchestration to LangGraph

The historical sessions above describe the earlier plain-loop implementation.
app/evidence/research.py now uses LangGraph 1.2.22, pinned in requirements.txt. All 76 Python
tests and four frontend tests pass. The opt-in cached-model integration smoke
also passed with actual BGE embeddings, Qdrant, and a scripted generation client.

**What LangGraph does:** ResearchState defines per-run data. The decide node asks
the model for tool calls; the tools node executes only validated local tools and
updates evidence/counters. Conditional edges return to decide or stop. Normal
completion routes to assess, which reuses citation validation. This is graph-driven
execution, not a loop hidden inside one decorative graph node.

**State updates:** each node returns changed fields. Lists/maps are copied rather
than mutated in place; the next node sees the merged state. No message reducer is
needed here because each sequential node returns its full updated message list.
Tests verify the executed node order and that a new invocation starts clean.

**Termination:** application counters/deadline still enforce budgets. LangGraph's
recursion_limit is an additional guard based on graph steps, not the same thing as
the number of LLM calls. Explicit path maps also make conditional routes visible
to graph inspection. Returning partial results still skips the assessment node.

**What this does not add:** durable memory, checkpoints, human approval interrupts,
hosted deployment, or better reasoning by itself. The public result now identifies
the orchestrator and executed node names, not private model reasoning.

**Verification boundaries:** tests include normal search/read/assessment, query
reformulation, errors, limits, and an opt-in run through cached real vectors.
Generated decisions and verdicts are scripted in these checks. No .env is present
in this checkout, so real Groq output quality remains untested here. LangGraph,
RAG and Qdrant are implemented; do not claim measured answer accuracy or improved
agent quality from these plumbing tests.

Explain back: which behavior comes from LangGraph's edges, which decisions come
from the model, and which limits are enforced by our application code?

### Session 11: first live checks and real integration failures

Configured the ignored local .env without displaying credentials. Network-restricted
requests initially failed; outside that restriction Groq reported model_not_found
for the old Llama ID. Verified the account's model list and Groq documentation,
then migrated the shared default to openai/gpt-oss-20b. Evidence/research requests
use low reasoning effort with bounded token budgets; truncated replies cannot
silently become completed research. The foundation hello_groq example was updated too.

**The important live failure:** generated quotes flattened local Markdown line
breaks. The words were correct, but exact substring validation rejected them.
We added deterministic whitespace-only restoration to the original source text,
then kept exact validation. Repairs are exposed in the result. Fabricated wording,
different punctuation, missing IDs, and altered numbers still fail. This preserves
source traceability without turning validation into fuzzy semantic matching.

**Final live smoke:** the scoped retention claim was supported with a citation;
the blood-pressure claim abstained with no citations; LangGraph performed a live
search and read and completed its final assessment. Times were approximately
0.96, 0.57, and 3.25 seconds for those specific runs. Review notes are saved in
eval_results/live_smoke_review.md; full results are local in .data/live_smoke.json.
No credentials or private model reasoning were recorded.

The regression suite ran 79 Python checks with no failures and one optional smoke
skipped; four frontend tests also passed. The optional cached-model smoke had
passed previously. Live smoke success is not general accuracy: we still need
contradictions, mixed evidence, adversarial inputs, repeat runs, and held-out cases.

Explain back: how is restoring source whitespace different from accepting a
paraphrased citation, and why did mocked tests miss this real formatting failure?

### Session 12: PostgreSQL persistence and optimistic concurrency

Added app/storage.py, migration 001, optional Psycopg dependencies, isolated local
PostgreSQL setup, repository-backed API sessions, and saved evidence/research runs.
PostgreSQL 17 was already installed; a separate password-protected cluster was
created under ignored .data, bound to localhost:55432. Existing services were not
changed. DATABASE_URL was added only to ignored .env without printing credentials.

**Two databases, different jobs:** Qdrant holds vectors and evidence payloads for
similarity search. PostgreSQL holds application state: conversations, token counts,
model choices, revisions, and analysis results. JSONB preserves structured messages
and reports; typed columns retain IDs, timestamps, and update revisions.

**Transactions:** each repository operation opens a connection that commits on
success and rolls back on exceptions. Model generation happens outside a database
transaction. Holding a database lock while waiting for an LLM would waste scarce
connections and block unrelated work.

**Optimistic concurrency:** two requests can load the same session revision.
UPDATE ... WHERE revision=old_revision allows only the first writer to succeed.
The second receives HTTP 409 and must reload. This prevents lost turns but cannot
refund inference quota already spent by the stale request. Loaded sessions are
copies; failed calls do not mutate the saved record.

**Persistence versus caching:** a fresh API/repository instance loads records from
PostgreSQL rather than relying on a Python dictionary. If DATABASE_URL is absent,
the explicit memory mode remains available for a small demo. If a configured DB
fails, the API returns 503; it does not silently switch to memory and lose data.

**Run recovery:** evidence/research APIs save results with run IDs. GET /api/runs/ID
reopens them without generation. The browser remembers only last-used IDs, and
reopens saved results on demand. It can restore the most recent debate on reload.
These are final/partial result snapshots, not resumable LangGraph checkpoints.

**Tests:** shared contract checks ran against both memory and real PostgreSQL.
They cover round trips, preserved prompts/models/tokens, failed generation, stale
updates, Unicode/SQL-quote input, and missing records. A fresh API instance reopened
a saved debate and partial run. Tests clean only their own unique rows. The new
browser reopen handler has a DOM-contract test, not yet a manual visual check.

**Still local development:** DB helpers start/stop a dedicated cluster, not an
autostart service or production DB. Saved arguments now need ownership/authentication
before public exposure. Backup/retention policy, source-ingestion records, pooling,
multi-worker Qdrant, and deployment remain outstanding.

Explain back: why can 'save both messages after generation' still lose data when
two requests run concurrently, and how does the revision check prevent that?

### Session 13: accounts, revocable sessions, and authorization

Added local signup/login/logout/me APIs and a login UI. Authentication is on by
default; local demos/tests can explicitly disable it. Migration 002 adds users,
hashed sessions, and record owners. It preserves legacy ownerless records without
assigning them to anyone. Do not claim those records as the next registered user.

**Authentication versus authorization:** a cookie identifies the logged-in user.
Repository queries also filter by that user's ID. Knowing another debate/run ID
does not grant access; other users get 404. This check covers reads, turns, scores,
analysis, and saved-run reopening. Public health/persona endpoints expose no records.

**Passwords versus tokens:** passwords use randomly salted PBKDF2-HMAC-SHA256 with
600,000 iterations, not a fast plain hash. Random browser session tokens have high
entropy, so their SHA-256 digest can be stored for lookup. Raw tokens go in HttpOnly,
SameSite=Strict cookies, not localStorage. Tokens expire after seven days, rotate
on login in that browser, and are revoked on logout. This is not JWT authentication.

**Browser boundaries:** JSON write requests check Origin/Fetch Metadata. HTTPS
deployments must set the public origin and use Secure cookies; local HTTP uses
non-Secure cookies only for development. API responses are no-store, and validation
errors omit invalid input values so passwords are not echoed. Rate limiting is
ten auth attempts per source IP per minute per process, not a distributed solution.

**Late-response race:** a model request may start under account A and finish after
sign-out. Clearing visible results alone is insufficient: the old response can
repopulate them. Auth changes now advance a frontend epoch; old responses are
discarded. Tests cover this and password clearing. This is local UI hygiene, not
a replacement for server-side ownership checks.

**Verification:** 105 Python checks ran with PostgreSQL integration enabled, no
failures and one optional embedding smoke skipped. Seven frontend tests pass.
Cases cover hashing/salts, rotation/logout, expiry/forgery, cross-user 404s,
cross-origin writes, throttling, Secure cookies, and SQL ownership/token storage.
New login controls are DOM-contract-tested, not yet manually browser-verified.

**Limits before public launch:** no verified email, password reset, MFA, account
deletion/retention policy, distributed rate limiter, or per-user inference quota.
Open signup alone does not protect API spend. There has been no security audit.
The local .env and database password remain ignored; no real user account was
created for you. Create your own account through the UI after starting the app.

Explain back: why doesn't a UUID run ID replace an ownership check, and why do we
hash passwords slowly while hashing random session tokens with SHA-256?

### Session 14: opening a file is not running the application

A user screenshot showed index.html opened through file://. The root-relative
CSS/JS paths and API requests require an HTTP server; they do not resolve to our
FastAPI application from a local file. The sign-in script consequently never
initialized, leaving a misleading 'Checking sign-in' message and an unstyled form.

Started the existing local PostgreSQL cluster, applied migrations, and started
uvicorn on 127.0.0.1:8000. The real served login page was browser-verified with
styles and initialized sign-in state. No user account or live model request was
created during this check. Added start.ps1 and an inline file-opening notice that
works even when the external scripts cannot load. Kept the real server running
for the user; use Ctrl+C in its terminal to stop it.

Explain back: why can an HTML file open in a browser while the application behind
its login button is still not running?

### Session 15: search matches are not inspected evidence

A real user tried a CEO-intelligence claim outside the current two-study corpus.
The agent searched twice, saw remote-work previews, and opened no passages. It
correctly produced no supporting citations, but the fallback explanation wrongly
said no passages were returned from the index. Search had returned candidates;
the assessor simply had no inspected evidence.

We now distinguish candidates_not_inspected, no_candidates, and not_searched.
Results expose candidate/read counts, and the UI labels completed abstention as
'Research finished — evidence insufficient'. The coverage notice explicitly names
the current remote/hybrid-work scope and says there is no web search. Saved old
runs remain historical; the UI can infer counts from their events.

Thirteen research tests and seven frontend tests pass for this change. The result
does not establish whether a CEO is smart: that vague claim also needs a clear
definition and relevant evidence. Adding a framework does not create a corpus or
turn similarity into factual support.

### Session 16: public web sources and provider reliability

Added explicit web mode to the API/UI, with configured Tavily or optional DDGS/Bing
search and a separate public-page reader. The local corpus path remains unchanged.
The agent still uses search/read tools, but web search returns candidates, and read
fetches a page excerpt before the assessor can cite it. Normalized page text,
source URL, timestamp, hash, and excerpt offsets preserve provenance.

**A real external integration can fail independently:** one key-free search worked;
subsequent attempts failed. The page reader fetched python.org successfully, while
an academic source failed TLS. We did not bypass certificate checks or challenges.
The Tavily adapter has mock checks but no live key yet. No end-to-end live web-agent
quality claim is justified by these tests.

**SSRF boundary:** reading an arbitrary model-generated URL could reach localhost
or cloud metadata services. Our tools accept only IDs returned by search, require
public HTTPS, validate all DNS answers, connect to a validated IP, and preserve TLS
hostname verification. Redirects get checked again. Tests cover private/mixed DNS,
redirects, URL credentials, content types, size, and connection pinning. These are
focused safeguards, not a complete security audit.

**Evidence boundary:** snippets stay previews. Reads select a short lexical-match
excerpt from fetched text, without executing scripts or sending browser credentials.
An excerpt may miss context or come from an unreliable site, so citations are not
proof of truth. Web results are not silently inserted into the curated Qdrant corpus.

**Failure semantics:** if tools fail and no inspected evidence exists, return a
partial tool_error run. Do not turn a failed provider into 'no evidence exists'.
Web mode exposes provider metadata and is opt-in because queries go to an external
search service. Optional Tavily credentials belong only in ignored .env; never chat
or tracked source. Live provider credits/quotas differ from Groq generation quota.

Next verification needs a configured search provider, then real web-agent cases:
supported, contradicted, vague/non-checkable, missing, blocked sources, and malicious
page prose. Keep the current local retrieval/evaluation baseline for comparison.

### Session 17: repeatable checks through CI

The Tavily key is still missing, so reliable live web verification cannot proceed.
Independent work continued on .github/workflows/ci.yml: pushes/PRs/manual dispatch
will run Python 3.12/3.13 tests with a temporary PostgreSQL 17 service and a separate
Node 22 frontend job. No production keys or embedding downloads are used.

**CI versus evaluation:** CI checks regression contracts on known cases; it does
not establish live model quality or prevent every hallucination. Real model runs
remain a separate, explicitly authorized activity with quota/cost and human review.

**Environment parity:** the workflow was YAML-checked and equivalent checks ran
locally. That is not the same as a green GitHub run. Linux wheels, action execution,
and the two Python versions still need first hosted verification after pushing.

**Build context is another boundary:** .gitignore does not control Docker COPY.
Our .data folder was Git-ignored but not Docker-ignored, which could include local
database records/passwords and large embedding caches in a future image. Added
.data and environment variants to .dockerignore while retaining .env.example.
No image was built or published, and no local credentials were printed.

Local CI-equivalent validation completed: 120 Python checks, zero failures, one
optional embedding smoke skipped; eight frontend tests passed and pip check found
no broken requirements. The quiet run took about 50 seconds rather than its earlier
20-second runtime. A diagnostic rerun started just as that result arrived and was
stopped as redundant; no code change was justified by the slower run alone.

Explain back: why can all local tests pass while a first CI run fails, and why
does a secret being Git-ignored not guarantee it stays out of a Docker image?

For future sessions, use:

- Date and module:
- Concept in my own words:
- Files changed and commands run:
- Observed result:
- Failure case and what it taught me:
- Remaining question:

## Session 18 — Character-style UI and frontend separation

The interface now uses a profile-and-tabs layout inspired by the supplied Character.AI page. Chat, Evidence Lab and Quick critique share dark tokens, typography, forms and result cards. The reference was inspected in the browser; its dark screenshot differed from the scraper's light-theme branding metadata, so we used the visible layout and documented the difference in DESIGN.md.

HTML defines structure; evidence-lab.css defines appearance; workspace-ui.js manages tab selection; debate-ui.js manages conversations; evidence-lab.js manages research; auth-ui.js manages identity. This keeps a design edit from requiring changes inside a long inline application script. Styling uses local CSS with system fonts and needs no Tailwind CDN or frontend build step.

Tabs change the hidden state and aria-selected together. Arrow keys, Home and End move focus. Switching tabs preserves drafts; changing accounts still invalidates old requests and clears private content. A failed debate turn keeps the user's text and does not append a message the server never accepted. Pending turns block duplicate submission. Raw research JSON remains available inside collapsed tool disclosures.

Validation: existing safe-rendering/account/result tests plus regressions for duplicate turns, failed-turn draft retention, collapsed tool details and keyboard tabs. Browser checks cover desktop layout, keyboard tabs, persona selection and reopening a real saved run without a new model call. Responsive CSS is implemented, but the final mobile viewport check remains unverified: the browser viewport override continued reporting 1280px after requesting 390px. Visual work does not establish new RAG accuracy or web-search reliability.

Explain back: why should hidden panels keep their DOM state, while signing out must explicitly clear it? Why should API errors preserve a draft rather than pretending a message was sent?

## Session 19 — Spending fewer tokens without extra summary calls

Previously every chat request resent the entire conversation. Debate.request_messages now chooses up to four recent complete user/assistant pairs under a 12,000-character history budget, adds up to 800 characters each from the opening user/assistant exchange when older history is omitted, and includes the latest input (maximum 4,000 characters). The full saved transcript stays intact. These excerpts are deterministic selections, not semantic summaries; no summarizer API call is added. Old details may need to be restated.

Chat uses a 120-word instruction, low reasoning for compatible GPT-OSS models and a 768-completion-token cap. Completion budgets include reasoning on reasoning models. Character counts are not token counts; neither the cap nor a mock test proves that answer quality is unchanged.

AnalysisCache stores up to 128 successful results for ten minutes, keyed by owner, task, selected model and input hash. It deduplicates concurrent identical generations with a Future while releasing the global lock during network work. A new argument or debate turn produces a different key. Results are copied to avoid mutable-state leaks. Errors are not cached. The cache is per process and resets on restart; it is not persistent or distributed. The UI also disables pending analysis actions.

The web API's Groq client uses max_retries=0 to prevent hidden HTTP retries; JSON-output repair still allows one retry. Persona selection itself makes no LLM call. Research is explicit and may make multiple model calls; these changes do not turn agentic research into a single-call operation.

Checks use mocked provider responses: bounded context, intact transcripts, no extra summary calls, invalid input rejection, analysis reuse/invalidation, ownership, expiration and concurrency. No live paid request is needed to verify those contracts. Real token savings and response quality still need a controlled live comparison if desired.

Explain back: why does a local excerpt cost zero extra requests but sometimes lose context? Why must a cached score include the model and input in its key? Why are API call count and token usage different measurements?

## Session 20 — Similarity is not evidence relevance

Qdrant returns nearest vectors even if the corpus contains no answer. A CEO-intelligence claim previously retrieved remote-work studies; high ranking alone did not make those passages evidence. RelevantLocalIndex now screens local candidates using a transparent keyword heuristic before they can be read or assessed. It ignores generic work-arrangement terms, normalizes a small set of outcome synonyms and requires at least half of the remaining unique claim terms to occur in the title/text. This is not entailment checking and can miss paraphrases or accept accidental overlap.

For agent research, the original claim remains the screening target even when the planner reformulates a query. A local preflight runs before the planner: no accepted candidates means an insufficient-evidence report, zero LLM calls and no automatic web request. The event is labelled local preflight rather than pretending an agent ran. When research continues, two empty searches with no discovered/inspected evidence stop the graph before another planning call. Evidence-plus-critique still needs its rhetorical diagnosis call; it skips the factual-assessment call when the selected quote has no relevant local evidence.

Verification used mocked control-flow tests plus the real cached BGE/Qdrant index: all three top candidates were rejected for CEO intelligence and remote-work/blood-pressure claims; hybrid-retention and performance-review queries retained matches. The real-index unrelated research report had zero LLM calls. These few examples are a smoke check, not held-out relevance accuracy. Existing raw retrieval evaluation still measures semantic ranking separately from the application-level filter.

Explain back: why can the nearest vector be irrelevant? Why must reformulated queries remain constrained by the original claim? Why is rejecting evidence different from disproving a claim?

## Session 21 — Rendering AI replies as readable chat

Model replies were shown with white-space preservation only, so Markdown markers appeared literally and long responses sat inside a second scroll area. message-format.js now renders a limited Markdown subset (headings, strong/emphasis, lists, quotes and code) using DOM nodes and textContent. It never interprets raw HTML or creates model-supplied links/media. This avoids turning generated content into executable markup and adds no network dependency.

The bot message layout uses a readable line width, paragraph spacing, a small persona label and an unboxed response. User messages stay compact bubbles. Long saved paragraphs are visually split at sentence boundaries using Intl.Segmenter when available; the stored content is unchanged. The nested transcript scrollbar is removed and the composer stays at the viewport bottom while scrolling through the conversation.

Verified on the existing saved two-turn conversation: six section headings rendered, the latest reply had five readable paragraph blocks, and the message container had visible overflow rather than an inner scrollbar. Fourteen frontend tests pass, including inert raw HTML and unfinished code fences. No new LLM request was needed. This improves presentation of old responses; it does not repair unsupported factual claims inside them.

## Session 22 — A shared orange and charcoal theme

The UI now uses warm black/charcoal surfaces, cream text and terracotta actions. Major headings and assistant messages use Georgia for a calmer reading style; controls keep the system sans-serif font. Reusing shared color tokens and consistent component colors makes Chat, Evidence Lab and login belong to one interface. Semantic success/error colors remain distinct from the decorative accent. The theme was visually verified on the saved chat and Evidence Lab without new model requests or browser console errors. It is Claude-inspired styling, not a copied or inspected Claude design system.

## Session 23 — Removing visual clutter

The workspace now removes the duplicate bot profile/sidebar, repeated marketing copy, decorative badges and footer. Chat shows the active persona, actions, messages and composer; the persona picker heading disappears during a conversation. Token totals remain in backend data but the visible status only shows turn progress. Research source limitations/privacy and saved-run controls remain accessible through disclosures, while web-mode help still explains external search before submission. Fourteen frontend tests pass; live Chat and Evidence Lab were visually checked with no console errors and no model calls.

## Session 24 — Claim CI evidence contracts

The first Claim CI slice is a read-only CLI, separate from the debate interface. A user declares claim types, explicit dependency files, required observation cases or benchmark thresholds in .claimci.json. The checker outputs JSON/Markdown with supported, contradicted or unverified findings, file hashes, previous-status comparisons and commit metadata. It never imports target code, executes its tests, fetches URLs or asks an LLM for a verdict.

Static Python network/provider calls are review signals. They do not establish that a path executed or transmitted private data. Network locality needs recorded observation cases. Citation checks use actual response snapshots and exact quote containment in recorded passages; this does not prove entailment. Benchmark checks require matching metric/unit, adequate samples, duration, method and environment; throughput and concurrent users are not interchangeable. Artifacts must declare their recorded commit/time and match the current hashes of every configured dependency. Fixture-only, missing, stale or incomplete artifacts cannot pass.

The registry deliberately does not invent production evidence from the existing mocked tests. Its two initial claims currently report unverified because observation artifacts are missing. Artifacts remain self-reported inputs, not signed attestations. A future collector must supply trustworthy observations. The checker compares dependency fingerprints across reports but still evaluates all claims; selective agent reruns and automatic discovery are not implemented.

The GitHub workflow has read-only permissions and writes a Markdown job summary using the official GITHUB_STEP_SUMMARY mechanism. It needs no LLM key and makes no PR comments. It has been parsed locally, not run on GitHub. Dirty local snapshots withhold commit links so file/line evidence does not point at an unrelated committed version. Fourteen new tests exercise evidence coverage, artifact freshness, counterexamples, benchmark units, input safety and report/CLI behavior.

The existing LangGraph, RAG and Qdrant research components remain intact. Claim CI starts with deterministic evidence contracts so future agent recommendations cannot silently override missing observations. This is engineering foundation, not proof of product uniqueness or real user adoption.

## Session 25 — Real observations versus scripted probes

tools/claimci_collect.py now runs known scenarios from this trusted repository in a short-lived child process. The default offline profile executes real cached BGE/Qdrant searches for a retention claim and an unrelated claim. tools/claimci_network.py installs a Python audit hook that records and blocks the documented socket/DNS operations during those windows. Registration is checked explicitly; incomplete runs cannot turn zero events into a passing finding. Counts record attempted events, not completed HTTP requests. Python audit hooks are not an OS sandbox and do not cover native I/O or activities outside those windows. The registry claim was narrowed accordingly.

The collector records start/completion times, run IDs, package/Python versions, commit/working-tree metadata and hashes of the declared code/corpus files. Files are rechecked after collection. Observation JSON is written atomically to an ignored directory and excluded from Docker images. Artifacts remain self-reported and are not cryptographically attested. Inspect timestamps after failed runs; historical evidence must not be presented as a fresh capture.

The validator profile runs exact, invented and unknown-passage quote probes against the actual validation function and local source text. Those inputs are scripted, so the resulting artifact is fixture_only=true and cannot verify the live-model response claim. The optional live-citations profile requires --allow-live-llm, directly invokes the assessor, counts actual requests and caps usage at one call with SDK and JSON repair retries disabled. It was tested with a fake provider but was not run live. The CLI checker remains read-only; the separate collector deliberately executes only the known local operations.

Observed offline result: cached-query and missing-evidence both completed with zero monitored Python socket/DNS events. The report has one supported scoped instrumentation claim and one unverified live-citation claim. No Groq request was made. Twenty-one Claim CI/collector tests cover case coverage, interrupted observations, real child-process blocking, stale hashes, fixture labelling and quota opt-in; the full Python suite ran 156 tests with one optional cached-embedding smoke skipped. The new GitHub workflow only collects labelled validator probes and has not been run hosted.

## Session 26 — A post-interview evidence review council

The new `/review` module begins after an interview has finished. It accepts a
speaker-labelled transcript, job description, up to three competency criteria
and optional resume context. It does not conduct interviews. Start with the
synthetic sample to inspect the complete flow without spending API quota.

**RAG:** `app/review/evidence.py` splits answers into addressable sources with
speaker labels and line ranges, then wraps them as LangChain Documents. The
cached local BGE encoder creates embeddings. A fresh in-memory Qdrant collection
retrieves Candidate passages for each criterion, with nearby Interviewer questions
for context. The collection closes after retrieval. Resume context cannot be
cited as demonstrated interview skill. This avoids mixing one person's evidence
with another's, but retrieval can still omit relevant answers. Nearest passages
are candidates for evidence, not proof that a skill exists or is absent.

**LangGraph:** `app/review/council.py` implements an explicit workflow:
ingest -> retrieve -> two independent reviewers -> validate -> adjudicate ->
validate final. The reviewers run concurrently against the same frozen evidence
and rubric. They do not see each other's initial findings. A failed reviewer or
invalid quote skips the judge; the report remains partial instead of fabricating
a combined answer. This first version is a bounded council workflow. It has no
open-ended tool planning or autonomous challenge loop.

**LangChain and providers:** `app/review/providers.py` exposes the same Runnable
contract over explicit Groq/OpenRouter HTTP routes. API keys authenticate a
request; different keys do not make identical models independent. Configuration
requires distinct model IDs and declared families, including the judge. These
family labels are operator assertions. The wrapper handles format, timeouts and
budgets; it does not remove model bias. The judge receives anonymous reviewer
IDs in a logged shuffled order plus the original selected evidence, without
provider labels. A third model can still share errors or prefer a writing style.

**Grounding checks:** `app/review/models.py` validates structured output and
requires exact nonblank Candidate quotes from supplied source IDs. Unknown IDs,
invented quotes, resume citations as demonstrated skill, duplicate criteria and
missing criteria are rejected. A judge cannot label disagreeing statuses as
agreement. Exact quote containment only verifies the reference; a model can quote
correctly and interpret it incorrectly. `not_assessed` refers to the retrieved
excerpts and must not be read as lack of ability.

**Costs and state:** a new live run uses at most two reviewer requests and one
judge request, with no automatic retries. Identical complete requests reuse an
account/configuration/input-specific result for ten minutes. Partial reports are
saved but not cached as success. Reports include model labels, actual request
counts, reported usage, reviewer order, graph stages and hashes of the rubric and
policy. PostgreSQL ownership checks also apply when reopening a report. Source
preview requires no model call; live processing requires preview confirmation and
authorization for the external services. There is no automatic PII redaction yet.

**What was actually tested:** 168 Python tests (one optional embedding smoke
skipped) and 16 frontend tests passed, covering provider mocks, exact citations,
partial failures, graph routing, source isolation and account-scoped reuse.
The real browser synthetic run used the cached encoder and Qdrant, saved to
PostgreSQL and reopened with zero model requests. Its model outputs are explicitly
scripted, so this proves workflow integration, not live assessment quality or
fairness. Live route smoke tests, an equal-budget single-model baseline, held-out
human labels, order-swap tests and human correction controls remain to be built.

Explain back: why do separate API keys not establish independence? Why must both
reviewers see the same evidence? What can an exact quote check prove, and what
can it not prove? Why should an unasked or unretrieved skill stay `not_assessed`?
When should the workflow stop rather than spend another model call?

## Session 27 — Removing inactive code and redundant retrieval work

Removed the original one-shot tutorial and an outdated manual fixture server;
the real app's labelled synthetic review sample now covers that preview flow.
Unused imports, styles, tab attributes, an unused sample-data endpoint/export and
the API's redundant Python path mutation were removed. Reviewer/judge output
validation now shares one path, with adjudication consistency checks in the
models module. The synthetic adapter reuses the real model-route metadata type.

Qdrant now indexes only Candidate answers. Questions and resume context are
attached from the original sources after search, so embedding and storing them
was unnecessary. Source quotes and ownership remain unchanged. After cleanup,
168 Python tests ran (one optional skip), all 16 frontend tests passed, and a
real cached-encoder demo completed with zero model requests. Cleanup should
remove inactive paths and repeated work while preserving verified behavior.

## Session 28 — Feature packages and an application factory

Application code now lives under `app/`, browser assets under `frontend/`,
development/evaluation entry points under `tools/`, and documentation under
`docs/`. `app/main.py` is the ASGI entry point. `app/api/application.py` assembles
authentication, feature routers, static assets and error handling through
`create_app`. Tests inject repositories and model clients instead of repeatedly
reloading a global API module. Each application owns its caches; its lifespan
closes only the model client it created.

This separation lets source preview work without generation credentials and
allows two app instances to keep their private state separate. Shared absolute
paths in `app/core/paths.py` keep the existing database, embedding cache, SQL
migrations and frontend resources in place when modules move. CLI commands use
`python -m tools.<name>`; imports no longer depend on a modified Python path.

All 170 Python tests ran successfully (one optional skip), with 16 frontend
contracts passing. Tests include another working directory, keyless preview,
instance isolation and real PostgreSQL reopening. A separate HTTP smoke used
the real cached encoder, saved/reopened a synthetic report and made zero model
requests. The new entry point served both workspaces and assets successfully.
See [the architecture and data flow](ARCHITECTURE.md) for module boundaries.
