import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from abs_waterfall_check.web import App


class DraftProviderTests(unittest.TestCase):
    clause = "All prepayment charges received for the period are paid to Class P."
    response = '{"recipient":"class_p","evidence":"P1"}'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = App(temp.name, local_drafting=False)
        self.deal = self.app.post("/api/deals", {
            "name": "PRIVATE-DEAL-NAME", "clause": "PRIVATE-SECTION-NAME",
            "source_text": "PRIVATE-OTHER-PARAGRAPH\n\n" + self.clause,
            "kind": "prepayment_charges_to_class_p",
        })
        self.path = "/api/deals/" + self.deal["id"] + "/draft"
        self.choices = {"OPENAI_API_KEY": "test-key", "OPENAI_DRAFT_MODEL": "test-openai",
                        "ANTHROPIC_API_KEY": "test-key", "ANTHROPIC_DRAFT_MODEL": "test-claude"}
        self.sha = hashlib.sha256(self.clause.encode()).hexdigest()

    @patch("abs_waterfall_check.draft_providers.HTTPSConnection")
    def test_exact_consent_and_config_are_required_before_transmission(self, dial):
        with patch.dict(os.environ, {}, clear=True):
            configured = self.app.get("/api/dashboard")["draft_providers"]
            self.assertFalse(any(p["available"] for p in configured))
            with self.assertRaisesRegex(ValueError, "configure"):
                self.app.post(self.path, {"provider":"openai", "excerpt":self.clause, "consent_sha256":self.sha})
        with patch.dict(os.environ, self.choices):
            for body in ({"provider":"anthropic"}, {"provider":"openai", "excerpt":self.clause},
                         {"provider":"openai", "excerpt":"prepayment", "consent_sha256":self.sha},
                         {"provider":"openai", "excerpt":"foreign excerpt", "consent_sha256":self.sha},
                         {"provider":"local", "excerpt":self.clause}):
                with self.assertRaises(ValueError):
                    self.app.post(self.path, body)
            self.assertNotIn("test-key", json.dumps(self.app.get("/api/dashboard")))
        dial.assert_not_called()

    @patch("abs_waterfall_check.draft_providers.HTTPSConnection")
    def test_both_transports_use_only_the_reviewed_excerpt_and_bind_to_approval(self, dial):
        with patch.dict(os.environ, self.choices):
            for provider, host, path, reply in (
                ("openai", "api.openai.com", "/v1/responses", {"status": "completed", "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": self.response}]}]}),
                ("anthropic", "api.anthropic.com", "/v1/messages", {"stop_reason": "end_turn", "content": [
                    {"type": "text", "text": self.response}]}),
            ):
                mock_response = dial.return_value.getresponse.return_value
                mock_response.status = 200
                mock_response.read.return_value = json.dumps(reply).encode()
                deal = self.app.post(self.path, {"provider":provider, "excerpt":self.clause, "consent_sha256":self.sha})
                dial.assert_called_with(host, timeout=45)
                method, endpoint, raw, headers = dial.return_value.request.call_args.args
                self.assertEqual((method, endpoint), ("POST", path))
                self.assertIn(self.clause, raw)
                self.assertNotIn("PRIVATE-", raw)
                self.assertNotIn("test-key", raw)
                body = json.loads(raw)
                if provider == "openai":
                    self.assertFalse(body["store"])
                    self.assertEqual(headers["Authorization"], "Bearer test-key")
                else:
                    self.assertEqual(headers["x-api-key"], "test-key")
                self.assertEqual(deal["draft"]["inference_backend"], provider)
                self.assertEqual(deal["draft"]["support"], self.clause)
                self.assertFalse(deal["approved"])
                approved = self.app.post(self.path.replace("/draft", "/approve"), {
                    "reviewer":"Reviewer", "confirmed":True, "sha256":deal["draft_sha256"]})
                self.assertTrue(approved["approved"])

    @patch("abs_waterfall_check.draft_providers.HTTPSConnection")
    def test_refusal_or_truncated_output_cannot_be_approved(self, dial):
        with patch.dict(os.environ, self.choices):
            response = dial.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"status":"incomplete","output":[]}'
            with self.assertRaisesRegex(ValueError, "did not finish"):
                self.app.post(self.path, {"provider":"openai", "excerpt":self.clause, "consent_sha256":self.sha})
            self.assertNotIn("draft", self.app.detail(self.deal["id"]))
