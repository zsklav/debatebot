# Small development corpus

Only files listed in manifest.json are indexed. README and the fictional example
are excluded. These two short editorial summaries are adaptations, not original
paper excerpts, and are not an exhaustive or balanced literature review.

## Attribution and changes

- Nicholas Bloom, Ruobing Han, and James Liang (2024),
  [Hybrid working from home improves retention without damaging performance](https://www.nature.com/articles/s41586-024-07500-2).
- Michael Gibbs, Friederike Mengel, and Christoph Siemroth (2024),
  [Employee innovation during office work, work from home and hybrid work](https://www.nature.com/articles/s41598-024-67122-6).

Both article pages explicitly identify their article text as
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). DebateBot paraphrased
selected abstract findings, shortened the text, and added scope cautions. No
figures or third-party images are included. Preserve authors, source links,
license, and this changes notice when reusing these adaptations. Page content
and licenses were inspected on 2026-11-14.

Line references in retrieval output refer to these local summaries, not the
publisher's HTML or PDF. Returned metadata makes the representation explicit.
Check the linked papers before making consequential conclusions. The studies
examine different work arrangements, populations, and outcomes; apparent
differences are not evidence that either paper invalidates the other.

retrieval_cases.json contains hand-authored development queries with relevant
passage substrings. They are smoke checks for retrieval plumbing, not held-out
evidence of broad retrieval accuracy. Two no-evidence cases demonstrate that a
nearest-neighbor search can still return a passage for an unanswerable question.
