import hashlib
import tempfile
import unittest
from pathlib import Path

from abs_waterfall_check import local
from abs_waterfall_check.web import App, case_digest
from abs_waterfall_check.web_examples import seed_examples


class WebWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        seed_examples(self.root)
        self.app = App(self.root)

    def test_bundled_verdicts_bind_exact_draft_and_rule_and_survive_restart(self):
        for deal_id, case_id, status, difference in (
            ("ford", "ford-stress", "BLOCK", 5_000_000),
            ("mastr", "mastr-original", "BLOCK", 5_586_112),
            ("mastr", "mastr-amended", "PASS", 0),
        ):
            with self.subTest(case=case_id):
                run = self.app.post(f"/api/deals/{deal_id}/runs", {"mode": "check", "case_id": case_id})
                self.assertEqual((run["status"], run["discrepancy_cents"]), (status, difference))
                receipt = run["rule_provenance"]
                rule_file = self.root / "rules" / f"{receipt['rule_id']}.json"
                draft_file = self.root / "drafts" / f"{deal_id}.json"
                self.assertEqual(receipt["approved_rule_sha256"], hashlib.sha256(rule_file.read_bytes()).hexdigest())
                self.assertEqual(receipt["draft_sha256"], hashlib.sha256(draft_file.read_bytes()).hexdigest())
                self.assertEqual(App(self.root).get(f"/api/runs/{run['run_id']}/audit"), run)
                self.assertEqual(local.status_for(self.root, run["run_id"]), status)

    def test_exported_block_replays_against_same_approval_in_fresh_store(self):
        endpoint = "/api/deals/ford/runs"
        blocked = self.app.post(endpoint, {"mode": "check", "case_id": "ford-stress"})
        case = self.app.get(f"/api/runs/{blocked['run_id']}/replay")
        with tempfile.TemporaryDirectory() as destination:
            seed_examples(destination)
            fresh = App(destination)
            replay = fresh.post(endpoint, {"mode": "replay", "replay": case})
            self.assertEqual(replay["status"], "BLOCK")
            self.assertEqual(replay["candidate"], blocked["candidate"])
            self.assertEqual(replay["rule_provenance"], blocked["rule_provenance"])
            corrected = fresh.post(endpoint, {"mode": "replay", "replay": case, "adapter": "good"})
            self.assertEqual(corrected["status"], "PASS")
            changed = {**case, "scenario": {**case["scenario"], "cash": 0}}
            self.assertEqual(fresh.post(endpoint, {"mode": "replay", "replay": changed})["status"], "HOLD")

    def test_dashboard_summarizes_underpayment_and_distinguishes_review_inputs(self):
        endpoint = "/api/deals/ford/runs"
        body = {"mode": "check", "case_id": "ford-stress"}
        blocked = self.app.post(endpoint, body)
        repeated = self.app.post(endpoint, body)
        passed = self.app.post(endpoint, {**body, "adapter": "good"})
        replay = self.app.post(endpoint, {"mode": "replay", "replay": blocked["replay_case"]})
        hold = self.app.post(endpoint, {**body, "adapter": "missing"})
        rows = {r["run_id"]: r for r in self.app.get("/api/dashboard")["runs"]}
        row = rows[blocked["run_id"]]
        self.assertEqual(row["largest_underpayment"], {
            "tranche": "class_a_principal", "amount_cents": 5_000_000,
            "expected_cents": 5_000_000, "proposed_cents": 0})
        self.assertEqual(row["review_scope"], rows[repeated["run_id"]]["review_scope"])
        for run in (passed, replay, hold):
            self.assertNotEqual(row["review_scope"], rows[run["run_id"]]["review_scope"])
        for run in (passed, hold):
            self.assertIsNone(rows[run["run_id"]]["largest_underpayment"])
        self.assertEqual(rows[hold["run_id"]]["reason"], "Unknown local candidate adapter")

    def test_damaged_or_missing_receipt_holds_check(self):
        deal = self.app.get("/api/deals/mastr")
        rule_id = deal["rule_provenance"]["rule_id"]
        receipt_file = self.root / "approvals" / f"{rule_id}.json"
        receipt = local.load(receipt_file)
        local.save(receipt_file, {**receipt, "approved_rule_sha256": "0" * 64})
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    receipt_file.unlink()
                run = self.app.post("/api/deals/mastr/runs", {"mode": "check", "case_id": "mastr-amended"})
                self.assertEqual(run["status"], "HOLD")
                self.assertTrue(run["reason"])
                self.assertFalse(run["replay_available"])
                self.assertEqual(local.status_for(self.root, run["run_id"]), "HOLD")

    def test_filed_collection_and_reporting_context_follow_the_actual_inputs(self):
        endpoint = "/api/deals/mastr/runs"
        filed = self.app.post(endpoint, {"mode": "check", "case_id": "mastr-original"})
        self.assertTrue(filed["filed_payout"])
        self.assertTrue(filed["retrospective"])
        case = filed["replay_case"]
        replay = self.app.post(endpoint, {"mode": "replay", "replay": case})
        self.assertTrue(replay["filed_payout"])
        modeled = self.app.post(endpoint, {"mode": "replay", "replay": case,
                                           "payouts": {"class_p": 1}})
        self.assertFalse(modeled["filed_payout"])
        self.assertTrue(modeled["retrospective"])
        self.assertEqual([s["label"] for s in modeled["filing_sources"]],
                         ["Class P entitlement", "Corrected collection"])
        custom = self.app.post(endpoint, {"mode": "check",
                               "scenario": {"collected_prepayment_charges": 2},
                               "payouts": {"class_p": 1}})
        self.assertIsNone(custom["retrospective"])
        legacy = {**custom, "retrospective": filed["retrospective"]}
        local.save(self.app.run_path(custom["run_id"]), legacy)
        path = "/api/runs/" + custom["run_id"]
        self.assertIsNone(self.app.get(path)["retrospective"])
        self.assertEqual(self.app.get(path + "/audit"), legacy)

    def test_distribution_period_separates_identical_checks_and_survives_replay(self):
        endpoint = "/api/deals/mastr/runs"
        body = {"mode": "check", "scenario": {"collected_prepayment_charges": 2},
                "payouts": {"class_p": 1}}
        first, second = [self.app.post(endpoint, {**body, "distribution_date": d})
                         for d in ("2025-01-30", "2025-02-28")]
        scopes = {r["run_id"]: r["review_scope"] for r in self.app.get("/api/dashboard")["runs"]}
        self.assertNotEqual(scopes[first["run_id"]], scopes[second["run_id"]])
        replay = self.app.post(endpoint, {"mode": "replay", "replay": first["replay_case"]})
        self.assertEqual(replay["distribution_date"], "2025-01-30")
        held = self.app.post(endpoint, {**body, "distribution_date": "2025-02-30"})
        self.assertEqual(held["status"], "HOLD")
        self.assertEqual(held["submitted_payouts"], body["payouts"])
        self.assertEqual(held["scenario"], body["scenario"])
        partial = self.app.post("/api/deals/ford/runs", {"mode": "check",
            "scenario": {"cash": 0, "class_b_interest": -1}, "payouts": {"class_a_principal": 1}})
        self.assertEqual(partial["scenario"], {"cash": 0})
        self.assertEqual(partial["submitted_payouts"], {"class_a_principal": 1})

    def test_legacy_filed_replay_recovers_distribution_date_from_bundled_evidence(self):
        endpoint = "/api/deals/mastr/runs"
        run = self.app.post(endpoint, {"mode": "check", "case_id": "mastr-original"})
        legacy = {k: v for k, v in run["replay_case"].items()
                  if k not in ("collection_case_id", "filed_payout", "distribution_date")}
        legacy["case_sha256"] = case_digest(legacy)
        with tempfile.TemporaryDirectory() as destination:
            seed_examples(destination)
            fresh = App(destination)
            replay = fresh.post(endpoint, {"mode": "replay", "replay": legacy})
            old_report = {k: v for k, v in replay.items() if k not in (
                "distribution_date", "collection_case_id", "replayed_filed_payout", "filed_payout")}
            fresh_path = "/api/runs/" + replay["run_id"]
            local.save(fresh.run_path(replay["run_id"]), old_report)
            self.assertEqual(fresh.get(fresh_path)["distribution_date"], "2006-06-26")
            self.assertFalse(fresh.get(fresh_path)["filed_payout"])
            self.assertEqual(fresh.get(fresh_path + "/audit"), old_report)
        self.assertTrue(replay["filed_payout"])
        self.assertEqual(replay["distribution_date"], "2006-06-26")
