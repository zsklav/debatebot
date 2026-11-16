# Google Meet import: setup and test

The notebook can fetch native Google Meet transcripts after a meeting ends.
Select one meeting; a bounded background job waits for its transcript, fetches
all supported pages and opens speaker confirmation. Confirm the candidate and
interviewers to create a private notebook. Original timestamps and resource IDs
are retained. Re-importing the same conference under the same account reopens
the existing job/notebook instead of creating duplicates.

This does not join a meeting, record audio or start transcription. The source
must already have been transcribed by Meet. A transcript can cover only part
of a meeting, and speech recognition can be wrong. Inspect it before reviewing.

## 1. Test the workflow now, without Google credentials

From the repository root, install requirements and apply migrations:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.storage migrate
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

1. Open [Interview Notebook](http://127.0.0.1:8000/notebook) and sign in.
2. Expand **Import from Google Meet**, then select **Try sample import** (no Google or model calls).
3. The job changes from waiting to ready, then opens the imported preview.
4. Assign the synthetic interviewer to Interviewer and the candidate to Candidate.
5. Select **Save notebook**. Check the Q&A timeline, **Sources** disclosures,
   note editing, revision history and **Review Council**.
6. Select the demo again. It offers the same saved notebook, not a duplicate.

The demo uses a fixed synthetic transcript and the actual queue, parser,
ownership checks and persistence. It makes **zero Google or LLM requests**.
It does not test Google authentication, account eligibility or live API access.

## 2. Check whether your Meet account can produce transcripts

On a computer, start a test meeting as host and look under **Meeting tools →
Transcribe → Start transcription**. Availability depends on the Workspace
edition and administrator settings. If the option is missing, check with the
account administrator; connecting OAuth cannot add this entitlement. Supported
editions, languages and host permissions are listed in
[Google's transcription help](https://support.google.com/meet/answer/12849897?hl=en).

Use an English synthetic conversation for the first test. Start transcription,
speak as two identified participants, and end the meeting. Allow Google time to
generate its artifact. The app distinguishes a pending artifact from an access
error; it cannot infer account eligibility from a missing transcript alone.
An authorized TXT/VTT/JSON export remains available through the file importer.

## 3. Create a Google OAuth Web client

Use your own Google Cloud project:

1. Enable **Google Meet REST API** in the API library.
2. Configure the OAuth consent screen / Google Auth Platform. For external
   testing, add the Google account you will connect as a test user.
3. Configure the readonly scope
   `https://www.googleapis.com/auth/meetings.space.readonly`.
4. Create an OAuth client with application type **Web application**. Register
   this exact **authorized redirect URI**:

   ```text
   http://127.0.0.1:8000/api/integrations/google/callback
   ```

5. Download the client JSON. Keep it outside the repository. Import its settings:

   ```powershell
   .\.venv\Scripts\python.exe -m tools.setup_google --credentials 'C:\path\client_secret_download.json'
   ```

The helper writes client settings and a generated encryption key to ignored
`.env`, without printing them. Existing keys are preserved. Back up
`GOOGLE_TOKEN_ENCRYPTION_KEY` securely; changing it makes stored Google grants
unreadable and requires reconnecting. Never commit `.env` or the client JSON.

For a public HTTPS host, register its callback instead and run the helper with
`--origin https://your-host.example`. Set `DEBATEBOT_PUBLIC_ORIGIN` to that origin
and use HTTPS cookies. Hosted environment variables must include the four
`GOOGLE_*` settings shown in `.env.example`. Keep one application worker: the
short-lived OAuth state and existing local Qdrant access are process-local.
Do not deploy an ephemeral database if you expect jobs/accounts to survive restarts.

The implemented flow follows Google's
[web server OAuth protocol](https://developers.google.com/identity/protocols/oauth2/web-server)
with state, browser/session binding, PKCE and offline refresh. The requested
scope grants read access to meeting metadata, participants and transcripts; the
app does not request Drive, Calendar, microphone or recording access.

## 4. Connect and import a real synthetic meeting

1. Restart the server after changing settings. Open the exact callback origin
   (`127.0.0.1`, not `localhost`, with the local defaults).
2. Sign into your DebateBot account, then select **Connect Google**. Review and
   approve Google's consent screen yourself.
3. Select **Find meetings**, optionally filtered by a Meet link/code. The latest
   20 accessible conference records are displayed. Select the intended meeting.
4. The background job waits for the meeting to end and for all listed transcripts
   to reach `FILE_GENERATED`. The UI polls job status; those UI reads do not
   contact Google. You can leave the page while the server continues running.
5. When ready, confirm speakers. Display names are hints; roles are never inferred
   from names. Duplicate names retain distinct speaker IDs.
6. Create the notebook, inspect the transcript, and continue to Review Council
   with the unchanged original evidence. Human notes are never substituted as
   assessment evidence.

**Disconnect Google** revokes the grant and removes the encrypted local token
record after successful revocation. It does not delete already imported
notebooks. If Google refuses revocation, the app keeps the connection for a
retry and shows an error. Account/data deletion and retention controls remain
future work; use synthetic or explicitly authorized data.

## Limits and request budgets

- Up to 30 checks or 20 minutes per job. Explicit retry starts a new waiting
  window; refresh/reopening alone does not. Jobs resume from stored state after
  restart when PostgreSQL is configured. Memory mode is volatile.
- A check has a 150-second read deadline and at most 30 Meet API reads; each list
  has at most five pages of 100 items. Oversized/incomplete results fail instead
  of being silently truncated. Import limits are 60,000 characters, 240 turns and
  12 speakers. These are prototype limits, unsuitable for many long interviews.
- `recorded Meet API reads` counts reads acknowledged by completed checks,
  including a retried unauthorized read. It excludes OAuth refresh/revocation,
  meeting-list requests and reads interrupted by a crash/cancellation. It is
  not an account billing or quota meter.
- Polling, parsing, source excerpts, note edits and exports make zero model
  requests. **Generate AI notes** is optional: it explicitly sends the original
  transcript to the configured Groq evidence model in **one request**, with
  automatic retries disabled. No human notes are sent. Exact source quotes are
  validated; they do not prove the summary's interpretation or completeness.
- Saved valid AI notes are reused for the same original source/model/policy,
  even after human note edits. A transient ten-minute cache also prevents a
  repeat model call if generation succeeded but a concurrent save conflicted.
- Review Council has its own explicit consent and separate maximum of three
  model requests per new review. It is never run automatically by the importer.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Connect is disabled | Import your Web client JSON with the helper; restart the server. |
| `redirect_uri_mismatch` | Register the exact callback, including scheme, host, port and path. |
| Access denied in Google consent | Check the test-user list, organization policies and chosen account. |
| Connection expired / failed | Reopen the notebook and reconnect; callback state is one-use and expires in ten minutes. A server restart also clears pending states. |
| Waiting / no transcript | Check that Meet transcription was actually enabled; allow artifact processing time. |
| Import access error | The connected account must have permission to read that conference's artifacts. |
| Import exceeds limits | Import a shorter authorized transcript; no partial notebook was created. |
| Worker/storage unavailable | Apply migration 005 and verify PostgreSQL; keep the API process running. |
| Google refresh expired/revoked | Reconnect. Testing-mode grants and organization policies can require renewed authorization. |

Provider payloads and token values are not returned as public errors. Uvicorn
callback access logs redact the query to avoid logging authorization codes.

## Validation scope

OAuth exchange, refresh, state rejection, scope checks and native paginated Meet
responses are tested using HTTP mocks. Real PostgreSQL tests cover leases,
restart/reopen and atomic idempotent notebook creation. AI notes are tested using
scripted responses, including failed citations, truncated output, reuse and
concurrent saves. Live Google OAuth/import and live AI-summary quality still
require the credentialed test above; synthetic success is not evidence of them.

API contracts:
[conference records](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords/list),
[transcript state](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts),
[entries and pagination](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries/list),
[participants](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.participants).
