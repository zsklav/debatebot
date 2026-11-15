# Post-interview evidence review council

Status: first module implemented at `/review`, with a real local evidence pipeline
and an explicitly scripted synthetic demo. Live multi-model routes are configurable
but have not been exercised with real provider credentials in this session.
Interviews happen elsewhere. This product covers the review that happens after
an interview. It does not build a resume-screening interview platform, generate
interview questions, conduct interviews, schedule sessions or record live audio.
Existing DebateBot/Claim CI remains intact. The first implementation is an
isolated module inside the current repository, not a replacement.

## Inputs

- Completed interview transcript with speaker labels and source line IDs.
- Job description and a predefined, versioned competency rubric.
- Optional plain-text resume context. Separate interviewer-note import is planned.

Start with pasted/structured text; add PDF/DOCX imports after the first complete
review works. A generated summary alone is not equivalent to the original
interview evidence. Allow users to correct transcript/speaker extraction first.

## Workflow

1. Normalize supplied documents and assign stable source/excerpt IDs. Extract
   only job-relevant information; exclude personal attributes from assessments.
2. Retrieve passages for each declared competency using owner/review-scoped
   Qdrant collections or filters. Keep resume claims distinct from demonstrated
   interview answers and interviewer impressions.
3. Run two reviewers from different model families against the same competency
   rubric and frozen evidence snapshot. Both identify demonstrated strengths,
   gaps, uncertainty and source references. Neither sees the other's initial
   findings; fixed pros-only/cons-only assignments would not be comparable
   competency assessments. Preserve each model/version and settings for audit.
4. Deterministically validate quoted excerpts and source IDs. Invalid citations
   fail the finding rather than becoming plausible-looking report prose.
5. A council synthesizer compiles the report. It checks disagreements against
   original excerpts and leaves unresolved findings explicitly unresolved. An
   optional bounded challenge round is planned; none runs in the first slice.
6. A human inspects individual findings and their sources. Accept/correct/dispute
   controls and persisted overrides are planned, not implemented in this slice.

Different API keys control authentication/quota; they do not establish model
diversity or fairness. Different model families can still share errors. Panel
benefit and group-level fairness must be measured separately against human
review and an equal-budget single-model baseline.

## Report

- Interview summary with references to the underlying answers.
- Per-competency demonstrated evidence and job-specific rubric assessment.
- Strengths and weaknesses supported by quoted answers.
- Not assessed / insufficient evidence / disputed findings kept separate from
  demonstrated weaknesses.
- Resume claims not verified by the supplied interview.
- Reviewer disagreements and the synthesizer's evidence-based resolution or
  explicit abstention.
- Concrete verification suggestions for the human reviewer.

The report assists post-interview review. It does not autonomously hire/reject,
rank people by irrelevant characteristics, infer honesty/personality/intelligence
from language or voice, or turn an untested skill into a lack of ability.

## Example

Resume: candidate lists Redis. Transcript: candidate describes caching a query
but gives no invalidation strategy. A reviewer may record a basic caching example;
an evidence challenger can object to an unsupported advanced-distributed-systems
rating. The synthesizer reports the demonstrated caching explanation and leaves
advanced proficiency unverified, with source excerpts. It does not infer that the
candidate lied or that an unasked skill is absent.

## Stack

- FastAPI: source-preview, review-start and saved-report endpoints. Human-feedback
  endpoints are planned.
- LangChain: document adapters, model integration and structured-output adapters.
- LangGraph: ingestion -> retrieval -> bounded reviewer branches -> validation ->
  disagreement handling -> synthesis -> human review. No interview loop.
- Qdrant + RAG: retrieve interview evidence for each supplied rubric criterion;
  the JD/rubric are passed as context rather than separately indexed. Per-run
  collection isolation keeps other candidates' evidence out of the report.
  The existing two remote-work studies are not a suitable competency knowledge base.
- PostgreSQL: private reports, source locations, rubric versions and findings.
  Reuses existing account-ownership patterns. Overrides are planned.

Keep the first call budget at two reviewer calls plus one synthesis. Include
summary generation in synthesis. Allow one additional disagreement call only
when required. Extraction/retrieval and quote validation need no LLM by default.
These are design targets, not measured latency/cost or reliability guarantees.

## First implementation slice

Import a completed text transcript plus JD/rubric -> validate/source-index inputs
-> retrieve criterion-specific answers -> two independent reviews -> validate
quotes -> anonymous adjudication -> save an evidence-linked report. This flow is
implemented. Human corrections, optional challenge rounds and live-quality
evaluation remain next steps. No live interviewing or question generation.

Use synthetic or explicitly consented development transcripts. Measure evidence
coverage, unsupported findings, rubric consistency, false gap findings, source
validation, latency and tokens under equal-budget single-LLM comparisons. Keep
PII out of model context when unnecessary, enforce private storage/retention,
and disclose external-model processing. Do not publish numerical confidence as
calibrated probability without validation.


## Multi-model council and adjudication layer

Architecture implemented in the first module (live route quality/fairness not validated):

