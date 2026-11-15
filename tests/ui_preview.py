"""Manual UI fixture server. No Groq calls. Run from repository root on port 8765."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


class Preview(SimpleHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/personas":
            self.send_json({"personas": []})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path not in {"/api/findhole/evidence", "/api/research"}:
            self.send_json({"detail": "Fixture supports evidence and research only"}, 404)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "setup-error" in str(data):
            self.send_json({"detail": "Fixture setup error"}, 503)
            return
        source = {"title": "Fixture source — not a live result", "source_url": "https://example.org/study",
                  "authors": "Fixture author", "source_date": "2024-06-12", "license": "Test fixture",
                  "representation": "Editorial summary fixture", "start_line": 3, "end_line": 5}
        passage = {"chunk_id": "fixture-1", "text": "Quitting decreased in the studied workforce.", **source}
        assessment = {"verdict": "supported", "explanation": "Fixture evidence supports the limited claim, not every workforce.",
                      "suggested_revision": "Limit the claim to the studied population.", "citations": [
                          {"quote": passage["text"], "relation": "supports", "source": source}],
                      "retrieved_passages": [passage]}
        if self.path == "/api/research":
            self.send_json({"status": "partial", "termination_reason": "step_limit", "tool_calls": 4,
                            "searches": 2, "elapsed_seconds": 3.2, "assessment": None,
                            "read_passages": [passage], "events": [{"tool": "search_evidence",
                            "arguments": {"query": data["claim"]}, "output": {"results": [passage]}}]})
        else:
            self.send_json({"weakness": {"severity": 7, "weakest_claim": data["argument"],
                            "why_weak": "Fixture diagnosis: the conclusion may exceed the population studied.",
                            "the_attack": "What justifies extending this finding to every workplace?",
                            "how_to_defend": "Name the work arrangement and population."}, "evidence": assessment})


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "web"
    print("FIXTURE PREVIEW ONLY: http://127.0.0.1:8765/static/index.html", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), partial(Preview, directory=str(root))).serve_forever()
