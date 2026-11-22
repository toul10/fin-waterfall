import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = [sys.executable, str(ROOT / "scripts" / "start_app.py")]


class OtherService(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = {"/health": {"status": "ok"}, "/v1/models": {"data": [{"id": "different-model"}]}}
        self.send_response(200 if self.path in payload else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload.get(self.path, {})).encode())

    def log_message(self, *args):
        pass


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = self.root / "store"

    def launch(self, *args, store=None):
        command = [*LAUNCH, "--store", str(store or self.store), *args]
        with subprocess.Popen(
            command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as proc:
            try:
                output, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                output, _ = proc.communicate()
                self.fail(f"Launcher did not exit. {output}")
            return subprocess.CompletedProcess(command, proc.returncode, output)

    def other_service(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), OtherService)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return str(server.server_port)

    def dashboard(self):
        log = self.root / "dashboard.log"
        with log.open("w") as output:
            proc = subprocess.Popen(
                [*LAUNCH, "--store", str(self.store), "--without-qwen", "--port", "0"],
                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )

        def stop():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)

        self.addCleanup(stop)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            content = log.read_text()
            match = re.search(r"Waterfall workspace: http://127\.0\.0\.1:(\d+)", content)
            if match:
                return match.group(1)
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        self.fail(f"Dashboard did not start. {log.read_text()}")

    def test_existing_workspace(self):
        port = self.dashboard()
        same = self.launch("--without-qwen", "--port", port)
        self.assertEqual(same.returncode, 0, same.stdout)
        self.assertIn(f"http://127.0.0.1:{port}", same.stdout)

        other_store = self.root / "other-store"
        different = self.launch("--without-qwen", "--port", port, store=other_store)
        self.assertNotEqual(different.returncode, 0, different.stdout)
        self.assertFalse(other_store.exists())
        self.assertEqual(self.launch("--without-qwen", "--port", port).returncode, 0)

    def test_unrelated_dashboard_listener_checked_before_qwen(self):
        result = self.launch("--port", self.other_service(), "--llama-server", str(self.root / "missing"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("--port 0", result.stdout)
        self.assertFalse(self.store.exists())

    def test_running_model_must_offer_requested_alias(self):
        result = self.launch("--port", "0", "--model-port", self.other_service(),
                             "--use-running-model", "--model", "expected-model")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("expected-model", result.stdout)
        self.assertFalse(self.store.exists())

    def test_occupied_managed_model_port(self):
        result = self.launch("--port", "0", "--model-port", self.other_service(),
                             "--llama-server", str(self.root / "missing"))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("--model-port 0", result.stdout)
        self.assertFalse(self.store.exists())
