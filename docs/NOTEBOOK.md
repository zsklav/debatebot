# Interview Notebook

Open `/notebook` after starting the existing FastAPI server and applying
`python -m app.storage migrate` (migrations 004–005). The notebook uses the existing
account. PostgreSQL saves notebooks and revision history; memory mode loses
them on process restart. Importing, excerpt drafts and editing make **zero model
requests** and do not need generation credentials or cached embedding weights.
Google Meet can also be connected for automated native transcript import; see
[setup and testing](GOOGLE_MEET_SETUP.md), including a zero-call synthetic demo.

## Workflow

1. Paste text or import a `.txt`, `.md`, `.vtt` or `.json` transcript. The
   synthetic sample demonstrates all controls without using real interview data.
2. Preview the imported turns. Assign exactly one speaker as Candidate, assign
   the interviewers, and explicitly exclude other speakers. Names do not imply
   roles. Missing labels or ambiguous multi-speaker caption cues are rejected.
3. Save the notebook. The timeline groups interviewer prompts with the following
   candidate answers. An answer without a preceding prompt remains visible;
   a prompt without a captured answer is labelled accordingly.
4. Edit the source excerpt drafts into your own notes. Save explicitly. Source
   text, source links and timestamps remain unchanged. Conflicting saves from
   an older revision return 409 instead of overwriting another edit.
5. Reopen recent notebooks, inspect the latest 20 saved revisions, or download
   the current notes and quoted sources as Markdown. All endpoints require the
   current owner; other owners receive 404.
6. Open the notebook in Review Council. The original normalized transcript is
   loaded; note edits are excluded. Add a JD and rubric, preview the sources and
   authorize external processing before a live assessment. Editing the transcript
   in that form detaches the notebook link. The API independently verifies that
   linked reviews use an owned notebook and its unchanged transcript.

Drafts are **exact answer excerpts**, capped at 600 characters per question
group, not AI-generated summaries. Human-edited notes are labelled separately.
They can contain a reviewer's interpretation; their source links do not prove
that interpretation. Optional **Generate AI notes** sends only original sources
to the configured Groq evidence model in one explicitly authorized request.
AI points and exact supporting quotes appear separately from human notes.
Invalid or incomplete responses are not saved or retried automatically.
Valid notes are reused for unchanged original source/model/policy; editing
human notes does not trigger generation.
The council continues to use its configured, separately budgeted model routes.
Editing notes alone does not invalidate reuse of the same original-source review.

## Import formats

Text supports optional elapsed or timezone-qualified ISO timestamps:

```text
[00:12] Interviewer: Describe your caching implementation.
[00:19 --> 00:31] Candidate: I used a 60-second TTL.

Interviewer: What trade-offs did you consider?
Candidate: I considered two cases.
\tTrade-offs: stale reads versus fresh fetches.
```

The `\t` in this example represents a literal tab. Tab-indent continuation lines
containing colons so they are not mistaken for a new speaker label. Timestamps
may be omitted; the app displays “Time unavailable” and never fabricates them.
Timestamped turns must be chronological and use one clock system. Start/end
ranges cannot be reversed. Times come from the supplied text, not verified audio.

WebVTT uses voice tags or a `Speaker:` prefix in each cue:

```vtt
WEBVTT

00:00:12.000 --> 00:00:18.000
<v Interviewer>Describe your caching implementation.</v>

00:00:19.000 --> 00:00:31.000
<v Candidate>I added Redis caching with a 60-second TTL.</v>
```

Exported Meet JSON accepts a complete `transcriptEntries` array or a bare array.
The optional `participants` object below is this app's simple ID-to-name mapping;
it is not a native Google participant-list response:

```json
{
  "participants": {
    "conferenceRecords/example/participants/p1": "Asha",
    "conferenceRecords/example/participants/p2": "Ravi"
  },
  "transcriptEntries": [
    {
      "participant": "conferenceRecords/example/participants/p1",
      "text": "Describe the cache.",
      "startTime": "2026-11-15T10:00:00Z",
      "endTime": "2026-11-15T10:00:05Z"
    },
    {
      "participant": "conferenceRecords/example/participants/p2",
      "text": "I used a 60-second TTL.",
      "startTime": "2026-11-15T10:00:06Z",
      "endTime": "2026-11-15T10:00:15Z"
    }
  ]
}
```

Without the name mapping, resource IDs remain the speaker labels and still need
role assignment. Absolute timestamps stay absolute; the first utterance is not
silently treated as meeting start. An export containing a nonempty `nextPageToken`
is rejected: collect all pages first, then import a complete interview.

Meet's transcript-entry fields are documented in the
[official API reference](https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries).
File import does not contact Google. The separate connected importer reads native
participants and all supported transcript-entry pages before using this parser.

## Implementation and limits

`app/core/transcripts.py` supplies the shared parser used by notebooks and
Review Council. `app/notebook/` owns draft construction, HTTP contracts and
Markdown export. The existing repositories store current documents and atomic
revision events. Review citations retain their source IDs and supplied timestamps.

Imports are bounded to 60,000 characters, 240 turns and 12 distinct speakers.
Files must be UTF-8. Input is expected to contain readable English; no ASR or
translation is performed. Speaker assignments and transcript accuracy require
human inspection. Source snapshots are immutable through the editing API;
correct source text or attribution by importing a new notebook. Existing notes
can be edited up to 1,200 characters each.

Google OAuth and automatic native artifact fetching are implemented separately
in `app/integrations/`, with encrypted refresh grants and durable bounded jobs.
Live Google access still needs an OAuth client and credentialed testing.
Meeting-joining bots, recording, audio transcription, DOCX/PDF parsing, semantic
rubric coverage maps and finding-level assessment overrides remain future work. A missing
answer in the imported timeline does not establish lack of ability. Report and
notebook deletion/retention controls remain future work. Use synthetic, redacted
or explicitly authorized material; imported content is stored in the private
notebook, and automatic PII redaction is not provided.

Verified with parser/ownership/lost-update/review-link regressions, real PostgreSQL
reopening, and browser creation/edit/history/source navigation. The browser
handoff preserved the timestamped original answer and excluded the edited note.
No external model request or real Google meeting was used for verification.
AI notes and Google HTTP behavior are verified with mocks, not live quality results.
