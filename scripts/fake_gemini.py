"""Local Vertex-compatible test double for synthetic data. It records requests in memory.

It recognizes the two supported single-clause rules and synthetic priorities of
payments written with one numbered step per paragraph."""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from threading import Lock

from abs_waterfall_check import intake
from abs_waterfall_check.engine import STEPS


ENUMERATOR = re.compile(r"\((?:[ivxlcdm]{1,6}|\d{1,2}|[a-z])\)\s*(?:(?:first|second|third|fourth|fifth|sixth|"
                        r"seventh|eighth|ninth|tenth|eleventh|twelfth),\s*)?", re.I)


def waterfall_proposal(paragraphs):
    """Read synthetic numbered steps such as "(ii) second, to the Trustee, the Trustee Fee;"."""
    funds, steps = "Available Funds", []
    for index, paragraph in enumerate(paragraphs, 1):
        text = " ".join(paragraph.split())
        named = re.search(r"\bapply (?:the )?([A-Z][\w ]*?(?:Funds|Amount))\b", text)
        if named and not steps:
            funds = named[1]
        marker = ENUMERATOR.match(text)
        if not marker:
            continue
        body = text[marker.end():].rstrip(" ;.")
        if re.search(r"\bremaining\b", body, re.I):
            steps.append({"payees": [re.split(r"\bto (?:the )?", body)[-1]], "limit": "remainder",
                          "evidence": f"P{index}"})
            continue
        payable = body.split(", the ", 1)[1] if ", the " in body else re.sub(r"^(?:to )?the ", "", body)
        payees = [name for name in re.split(r",\s*(?:and\s+)?the\s+|\s+and\s+the\s+", payable) if name]
        steps.append({"payees": payees, "limit": "due", "evidence": f"P{index}"})
    return {"funds_label": funds, "steps": steps}


def fixture_proposal(body):
    text = body["contents"][0]["parts"][0]["text"]
    schema = body["generationConfig"]["responseJsonSchema"]
    paragraphs = re.split(r"\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*", text.strip())
    if "recipient" in schema["properties"]:
        for index, paragraph in enumerate(paragraphs, 1):
            if intake.ALL_CHARGES.search(paragraph) and intake.CLASS_P.search(paragraph):
                return {"recipient": "class_p", "evidence": f"P{index}"}
        return {"recipient": "", "evidence": ""}
    if "steps" in schema["properties"]:
        return waterfall_proposal(paragraphs)
    evidence = dict.fromkeys(STEPS, "")
    positions = {}
    for index, paragraph in enumerate(paragraphs, 1):
        for step, pattern in zip(STEPS, (intake.SENIOR, intake.JUNIOR)):
            match = pattern.search(" ".join(paragraph.split()))
            if match and not evidence[step]:
                positions[step] = index, match.start()
                evidence[step] = f"P{index}"
    order = sorted(positions, key=positions.get) if all(evidence.values()) else []
    return {"order": order, "evidence": evidence}


class FakeVertex(ThreadingHTTPServer):
    def __init__(self, address):
        super().__init__(address, Handler)
        self.requests = deque(maxlen=256)
        self.fail_next = False
        self.guard = Lock()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"ok": True})
        if self.path == "/requests":
            with self.server.guard:
                requests = list(self.server.requests)
            return self.reply(200, {"requests": requests})
        self.reply(404, {})

    def do_POST(self):
        if self.path == "/fail-next":
            with self.server.guard:
                self.server.fail_next = True
            return self.reply(200, {"ok": True})
        if not re.fullmatch(r"/v1/projects/[\w-]+/locations/[\w-]+/publishers/google/models/gemini-[\w.-]+:generateContent", self.path):
            return self.reply(404, {})
        try:
            size = int(self.headers.get("Content-Length", -1))
            if not 0 < size <= 1_000_000:
                return self.reply(413, {})
            body = json.loads(self.rfile.read(size))
            with self.server.guard:
                self.server.requests.append({"path": self.path, "body": body})
                fail, self.server.fail_next = self.server.fail_next, False
            if fail:
                return self.reply(503, {"error": {"message": "Synthetic outage"}})
            result = fixture_proposal(body)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            return self.reply(400, {"error": {"message": "Unexpected test request"}})
        self.reply(200, {"candidates": [{"finishReason": "STOP", "content": {
            "role": "model", "parts": [{"text": json.dumps(result)}]}}]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--bind", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1")
    args = parser.parse_args()
    with FakeVertex((args.bind, args.port)) as server:
        print(f"Fake Gemini listening at http://{args.bind}:{args.port} (synthetic data only)", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
