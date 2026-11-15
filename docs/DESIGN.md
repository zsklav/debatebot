# DebateBot interface design

## Current layout — focused workspace

Removed the duplicate character-profile sidebar, slogans, decorative badges, repeated headings and footer. The app uses one centered 840px workspace, compact account controls, three tool tabs and the active task. Active conversations show the persona and useful actions without the picker heading. Research scope/privacy information and saved-run controls remain available in collapsed disclosures; external-search disclosure remains visible in web-mode help. Token totals are retained by the API but omitted from the main chat status.

## Current theme — orange and charcoal

The current implementation follows the user's requested Claude-inspired direction: warm charcoal #171614, surfaces #211f1c, cream text #f4eee5, muted text #aaa398, orange accent #e7a37c and terracotta actions #dfa177 with dark labels. System UI fonts serve controls; Georgia serves major headings and assistant replies. The abstract orange mark is a local text/CSS asset. These are original implementation choices, not extracted Claude design tokens. The earlier Character.AI research below records the previous layout reference; its palette has been superseded. Chat and Evidence Lab were visually checked with no browser console errors.


Reference: https://character.ai/character/URqCI5OM/debate-bot-engaging-debates
Inspected 2026-11-14 using Firecrawl content/branding and a live desktop browser screenshot.

## Observed reference

The actual browser screenshot showed a dark charcoal page, a left-hand character profile with a circular avatar and white pill Chat button, and a right-hand content column with underlined tabs. Type is compact sans serif, with muted supporting copy and minimal borders. Firecrawl's branding response identified atHauss, 14px body text, 20px headings and 30px button radii, but returned **light-theme colors** (#F4F4F5 background and #202024 primary). Those light colors were not used as evidence for the observed dark screenshot. Dimensions below are implementation choices, not exact extracted CSS.

## Local implementation choices

- Background #18181b; surface #202024; elevated surface #27272c.
- Primary text #f4f4f5; supporting text #a1a1aa; borders #303035.
- System Segoe UI/sans serif; no remote font dependency or proprietary font copy.
- 280px profile column with a flexible content column and 60px gap on desktop.
- Circular CSS illustration with muted lavender colors. No Character.AI logo or avatar asset is reused.
- White pill actions, underlined tabs, 14–16px rounded cards.
- Chat is the default tab. Evidence Lab and Quick critique preserve drafts and results when switching.
- Below 650px the profile becomes a compact stacked header. This responsive treatment is our adaptation; the supplied reference was inspected at desktop size.

## Files

- frontend/index.html: semantic page structure and existing form contracts.
- frontend/evidence-lab.css: shared tokens, profile, forms, chat, evidence and breakpoints.
- frontend/workspace-ui.js: accessible keyboard-operable tabs.
- frontend/debate-ui.js: isolated debate interactions.
- frontend/evidence-lab.js: safe evidence rendering and research requests.
- frontend/auth-ui.js: sign-in and account lifecycle.

The reference is a visual direction. DebateBot retains its own identity and actual features; no interaction counts or usage claims are copied. The reference's exact authenticated chat view, animations and mobile layout were not captured.

## Validation limits

The final desktop UI, persona selection, keyboard navigation and reopening saved research were verified in the live local app with no console errors. The browser viewport override did not apply the requested 390px width (DOM still reported 1280px), so final mobile device QA remains pending. Responsive rules are implemented, not claimed as device-verified.

## Chat response readability

Bot responses use a local safe Markdown subset (message-format.js), readable line length, paragraph spacing and labelled sections. Human messages remain plain-text bubbles. Long saved paragraphs can be split visually into sentence groups; saved text is preserved. The transcript uses page scrolling and a sticky composer instead of nested scrollbars. Formatting performs no model calls and does not establish the factual correctness of generated claims.
