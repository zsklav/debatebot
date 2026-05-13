---
title: DebateBot
sdk: docker
app_port: 8080
pinned: false
license: mit
short_description: An AI that disagrees with you, three different ways
---

# DebateBot

An AI that disagrees with you — three different ways. Built to pressure-test
arguments before you ship them in a PR, an essay, or a behavioral-interview
answer.

Unlike sycophantic LLMs that agree with whatever you say, DebateBot picks
the strongest counter-argument, asks the question that exposes the hidden
assumption, or builds the steel-manned opposite case — and then surgically
identifies the single weakest claim in your argument.

---

## What's actually interesting under the hood

Most Gen AI side projects are a chat UI over an API. The interesting parts
here are the production-engineering patterns:

- **LLM-as-judge** scorer that grades arguments on 4 axes (logic, evidence,
  opposition handling, clarity) with calibrated 1-10 ratings. Verified
  calibration: same claim defended weakly scores 2/10, defended rigorously
  scores 7/10 — a 5-point spread.
- **Structured output** via a 3-layer defense (prompt schema + Groq JSON
  mode + Pydantic validation + retry-on-failure). No string-mashing
  hopes-and-prayers parsing.
- **Find-the-hole** surgical analyzer that picks the single most
  load-bearing weakness in your argument, quotes it verbatim, and
  constructs the precise attack. Forced grounding via "verbatim quote
  required" in the prompt — no generic "consider adding more evidence"
  feedback.
- **Eval harness** with 10 hand-designed test cases across 10 fallacy
  categories. The interesting story is documented below.

## The eval story (the part I'm proudest of)

I built a benchmark of 10 arguments, each with a known intended weakness
(false dichotomy, appeal to authority, slippery slope, strawman, etc.).
An LLM-as-judge eval runner compares find_hole's output against the
intended weakness and marks each case HIT / PARTIAL / MISS.

| Iteration | HIT | PARTIAL | MISS | Note |
|---|---|---|---|---|
| Baseline | 7 | 2 | 1 | Content-level bias — caught evidence gaps, missed structural fallacies |
| Fix #1 (broad categories) | 7 | 2 | 1 | **Same headline number, composition shifted**: cherry-picking improved, vague_terms regressed |
| Fix #2 (per-fallacy quote rules) | **10** | 0 | 0 | Clean win |

The middle row is the lesson. Without the eval harness, the
composition-shifted side-effect of Fix #1 would have been invisible.
Vibes can't tell you that. Measurement can.

See [`LEARNINGS.md`](LEARNINGS.md) Day 6 for the full diagnosis and the
exact prompt changes.

## Architecture

```
                       ┌───────────────────┐
                       │  web/static/...   │ vanilla JS + Tailwind via CDN
                       │  index.html       │ (no build step)
                       └────────┬──────────┘
                                │ fetch()
                       ┌────────▼──────────┐
                       │  web/api.py       │ FastAPI (transport only,
                       │  + in-mem sessions│  zero business logic)
                       └────────┬──────────┘
                                │
        ┌───────────────┬───────┼───────┬───────────────────┐
        ▼               ▼               ▼                   ▼
   ┌──────────┐  ┌───────────┐  ┌──────────┐         ┌──────────────┐
   │personas. │  │ debate.py │  │ scorer.  │         │ find_hole.py │
   │py        │  │           │  │ py       │         │              │
   │3 system  │  │ multi-    │  │ 4-axis   │         │ surgical     │
   │prompts   │  │ turn      │  │ LLM-as-  │         │ weakness     │
   │as config │  │ state     │  │ judge    │         │ analyzer     │
   └─────┬────┘  └─────┬─────┘  └─────┬────┘         └──────┬───────┘
         │             │              │                     │
         └─────────────┴──────────────┴─────────────────────┘
                                │
                       ┌────────▼──────────┐
                       │   Groq API        │  Llama 3.3 70B
                       │   (free tier)     │
                       └───────────────────┘

Separately: benchmark.py + eval_findhole.py — measurement infrastructure.
```

## Quick start

Requires Python 3.10+. Get a free Groq API key at
[console.groq.com](https://console.groq.com).

```bash
git clone https://github.com/zsklav/debatebot.git
cd debatebot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env       # then put your key in .env: GROQ_API_KEY=gsk_...
```

### Run the CLI

```bash
.venv/bin/python chat.py
```

Pick a persona, make a claim, defend it across a few turns. Type `/score`
to get a 4-axis grade or `/findhole` to surface the surgical attack.

### Run the web app

```bash
.venv/bin/uvicorn web.api:app --reload
```

Open http://127.0.0.1:8000 in your browser.

### Run the eval harness

```bash
.venv/bin/python eval_findhole.py
```

Takes ~30 seconds. Output: per-case verdicts + category breakdown +
diagnostic detail on any miss/partial.

## File map

| File | Purpose |
|---|---|
| `hello_groq.py` | Minimal one-shot LLM call — the foundation example. |
| `personas.py` | The three personas as a configuration dict. |
| `debate.py` | `Debate` class encapsulating per-session conversation state. |
| `scorer.py` | LLM-as-judge argument scorer. |
| `find_hole.py` | Surgical weakness analyzer (the differentiator). |
| `benchmark.py` | 10-case eval benchmark with intended-weakness annotations. |
| `eval_findhole.py` | Eval runner with LLM-as-judge grading. |
| `chat.py` | Interactive CLI (single-persona, multi-persona compare, standalone find-the-hole). |
| `web/api.py` | FastAPI HTTP layer. |
| `web/static/index.html` | Single-page chat UI. |
| `Dockerfile` + `fly.toml` | Deploy artifacts for Fly.io. |
| `LEARNINGS.md` | Concept-by-concept walkthrough with interview talking points. |

## Tech stack

- **Python 3.12**, **FastAPI**, **Pydantic v2**
- **Llama 3.3 70B** via **Groq** free tier
- Vanilla JS + Tailwind via CDN on the frontend (no build step)
- Docker + Fly.io for deploy

## What's deliberately out of scope (v1)

- **Auth / users**. Single-user demo; production would add JWT auth and
  per-user rate limiting.
- **Persistent sessions**. In-memory dict keyed by UUID; production would
  back this with Redis (multi-worker safety, survivability across
  restarts).
- **CI / tests beyond the eval harness**. The eval *is* the test suite for
  the only thing that matters here — prompt quality.

These were chosen deliberately so the project stays small enough to
explain line-by-line in an interview.

## Reading guide

If you have 5 minutes: scan this README and skim [`LEARNINGS.md`](LEARNINGS.md).

If you have 30 minutes: read `find_hole.py` + `scorer.py` (the heart of
the project) and the Day 4-6 sections of `LEARNINGS.md`.

If you have an hour: run `eval_findhole.py`, then read `benchmark.py` and
Day 6 of `LEARNINGS.md` to see the measure-diagnose-fix-remeasure loop in
action.
