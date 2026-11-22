"""Hosted consent, exact egress and deterministic post-approval checks."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check.gemini import Gemini
from abs_waterfall_check.web import App
from scripts.fake_gemini import FakeVertex


class GeminiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = FakeVertex(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.server = server
        self.gemini = Gemini("pilot-local", "us-central1", "gemini-test", f"http://127.0.0.1:{server.server_port}")
        self.app = App(tmp.name, gemini=self.gemini)
        self.clause = json.loads((Path(__file__).resolve().parents[1] / "abs_waterfall_check"
            / "web_examples_data" / "ford" / "draft.json").read_text())["support"]
        self.body = {"name": "PRIVATE-NAME", "clause": "PRIVATE-REFERENCE",
                     "source_text": f"PRIVATE-HEADER\n\n{self.clause}\n\nPRIVATE-TRAILER",
                     "kind": "payment_priority", "amount_cents": 953862103}
        self.deal = self.app.post("/api/deals", self.body)
        self.path = "/api/deals/" + self.deal["id"]

    def preview(self, path=None, excerpt=None, actor="alice"):
        return self.app.post((path or self.path) + "/draft-preview",
                             {"excerpt": excerpt or self.clause}, actor=actor)

    def send(self, token, *, path=None, actor="alice", **kwargs):
        body = dict(provider="gemini", consent_token=token, confirmed=True, excerpt=self.clause)
        return self.app.post((path or self.path) + "/draft", {**body, **kwargs}, actor=actor)

    def test_modified_actor_selection_provider_and_stale_tokens_cannot_send(self):
        token = self.preview()["consent_token"]
        other = self.app.post("/api/deals", self.body)
        for kwargs in ({"confirmed": False}, {"actor": None}, {"actor": "bob"},
                       {"path": "/api/deals/" + other["id"]}, {"excerpt": self.clause[:60]},
                       {"provider": "local"}, {"provider": "openai"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.send(token, **kwargs)
        self.preview(excerpt=self.clause[:60])
        self.preview()
        with self.assertRaises(ValueError):
            self.send(token)
        fresh = self.preview()["consent_token"]
        with patch("abs_waterfall_check.web.time.time", return_value=9e10), self.assertRaises(ValueError):
            self.send(fresh)
        self.assertEqual(list(self.server.requests), [])

    def test_intake_failure_retry_approval_and_payment(self):
        result = self.app.post("/api/intake", {**self.body, "provider": "local",
                                               "distribution_date": "2026-05-15"})
        self.assertEqual(result["state"], "consent_needed")
        self.path = "/api/deals/" + result["deal"]["id"]
        preview = self.preview()
        self.assertEqual(preview["excerpt"], self.clause)
        self.assertEqual(list(self.server.requests), [])
        self.server.fail_next = True
        failed = self.send(preview["consent_token"])
        self.assertEqual(failed["state"], "draft_retry")
        self.assertIn("HTTP 503", failed["reason"])
        self.assertEqual(failed["deal"]["suggested_excerpt"], self.clause)
        self.assertEqual(failed["deal"]["initial_allocation"]["amount_cents"], 953862103)
        with self.assertRaises(ValueError):
            self.send(preview["consent_token"])
        token = self.preview()["consent_token"]
        draft = self.send(token)
        self.assertEqual(draft["draft"]["inference_backend"], "gemini")
        self.assertEqual(draft["draft"]["support"], self.clause)
        self.assertFalse(draft["approved"])
        with self.assertRaises(ValueError):
            self.send(token)
        self.assertEqual(len(self.server.requests), 2)
        for record in self.server.requests:
            self.assertEqual(record["body"]["contents"], [{"role": "user", "parts": [{"text": self.clause}]}])
            self.assertEqual(record["path"], f"/v1/{self.gemini.resource}:generateContent")
            envelope = json.dumps(record)
            for secret in ("PRIVATE-", "953862103", "2026-05-15", token, "alice", self.deal["id"]):
                self.assertNotIn(secret, envelope)
        approved = self.app.post(self.path + "/approve", {"reviewer": "reviewer", "confirmed": True,
            "sha256": draft["draft_sha256"], "effective_from": "2026-01-01"})
        self.assertTrue(approved["approved"])
        for a, b, expected in ((0, 5_000_000, "BLOCK"), (5_000_000, 0, "PASS")):
            run = self.app.post(self.path + "/runs", {"mode": "check", "distribution_date": "2026-05-15",
                "scenario": {"cash": 5_000_000, "class_a_principal": 6_000_000, "class_b_interest": 5_000_000},
                "payouts": {"class_a_principal": a, "class_b_interest": b}})
            self.assertEqual(run["status"], expected)
            self.assertEqual(run["rule_provenance"], approved["rule_provenance"])
        self.assertEqual(len(self.server.requests), 2)

    def test_transport_config_is_server_only_and_fake_disabled_on_cloud_run(self):
        with patch.dict(os.environ, {"K_SERVICE": "waterfall"}), self.assertRaises(ValueError):
            Gemini("pilot-local", "us-central1", "gemini-test", "http://127.0.0.1:8799")
        with self.assertRaises(ValueError):
            Gemini("pilot-local", "us-central1", "gemini-test", "http://example.org:8799")
        self.assertEqual(Gemini("pilot-local", "eu", "gemini-test").api_host, "aiplatform.eu.rep.googleapis.com")
        self.assertEqual(Gemini("pilot-local", "global", "gemini-test").api_host, "aiplatform.googleapis.com")
        prod = Gemini("pilot-local", "us-central1", "gemini-test")
        with patch.object(Gemini, "identity_headers", return_value={"authorization": "Bearer mock"}), \
             patch("abs_waterfall_check.gemini.HTTPSConnection") as transport:
            response = transport.return_value.getresponse.return_value
            response.status = 403
            with self.assertRaisesRegex(ValueError, "HTTP 403"):
                prod.proposal("static", "confirmed", {"required": []})
            transport.assert_called_once_with("us-central1-aiplatform.googleapis.com", timeout=45)
            method, path, raw, headers = transport.return_value.request.call_args.args
            self.assertEqual(method, "POST")
            self.assertEqual(path, f"/v1/{prod.resource}:generateContent")
            self.assertEqual(headers["authorization"], "Bearer mock")
            self.assertEqual(json.loads(raw)["contents"][0]["parts"], [{"text": "confirmed"}])
