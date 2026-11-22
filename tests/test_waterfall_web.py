"""End-to-end workspace flow for a general priority of payments."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check import local, releases, report
from abs_waterfall_check.gemini import Gemini
from abs_waterfall_check.web import App, dashboard_run
from abs_waterfall_check.web_examples import seed_examples
from scripts.fake_gemini import FakeVertex
from tests.test_waterfall_drafting import EXCERPT, PROPOSAL, SOURCE

DUES = {"servicing_fee": 2_000_000, "indenture_trustee_fees": 300_000, "owner_trustee_fees": 100_000,
        "class_a_1_interest": 4_000_000, "class_a_2_interest": 6_000_000, "class_a_3_interest": 10_000_000,
        "first_priority_principal_payment": 0, "class_b_interest": 2_500_000, "reserve_account_deposit": 5_000_000,
        "regular_principal_payment": 90_000_000}
PAID = {**DUES, "regular_principal_payment": 70_100_000, "certificateholders": 0}


class WaterfallWorkspace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        seed_examples(self.root)
        self.app = App(self.root)
        model = patch.object(local, "_local_proposal", return_value=PROPOSAL)
        self.model = model.start()
        self.addCleanup(model.stop)

    def approved_deal(self):
        deal = self.app.post("/api/deals", {"name": "Synthetic Auto Receivables Trust 2026-1",
                                            "clause": "Section 8.2(c)", "kind": "priority_of_payments",
                                            "source_text": SOURCE})
        path = "/api/deals/" + deal["id"]
        drafted = self.app.post(path + "/draft", {"provider": "local", "excerpt": EXCERPT})
        self.assertEqual((drafted["draft"]["schema"], len(drafted["draft"]["steps"])), (3, 8))
        approved = self.app.post(path + "/approve", {"reviewer": "reviewer", "sha256": drafted["draft_sha256"],
                                                     "effective_from": "2026-06-01", "confirmed": True})
        self.assertTrue(approved["approved"])
        return path

    def check(self, path, payouts, scenario=None):
        return self.app.post(path + "/runs", {"mode": "check", "distribution_date": "2026-06-15",
                                              "payout_purpose": "actual", "adapter": "observed",
                                              "scenario": scenario or {"cash": 100_000_000, **DUES},
                                              "payouts": payouts})

    def test_allocation_asks_for_each_reached_step_then_allocates(self):
        path = self.approved_deal()
        ask = lambda dues: self.app.post(path + "/allocation", {"distribution_date": "2026-06-15",
                                                               "amount_cents": 100_000_000, "dues": dues})
        first = ask({})
        self.assertEqual((first["state"], first["missing_fields"]), ("needs_input", ["servicing_fee"]))
        trustees = ask({"servicing_fee": 2_000_000})
        self.assertEqual(trustees["missing_fields"], ["indenture_trustee_fees", "owner_trustee_fees"])
        self.assertIn("Indenture Trustee Fees, Owner Trustee Fees", trustees["reason"])
        ready = ask(DUES)
        self.assertEqual((ready["state"], ready["payouts"], ready["remaining_cents"]), ("ready", PAID, 0))
        self.assertEqual(ready["scenario"], {"cash": 100_000_000, **DUES})
        short = self.app.post(path + "/allocation", {"distribution_date": "2026-06-15", "amount_cents": 1_500_000,
                                                     "dues": {"servicing_fee": 2_000_000}})
        self.assertEqual((short["state"], short["payouts"]["servicing_fee"], short["remaining_cents"]),
                         ("ready", 1_500_000, 0))
        with self.assertRaisesRegex(ValueError, "Supply only the amounts due"):
            ask({"certificateholders": 1})
        early = self.app.post(path + "/allocation", {"distribution_date": "2026-05-15", "amount_cents": 1})
        self.assertEqual(early["state"], "needs_approval")

    def test_checks_pass_block_and_hold_with_named_payments(self):
        path = self.approved_deal()
        passed = self.check(path, PAID)
        self.assertEqual((passed["status"], passed["discrepancy_cents"], passed["affected_tranche"]), ("PASS", 0, ""))
        misdirected = {**PAID, "regular_principal_payment": 69_100_000, "certificateholders": 1_000_000}
        blocked = self.check(path, misdirected)
        self.assertEqual((blocked["status"], blocked["discrepancy_cents"]), ("BLOCK", 1_000_000))
        self.assertEqual(blocked["affected_tranche"], "Regular Principal Payment, Certificateholders")
        summary = dashboard_run(blocked)
        self.assertEqual(summary["largest_underpayment"]["tranche"], "regular_principal_payment")
        self.assertTrue(blocked["replay_available"])
        replay = self.app.get(f"/api/runs/{blocked['run_id']}/replay")
        replayed = self.app.post(path + "/runs", {"mode": "replay", "replay": replay})
        self.assertEqual(replayed["status"], "BLOCK")
        partial = dict(PAID)
        partial.pop("certificateholders")
        held = self.check(path, partial)
        self.assertEqual(held["status"], "HOLD")
        self.assertEqual(held["submitted_payouts"], partial)
        self.assertEqual(held["scenario"], {"cash": 100_000_000, **DUES})
        self.assertEqual(report.incomplete_reason(held),
                         "Complete these submitted payments in USD. Certificateholders payment.")
        missing_cash = self.check(path, PAID, scenario=dict(DUES))
        self.assertEqual(report.incomplete_reason(missing_cash),
                         "Complete these distribution fields in USD. Available Funds.")
        for run in (passed, blocked, held):
            self.assertTrue(report.distribution_pdf(run).startswith(b"%PDF-"))
        names, inputs, payments = report.labels(passed["rule"])
        self.assertEqual((names["class_a_2_interest"], inputs["cash"], inputs["reserve_account_deposit"],
                          payments["certificateholders"]),
                         ("Class A-2 Interest", "Available Funds", "Reserve Account Deposit due",
                          "Certificateholders payment"))
        self.assertNotIn("certificateholders", inputs)

    def test_pro_rata_cent_rounding_is_accepted_and_recorded(self):
        path = self.approved_deal()
        dues = {"servicing_fee": 0, "indenture_trustee_fees": 0, "owner_trustee_fees": 0,
                "class_a_1_interest": 1, "class_a_2_interest": 1, "class_a_3_interest": 1}
        scenario = {"cash": 2, **dues}
        expected = {**dict.fromkeys(PAID, 0), "class_a_1_interest": 1, "class_a_2_interest": 1}
        exact = self.check(path, expected, scenario)
        self.assertEqual((exact["status"], exact["expected"]), ("PASS", expected))
        other = {**expected, "class_a_1_interest": 0, "class_a_3_interest": 1}
        rounded = self.check(path, other, scenario)
        self.assertEqual((rounded["status"], rounded["rounding_accepted"]),
                         ("PASS", ["class_a_1_interest", "class_a_3_interest"]))
        self.assertEqual((rounded["discrepancy_cents"], rounded["affected_tranche"]), (0, ""))
        self.assertIsNone(dashboard_run(rounded)["largest_underpayment"])
        self.assertTrue(report.distribution_pdf(rounded).startswith(b"%PDF-"))

    def test_intake_requires_the_analyst_selection(self):
        body = {"source_text": SOURCE, "kind": "priority_of_payments", "amount_cents": 100_000_000,
                "distribution_date": "2026-06-15"}
        selection = self.app.post("/api/intake", body)
        self.assertEqual((selection["state"], selection["kind"]), ("select_excerpt", "priority_of_payments"))
        drafted = self.app.post("/api/intake", {**body, "excerpt": EXCERPT})
        self.assertEqual(drafted["state"], "drafted")
        deal = drafted["deal"]
        self.assertEqual((deal["kind"], deal["name"], deal["clause"], deal["draft"]["schema"]),
                         ("priority_of_payments", "Synthetic Auto Receivables Trust 2026-1", "Source lines 6–23", 3))
        self.assertEqual(deal["initial_allocation"], {"amount_cents": 100_000_000, "distribution_date": "2026-06-15"})
        # Without the explicit choice, intake proposes the single two-step clause as before.
        automatic = self.app.post("/api/intake", {"source_text": SOURCE})
        self.assertEqual(automatic["deal"]["kind"], "payment_priority")

    def test_releases_are_limited_to_single_clause_rules(self):
        path = self.approved_deal()
        deal_id = path.rsplit("/", 1)[1]
        self.assertNotIn(deal_id, [d["id"] for d in self.app.get("/api/releases")["deals"]])
        with self.assertRaisesRegex(ValueError, "Release comparisons cover the two single-clause rules"):
            self.app.get("/api/releases/" + deal_id)
        # The command line reaches the comparison without the workspace route.
        models = [{"id": "pricing", "version": v, "endpoint": "http://127.0.0.1:9/calculate"} for v in ("1", "2")]
        for target in ({"rule_id": self.app.get(path)["rule_id"]}, {"rule_id": None, "deal_id": deal_id}):
            with self.subTest(target=sorted(target)):
                refused = releases.check_release(self.root, target.pop("rule_id"), *models, **target)
                self.assertEqual((refused["status"], refused["reason"], refused["rows"]),
                                 ("HOLD", releases.SINGLE_CLAUSE_ONLY, []))

    def test_altered_approved_steps_invalidate_the_approval(self):
        path = self.approved_deal()
        deal_id = path.rsplit("/", 1)[1]
        rule_id = self.app.get(path)["rule_id"]
        rule_file = self.root / "rules" / f"{rule_id}.json"
        rule = json.loads(rule_file.read_text())
        rule["steps"][0]["payees"][0]["label"] = "Owner distribution"
        rule_file.write_text(json.dumps(rule))
        detail = self.app.get("/api/deals/" + deal_id)
        self.assertFalse(detail["approved"])
        self.assertEqual(self.check(path, PAID)["status"], "HOLD")



class HostedWaterfall(unittest.TestCase):
    """The tester path: quick intake, exact-excerpt consent, Gemini draft, approval and check."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        server = FakeVertex(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.server = server
        gemini = Gemini("pilot-local", "us-central1", "gemini-test", f"http://127.0.0.1:{server.server_port}")
        self.app = App(temp.name, gemini=gemini)

    def test_consented_gemini_draft_is_approved_and_checked(self):
        body = {"source_text": SOURCE, "kind": "priority_of_payments", "amount_cents": 100_000_000,
                "distribution_date": "2026-06-15"}
        self.assertEqual(self.app.post("/api/intake", body, actor="alice")["state"], "select_excerpt")
        saved = self.app.post("/api/intake", {**body, "excerpt": EXCERPT}, actor="alice")
        self.assertEqual((saved["state"], saved["deal"]["kind"], saved["deal"]["suggested_excerpt"]),
                         ("consent_needed", "priority_of_payments", EXCERPT))
        path = "/api/deals/" + saved["deal"]["id"]
        grant = self.app.post(path + "/draft-preview", {"excerpt": EXCERPT}, actor="alice")
        self.assertEqual((grant["excerpt"], list(self.server.requests)), (EXCERPT, []))
        drafted = self.app.post(path + "/draft", {"provider": "gemini", "consent_token": grant["consent_token"],
                                                  "confirmed": True, "excerpt": EXCERPT}, actor="alice")
        self.assertEqual([[p["label"] for p in s["payees"]] for s in drafted["draft"]["steps"]],
                         [s["payees"] for s in PROPOSAL["steps"]])
        sent = self.server.requests[0]["body"]
        self.assertEqual(sent["contents"], [{"role": "user", "parts": [{"text": EXCERPT}]}])
        self.assertEqual(sent["generationConfig"]["maxOutputTokens"], 16384)
        self.app.post(path + "/approve", {"reviewer": "reviewer", "sha256": drafted["draft_sha256"],
                                          "effective_from": "2026-06-01", "confirmed": True}, actor="reviewer")
        run = self.app.post(path + "/runs", {"mode": "check", "distribution_date": "2026-06-15",
                                             "payout_purpose": "actual", "adapter": "observed",
                                             "scenario": {"cash": 100_000_000, **DUES}, "payouts": PAID})
        self.assertEqual(run["status"], "PASS")

    def test_single_clause_requests_keep_their_output_budget(self):
        ford = json.loads((Path(__file__).resolve().parents[1] / "abs_waterfall_check" / "web_examples_data"
                           / "ford" / "draft.json").read_text())["support"]
        deal = self.app.post("/api/deals", {"name": "Ford", "clause": "Section 8.2(c)", "kind": "payment_priority",
                                            "source_text": ford})
        path = "/api/deals/" + deal["id"]
        grant = self.app.post(path + "/draft-preview", {"excerpt": ford}, actor="alice")
        drafted = self.app.post(path + "/draft", {"provider": "gemini", "consent_token": grant["consent_token"],
                                                  "confirmed": True, "excerpt": ford}, actor="alice")
        self.assertEqual(drafted["draft"]["order"], ["class_a_principal", "class_b_interest"])
        self.assertEqual(self.server.requests[0]["body"]["generationConfig"]["maxOutputTokens"], 4096)


if __name__ == "__main__":
    unittest.main()
