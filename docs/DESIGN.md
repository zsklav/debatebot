# DebateBot interface

## Visual identity

Keep the orange and charcoal palette, with DebateBot's own wordmark and a local
speech-bubble SVG. The same mark is used in the header, favicon and assistant
message labels. There are no star marks, copied brand assets, external fonts or
image-generation dependencies.

- Page: #171614; surfaces: #211f1c; text: #f4eee5.
- Muted text: #aaa398; borders: #38332d.
- Accent: #e7a37c; primary action: #dfa177 with a dark label.
- System sans-serif throughout, including headings and assistant replies.
- Eight-pixel corners for most fields, buttons and cards; restrained borders.

## Layout

The app shell fills the viewport with fluid 20–56px gutters. The previous 960px
shell and 840px centered workspace limits are removed. Individual paragraphs
retain a readable line length rather than stretching text across every pixel.

A shared header provides Debate, Notebook and Review Council navigation, an
active-page indicator, and compact account controls. There is no duplicate
navigation inside each workspace.

- **Notebook:** a 290px import/history sidebar and a flexible editing column.
  Saved notebooks and Google Meet controls stay beside the active task. Optional
  AI notes begin as a disclosure; source-linked notes occupy the main column.
  Meet connection/activity is collapsed until requested or an active import needs
  attention. Repeated draft explanations, timestamps and provenance details are
  inside Sources/Notebook details. The visible header keeps Saved/Unsaved and a
  sample label. Import title/format controls live in Import options.
- **Review Council:** interview sources and assessment criteria sit side by
  side. Source preview, consent and the run action follow the input columns.
  Report cards use two columns when space permits.
  Extra competencies, provider identities and scope information use disclosures;
  explicit model consent and setup errors remain available before a live run.
- **Debate:** three persona choices share a row on desktop; active chat uses
  the full canvas with a sticky composer and bounded prose width.
- **Evidence Lab:** inputs and results sit side by side once results exist.
  Scope and saved-run controls remain available without a second profile panel.
- **Quick critique:** explanatory copy sits beside the argument input.

Below 900px, the major columns stack. Below 650px, navigation moves to a second
header row and account controls remain compact. These breakpoints are implemented;
this update was visually checked at the browser's normal desktop size rather
than a verified mobile-device viewport.

## Interaction and verification

Existing form IDs, ownership checks, source links, consent and request budgets
are preserved. Model text remains inert; chat formatting uses the local safe
Markdown renderer. AI note generation stays behind explicit consent.

Verified in the browser: shared navigation, restored chat, saved notebook
selection, the AI-notes disclosure, notebook-to-review handoff and source preview
without model calls. At a 1265px viewport, the workspace measured 1188px wide
with no horizontal overflow. All 20 existing frontend contracts pass.
The copy cleanup also verified unsaved/revert feedback, collapsed original
sources and sample transcript preview with import options closed.

Shared rules live in frontend/evidence-lab.css; notebook and council layouts
have their own stylesheets. The logo is frontend/debatebot-mark.svg.