Evidence snapshot + rubric -> model-family A reviewer and model-family B reviewer
-> deterministic schema/source/quote checks -> anonymous findings + disagreements
-> adjudicator -> human-reviewed report.

The provider wrapper supplies a common request/response interface, configuration,
timeouts, token budgets and output validation. It does not make its underlying
models neutral. Provider/model identity stays in the audit record; the adjudicator
receives reviewer IDs, matched criteria and original evidence, without vendor
labels. Reviewer order is randomized with the order/seed recorded. Style may
still reveal origin, and anonymization is not a guarantee against preference.

The adjudicator is a separate model role, preferably distinct from the initial
reviewer families when practical. It resolves findings against transcript
quotations rather than picking a model winner or averaging unrelated scores.
If the evidence cannot resolve a disagreement, it reports that dispute for a
human instead of inventing consensus. A third model is still a possible source
of bias; a wrapper around it is not an independent fairness authority.

Test order-swapped adjudication, repeated-run stability, unsupported-finding
rates and agreement with job-relevant human rubrics. Compare model combinations
under the same evidence and budgets. General LLM judge benchmarks do not
establish reliable or fair employment assessment, and numerical self-confidence
is not a calibrated probability.

Primary research informing the design:

- Zheng et al., [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685): documents position, verbosity and self-enhancement bias in general LLM evaluation.
- Verga et al., [Replacing Judges with Juries](https://arxiv.org/abs/2404.18796): reports benefits from diverse evaluator panels in the studied LLM-output evaluation settings; this does not validate hiring fairness for this proposed product.


## First implementation: running and limits

Open `/review` on the existing FastAPI server and sign in with the existing
account. `Try synthetic sample` uses the real parser, isolated in-memory Qdrant,
cached FastEmbed encoder, LangChain Document/Runnable contracts and LangGraph
workflow. Model outputs are **scripted and labelled**, with zero external model
calls. The sample is not a model benchmark or a real candidate assessment.

For another transcript, paste/import `.txt` or `.md`, enter the JD and up to three
job-related criteria, then preview and correct normalized sources. PDF/DOCX/audio,
resume parsing, interviewer-note imports and interview conducting are not in the
first slice. Resume context is optional plain text, distinct from demonstrated
interview evidence. The first version expects English text and Candidate:/
Interviewer: turn labels. Contact/demographic data is not automatically redacted;
use synthetic/redacted or authorized material and inspect the preview.

Live routes are server-side `POST_REVIEW_A/B/JUDGE_PROVIDER`, `_MODEL`, `_FAMILY`
and optional `_API_KEY` variables (see `.env.example`). Initial adapters support
Groq and OpenRouter's OpenAI-compatible endpoints. Keys may fall back to the
corresponding server provider key. Three distinct explicit model IDs and declared
families are required; family labels are operator assertions, not verified model
independence. There is no automatic provider fallback or JSON repair retry.
OpenRouter automatic/free routes are excluded in this first adapter. The judge
gets anonymous findings, original evidence and shuffled reviewer order; model
identity/style bias is not guaranteed to disappear. No live provider request
was made while implementing or testing this slice.

Requests are bounded to two concurrent reviewers plus one adjudicator. Reviewer
failure/invalid evidence skips adjudication and stores a clearly partial report.
An invalid final output stays partial. Both reviewers see the same frozen
retrieved excerpt subset; source validation ensures exact Candidate quotations,
not semantic correctness or complete evidence recall. Paired questions provide
context but cannot establish a candidate's demonstrated skill. Resume assertions
cannot be cited as demonstration. The rubric and policy have content hashes.

`POST /api/reviews/prepare` normalizes locally; `/run` requires source confirmation
and explicit external-processing authorization; `/demo` uses only the fixed
synthetic sample. `/configuration` exposes model labels, never keys. Reports are
stored/reopened through owner-scoped PostgreSQL run storage after applying
`python -m app.storage migrate` (migration 003). Complete identical requests reuse an
owner/config/input-keyed result for ten minutes; partial results do not cache as
successes. Source vectors exist only in a per-invocation in-memory collection and
are closed after retrieval; private corpus vectors are not shared between users.
Reports include source text, so use this local prototype only with appropriate
material. Report deletion/retention controls, human corrections, automated
challenge rounds, production quotas and a held-out fairness/quality evaluation
remain pending. Do not deploy it as an employment decision system.

Verification: 168 Python tests ran with PostgreSQL integration enabled (one
optional cached-model smoke skipped), and 16 frontend tests passed. A local
browser run exercised the actual cached encoder/Qdrant/graph, saved a synthetic
report in PostgreSQL and reopened the same report without another model request.
It completed in 1.96 seconds on this machine; this single scripted run does not
measure live latency or model quality. No browser warnings/errors were recorded.

Official API references checked during implementation:

- https://reference.langchain.com/python/langchain_core/runnables/base/RunnableLambda
- https://console.groq.com/docs/openai
- https://openrouter.ai/docs/api-reference/overview
