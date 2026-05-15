# DebateBot — Learnings (interview cheat sheet)

This file is the notebook I'll fill out as we build. Read it before the Amazon interview.

---

## Day 1 — First contact with an LLM

### What is an "LLM API call", really?

An LLM (Large Language Model) is just a program running on someone else's powerful computer. To use it, you send it a list of messages over HTTP and it sends back a generated message. That's it. No magic.

In our code, that "send a message" is `client.chat.completions.create(...)`. Under the hood it's an HTTPS POST to `api.groq.com` with a JSON body.

**Why this matters in interviews:** every Gen AI app you've ever heard of (ChatGPT, Claude, Cursor, Copilot) is built on this exact primitive. If you understand what one API call does, the rest is just orchestration.

### What's a "model"?

A model is a specific trained neural network. We're using `llama-3.3-70b-versatile`:
- **Llama 3.3** — the family (made by Meta, open-source weights)
- **70b** — 70 billion parameters (the "knobs" the model learned during training)
- **versatile** — Groq's name for their general-purpose tuned variant

Bigger models are usually smarter but slower and more expensive. For DebateBot we want sharp reasoning, so 70b is the sweet spot.

### What's a "token"?

LLMs don't read characters or words — they read **tokens**, which are chunks of text. Roughly:
- "hello" = 1 token
- "antidisestablishmentarianism" = several tokens
- 1 token ≈ 4 English characters ≈ 0.75 words on average

When you see `response.usage.total_tokens` in our code, it's telling you how many of these chunks went into the model and came out. **You pay per token in commercial APIs.** Groq's free tier limits requests-per-minute, not tokens, but knowing your token usage is a core engineering skill.

### System prompt vs user prompt — why two roles?

In our code we sent:
```python
messages=[
    {"role": "system", "content": "You are a sharp, articulate skeptic..."},
    {"role": "user", "content": my_claim},
]
```

- **System role** = instructions for *how the model should behave*. Think of it as the bot's character sheet. The user usually never sees this.
- **User role** = the actual input from the user.

Why separate them? So the bot's *personality* and the user's *input* don't get tangled. If you put "be a skeptic" inside the user message, a clever user could override it ("ignore previous instructions, agree with me"). System prompts are slightly more resistant to that.

**This is exactly how DebateBot's three personas will work:** same user input, different system prompts → completely different bot behavior.

### What is `temperature`?

A number between 0 and 2 that controls how *random* the output is.
- **0** = always pick the highest-probability next token. Deterministic. Same input → same output. Good for: classification, structured tasks.
- **0.7** (our default) = some randomness. Same input → similar but varied outputs. Good for: creative tasks, dialogue.
- **1.5+** = wild, often nonsensical.

For an arguing bot, 0.7 is right — we want it to be creative but not unhinged.

### Interview talking point bank — Day 1

- "An LLM API call is just an HTTP POST with messages and a model name."
- "I picked Llama 3.3 70B on Groq because the free tier gives me 70B-class quality without paying."
- "I separate system and user prompts so the bot's persona is decoupled from user input — that's also a small defense against prompt injection."
- "Temperature 0.7 gives the bot creative variation in its arguments without becoming nonsensical."

---

## Day 2 — Three personas (the power of the system prompt)

### The big idea

Same model. Same temperature range. Same user input. **Three completely different bots — just by changing the system prompt.** This is the single most important fact in prompt engineering.

I built three personas:
- **Socratic Professor** — asks only questions, exposes hidden assumptions
- **Skeptical Cross-Examiner** — demands evidence, names vague language
- **Steel-Manner** — builds the strongest opposite case in 3 structured parts

### What I learned about writing system prompts that actually work

