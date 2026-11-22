import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check import engine, local
from abs_waterfall_check.web import App
from abs_waterfall_check.web_examples import seed_examples


class DistributionApprovalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        seed_examples(self.root)
        self.app = App(self.root)
        self.deal = self.app.detail("ford")
        self.path = "/api/deals/ford/"
        self.inputs = {"mode": "check", "scenario": self.deal["cases"][0]["scenario"],
                       "payouts": {"class_a_principal": 0, "class_b_interest": 5_000_000},
                       "payout_purpose": "actual"}

    def check(self, day):
        return self.app.post(self.path + "runs", {**self.inputs, "distribution_date": day})

    def test_boundary_retains_exact_historical_terms_while_drafting_and_after_approval(self):
        old = self.check("2026-01-31")
        self.assertEqual(old["status"], "BLOCK")
        self.assertEqual(old["payout_purpose"], "actual")
        new_draft = {**self.deal["draft"], "order": list(reversed(engine.STEPS)), "rule_version": "2"}
        with patch("abs_waterfall_check.local.extract", return_value=new_draft):
            pending = self.app.post(self.path + "draft", {})
        self.assertFalse(pending["approved"])
        self.assertEqual(self.check("2026-01-31")["approved_rule_id"], old["approved_rule_id"])
        approved = self.app.post(self.path + "approve", {
            "sha256": pending["draft_sha256"], "reviewer": "Second reviewer",
            "confirmed": True, "effective_from": "2026-02-01"})
        self.app = App(self.root)
        self.assertEqual(len(approved["approval_versions"]), 2)
        self.assertEqual(self.check("2026-01-31")["approved_rule_id"], old["approved_rule_id"])
        at_boundary = self.check("2026-02-01")
        self.assertEqual(at_boundary["status"], "PASS")
        self.assertEqual(at_boundary["approved_rule_id"], approved["rule_id"])
        self.assertEqual(at_boundary["rule"]["effective_from"], "2026-02-01")
        planned = self.app.post(self.path + "allocation", {"distribution_date": "2026-02-01",
            "amount_cents": 5_000_000, "dues": {"class_b_interest": 5_000_000}})
        self.assertEqual(planned["payouts"], at_boundary["expected"])
        self.assertEqual(planned["rule_provenance"], at_boundary["rule_provenance"])
        previous = self.app.post(self.path + "allocation", {"distribution_date": "2026-01-31",
            "amount_cents": 5_000_000})
        self.assertEqual(previous["missing_field"], "class_a_principal")
        self.assertEqual(previous["rule_provenance"], old["rule_provenance"])
        replay = self.app.post(self.path + "runs", {"mode": "replay", "replay": old["replay_case"]})
        self.assertEqual(replay["status"], "BLOCK")
        self.assertEqual(replay["rule_provenance"], old["rule_provenance"])
        self.assertEqual(replay["payout_purpose"], "actual")

    def test_missing_uncovered_and_invalid_dates_preserve_submitted_values(self):
        for day in (None, "2025-10-31", "2026-02-29"):
            with self.subTest(day=day):
                held = self.check(day)
                self.assertEqual(held["status"], "HOLD")
                self.assertNotIn("approved_rule_id", held)
                self.assertIn("date", held["reason"])
                self.assertEqual(held["submitted_inputs"]["distribution_date"], day)
                self.assertEqual(held["submitted_inputs"]["payouts"], self.inputs["payouts"])

    def test_selected_approval_damage_never_falls_back_to_previous_terms(self):
        approved = self.app.post(self.path + "approve", {
            "sha256": self.deal["draft_sha256"], "reviewer": "Second reviewer",
            "confirmed": True, "effective_from": "2026-02-01"})
        rule_path = self.root / "rules" / (approved["rule_id"] + ".json")
        local.save(rule_path, {**local.load(rule_path), "effective_from": "2026-01-01"})
        held = self.check("2026-02-01")
        self.assertEqual(held["status"], "HOLD")
        self.assertNotIn("expected", held)
        plan = self.app.post(self.path + "allocation", {"distribution_date": "2026-02-01", "amount_cents": 0})
        self.assertEqual(plan["state"], "needs_approval")
        self.assertNotIn("payouts", plan)
        self.assertEqual(self.check("2026-01-31")["approved_rule_id"], self.deal["rule_id"])

    def test_undated_legacy_approval_requires_a_date_for_distributions(self):
        legacy = self.app.post(self.path + "approve", {
            "sha256": self.deal["draft_sha256"], "reviewer": "Legacy reviewer", "confirmed": True})
        self.assertTrue(legacy["approved"])
        self.assertEqual(self.check("2026-02-01")["status"], "HOLD")
        self.assertIn("effective date", self.check("2026-02-01")["reason"])
        self.assertEqual(self.app.post(self.path + "runs", {
            "mode": "check", "case_id": "ford-stress"})["approved_rule_id"], self.deal["rule_id"])

    def test_allocation_only_requests_obligations_reached_by_available_cash(self):
        request = {"distribution_date": "2026-01-31", "amount_cents": 5_000_000}
        def plan(**updates):
            return self.app.post(self.path + "allocation", {**request, **updates})

        self.assertEqual(plan()["missing_field"], "class_a_principal")
        self.assertEqual(plan(dues={"class_a_principal": 4_999_999})["missing_field"], "class_b_interest")
        for amount, dues, payouts, remainder in (
            (0, {}, [0, 0], 0),
            (5_000_000, {"class_a_principal": 10_000_000}, [5_000_000, 0], 0),
            (5_000_000, {"class_a_principal": 3_000_000, "class_b_interest": 500_000}, [3_000_000, 500_000], 1_500_000),
        ):
            with self.subTest(amount=amount, dues=dues):
                result = plan(amount_cents=amount, dues=dues)
                self.assertEqual(result["state"], "ready")
                self.assertEqual(result["payouts"], dict(zip(engine.STEPS, payouts)))
                self.assertEqual(result["remaining_cents"], remainder)
                self.assertEqual(result["scenario"], {"cash": amount, **dues})
                self.assertNotIn("status", result)
        self.assertEqual(list((self.root / "runs").glob("*.json")), [])

    def test_class_p_uses_qualifying_charges_and_rejects_unusable_amounts(self):
        path = "/api/deals/mastr/allocation"
        body = {"distribution_date": "2006-06-26", "amount_cents": 24_625_394}
        plan = self.app.post(path, body)
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(plan["payouts"], {"class_p": 24_625_394})
        self.assertEqual(plan["scenario"], {"collected_prepayment_charges": 24_625_394})
        self.assertEqual(plan["remaining_cents"], 0)
        self.assertEqual(plan["rule_provenance"], self.app.detail("mastr")["rule_provenance"])
        for amount in (None, False, -1, "12.30", 19.10, 2**53):
            with self.subTest(amount=amount):
                needs = self.app.post(path, {**body, "amount_cents": amount})
                self.assertEqual(needs["missing_field"], "amount_cents")
                self.assertNotIn("payouts", needs)
        self.assertEqual(self.app.post(path, {"amount_cents": 4})["missing_field"], "distribution_date")
        self.assertEqual(self.app.post(path, {**body, "distribution_date": "2006-01-01"})["state"], "needs_approval")
        with self.assertRaises(ValueError):
            self.app.post(path, {**body, "dues": {"class_a_principal": 0}})

    @patch("abs_waterfall_check.allocation_evidence.ford_period_dues")
    def test_source_only_supplies_reached_missing_dues(self, evidence):
        evidence.return_value = {"dues": dict(zip(engine.STEPS, (8, 3))), "label": "Period worksheet",
                                 "source_url": "https://www.sec.gov"}
        payload = {"distribution_date": "2026-01-31", "amount_cents": 10}
        first = self.app.post(self.path + "allocation", {**payload, "dues": {"class_a_principal": 9}})
        self.assertEqual(first["scenario"], {"cash": 10, "class_a_principal": 9, "class_b_interest": 3})
        self.assertEqual(first["period_evidence"]["fields"], ["class_b_interest"])
        later = self.app.post(self.path + "allocation", {**payload, "dues": {"class_a_principal": 10}})
        self.assertNotIn("period_evidence", later)
        self.assertNotIn("class_b_interest", later["scenario"])
        evidence.assert_called_once_with("ford", self.deal["rule_provenance"]["approved_rule_sha256"],
                                         "2026-01-31", 10)
