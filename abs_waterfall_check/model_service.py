"""Toy cashflow service for exercising the local release adapter."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import STEPS, cents, faulty, good
from .releases import unique_object


def parse_scenario(raw):
    scenario = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(scenario, dict) or set(scenario) not in (
        {"collected_prepayment_charges"}, {"cash", *STEPS}
    ):
        raise ValueError("Unexpected scenario fields")
    for amount in scenario.values():
        cents(amount)
    return scenario


def make_server(port, model_id, version, behavior, delay=0):
    if behavior not in ("good", "faulty") or delay < 0:
        raise ValueError("Choose good or faulty and a nonnegative delay")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/calculate":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("Invalid request length")
                scenario = parse_scenario(self.rfile.read(size))
                if "collected_prepayment_charges" in scenario:
                    payouts = {"class_p": scenario["collected_prepayment_charges"] if behavior == "good" else 0}
                else:
                    payouts = (good if behavior == "good" else faulty)(scenario)
                data = json.dumps({"model_id": model_id, "version": version, "payouts": payouts}).encode()
            except (ValueError, TypeError, KeyError):
                self.send_error(400)
                return
            time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