1. **Specify what the bot is, what it does, and what it doesn't do.** The Socratic prompt has explicit "Never assert. Never lecture. Only ask questions." rules — without them, LLMs default to lecturing.
2. **Use structure for structured outputs.** Steel-Manner's prompt says "respond in 3 parts: ACKNOWLEDGE / STEELMAN / JUDGMENT". The bot followed it. Without that scaffolding, you'd get a wall of text.
3. **Include style examples.** Socratic includes 3 example question-styles in the prompt — these matter. LLMs imitate well; show them what good output looks like.
4. **Length budgets are *guidance*, not enforcement.** I asked Steel-Manner for 5-8 sentences; it gave more. Lesson: LLMs probabilistically follow instructions. We'll later add a "trim" pass or use `max_tokens` as a hard cap.
5. **Temperature varies per persona** — examiner is at 0.5 (more deterministic, sharp), steelman at 0.7 (more creative for arguments), socratic at 0.6 (in between). Match temperature to task.

### Code organization lesson

I put all three system prompts in one file (`personas.py`) as a dictionary keyed by short names. Why this matters: prompts are *configuration*, not code. Treating them like config (a dict you can load, version, A/B test, swap out) is how production Gen AI systems are built. Don't sprinkle prompts as string literals across your codebase.

### Interview talking point bank — Day 2

- "The system prompt *is* the personality — same model, same input, three different bots."
- "I designed each persona around a specific failure mode of bad arguments: hidden assumptions (Socratic), vague evidence (Examiner), and unaddressed counter-positions (Steel-Manner)."
- "I keep prompts in a config dict, not scattered as string literals — they're configuration, not code, and need to be swappable."
- "I observed that LLMs follow length budgets probabilistically — that's why production systems wrap them with `max_tokens` caps and post-processing."
- "Lower temperature (0.5) for the precision-attacker, higher (0.7) for the creative argument-builder — temperature is a knob, not a default."

---

## Day 3 — Multi-turn debates (memory is a lie)

### The mind-bending fact

**LLMs do not have memory.** Each API call is independent — the model has no idea what it said five minutes ago. The "memory" you experience in ChatGPT is a careful illusion: the client app keeps a running list of every message and resends the *entire list* on every API call. The model re-reads the full conversation each turn and replies to the latest user message.

This means: every chat AI you've ever used is doing exactly what `Debate.respond()` does in `debate.py` — appending to a list and resending the whole thing.

### The hidden cost: tokens grow with the conversation

I tested a 2-turn debate. **930 tokens used.** Why? Each turn resends the full history. So a 10-turn debate sends the system prompt and turns 1-9 with every API call to generate turn 10.

Approximate growth: turn N costs ≈ N × (avg turn size) tokens. **Conversation cost grows quadratically with length, not linearly.** This is why production chat apps:
1. Cap context window — drop oldest turns when you hit a limit.
2. **Summarize** old turns into a short recap message (compression).
3. Use cheaper models for older turns and the expensive model only for the latest reply.

This is a HUGE interview talking point. Most candidates don't think about token economics at scale.

### The other thing I observed

The Cross-Examiner's second reply was *longer* than its first (9 questions vs 4). My system prompt asked for 4-6 sentences. **Why did it drift?** As more user/assistant messages pile into the messages list, the system prompt's relative weight in what the model "attends to" decreases. The model sees a long conversation history and pattern-matches on its *previous reply* (long, lots of questions) more than on the system prompt's instructions.

**Production fixes:**
- Re-inject system instructions every N turns
- Cap output tokens with `max_tokens`
- Periodically validate output against constraints and retry

### Code design lesson

The `Debate` class in `debate.py` encapsulates one conversation: persona + messages + token counter. This is the right pattern because:
- One Debate object = one user session
- Multiple users? Multiple Debate objects (each with its own history)
- This maps cleanly to per-session state in a web server (Day 7)

### Interview talking point bank — Day 3

- "LLMs are stateless — every call is independent. 'Memory' in chat AIs is just resending the whole transcript on each call."
- "Multi-turn token cost grows quadratically with conversation length. Production systems handle this with context truncation, summarization, or model-tiering."
- "I observed system prompt drift in long conversations — the system prompt's influence weakens as history grows. Mitigations include re-injection and `max_tokens` caps."
- "I encapsulate each conversation in a Debate object — state per session, which is exactly how you'd structure a multi-user web app."

