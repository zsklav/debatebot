# First live smoke review — 2026-11-14

This is a three-case, manually inspected execution check, not a held-out benchmark
or an estimate of accuracy. Full local results are ignored by Git at
`.data/live_smoke.json`. No credentials or private reasoning were recorded.

## Environment and fixes

Groq returned `model_not_found` for the previous Llama model. The account's model
list included `openai/gpt-oss-20b`, and Groq's documentation lists JSON mode and
tool use for it. It is now the shared default. GPT-OSS uses low reasoning effort
in evidence/research requests. Planner output is capped at 1500 tokens; assessment
at 2400. Truncation is rejected or becomes a partial research run.

Initial live citations flattened newlines in wrapped local Markdown. The citation
restoration step now finds matches differing only in whitespace and substitutes
the exact original source substring. The exact validator still runs afterward.
No words, punctuation, or numerical values are fuzzily corrected. Each restoration
is recorded in `citation_repairs`. Regression checks cover both repair and rejection.

## Reviewed final run

| Case | Observed result | Elapsed time |
| --- | --- | --- |
| Fixed assessment of a two-day hybrid schedule reducing quitting in the studied Chinese technology workforce | Supported; citation points to the retention summary and preserves population scope | 0.96 s |
| Working from home lowers blood pressure | Insufficient evidence; no citations; explanation states the corpus does not cover health outcomes | 0.57 s |
| Agent investigates the scoped retention question | Complete; two tool calls (search/read), followed by citation-validated assessment | 3.25 s |

Agent node order: `decide → tools → decide → tools → decide → assess`.
The cited passage and proposed revision were inspected against the local summary.
These are adapted study summaries, not original-paper quotations. Both successful
retention responses required whitespace restoration and retained that audit record.

## Limits

Only one model, two source summaries, and three hand-selected cases were checked.
These results do not establish broad accuracy, resistance to injection, multi-query
adaptation under a live model, stable latency, or superiority over fixed retrieval.
Live contradiction/mixed-evidence cases, full argument diagnosis, browser-backed
live runs, and a held-out comparison remain to be evaluated. No claims of reduced
hallucination or production readiness follow from this smoke.

Reference: [Groq GPT-OSS 20B documentation](https://console.groq.com/docs/model/openai/gpt-oss-20b).
