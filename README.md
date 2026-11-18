---
title: DebateBot
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: Evidence-backed debates and post-interview review
---

# DebateBot · Review Council

**After an interview, get an evidence report: for each skill, the candidate's exact
words, checked by code.** Built with **FastAPI, LangGraph, LangChain, Qdrant and
PostgreSQL**.

| Step | What happens | Model calls |
|---|---|---|
| 1. Import | Paste a transcript, or import VTT / Meet JSON in the Notebook | 0 |
| 2. Pick a role | Choose a preset (backend, frontend, ML) or write up to 3 skills | 0 |
| 3. Preview | See the exact transcript pieces that will be sent | 0 |
| 4. Review | Local retrieval picks evidence; two reviewers assess it; a judge reconciles | up to 3 |
| 5. Report | Each skill: status, exact quotes, agreement. A person makes every decision | 0 |

Every quote must appear word for word in a candidate answer, or the report rejects
it. The tool never recommends hiring or ranks people. The debate workspace is a
separate practice tool in the same app.

## Live demo

[valkszz-debatebot.hf.space](https://valkszz-debatebot.hf.space) runs on a free
Hugging Face Space (CPU). Create an account, then open **Notebook** and **Review
Council** and use **Try a sample**.

Limits of the public demo:

- **Scripted sample by default.** The Review Council sample uses scripted outputs and
  is labelled synthetic. The retrieval, graph and validation steps are real. Live
  reviews run only when the Space owner adds a free-model key, with a daily cap
  ([how to turn them on](docs/HF_SPACE_LIVE_REVIEW.md)). The `/review` page shows
  whether live reviews are on and how many are left today.
- **Debate, research and AI notes are off** (they need a Groq key).
- **Storage is volatile.** Without a database, accounts and saved reports disappear
  when the Space restarts or sleeps.
- **Do not enter real interview data.** This is a demonstration.
- **Google Meet import is not available.** Only the synthetic import demo works.
- Open it at the direct link, not inside the Hugging Face page.

## What works

- **Interview Notebook** at `/notebook`: import speaker-labelled text, WebVTT or
  exported Meet JSON; map speakers, browse timestamped questions/answers, edit
  source excerpt notes, save revision history and export Markdown. This flow
  makes zero model requests by default. Optional AI notes use one explicitly
  authorized Groq request with exact-source validation and saved-result reuse.
  [Notebook formats and limits](docs/NOTEBOOK.md).
- **Google Meet automation**: OAuth with readonly access, encrypted grants,
  bounded transcript polling, pagination, explicit speaker confirmation and
  duplicate-safe notebook creation. A labelled synthetic automation test needs
  no Google credentials. [Connect and test](docs/GOOGLE_MEET_SETUP.md).
- **Post-interview Review Council** at `/review`: speaker-labelled transcript,
  job description and up to three competency criteria; two independent reviewers
  assess the same retrieved evidence, then an anonymous adjudicator reconciles
  their findings. Exact Candidate quotations are validated before a report can
  be completed. Resume claims remain separate from demonstrated skills.
- **Synthetic sample**: exercises the real cached encoder, isolated Qdrant
  retrieval, LangGraph workflow and report storage with clearly labelled scripted
  outputs. It makes zero external model requests.
- **Debate workspace** at `/`: three personas, multi-turn chat, argument scoring
  and a single-weakness critique. Successful identical analyses are reused.
- **Evidence Lab**: local cited evidence assessment and bounded agent research;
  optional web research has separate external-search disclosure.
- **Private saved records**: cookie authentication and owner-scoped storage.
  PostgreSQL persists records; explicitly disclosed memory mode is volatile.
- **Claim CI**: separate read-only CLI for checking declared repository claims
  against recorded artifacts. Its collector is an opt-in local development tool.

The council is a post-interview evidence assistant. It does not conduct interviews,
rank applicants or make hiring decisions. Multiple model families and exact quotes
are engineering controls, not proof of correct or fair assessment.

## Current validation

The council has run live on three free OpenRouter models against synthetic
interviews. A development evaluation (`python -m tools.review_eval`) scores the
council and each reviewer on nine synthetic cases labelled by the project author,
not by an independent reviewer. Strict scores were close and the sample is tiny, so
it shows no accuracy and no advantage of the council over one model. Failures
occurred and are reported: free-model rate limits (HTTP 429) and rejected outputs
produce partial reports, never invented conclusions. See
[the live test notes](docs/LIVE_REVIEW_TEST.md).

Review family labels are configuration assertions, not verified independence.
Human corrections, report deletion/retention controls and a held-out,
independently labelled evaluation remain pending. Real Google Meet import has not
been tested with a Google account.

## Run locally

Python 3.12 or 3.13 and Node 22 for frontend tests. Run commands from the repository
root. Do not open the HTML files directly.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally. Never commit credentials.

- `GROQ_API_KEY`: needed for debate/research generation; source preview and the
  scripted council do not need a generation key.
- `DATABASE_URL`: PostgreSQL connection string. Without it, accounts and reports
  use volatile process memory. Apply migrations when configuring PostgreSQL:

```powershell
.\.venv\Scripts\python.exe -m app.storage migrate
```

For the Windows development PostgreSQL helper (expects PostgreSQL 17 installed):

```powershell
.\.venv\Scripts\python.exe -m tools.dev_postgres start
```

The first retrieval setup downloads the local BGE embedding model and indexes the
included two-study editorial corpus. It makes no LLM request:

```powershell
.\.venv\Scripts\python.exe -m app.evidence.retrieval index
```

Then start the server:

```powershell
.\start.ps1
# Or:
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [Interview Notebook](http://127.0.0.1:8000/notebook),
[Review Council](http://127.0.0.1:8000/review) or
[DebateBot](http://127.0.0.1:8000/). Create a local account and try the synthetic
sample. The server uses cached embedding files and never downloads them at
startup. Stop the API before reindexing the on-disk evidence store; use one
worker with local Qdrant.

The notebook works without embedding setup. Expand **Import from Google Meet**
and use **Try sample import** to exercise the queue without Google or model calls. For actual Meet imports,
[configure your OAuth Web client](docs/GOOGLE_MEET_SETUP.md); native transcription
must already be enabled in Meet. Live Google access has not yet been tested with
user credentials. Use **Open in Review Council** to assess the original source
transcript; edited notes are never substituted as interview evidence.

## Enable live council reviews

Configure `POST_REVIEW_A`, `POST_REVIEW_B` and `POST_REVIEW_JUDGE` variables in
`.env.example`: explicit provider, model ID, declared family and server-side key.
Initial providers are Groq and OpenRouter. All three routes must declare distinct
models and families; separate API keys alone do not establish diversity. Restart
the server after changing settings.

A new live run uses **at most three model requests**, with no automatic retry or
provider fallback. Complete identical requests are reused for ten minutes within
one account and configuration. Partial reports remain partial and are not cached
as successful assessments. Source preview requires no model call; live processing
requires source confirmation and authorization for the configured external
services. Use synthetic/redacted or explicitly authorized interview material.
There is no automatic PII redaction in this prototype.

See [review setup, scope and limits](docs/INTERVIEW_PLATFORM_PLAN.md).
For the prepared free model combination and zero-price guard, follow the
[first live test](docs/LIVE_REVIEW_TEST.md). It needs an OpenRouter API key and
is subject to free-tier capacity/rate limits; no credits purchase is required
for basic free-model access. The live provider test is still pending.

## Repository layout

```text
app/                 Python application
  main.py            ASGI entry point
  api/               Application factory and HTTP routers
  core/              Shared cache, paths and model configuration
  debate/            Persona conversations, scoring and critique
  evidence/          Ingestion, retrieval, assessment and research agent
  review/            Post-interview contracts, council, adapters and routes
  notebook/          Timestamped imports, editable notes, history and export
  integrations/      Google OAuth, native Meet reads and durable import jobs
  auth.py            Accounts and cookie sessions
  storage.py         Memory/PostgreSQL repositories and migration CLI
frontend/            Local HTML, CSS and JavaScript; no CDN or build step
tools/               CLI, evaluation, Claim CI and development commands
migrations/          Ordered PostgreSQL migrations
corpus/              Cited editorial study summaries for Evidence Lab
tests/               Python and frontend regression contracts
docs/                Architecture, implementation plans and learning notes
requirements/        Core, retrieval, storage and web-search dependency groups
```

[Architecture and data flow](docs/ARCHITECTURE.md) ·
[AI learning guide](docs/AI_LEARNING_GUIDE.md) ·
[Roadmap](docs/ROADMAP.md) · [Interface design](docs/DESIGN.md)

## Checks

```powershell
$env:RUN_POSTGRES_TESTS='1'
$env:RUN_LOCAL_EMBEDDING_SMOKE='0'
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_ui.cjs
.\.venv\Scripts\python.exe -m pip check
```

PostgreSQL integration needs `DEBATEBOT_TEST_DATABASE_URL`, or the development
`DATABASE_URL` in `.env`. The optional cached-embedding smoke is separate from
mocked model tests. Ordinary tests do not make live LLM requests. GitHub Actions
runs Python 3.12/3.13 with PostgreSQL and the frontend contracts.

## Development tools

Run module entry points from the repository root:

| Command (`python -m …`) | Purpose |
| --- | --- |
| `tools.chat` | Interactive debate/critique CLI; uses API quota |
| `tools.eval_findhole` | Original ten-case development evaluation; uses API quota |
| `tools.live_smoke` | Opt-in live evidence/research smoke; uses API quota |
| `app.evidence.retrieval search --query "hybrid retention" --offline` | Search the cached local index |
| `app.evidence.ingestion examples/chunking_demo.md --source-id demo-fictional` | Inspect deterministic text chunks |
| `tools.claimci` | Read-only artifact-based claim report |
| `tools.claimci_collect` | Collect scoped local observations; offline by default |

The original ten fallacy examples and two-study retrieval cases are development
sets, not held-out accuracy estimates. See [Claim CI documentation](docs/CLAIM_CI.md)
and [recorded development evaluations](eval_results/README.md).

## Container

`Dockerfile` installs the complete dependency set, runs as uid 1000 (as Hugging Face
Spaces does), downloads the local embedding model and indexes the study-summary
corpus at build time, then starts one `app.main:app` worker on port 8080. `.env`,
`.data/` and observation artifacts are excluded from the image. The image builds and runs on a Hugging Face Docker Space (see the live demo
above).

### Public demo configuration

No model key is set on a public Space, so nobody else can spend its quota or send
transcripts to a model provider.

| Works without keys | Needs a key or setup |
| --- | --- |
| Accounts, notebooks, transcript import, editing and export | Debate, research and AI notes (`GROQ_API_KEY`) |
| Review Council **Try a sample** (scripted outputs, labelled synthetic) | Live Review Council (`OPENROUTER_API_KEY` and `POST_REVIEW_*`) |
| Synthetic Meet import demo | Real Google Meet import (OAuth client, not tested) |

Without `DATABASE_URL`, accounts and reports are volatile and disappear when the
Space restarts. Open the app at its direct `*.hf.space` address, not inside the
Hugging Face page: the app sends `X-Frame-Options: DENY` and its session cookie is
`SameSite=Strict`, so an embedded frame will not work. Set the Space variable
`DEBATEBOT_PUBLIC_ORIGIN` to that exact `https://` address.