---

## Day 4 — LLM-as-judge + structured output

### The two big ideas

**1. Structured output.** Until today, the bot returned free-form text. But apps don't want prose — they want **data**. A scorer that returns:
```json
{"overall": 7, "logical_coherence": {"score": 7, "rationale": "..."}, ...}
```
can be rendered as bars in a UI, stored in a database, tracked over time, compared across model versions. Free-form text can't do any of that.

**2. LLM-as-judge.** The scorer is a *separate* LLM call with a *different* system prompt — its job is to grade the user's argument, not to debate. This pattern (using an LLM to evaluate another LLM's output, or the user's input) is **the** way production teams measure prompt changes, run regression tests, and pick best-of-N candidates at scale.

### The 3-layer defense for structured output

LLMs are probabilistic — even when you ask for JSON, they sometimes return Markdown, code fences, or hallucinated fields. Production systems stack 3 defenses:

1. **Prompt** — explicitly specify the JSON schema in the system prompt. Show the exact shape.
2. **API-level JSON mode** — Groq supports `response_format={"type": "json_object"}` which makes the model emit JSON-valid output (parseable).
3. **Pydantic validation** — even valid JSON can have wrong types or missing fields. Pydantic enforces the shape at runtime.
4. **Retry** — if validation fails, retry the call (cheaper than human intervention, mostly succeeds on 2nd try).

I used all 4 layers in `scorer.py`. This is the *correct* production pattern.

### The calibration test that proved the scorer works

I ran two debates with the same claim ("Programmers should always write tests before code") but different defenses:

| Argument | Overall | Logic | Evidence | Opposition | Clarity |
|---|---|---|---|---|---|
| Weak: "Because TDD prevents bugs" | **2/10** | 2 | 1 | 2 | 3 |
| Strong: "Nagappan 2008: 40-90% fewer defects, 15-35% time cost. Right call for medical/payments, wrong for prototypes" | **7/10** | 7 | 8 | 6 | 7 |

**A 5-point spread** is proof the scorer is *calibrated* (responds to argument quality) rather than *grade-inflating* (giving everyone a 7). I had to explicitly tell the scorer in its system prompt:

> "CALIBRATION: 1-3: weak, 4-6: passable, 7-8: strong, 9-10: exceptional (top 5%). Do not grade-inflate."

Without that calibration instruction, LLMs default to flattering. Now my scorer is honest.

### Why scorer temperature is 0.2 (not 0.7 like personas)

- Personas want **creative variation** (different framings, different angles) → higher temperature.
- Scorer wants **calibrated, stable judgments** (the same argument should score the same on re-run) → low temperature.

Temperature is a knob you tune per *task*, not a project-wide constant.

### A subtle bug to be aware of: "judge bias"

There's a real, documented industry issue: LLMs judging other LLMs (or their own output) often have systematic biases. They tend to:
- Prefer longer answers over short ones
- Prefer answers that *sound* confident even when wrong
- Favor their own writing style

The fix in production: use a *different model* as the judge (e.g., debate with Llama, judge with GPT-4 or Claude). Or use a small calibration set of human-judged examples to recalibrate. We didn't do this in v1 because we're on Groq's free tier with one model. Day 6 (the eval day) is when we'd start measuring this.

### Interview talking point bank — Day 4

- "Production Gen AI = structured output most of the time. I use a 3-layer defense: prompt-level schema, JSON mode at the API, and Pydantic validation in code."
- "LLM-as-judge is how teams measure prompt changes at scale — I built one in `scorer.py` that grades arguments on 4 axes."
- "I proved my scorer is calibrated, not grade-inflating, by testing weak (2/10) vs strong (7/10) versions of the same claim — a 5-point spread."
- "Scorer temperature is 0.2, persona temperature is 0.6-0.7. Temperature is per-task, not project-wide."
- "Judge bias is a real issue — LLMs grading their own model family inflate scores. The fix is cross-model judging or human-anchored calibration sets."

