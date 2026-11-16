# First live Review Council test

Three explicit free routes have been prepared in the local ignored `.env`, and a
local OpenRouter key is configured. Two live attempts on the synthetic caching
notebook each returned a **partial** report: Reviewer 2 (Ling) produced valid,
quote-checked findings, Reviewer 1 (Gemma, three attempts in total) received HTTP 429 from the free
endpoint, and the judge was skipped by design. Reviewer A was then switched to
Particle, because ERNIE 5.5 Flash has mandatory high-effort reasoning that would
consume the 1800-token JSON budget. Particle has not been run yet. The full three-model path has not
yet completed live. Do not describe this setup as a successful live test yet.

| Role | Provider | Explicit model | Declared family |
| --- | --- | --- | --- |
| Reviewer A | OpenRouter | `particle-ai/particle-1:free` | Particle |
| Reviewer B | OpenRouter | `inclusionai/ling-3.2-flash:free` | Ling |
| Adjudicator | OpenRouter | `nvidia/nemotron-3.5-super-vl:free` | Nemotron |

To reproduce the non-secret route configuration in another checkout:

```dotenv
POST_REVIEW_FREE_ONLY=1
POST_REVIEW_A_PROVIDER=openrouter
POST_REVIEW_A_MODEL=particle-ai/particle-1:free
POST_REVIEW_A_FAMILY=particle
POST_REVIEW_B_PROVIDER=openrouter
POST_REVIEW_B_MODEL=inclusionai/ling-3.2-flash:free
POST_REVIEW_B_FAMILY=ling
POST_REVIEW_JUDGE_PROVIDER=openrouter
POST_REVIEW_JUDGE_MODEL=nvidia/nemotron-3.5-super-vl:free
POST_REVIEW_JUDGE_FAMILY=nemotron
```

The existing Groq key remains available for the other application features.
This council uses one server-side OpenRouter key for all three roles.
Separate keys are not required per model, and
separate keys alone do not create independent model families. These families
are configuration assertions, not proof of unbiased or correct assessment.

## Add the missing credential

