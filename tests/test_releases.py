import copy
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check import local, releases
from abs_waterfall_check.engine import good, faulty


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        draft = Path(__file__).resolve().parents[1] / "abs_waterfall_check/web_examples_data/ford/draft.json"
        self.rule_id = local.approve(self.root, draft, "test", hashlib.sha256(draft.read_bytes()).hexdigest())
        self.old = {"id": "old", "version": "1", "endpoint": "http://127.0.0.1:9000/calculate"}
        self.new = {**self.old, "id": "new", "version": "2"}

    def run_models(self, fn, **kwargs):
        with patch.object(releases, "calculate", side_effect=fn):
            result = releases.check_release(self.root, self.rule_id, self.old, self.new, **kwargs)
        self.assertEqual(local.status_for(self.root, result["run_id"]), result["status"])
        return result

    def test_release_replay_and_case_integrity(self):
        report = self.run_models(lambda m, s: (good if m == self.old else faulty)(s))
        self.assertEqual(report["status"], "BLOCK")
        self.assertGreater(len(report["replay_cases"]), 1)
        case = report["replay_cases"][-1]
        kwargs = dict(replay_case=case)
        with patch.object(releases, "calculate", side_effect=lambda m, s: good(s)):
            result = releases.check_release(self.root, self.rule_id, None, self.new, **kwargs)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([r["scenario"] for r in result["rows"]], [case["row"]["scenario"]])
        with tempfile.TemporaryDirectory() as portable:
            for name in ("rules", "approvals"):
                shutil.copytree(self.root / name, Path(portable) / name)
            with patch.object(releases, "calculate", side_effect=lambda m, s: good(s)):
                imported = releases.check_release(portable, self.rule_id, None, self.new, replay_case=case)
            self.assertEqual(imported["status"], "PASS")
        altered = copy.deepcopy(case)
        altered["row"]["label"] = "changed"
        altered["case_sha256"] = releases.digest({k:v for k,v in altered.items() if k != "case_sha256"})
        self.assertEqual(self.run_models(lambda m,s: good(s), replay_case=altered)["status"], "HOLD")

    def test_block_over_incomplete_and_baseline_fix(self):
        def model(m, scenario):
            if scenario["cash"] == 0:
                raise TimeoutError("timed out")
            return (good if m == self.old else faulty)(scenario)
        report = self.run_models(model)
        self.assertEqual(report["status"], "BLOCK")
        self.assertTrue(report["incomplete"])
        fixed = self.run_models(lambda m,s: (faulty if m == self.old else good)(s))
        self.assertEqual(fixed["status"], "PASS")
        self.assertTrue(fixed["baseline_failures"])

    def test_contract_delta_is_independent_of_current_payout(self):
        report = self.run_models(lambda m, s: faulty(s), case_ids=["boundary-shortfall"])
        row = report["rows"][0]
        expected_delta = {"class_a_principal": -5_000_000, "class_b_interest": 5_000_000}
        self.assertEqual(row["delta_cents"], expected_delta)
        self.assertEqual(row["candidate_minus_current_cents"], dict.fromkeys(expected_delta, 0))

        def missing_current(model, scenario):
            if model == self.old:
                raise TimeoutError("current timed out")
            return faulty(scenario)

        report = self.run_models(missing_current, case_ids=["boundary-shortfall"])
        self.assertEqual(report["status"], "BLOCK")
        row = report["rows"][0]
        self.assertEqual(row["delta_cents"], expected_delta)
        self.assertEqual(row["candidate_minus_current_cents"], {})

    def test_old_case_with_current_relative_delta_can_still_replay(self):
        report = self.run_models(lambda m, s: faulty(s), case_ids=["boundary-shortfall"])
        case = copy.deepcopy(report["replay_case"])
        case["row"]["delta_cents"] = case["row"].pop("candidate_minus_current_cents")
        case["case_sha256"] = releases.digest({k: v for k, v in case.items() if k != "case_sha256"})
        # Recreate a captured run in the original persisted format.
        report.update(replay_case=case, replay_cases=[case])
        local.save(self.root / "runs" / (report["run_id"] + ".json"), report)
        with patch.object(releases, "calculate", side_effect=lambda m, s: good(s)):
            replay = releases.check_release(self.root, self.rule_id, None, self.new, replay_case=case)
        self.assertEqual(replay["status"], "PASS")
        self.assertTrue(all(delta == 0 for delta in replay["rows"][0]["delta_cents"].values()))

    def test_missing_input_and_non_cent_payout(self):
        held = self.run_models(lambda m,s: good(s), cases=[{"id":"missing", "scenario":{}}],
                               case_ids=["missing"])
        self.assertEqual(held["status"], "HOLD")
        self.assertIn("inputs", held["action"])
        held = self.run_models(lambda m,s: dict.fromkeys(good(s), False))
        self.assertEqual(held["status"], "HOLD")

    def test_response_contract_and_destination(self):
        from unittest.mock import MagicMock
        import json
        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        scenario = {"collected_prepayment_charges": 100}
        with patch.object(releases, "HTTPConnection", return_value=conn) as dial:
            response.read.return_value = b'{"model_id":"old","version":"wrong","payouts":{}}'
            with self.assertRaisesRegex(ValueError, "service reported"):
                releases.calculate(self.old, scenario)
            self.assertEqual(json.loads(conn.request.call_args.args[2]), scenario)
            self.assertEqual(dial.call_args.args[0], "127.0.0.1")
            response.read.return_value = b'{"model_id":"old","version":"1","payouts":{"class_p":0,"class_p":1}}'
            with self.assertRaisesRegex(ValueError, "Duplicate JSON field"):
                releases.calculate(self.old, scenario)
            response.read.return_value = b'{"model_id":"old","version":"1","payouts":{},"unexpected":1}'
            with self.assertRaises(ValueError):
                releases.calculate(self.old, scenario)
            response.status = 302
            with self.assertRaisesRegex(ValueError, "302"):
                releases.calculate(self.old, scenario)
            for host in ("example.com", "127.0.0.1.example.com", "user@127.0.0.1"):
                with self.assertRaises(ValueError):
                    releases.calculate({**self.old,"endpoint":f"http://{host}:9000/"}, scenario)

    def test_model_service_rejects_malformed_scenarios(self):
        from abs_waterfall_check.model_service import parse_scenario
        for raw in (
            '{"collected_prepayment_charges":true}', '{"collected_prepayment_charges":0.1}',
            '{"collected_prepayment_charges":-1}', '{"collected_prepayment_charges":"1"}',
            '{"collected_prepayment_charges":0,"extra":0}', '{"cash":1}',
            '{"cash":0,"cash":1,"class_a_principal":1,"class_b_interest":1}',
            '{"cash":0,"class_a_principal":false,"class_b_interest":1}', '[]',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_scenario(raw)

    def test_invalid_selections_hold(self):
        for case_ids in ("boundary-zero", [], [True], ["bad"], ["boundary-zero", "boundary-zero"]):
            with self.subTest(case_ids=case_ids):
                result = self.run_models(lambda m,s: good(s), case_ids=case_ids)
                self.assertEqual(result["status"], "HOLD")
                self.assertFalse(result["rows"])
        repeated = {**self.old, "endpoint": "http://127.0.0.1:9001/calculate"}
        with patch.object(releases, "calculate") as calculate:
            result = releases.check_release(self.root, self.rule_id, self.old, repeated)
            calculate.assert_not_called()
        self.assertEqual(result["status"], "HOLD")
        self.assertIn("different current and candidate", result["action"])

    def test_workspace_deal_uses_saved_worksheet_and_approval(self):
        from abs_waterfall_check.web import App
        from abs_waterfall_check.web_examples import seed_examples
        seed_examples(self.root)
        app = App(self.root)
        deal = app.detail("ford")
        scenario = {"cash": 200, "class_a_principal": 200, "class_b_interest": 100}
        sheet = app.run(deal, {"mode": "check", "distribution_date": "2026-02-02",
                               "adapter": "good", "scenario": scenario})
        case_id = "saved-" + sheet["run_id"]
        with patch.object(releases, "calculate", side_effect=lambda m, s: good(s)):
            report = releases.check_release(self.root, None, self.old, self.new,
                                            deal_id="ford", case_ids=[case_id])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["rows"][0]["scenario"], scenario)
        self.assertEqual(report["deal_name"], deal["name"])
        self.assertIn(report["run_id"], [r["run_id"] for r in app.get("/api/dashboard")["runs"]])
        app.draft_path("ford").write_text("{}")
        held = releases.check_release(self.root, None, self.old, self.new, deal_id="ford")
        self.assertEqual(held["status"], "HOLD")
        self.assertIn("digest", held["reason"])

    def test_class_p_cases(self):
        draft = Path(__file__).resolve().parents[1] / "abs_waterfall_check/web_examples_data/mastr/draft.json"
        self.rule_id = local.approve(self.root, draft, "test", hashlib.sha256(draft.read_bytes()).hexdigest())
        report = self.run_models(lambda m,s: {"class_p": s["collected_prepayment_charges"]})
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["rows"][1]["expected"], {"class_p":1})
