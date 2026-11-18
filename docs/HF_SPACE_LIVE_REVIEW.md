# Turn on live reviews in the Hugging Face Space

By default the public Space has no model key, so only the scripted sample runs.
These steps let the Review Council call free OpenRouter models from the Space.

## Before you start

- Anyone who signs up on the Space can start a live review. Each one uses up to
  three model calls from your OpenRouter free quota.
- Transcripts are sent to OpenRouter. Keep the demo on synthetic or redacted text.
- Never paste the key into chat, code or a commit. It goes only into Space secrets.

## Steps

1. Create an OpenRouter API key in your OpenRouter account.
2. Open the Space, then **Settings > Variables and secrets**.
3. Add a **secret**: `OPENROUTER_API_KEY` = your key.
4. Add these **variables**. Use the three free models and families from
   [the live test notes](LIVE_REVIEW_TEST.md):

| Name | Value |
|---|---|
| `POST_REVIEW_FREE_ONLY` | `1` |
| `POST_REVIEW_DAILY_LIMIT` | `10` |
| `POST_REVIEW_A_PROVIDER` | `openrouter` |
| `POST_REVIEW_A_MODEL` | reviewer A model ID ending in `:free` |
| `POST_REVIEW_A_FAMILY` | reviewer A family |
| `POST_REVIEW_B_PROVIDER` | `openrouter` |
| `POST_REVIEW_B_MODEL` | reviewer B model ID ending in `:free` |
| `POST_REVIEW_B_FAMILY` | reviewer B family |
| `POST_REVIEW_JUDGE_PROVIDER` | `openrouter` |
| `POST_REVIEW_JUDGE_MODEL` | judge model ID ending in `:free` |
| `POST_REVIEW_JUDGE_FAMILY` | judge family |

5. Restart the Space.
6. Open `/review`, sign in, and check the line under the title. It should say
   `Live reviews left today: 10 of 10.`

## What protects the quota

- `POST_REVIEW_FREE_ONLY=1` blocks any model that is not an OpenRouter `:free` model,
  and every request carries a zero price cap.
- `POST_REVIEW_DAILY_LIMIT` stops new live reviews after the cap for the day.
  Repeating the same review within ten minutes reuses the saved result and is not
  counted. The count lives in memory, so a Space restart resets it.
- No automatic retry or fallback model. A rate limit gives a partial report.

To turn live reviews off again, delete the `OPENROUTER_API_KEY` secret and restart.
