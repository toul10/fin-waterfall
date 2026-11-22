import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from abs_waterfall_check import engine, intake, local
from abs_waterfall_check.web import App

ROOT = Path(__file__).resolve().parents[1]


def example(deal):
    file = ROOT / "abs_waterfall_check" / "web_examples_data" / deal / "draft.json"
    return json.loads(file.read_text())["support"]


class IntakeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name)
        self.app = App(self.store, model_token="private-test-token")

    def test_new_contract_recovers_name_order_and_exact_local_review_excerpt(self):
        quote = example("ford")
        text = f"Ford Credit Auto Owner Trust 2025-C\n\nTerms of the indenture.\n\n{quote}\n\nLater payments."
        raw = text.replace("\n", "\r\n").encode()
        body = {"file": {"name": "indenture.txt", "content_base64": base64.b64encode(raw).decode()},
                "amount_cents": 5000000, "distribution_date": "2026-05-15", "provider": "openai"}
        with patch.object(local, "_local_proposal", return_value={
                "order": list(engine.STEPS), "evidence": {engine.STEPS[0]: "P1", engine.STEPS[1]: "P2"}}) as qwen:
            result = self.app.post("/api/intake", body)
        self.assertEqual(result["state"], "drafted")
        self.assertEqual(qwen.call_args.args[2], "llama.cpp")
        self.assertEqual(qwen.call_args.args[-1], "private-test-token")
        deal = self.app.detail(result["deal"]["id"])
        self.assertFalse(deal["approved"])
        self.assertEqual(deal["source_text"], text)
        self.assertEqual(deal["draft"]["support"], quote)
        self.assertEqual(deal["name"], "Ford Credit Auto Owner Trust 2025-C")
        self.assertEqual(deal["clause"], "Source lines 5–7")
        self.assertEqual(deal["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(deal["initial_allocation"], {"amount_cents": 5000000, "distribution_date": "2026-05-15"})

    def test_multiple_candidates_require_a_choice_with_original_context(self):
        ford, mastr = example("ford"), example("mastr")
        text = ford + "\n\nAnother provision.\n\n" + mastr
        app = App(self.store, local_drafting=False)
        response = app.post("/api/intake", {"source_text": text})
        self.assertEqual(response["state"], "select_excerpt")
        self.assertEqual(response["source_text"], text)
        self.assertFalse(list((self.store / "deals").glob("*.json")))
        response = app.post("/api/intake", {"source_text": text, "excerpt": mastr})
        self.assertEqual(response["state"], "draft_needed")
        self.assertEqual(response["deal"]["suggested_excerpt"], mastr)
        self.assertEqual(response["deal"]["kind"], engine.PREPAYMENT_CHARGES_TO_CLASS_P)
        self.assertEqual(response["deal"]["source_text"], text)
        self.assertNotIn("initial_allocation", response["deal"])
        with self.assertRaises(ValueError):
            app.post("/api/intake", {"source_text": text, "excerpt": "Words not in the contract"})

    def test_gaps_qualifiers_and_ineligible_funds_get_no_draft(self):
        ford = example("ford")
        for text in [ford.replace("(v) fifth", "(vii) seventh"),
                     ford.replace("(v) fifth", "(v) unless default occurs, fifth"),
                     "All prepayment charges collected this period will be paid to Class Z.",
                     "Allocate all prepayment charges received to Class P, except on default.",
                     "Allocate some of the prepayment charges collected to Class P.",
                     "Class B interest is reported alongside Class A first-priority principal."]:
            with self.subTest(text=text):
                self.assertIn("reason", intake.suggest(text, "Pasted text"))
        reversed_text = (ROOT / "fixtures" / "synthetic-reversed-priority.txt").read_text()
        self.assertEqual(intake.suggest(reversed_text, "Pasted text")["kind"], engine.PAYMENT_PRIORITY)

    def test_bad_intake_data_leaves_no_new_deal(self):
        text = example("mastr")
        for fields in ({"amount_cents": -1}, {"amount_cents": True}, {"amount_cents": 2**53},
                       {"distribution_date": "2026-02-30"}, {"file": {"name": "unknown.exe"}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.app.post("/api/intake", {"source_text": text, **fields})
        self.assertFalse(list((self.store / "deals").glob("*.json")))

    def test_neighboring_conditions_cannot_be_removed_by_narrowing_the_selection(self):
        ford, mastr = example("ford"), example("mastr")
        for full, quote in (("Unless an Event of Default has occurred:\n\n" + ford, ford),
                            (mastr + " However, on default these payments go to Class R.", mastr)):
            for selected in (None, quote):
                with self.subTest(selected=selected):
                    result = intake.suggest(full, "contract.pdf", selected)
                    self.assertIn("qualification", result["reason"])
        full = "If invoices are missing, contact the agent.\n\nTrust distributions.\n\n" + mastr
        self.assertEqual(intake.suggest(full, "contract.pdf")["kind"], engine.PREPAYMENT_CHARGES_TO_CLASS_P)