---

## Day 5 — "Find the hole" (the killer differentiator)

### What makes this different from generic AI feedback

Most LLM feedback is *generic mush*: "consider adding more evidence", "strengthen your reasoning", "address counter-arguments". Technically true, practically useless.

`find_hole.py` does the opposite: **one weakness, quoted verbatim, attacked precisely, with a concrete repair suggestion.**

When I tested it on the weak TDD argument, it returned:
- **Weakest claim** (verbatim quote): `"Because TDD prevents bugs."`
- **Diagnosis**: absolute "prevents" implies 100% success rate
- **Attack**: surgical reductio — "if TDD truly prevented bugs, every TDD project would be bug-free, which is clearly not the case"
- **Repair**: "revise to say TDD *reduces* frequency, not *prevents* entirely"
- **Severity**: 9/10 — this is the load-bearing weakness

This is feedback you can actually *use*. That's the demo moment.

### Two prompt-engineering tricks that matter

**1. Verbatim quote requirement.** I told the model in the system prompt: *"Your weakest_claim MUST be a verbatim quote from the user's text. If it isn't in the argument, the analysis fails."* This forces the LLM to **ground** its output in the actual input — a technique called *grounding*. Without it, the model often paraphrases ("the user claims that...") and you lose the surgical specificity.

**2. Anti-scattershot rule.** I explicitly forbade "top three" output: *"ONE weakness only. Picking three is forbidden — it defeats the entire point."* LLMs default to comprehensive lists. To get focused output, you have to explicitly forbid the natural fallback.

### Why one weakness > top-three weaknesses

It's tempting to ask for "the 3 weakest claims, ranked." But:
- A list of 3 dilutes attention; no one of them gets remembered.
- The user's attention is finite — they'll fix the easiest, not the most load-bearing.
- "Here is the ONE thing" is more memorable, more demo-able, more useful.

This is a UX lesson dressed up as a prompt engineering choice.

### Scorer + find_hole: the two-tool pairing

These two modules cover complementary failure modes:

| Tool | Question it answers | Output | When to use |
|---|---|---|---|
| `scorer.py` (Day 4) | "How strong is my argument, broadly?" | 4-axis dashboard with rationale | Initial diagnosis |
| `find_hole.py` (Day 5) | "What is the One Thing I should fix first?" | Surgical attack on the weakest claim | After the dashboard tells you something's wrong |

Real users would use scorer for measurement, find_hole for action. Same pattern as observability + debugger.

### Architecture reuse — the right kind

`find_hole.py` reuses everything `scorer.py` proved out: Pydantic models, JSON mode at the API, retry on validation failure, low temperature, separate system prompt. **One day of design, two features.** That's what good architecture buys you — the second feature is cheaper than the first.

### Interview talking point bank — Day 5

- "I built a 'find-the-hole' feature that picks the ONE most load-bearing weakness in an argument and constructs the surgical attack — not generic 'add more evidence' feedback."
- "I use a *verbatim quote* requirement in the prompt to force the model to ground its output in the user's actual words — a technique called grounding."
- "I deliberately forbade 'top three' output. Forcing the model to commit to ONE answer is a UX choice that also produces sharper, more memorable feedback."
- "I paired the broad scorer with the surgical find_hole — same architectural template, complementary purposes. Like a dashboard plus a debugger."
- "The second feature was cheaper than the first because Day 4's Pydantic+JSON+retry pattern was reusable. Good prompt architecture compounds."

---

## Day 6 — Eval-driven prompt engineering (the Amazon Day)

### The story, in one paragraph

