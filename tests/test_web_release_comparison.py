import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check import engine
from abs_waterfall_check.web import App
from abs_waterfall_check.web_examples import seed_examples


class ReleaseWorkspaceTests(unittest.TestCase):
    def test_compare_export_replay_and_incomplete_payment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed_examples(root)
            app = App(root, pricing_models=[
                f"pricing@{v}=http://127.0.0.1:{9000+i}/calculate"
                for i, v in enumerate(("one", "two", "fixed"))])
            self.assertEqual(len(app.get("/api/releases")["models"]), 3)
            custom = app.post("/api/deals/ford/runs", {
                "mode": "check", "adapter": "good", "distribution_date": "2026-01-20",
                "scenario": {"cash": 10, "class_a_principal": 20, "class_b_interest": 10}})
            ids = {c["id"] for c in app.get("/api/releases/ford")["cases"]}
            self.assertIn("saved-" + custom["run_id"], ids)
            request = {"current_id": "pricing@one", "candidate_id": "pricing@two",
                       "case_ids": ["ford-stress", "saved-" + custom["run_id"]]}
            for invalid, advice in (({"candidate_id": "pricing@one"}, "different current and candidate"),
                                    ({"case_ids": []}, "Select at least one payment case")):
                held = app.post("/api/releases/ford/compare", {**request, **invalid})
                self.assertEqual(held["status"], "HOLD")
                self.assertIn(advice, held["action"])
                self.assertEqual(app.get("/api/runs/" + held["run_id"])["status"], "HOLD")
            with patch("abs_waterfall_check.releases.calculate", side_effect=lambda model, s:
                       (engine.faulty if model["version"] == "two" else engine.good)(s)):
                failed = app.post("/api/releases/ford/compare", request)
                self.assertEqual(failed["status"], "BLOCK")
                captured = app.get("/api/runs/" + failed["run_id"] + "/cases/1")
                self.assertEqual(captured["row"]["scenario"], custom["scenario"])
                corrected = app.post("/api/releases/ford/replay", {
                    "run_id": failed["run_id"], "index": 1, "candidate_id": "pricing@fixed"})
                self.assertEqual(corrected["status"], "PASS")
                self.assertEqual(corrected["rows"][0]["scenario"], custom["scenario"])
                held = app.post("/api/releases/ford/compare", {**request, "case_ids": ["missing-input"]})
                self.assertEqual(held["status"], "HOLD")
                self.assertEqual(app.get("/api/runs/" + held["run_id"])["status"], "HOLD")
                mixed = app.post("/api/releases/ford/compare", {
                    **request, "case_ids": ["ford-stress", "missing-input"]})
                summary = next(r for r in app.get("/api/dashboard")["runs"] if r["run_id"] == mixed["run_id"])
                self.assertEqual((summary["status"], summary["failed_case_count"], summary["incomplete_case_count"]),
                                 ("BLOCK", 1, 1))
                self.assertNotIn("rows", summary)
                self.assertNotIn("replay_cases", summary)
                stale = app.post("/api/releases/ford/compare", {**request, "rule_id": "other"})
                self.assertEqual(stale["status"], "HOLD")
                damaged = app.detail("ford")
                damaged["source_text"] = "changed"
                app.deal_path("ford").write_text(json.dumps(damaged))
                invalid = app.post("/api/releases/ford/compare", request)
                self.assertEqual(app.get("/api/runs/" + invalid["run_id"])["status"], "HOLD")
            self.assertNotIn("endpoint", json.dumps(app.get("/api/releases")))
