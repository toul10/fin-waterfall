import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIXTURE = ROOT / "fixtures" / "mastr-2006-he1-class-p.json"
RULE_KIND = "prepayment_charges_to_class_p"


class MastrClassPTests(unittest.TestCase):
    def cli(self, expected_code, *args):
        result = subprocess.run(
            [sys.executable, "-m", "abs_waterfall_check", *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return result.stdout.strip()

    def test_original_and_amended_payouts_use_approved_class_p_rule(self):
        deal = json.loads(SOURCE_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            excerpt = store / "excerpt.txt"
            excerpt.write_text(
                "Distribute all prepayment charges collected during the period to Class P.",
                encoding="utf-8",
            )
            clause = deal["rule_source"]["section"]
            draft = store / "draft.json"
            draft_digest = self.cli(
                0, "extract", excerpt, "--rule-kind", RULE_KIND,
                "--section", clause, "--deal", deal["deal"],
                "--deal-version", deal["deal_version"], "--rule-version", deal["rule_version"],
                "--output", draft,
            )
            self.assertEqual(draft_digest, hashlib.sha256(draft.read_bytes()).hexdigest())
            rule_id = self.cli(
                0, "approve", draft, "--sha256", draft_digest,
                "--reviewer", "CI fixture reviewer", "--store", store,
            )
            rule_bytes = (store / "rules" / f"{rule_id}.json").read_bytes()
            rule = json.loads(rule_bytes)
            self.assertEqual((rule["schema"], rule["type"], rule["recipient"]),
                             (2, RULE_KIND, "class_p"))
            provenance = {
                "rule_id": rule_id,
                "clause": clause,
                "draft_sha256": draft_digest,
                "approved_rule_sha256": hashlib.sha256(rule_bytes).hexdigest(),
            }

            inputs = {"collected_prepayment_charges": deal["collection"]["amount_cents"]}
            self.assertEqual(inputs["collected_prepayment_charges"], 24_625_394)
            scenario = store / "scenario.json"
            scenario.write_text(json.dumps(inputs), encoding="utf-8")
            for form, amount, status, code, delta in (
                ("original", 19_039_282, "BLOCK", 1, "-55861.12"),
                ("amended", 24_625_394, "PASS", 0, "0.00"),
            ):
                with self.subTest(filing=form):
                    observed = {"class_p": deal["observed"][form]["amount_cents"]}
                    self.assertEqual(observed["class_p"], amount)
                    payouts = store / f"{form}.json"
                    payouts.write_text(json.dumps(observed), encoding="utf-8")
                    receipt = json.loads(self.cli(
                        code, "register", scenario, "--store", store, "--rule-id", rule_id,
                        "--adapter", f"observed:{payouts}",
                    ))
                    self.assertEqual(receipt["status"], status)
                    report = json.loads((store / "runs" / f"{receipt['run_id']}.json").read_bytes())
                    self.assertEqual(report["status"], status)
                    self.assertEqual(report["rule_provenance"], provenance)
                    self.assertEqual(report["scenario"], inputs)
                    self.assertEqual(report["expected"], {"class_p": 24_625_394})
                    self.assertEqual(report["candidate"], observed)
                    self.assertEqual(report["delta_dollars"], {"class_p": delta})