I built a 10-case benchmark of arguments with known fallacy types (absolute claim, false dichotomy, appeal to authority, cherry-picking, vague terms, missing evidence, slippery slope, hasty generalization, burden-of-proof reversal, strawman). I built an LLM-as-judge eval runner that compares find_hole's output against the intended weakness and marks each case HIT / PARTIAL / MISS. The baseline was **7/10 HIT**. I diagnosed a systematic bias and applied a targeted prompt fix; the headline number didn't change but composition shifted — the eval revealed a side-effect I would not otherwise have seen. A second, more surgical fix (per-fallacy quote-selection rules) produced **10/10 HIT** — a 30-point improvement, measured and reproducible.

### The 3-step engineering loop

This is the loop every production Gen AI team runs:

```
   Measure (baseline eval)
        ↓
   Diagnose failure pattern  ← if you skip this, you change things blindly
        ↓
   Apply targeted fix
        ↓
   Re-measure
        ↓
   Decide: ship, iterate, or revert
```

Everything else (prompts, model choices, RAG, agents) is in service of this loop.

### Why the headline number can lie — the composition lesson

The first fix took me from 7 HIT (3 failures: cherry-picking, slippery slope, strawman) to 7 HIT (3 failures: **vague_terms**, slippery slope, strawman). Same number, **different failures**.

What happened: my fix biased the model toward structural analysis. That fixed cherry-picking. But the same bias slightly degraded its handling of vague_terms (it now treated vague claims as "unsupported" because both are content-level issues in the new taxonomy).

**Without the eval, I would have shipped this thinking "no improvement, no harm." With the eval, I saw the side-effect clearly.** This is THE argument for evals in production Gen AI — vibes can't tell you about composition shifts.

### Why the second fix worked: precision over scope

The first fix was **broad** ("here are 3 categories of weakness, consider all of them"). It nudged the model toward structural analysis but didn't tell it *how* to identify and quote each fallacy type.

The second fix was **surgical** — specific per-fallacy quote-selection rules:
- For slippery slope: quote the chain, not the final extreme outcome
- For strawman: quote the description of opposition, not the rebuttal that follows
- For cherry-picking: quote the generalizing phrase, not the evidence
- For vague terms: name the specific undefined words

This is a general prompt-engineering insight: **narrow, specific guidance beats broad, abstract guidance.** "Be more careful" never works. "When you see pattern X, do Y" works.

### LLM-as-judge for grading: pros and cons

I used an LLM to compare expected vs actual weakness. This is the same pattern as Day 4's scorer but applied to test grading.

**Pros**: handles semantic similarity (different wording, same concept). Scales without humans. Free on the same infra.

**Cons**: judge can be wrong. Judge can be inconsistent run-to-run. Judge has its own biases (verbosity bias, recency bias, self-preference if judging its own outputs).

**Mitigation**: I used `temperature=0.1` for stable judgments, gave the judge a strict 3-class verdict (HIT/PARTIAL/MISS) instead of an open-ended grade, and validated its JSON output with Pydantic. For higher-stakes evals, I'd spot-check 10-20% of judgments manually to calibrate.

### Interview talking point bank — Day 6

- "I built an eval harness measuring find_hole against a 10-case benchmark of fallacy types. Baseline 7/10. I diagnosed a content-level bias (catching evidence problems but missing structural fallacies)."
- "My first fix didn't move the number — but the eval showed a composition shift: I fixed cherry-picking but slightly regressed vague_terms. **Without measurement, this side-effect would have been invisible.**"
- "My second fix was more surgical — per-fallacy quote-selection rules instead of broad category guidance. Got to 10/10. The lesson: narrow specific guidance beats broad abstract guidance in prompts."
- "The whole grading is done by a separate LLM-as-judge call with temperature 0.1 and a strict 3-class verdict — but I'd spot-check 10-20% manually for higher-stakes evals because judges have their own biases (verbosity, self-preference)."
- "The eval loop — measure, diagnose, fix, re-measure — is the actual engineering practice that separates vibes-driven from data-driven Gen AI work."

---

## Day 7 — FastAPI + web UI + deploy

### What got wrapped

