import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
PRINCIPAL = "class_a_principal"
INTEREST = "class_b_interest"


class ApprovedReleaseTests(unittest.TestCase):
    def cli(self, code, *args):
        result = subprocess.run(
            [sys.executable, "-m", "abs_waterfall_check", *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout.strip()

    def approve_rule(self, store, fixture, order):
        excerpt = (FIXTURES / fixture).read_text(encoding="utf-8")
        draft = store / f"{fixture}.draft.json"
        clause = "Section 4.2"
        draft.write_text(json.dumps({
            "schema": 1,
            "deal": "Synthetic comparison",
            "deal_version": "1",
            "rule_version": "1",
            "clause": clause,
            "order": order,
            "support": excerpt.strip(),
            "contract_excerpt": excerpt,
            "extractor": "CI inert fixture",
        }), encoding="utf-8")
        digest = hashlib.sha256(draft.read_bytes()).hexdigest()
        rule_id = self.cli(0, "approve", draft, "--sha256", digest,
                           "--reviewer", "CI fixture reviewer", "--store", store)
        rule_path = store / "rules" / f"{rule_id}.json"
        self.assertEqual(json.loads(rule_path.read_bytes())["order"], order)
        return {
            "rule_id": rule_id,
            "clause": clause,
            "draft_sha256": digest,
            "approved_rule_sha256": hashlib.sha256(rule_path.read_bytes()).hexdigest(),
        }

    def assert_provenance(self, report, expected):
        provenance = report["rule_provenance"]
        self.assertEqual({key: provenance[key] for key in expected}, expected)

    def test_reverse_approval_changes_expected_payouts(self):
        correct = "json:" + shlex.join([sys.executable, str(FIXTURES / "json_payment_adapter.py"), "good"])
        reversed_order = "json:" + shlex.join([
            sys.executable, str(FIXTURES / "json_payment_adapter.py"), "faulty",
        ])
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            forward = self.approve_rule(store, "synthetic-private-contract.txt", [PRINCIPAL, INTEREST])
            reverse = self.approve_rule(store, "synthetic-reversed-priority.txt", [INTEREST, PRINCIPAL])
            case = store / "case.json"
            self.cli(1, "search", "--store", store, "--rule-id", reverse["rule_id"],
                     "--adapter", correct, "--output", case)
            search_report = json.loads(case.read_bytes())
            self.assert_provenance(search_report, reverse)
            regression = search_report["regression"]
            self.assertEqual(regression["scenario"]["cash"], 5_000_000)
            self.assertEqual(regression["expected"], {PRINCIPAL: 0, INTEREST: 5_000_000})
            self.assertEqual(regression["candidate"], {PRINCIPAL: 5_000_000, INTEREST: 0})

            for code, status, candidate in ((0, "PASS", reversed_order), (1, "BLOCK", correct)):
                with self.subTest(command="check", status=status):
                    replay = json.loads(self.cli(code, "check", case, "--store", store,
                                                 "--rule-id", reverse["rule_id"], "--adapter", candidate))
                    self.assertEqual(replay["status"], status)
                    self.assertEqual(replay["expected"], regression["expected"])
                    self.assert_provenance(replay, reverse)
            self.cli(2, "check", case, "--store", store, "--rule-id", forward["rule_id"],
                     "--adapter", correct)

            scenario = store / "scenario.json"
            scenario.write_text(json.dumps(regression["scenario"]), encoding="utf-8")
            registered = (
                (forward, 0, "PASS", {PRINCIPAL: 5_000_000, INTEREST: 0}),
                (reverse, 1, "BLOCK", {PRINCIPAL: 0, INTEREST: 5_000_000}),
            )
            for provenance, code, status, expected in registered:
                with self.subTest(command="register", status=status):
                    receipt = json.loads(self.cli(code, "register", scenario, "--store", store,
                                                   "--rule-id", provenance["rule_id"], "--adapter", correct))
                    self.assertEqual(receipt["status"], status)
                    report = json.loads((store / "runs" / f"{receipt['run_id']}.json").read_bytes())
                    self.assertEqual(report["expected"], expected)
                    self.assert_provenance(report, provenance)
