---
title: DebateBot
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: Evidence-backed debates and post-interview review
---

# DebateBot · Review Council

An AI application for challenging arguments and reviewing evidence from completed
interviews. Built with **FastAPI, LangGraph, LangChain, Qdrant and PostgreSQL**.

## What works

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

The synthetic council and local evidence pipeline have been exercised. Live
Groq/OpenRouter council adapters are implemented but **live council output quality
has not been evaluated**. The demo is not a model benchmark. Review family labels
are configuration assertions, not verified independence. Human corrections,
report deletion/retention controls and held-out quality/fairness evaluation remain
pending. Public deployment has not been verified.

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

Open [Review Council](http://127.0.0.1:8000/review) or
[DebateBot](http://127.0.0.1:8000/). Create a local account and try the synthetic
sample. The server uses cached embedding files and never downloads them at
startup. Stop the API before reindexing the on-disk evidence store; use one
worker with local Qdrant.

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

## Repository layout

```text
app/                 Python application
  main.py            ASGI entry point
  api/               Application factory and HTTP routers
  core/              Shared cache, paths and model configuration
  debate/            Persona conversations, scoring and critique
  evidence/          Ingestion, retrieval, assessment and research agent
  review/            Post-interview contracts, council, adapters and routes
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

`Dockerfile` starts `app.main:app` on port 8080 and installs the complete dependency
set. Configure runtime secrets, PostgreSQL and a prepared embedding cache/index
through the host. `.env`, `.data/` and observation artifacts are excluded from the
image. Container image construction and a public Hugging Face Spaces deployment
still need verification; the repository does not claim a working public URL.
