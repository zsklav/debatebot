# Retrieval development baseline

Executed 2026-11-14 with real CPU embeddings and local Qdrant. JSON reports record
model artifact identity, source content hashes, chunking settings, queries,
expected chunk IDs, retrieved IDs, and scores.

| Metric | k=1 | k=3 |
| --- | --- | --- |
| Answerable queries with at least one annotated chunk | 4/6 | 6/6 |
| Mean chunk recall (including overlapping duplicate evidence) | 0.5833 | 1.0 |
| No-evidence queries returning passages | 2/2 | 2/2 |

There are two summaries and four chunks. These eight hand-authored development
queries were not held out. A high top-3 score over four candidates demonstrates
working plumbing, not research-quality retrieval. No-evidence cases are excluded
from recall and remain visible in the report; the system has no calibrated
abstention mechanism yet.

The top-1 retention query retrieved the innovation paper. The performance query
retrieved the correct source but missed the specific annotated substring. The
coordination passage occurs in two overlapping chunks; retrieving one gives
0.5 recall but counts as a successful evidence hit. Labels are narrow substrings,
not exhaustive human relevance judgments. Do not use these scores as resume
accuracy claims.

Rerun from the repository root after indexing:

```powershell
.\.venv\Scripts\python.exe retrieval.py evaluate --offline --limit 1 --output eval_results\retrieval_development_k1.json
.\.venv\Scripts\python.exe retrieval.py evaluate --offline --limit 3 --output eval_results\retrieval_development_k3.json
```
