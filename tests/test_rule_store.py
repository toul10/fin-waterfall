import hashlib
import tempfile
import unittest
from pathlib import Path

from abs_waterfall_check.local import approve, extract, load, load_approved_rule, register, save


class RuleStoreTests(unittest.TestCase):
    def test_registered_audit_binds_the_approved_rule_bytes_to_the_exact_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clause = "Synthetic Agreement §2.1(a)–(b)"
            draft_path = root / "draft.json"
            draft = extract("Pay Class A principal before Class B interest.",
                            clause, "Synthetic", "1", "1")
            save(draft_path, draft)
            draft_digest = hashlib.sha256(draft_path.read_bytes()).hexdigest()
            rule_id = approve(root, draft_path, "Analyst", draft_digest)
            approved_path = root / "rules" / f"{rule_id}.json"
            approved_bytes = approved_path.read_bytes()
            rule, provenance = load_approved_rule(root, rule_id)
            self.assertEqual(rule["draft_sha256"], draft_digest)
            self.assertEqual(provenance, {
                "rule_id": rule_id,
                "approved_rule_sha256": hashlib.sha256(approved_bytes).hexdigest(),
                "draft_sha256": draft_digest,
                "clause": clause,
            })
            scenario_path = root / "scenario.json"
            save(scenario_path, {"cash": 500, "class_a_principal": 500, "class_b_interest": 500})
            run_id, status = register(root, rule_id, scenario_path, "good")
            self.assertEqual(status, "PASS")
            self.assertEqual(load(root / "runs" / f"{run_id}.json")["rule_provenance"], provenance)

            # Reformatting the same JSON changes its approved bytes.
            approved_path.write_bytes(approved_bytes + b"\n")
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_approved_rule(root, rule_id)
            run_id, status = register(root, rule_id, scenario_path, "good")
            self.assertEqual(status, "HOLD")
            self.assertNotIn("rule_provenance", load(root / "runs" / f"{run_id}.json"))

            approved_path.write_bytes(approved_bytes)
            (root / "approvals" / f"{rule_id}.json").unlink()
            with self.assertRaisesRegex(ValueError, "Missing approved rule or approval receipt"):
                load_approved_rule(root, rule_id)
