"""Drafting, evidence and approval for a general priority of payments."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check import engine, intake, local
from abs_waterfall_check.gemini import Gemini
from scripts.fake_gemini import FakeVertex, fixture_proposal

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "fixtures" / "synthetic-priority-of-payments.txt").read_text(encoding="utf-8")
START = SOURCE.index("Section 8.2(c)\nPriority")
EXCERPT = SOURCE[START:SOURCE.index("\n\nSection 8.3")]
PROPOSAL = {"funds_label": "Available Funds", "steps": [
    {"payees": ["Servicing Fee"], "limit": "due", "evidence": "P2"},
    {"payees": ["Indenture Trustee Fees", "Owner Trustee Fees"], "limit": "due", "evidence": "P3"},
    {"payees": ["Class A-1 Interest", "Class A-2 Interest", "Class A-3 Interest"], "limit": "due", "evidence": "P4"},
    {"payees": ["First Priority Principal Payment"], "limit": "due", "evidence": "P5"},
    {"payees": ["Class B Interest"], "limit": "due", "evidence": "P6"},
    {"payees": ["Reserve Account Deposit"], "limit": "due", "evidence": "P7"},
    {"payees": ["Regular Principal Payment"], "limit": "due", "evidence": "P8"},
    {"payees": ["Certificateholders"], "limit": "remainder", "evidence": "P9"},
]}


def draft_with(proposal, excerpt=EXCERPT, backend="llama.cpp"):
    with patch.object(local, "_local_proposal", return_value=proposal) as model:
        draft = local.extract(excerpt, "Section 8.2(c)", "Synthetic Auto Receivables Trust 2026-1", "1", "v1",
                              "qwen-local", 8080, backend, rule_kind=engine.PRIORITY_OF_PAYMENTS)
    return draft, model


class Drafting(unittest.TestCase):
    def test_draft_uses_server_ids_and_verbatim_paragraph_evidence(self):
        draft, model = draft_with(PROPOSAL)
        self.assertEqual((draft["schema"], draft["type"], draft["rounding"], draft["funds_label"]),
                         (3, "priority_of_payments", "largest_remainder", "Available Funds"))
        self.assertEqual([[p["id"] for p in s["payees"]] for s in draft["steps"]], [
            ["servicing_fee"], ["indenture_trustee_fees", "owner_trustee_fees"],
            ["class_a_1_interest", "class_a_2_interest", "class_a_3_interest"], ["first_priority_principal_payment"],
            ["class_b_interest"], ["reserve_account_deposit"], ["regular_principal_payment"], ["certificateholders"]])
        self.assertEqual(draft["steps"][2]["evidence"], "(iii) third, pro rata, to the Class A Noteholders, the "
                         "Class A-1 Interest, the Class A-2 Interest and the Class A-3 Interest;")
        self.assertTrue(draft["support"].startswith("(i) first, to the Servicer"))
        self.assertTrue(draft["support"].endswith("to the Certificateholders."))
        self.assertIn(draft["support"], draft["contract_excerpt"])
        self.assertEqual(draft["uncited_paragraphs"], [])
        self.assertTrue(local.waterfall_evidence(draft))
        # A long priority of payments needs more output than a two-step order.
        self.assertEqual((model.call_args.kwargs["max_tokens"], model.call_args.kwargs["timeout"]), (4096, 300))
        prompt = model.call_args.args[4]
        self.assertIn("[P1] Section 8.2(c)", prompt)
        self.assertIn("[P9] (viii) eighth", prompt)

    def test_names_are_cleaned_and_repeated_names_carry_their_step(self):
        proposal = {"funds_label": " Available Funds ", "steps": [
            {"payees": ["=Servicing Fee"], "limit": "due", "evidence": "P2"},
            {"payees": ["- Class A interest", "class A interest"], "limit": "due", "evidence": "P4"},
            {"payees": ["Servicing\nFee;"], "limit": "due", "evidence": "P5"},
        ]}
        draft, _ = draft_with(proposal)
        labels = [[p["label"] for p in s["payees"]] for s in draft["steps"]]
        self.assertEqual(labels, [["Servicing Fee"], ["Class A interest", "class A interest (step 2)"],
                                  ["Servicing Fee (step 3)"]])
        self.assertEqual(draft["steps"][2]["payees"][0]["id"], "servicing_fee_step_3")
        # Paragraphs between cited steps that no step cites are shown to the reviewer.
        self.assertEqual(draft["uncited_paragraphs"], [
            "(ii) second, pro rata, to the Indenture Trustee and the Owner Trustee, the Indenture Trustee Fees "
            "and the Owner Trustee Fees;", *[p for p in EXCERPT.split("\n\n")[5:]]])

    def test_unusable_proposals_are_rejected(self):
        steps = PROPOSAL["steps"]
        cases = {
            "no steps": {"funds_label": "Available Funds", "steps": []},
            "out of order": {"funds_label": "", "steps": [steps[1], steps[0]]},
            "remainder first": {"funds_label": "", "steps": [steps[-1], steps[0]]},
            "unknown evidence": {"funds_label": "", "steps": [{**steps[0], "evidence": "P99"}]},
            "unknown limit": {"funds_label": "", "steps": [{**steps[0], "limit": "percent"}]},
            "nameless payee": {"funds_label": "", "steps": [{**steps[0], "payees": ["  •  "]}]},
            "no payees": {"funds_label": "", "steps": [{**steps[0], "payees": []}]},
            "too many steps": {"funds_label": "", "steps": [steps[0]] * 41},
            "not an object": [],
        }
        for name, proposal in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                draft_with(proposal)

    def test_the_deterministic_stub_cannot_draft_a_general_rule(self):
        with self.assertRaisesRegex(ValueError, "configured drafting model"):
            local.extract(EXCERPT, "Section 8.2(c)", "Deal", "1", "1", rule_kind=engine.PRIORITY_OF_PAYMENTS)

    def test_api_providers_receive_a_schema_without_array_limits(self):
        with patch.object(local.draft_providers, "proposal", return_value=PROPOSAL) as api:
            local.extract(EXCERPT, "Section 8.2(c)", "Deal", "1", "1", "gpt-test", None, "openai",
                          rule_kind=engine.PRIORITY_OF_PAYMENTS)
        schema = api.call_args.args[4]
        self.assertEqual((api.call_args.kwargs["max_tokens"], api.call_args.kwargs["timeout"]), (8192, 120))
        self.assertNotIn("maxItems", json.dumps(schema))
        self.assertIn("[P2] (i) first", api.call_args.args[3])

    def test_approval_binds_the_reviewed_steps(self):
        draft, _ = draft_with(PROPOSAL)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.json"
            raw = local.save(path, draft)
            rule_id = local.approve(tmp, path, "reviewer", hashlib.sha256(raw).hexdigest(), effective_from="2026-06-15")
            rule, receipt = local.load_approved_rule(tmp, rule_id)
            self.assertEqual(rule["steps"], draft["steps"])
            self.assertEqual(engine.expected_payout(rule, {"cash": 1_000, "servicing_fee": 400,
                                                           "indenture_trustee_fees": 1_000, "owner_trustee_fees": 200}),
                             {**dict.fromkeys(engine.waterfall_payees(rule), 0),
                              "servicing_fee": 400, "indenture_trustee_fees": 500, "owner_trustee_fees": 100})
            tampered = json.loads(raw)
            tampered["steps"][3]["evidence"] = "(iv) fourth, the Class B Interest;"
            raw = local.save(path, tampered)
            with self.assertRaisesRegex(ValueError, "Invalid draft evidence"):
                local.approve(tmp, path, "reviewer", hashlib.sha256(raw).hexdigest())


class Intake(unittest.TestCase):
    def test_a_general_rule_needs_an_exact_selection(self):
        found = intake.suggest_waterfall(SOURCE, "synthetic.txt", "\n" + EXCERPT + "\n")
        self.assertEqual(found["kind"], "priority_of_payments")
        self.assertEqual(found["excerpt"], EXCERPT)
        self.assertEqual(found["name"], "Synthetic Auto Receivables Trust 2026-1")
        self.assertEqual(found["clause"], "Source lines 6–23")
        for selection in ("", "   ", "Not in the contract", "Section 8", "x" * 32001):
            with self.subTest(selection=selection[:20]), self.assertRaises(ValueError):
                intake.suggest_waterfall(SOURCE, "synthetic.txt", selection)

    def test_automatic_detection_still_finds_only_single_clause_rules(self):
        # The general rule is opt-in. Automatic intake keeps proposing the two adjacent steps.
        for selection in (None, EXCERPT):
            found = intake.suggest(SOURCE, "synthetic.txt", selection)
            self.assertEqual((found["kind"], found["clause"]), ("payment_priority", "Source lines 15–17"))


class FakeGemini(unittest.TestCase):
    def test_test_double_reads_the_synthetic_priority_of_payments(self):
        server = FakeVertex(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        gemini = Gemini("pilot-local", "us-central1", "gemini-test", f"http://127.0.0.1:{server.server_port}")
        draft = local.extract(EXCERPT, "Section 8.2(c)", "Synthetic", "1", "1", gemini.model, None, "gemini",
                              rule_kind=engine.PRIORITY_OF_PAYMENTS, gemini=gemini)
        self.assertEqual([[p["label"] for p in s["payees"]] for s in draft["steps"]],
                         [p for p in ([s["payees"] for s in PROPOSAL["steps"]])])
        self.assertEqual([s["limit"] for s in draft["steps"]], ["due"] * 7 + ["remainder"])
        sent = server.requests[0]["body"]
        # The selection is the entire user message, and the response format is the general rule.
        self.assertEqual(sent["contents"], [{"role": "user", "parts": [{"text": EXCERPT}]}])
        self.assertIn("steps", sent["generationConfig"]["responseJsonSchema"]["properties"])

    def test_existing_fixture_answers_are_unchanged(self):
        ford = json.loads((ROOT / "abs_waterfall_check" / "web_examples_data" / "ford" / "draft.json")
                          .read_text())["support"]
        body = lambda text, properties: {"contents": [{"parts": [{"text": text}]}],
                                         "generationConfig": {"responseJsonSchema": {"properties": properties}}}
        self.assertEqual(fixture_proposal(body(ford, {"order": {}, "evidence": {}})),
                         {"order": ["class_a_principal", "class_b_interest"],
                          "evidence": {"class_a_principal": "P1", "class_b_interest": "P2"}})
        self.assertEqual(fixture_proposal(body("No charges here.", {"recipient": {}, "evidence": {}})),
                         {"recipient": "", "evidence": ""})


if __name__ == "__main__":
    unittest.main()
