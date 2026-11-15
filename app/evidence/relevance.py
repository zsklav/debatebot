"""Conservative lexical screening of local semantic-search candidates.

This is a transparent heuristic, not an entailment judge or calibrated semantic
threshold. It can miss paraphrases; rejected matches never establish falsity.
"""

import math
import re

STOP = set("""a an the and or but if then than that this these those is are was were
be been being to of for from in on at by with without as it its their they them
we our you your i my do does did can could would should will may might not no
yes always never all any every some more less most much many how why what which
when where who whether about claim claims evidence study studies research result
results effect effects affect affects improve improves improved improving lower
lowers reduce reduces reduced reduction increase increases increased change
changes changed receive received worse better work working worked worker workers employee employees workforce
remote hybrid office home schedule schedules arrangement arrangements company
companies workplace workplaces telework teleworking wfh""".split())
ALIASES = {
    "quitting":"retention", "quit":"retention", "quits":"retention",
    "resignation":"retention", "resignations":"retention", "turnover":"retention",
    "attrition":"retention", "retaining":"retention",
    "innovative":"innovation", "ideas":"innovation", "idea":"innovation",
    "productivity":"performance", "productive":"performance", "output":"performance",
    "grades":"performance", "grade":"performance", "randomized":"randomization",
    "reviews":"performance", "ratings":"performance",
    "randomised":"randomization", "random":"randomization",
    "coordination":"coordination", "coordinated":"coordination",
    "unsynchronized":"coordination", "synchronization":"coordination",
}


def terms(text):
    words = re.findall(r"[a-z]+", text.lower())
    return {ALIASES.get(word, word) for word in words if word not in STOP and len(word) > 2}


def screen(query, hits):
    wanted = terms(query)
    accepted = []
    for hit in hits:
        available = terms(f"{hit.get('title', '')} {hit['text']}")
        # Topic-only queries are ambiguous: do not accept a passage simply for
        # mentioning hybrid/remote work. Ask for a measurable outcome instead.
        if wanted and len(wanted & available) >= max(1, math.ceil(len(wanted) / 2)):
            accepted.append(hit)
    return accepted


class RelevantLocalIndex:
    """Keep vector similarity as ranking, then screen before exposing evidence."""
    def __init__(self, index, claim=None):
        self.index, self.claim = index, claim
        self.last_screen = {}

    def for_claim(self, claim):
        return RelevantLocalIndex(self.index, claim)

    def search(self, query, *, limit=3):
        candidates = self.index.search(query, limit=limit)
        accepted = screen(self.claim or query, candidates)
        self.last_screen = {"method":"local-keyword-overlap-v1", "candidates":len(candidates),
                            "accepted":len(accepted), "rejected":len(candidates) - len(accepted),
                            "warning":"Heuristic relevance screening; may miss paraphrases. Not factual verification."}
        return accepted


def insufficient_local(claim, screening):
    return {"claim":claim, "verdict":"insufficient_evidence",
            "explanation":"No local passages passed the relevance screen for this claim. Nearest vector matches were not treated as evidence. This does not establish whether the claim is true or false.",
            "suggested_revision":"Specify a measurable outcome covered by the corpus, provide relevant sources, or explicitly select Web research.",
            "citations":[], "citation_repairs":[], "retrieved_passages":[],
            "evidence_status":"no_relevant_evidence", "relevance_screen":screening,
            "scope":"Local corpus only. Keyword screening can miss relevant paraphrases; citations do not prove entailment."}
