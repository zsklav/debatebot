"""Fixed synthetic sample, with explicitly scripted model outputs and no API calls."""

import json
from .models import ReviewInput
from .providers import ModelRoute

SAMPLE = {
    "job_description":"Backend engineer: explain practical caching decisions and transaction handling, including limitations and tests.",
    "transcript":"Interviewer: Describe your caching implementation.\nCandidate: I added Redis caching for product lookups with a 60-second TTL.\nInterviewer: How did you handle invalidation and concurrent writes?\nCandidate: I have not implemented invalidation beyond TTL or tested concurrent updates.",
    "resume":"Project claim: Used Redis caching in a backend API.",
    "criteria":[{"id":"caching", "label":"Caching", "description":"Explain the implemented caching mechanism and limitations; do not infer production-scale expertise."},
                {"id":"transactions", "label":"Transactions", "description":"Explain transaction boundaries, rollback and isolation using actual interview evidence."}]
}


class ScriptedAdapter:
    def __init__(self, role):
        self.route = ModelRoute(role, "scripted", "synthetic-sample", "not-a-real-model", "")
        self.requests, self.tokens = 0, 0
        self.role = role

    def invoke(self, messages):
        sources = json.loads(messages[-1]["content"])["sources"]
        answer = next(s for s in sources if "I added Redis caching" in s["text"])
        limitation = next(s for s in sources if "I have not implemented" in s["text"])
        status = "demonstrated" if self.role == "a" else "needs_verification"
        caching = {"criterion_id":"caching", "status":status,
                   "explanation":"A concrete TTL example is present; invalidation and concurrent-update handling remain unverified.",
                   "evidence":[{"source_id":answer["id"], "quote":answer["text"]}]}
        if self.role != "a":
            caching["evidence"].append({"source_id":limitation["id"], "quote":limitation["text"]})
        transactions = {"criterion_id":"transactions", "status":"not_assessed",
                        "explanation":"The supplied excerpts do not demonstrate transaction handling; this does not establish a lack of ability.", "evidence":[]}
        output = {"findings":[caching, transactions]}
        if self.role == "judge":
            caching["resolution"], transactions["resolution"] = "proposed_resolution", "agreed"
            output["summary"] = "SYNTHETIC DEMO: A basic caching example is present. Advanced cache invalidation needs verification, and transactions were not assessed in these excerpts. Human review is required."
        return json.dumps(output)


def payload():
    return ReviewInput.model_validate(SAMPLE)