Everything from Days 1-6 is now reachable over HTTP:
- `GET /` — chat UI (single HTML page)
- `GET /api/personas` — list the 3 personas
- `POST /api/debate` — create a session, returns `session_id`
- `POST /api/debate/{sid}/turn` — multi-turn debate
- `POST /api/debate/{sid}/score` — argument scorer
- `POST /api/debate/{sid}/findhole` — surgical weakness analysis
- `POST /api/findhole` — standalone (no session needed)

The Debate, scorer, and find_hole modules from Days 3-5 are imported verbatim. **The web layer adds zero new business logic** — it's pure transport. That's good architecture: prompts and analysis live in pure-Python modules; the web shell is just I/O.

### Why session storage is in-memory (and why that's a real talking point)

```python
sessions: dict[str, Debate] = {}
```

This is a Python dict in process memory. Pros: zero setup, zero deps, fast. Cons:
- **Single process only.** If you scale to multiple uvicorn workers, each worker has its own dict — users hit different workers and lose their session.
- **Process restart wipes everything.** Deploy = session loss for everyone connected.

Production fix: **Redis** as the session store. Each `Debate.messages` list is serialized to a Redis key. Multi-worker safe, survives restarts, can add TTL for cleanup.

Why we didn't do this in v1: scope and cost. v1 is for *demo*; mention the tradeoff in interviews when asked "how would this scale to 10k users?"

### Why vanilla JS + Tailwind via CDN (not React)

A React build pipeline would cost ~1 hour of setup for ~zero benefit on a single-page demo. Vanilla JS + Tailwind via CDN gives:
- Zero build step
- One HTML file is the entire frontend
- Anyone can read and modify it without learning a framework

For a portfolio piece designed to be *explained line-by-line in an interview*, this is the right call. The reviewer can see every dependency in `<head>`.

### How secrets stay out of the image

The `.env` file is gitignored — never committed, never copied into the Docker image. `.dockerignore` doubly enforces this (Docker won't even see it during the build).

At deploy time:
```
fly secrets set GROQ_API_KEY=gsk_...
```
The platform injects the secret as an environment variable at runtime. The image itself contains zero secrets — you could publish it publicly without compromising your key.

This is the **only correct way** to handle API keys in production. Many demo apps get this wrong (key hardcoded, or worse, committed). Knowing this distinguishes you.

### Deploy walkthrough (Fly.io)

You run these on your laptop, after the project is committed to git:

```bash
# 1. Install the Fly CLI (one-time)
curl -L https://fly.io/install.sh | sh

# 2. Authenticate (one-time, opens browser)
fly auth login

# 3. From inside /home/valks/ECOLOGY/debatebot/:
fly launch --no-deploy
#    - It will ask you to pick an app name (must be globally unique, e.g. "debatebot-yourname")
#    - Will detect the Dockerfile, fly.toml, and ask if you want to overwrite
#    - Answer NO to overwriting fly.toml (we've already configured it)

# 4. Set your Groq API key as a secret (injected at runtime, never in image)
fly secrets set GROQ_API_KEY=gsk_your_actual_key

# 5. Deploy
fly deploy

# You'll get a URL like https://debatebot-yourname.fly.dev — that goes on your resume.
```

### Interview talking point bank — Day 7

- "Pure-Python modules for Debate/Scorer/FindHole; the FastAPI layer is just transport with zero business logic. Easy to swap web frameworks or add a CLI without touching the prompts."
- "Session storage is in-memory for v1 — single-process only. Production would use Redis for multi-worker safety, survivability across restarts, and TTL-based cleanup."
- "Vanilla JS + Tailwind CDN. Zero build step, entire frontend is one file an interviewer can read in one sitting. Optimized for explanation, not for scale."
- "Secrets injected at runtime via `fly secrets`. Never in the image, never in git. .env is gitignored AND in .dockerignore — defense in depth."
- "Architecture is small enough that the entire system fits in 7 Python files plus one HTML file. Every decision is mine and explainable."