1. Create an API key in [OpenRouter settings](https://openrouter.ai/settings/keys).
   No credits purchase is needed for basic free-model access. OpenRouter currently
   documents 50 free-model API requests per day without the purchase threshold;
   a complete new council can use three of them. Capacity and model-specific
   limits can still reject a request. See [free variants](https://openrouter.ai/docs/guides/routing/model-variants/free)
   and [rate limits](https://openrouter.ai/docs/faq#how-are-rate-limits-calculated).
2. Add the real key only to local `.env`:

   ```dotenv
   OPENROUTER_API_KEY=your_real_key
   ```

   Do not paste keys into a chat or commit `.env`. Provider setup follows
   [OpenRouter's API documentation](https://openrouter.ai/docs/quickstart).
3. Restart the running local server after saving the key:

   ```powershell
   # In the terminal running Uvicorn, press Ctrl+C, then:
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   Do not start a second server on the same port. Route configuration does not
   confirm the key, credit balance or the provider's live response quality.

## Free-only controls

Local `.env` has `POST_REVIEW_FREE_ONLY=1`. Configuration and dispatch both reject
paid IDs, automatic routers and other providers whose price cannot be constrained
by this adapter. Each `:free` request sets prompt, completion, per-request and
image price ceilings to zero. There is no fallback model array, provider fallback
or automatic retry. A free endpoint error remains an error/partial report; it
never starts a paid alternative.

The three chosen models advertise JSON mode and optional reasoning. Reasoning
is disabled for these exact IDs to reserve the existing 1800-token output budget
for final JSON. This setting is not a model-quality claim. Free endpoints are
subject to availability changes; inspect partial results before explicitly
retrying, and do not remove the free-only guard to work around a limit.

These controls apply to Review Council. They do not select or constrain models
used by the separate chat, research or optional notebook AI-notes features.

## Run one small synthetic example

Use the existing saved synthetic notebook, or create one from the included
sample transcript. Open it and select **Review Council**; the original transcript
is loaded automatically. Google Meet is not needed for this test.

For **Job description**, paste:

```text
Backend developer who implements Redis caching for product lookups and explains
TTL, invalidation and consistency trade-offs using implementation evidence.
```

Keep only the default **Caching** competency and its rubric. Then:

1. Select **Preview sources**. This makes zero model requests.
2. Inspect the Candidate answers and supplied timestamps. Expand **Model
   providers** to check the configured destinations.
3. Check the external-processing consent box for this synthetic material.
4. Select **Run review** once. A new live run makes at most three model requests;
   invalid reviewer output skips the judge. There is no automatic repair retry.
5. Open **Run details** for actual request count, provider/model labels and usage.
   A partial report means a request or validation step failed; it is not a
   completed assessment. Inspect the failure before explicitly trying again.

**Try a sample** on the Review Council page runs scripted outputs. Use the normal
Preview/Run flow above when testing real model integration.

## What to check

The sample explicitly describes a 60-second Redis TTL and admits that invalidation
beyond TTL and concurrent-update tests are not implemented. A sensible report
should preserve those limitations and cite exact Candidate words. The unanswered
isolation-level question must not become proof of lack of skill.

Check exact quotes against Sources, unsupported claims, treatment of unanswered
questions, handling of model disagreement and partial failures. One successful
example verifies integration only; it does not measure accuracy or fairness.
After this smoke test, use a held-out set with human-reviewed expected findings
and compare against a single-model baseline with matched input and budgets.

Successful identical reviews are reused within the same account/configuration
for ten minutes. Notes edits alone do not change original-source evidence. New
input/configuration, expired cache or partial results may cause new requests.

## Verified before adding the key

On 2026-11-15, OpenRouter's public catalog listed the three exact free IDs with
zero prompt/completion prices, `response_format` support and non-mandatory
reasoning. Mocked request tests verify zero-price filters, paid-route rejection,
one-request budgets and no retry on a free rate-limit error. Catalog availability
and mocked responses are not a credentialed completion test. Check
[OpenRouter's model catalog](https://openrouter.ai/models) if a route stops working.

## Development evaluation against single-reviewer baselines

`eval/review_cases.json` holds six synthetic cases with expected statuses and quote
fragments. The labels are **drafts** until you check each one and set
`label_status` to `human_reviewed`.

```powershell
# Validate cases and show the worst-case request count. No model request.
.\.venv\Scripts\python.exe -m tools.review_eval

# Run at most three cases (nine requests). Saves after every case and skips finished ones.
.\.venv\Scripts\python.exe -m tools.review_eval --live --max-requests 9
```

Each reviewer sees the same frozen evidence and prompt as in the council, so a
reviewer's own output is the single-model baseline and costs no extra request. The
council costs up to three requests per case against one for a baseline. The tool
stops on the first 401/402/403/429 to protect the daily limit and reports partial
runs as "no result" instead of wrong answers. Results go to
`.data/review_eval.json` (ignored by Git).

Six cases are a development set: use the counts to find bugs, not to claim accuracy.
Judge-order bias, repeated-run variance and a larger held-out set remain future work.

## What the first nine-case development run showed

Two of nine cases returned partial reports, and both repeated on a second attempt.
In one, the judge labelled a finding `agreed` although the two reviewers had
different statuses. In the other, one reviewer's quote failed exact validation. The
guards stopped both outputs from being presented as assessments.

Two changes followed. `agreed` is now derived in code (all reviewer statuses and
the judge's status identical), because it is a fact about statuses rather than a
judgment; the judge chooses only `proposed_resolution` or `unresolved`. Any label
changed by code is listed in the run audit under `resolution_adjusted`. Quote
failures now carry a distinct fixed reason (unknown source ID, cited the resume,
cited a non-Candidate turn, quote not verbatim) in `failure_kinds`.

The nine labels are drafts and the sample is tiny. These changes have been tested
with scripted outputs only; they have not been re-run against live models.
