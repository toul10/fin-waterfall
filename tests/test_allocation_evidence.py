from pathlib import Path
import unittest

from abs_waterfall_check import engine, history, local
from abs_waterfall_check.allocation_evidence import ford_period_dues
from abs_waterfall_check.web_examples import DATA


class FiledFiguresTests(unittest.TestCase):
    def test_only_the_matching_period_cash_and_approval_supply_the_dues(self):
        folder = DATA / "ford"
        rule = local.load(folder / "rule.json")
        digest = local.load(folder / "approval.json")["approved_rule_sha256"]
        rows = local.load(Path(__file__).resolve().parents[1] / "fixtures/ford-history.json")
        self.assertEqual(set(local.load(folder / "statement-dues.json")),
                         {row["input"]["payment_date"] for row in rows})
        for row in rows:
            day = row["input"]["payment_date"]
            with self.subTest(distribution_date=day):
                period = history.replay(row, rule, lambda scenario: engine.expected_payout(rule, scenario))
                scenario = period["window"]["scenario"]
                cash = scenario["cash"]
                actual = ford_period_dues("ford", digest, day, cash)
                self.assertEqual(actual["dues"], {field: scenario[field] for field in engine.STEPS})
                self.assertEqual(actual["source_url"], row["source"])
                self.assertIsNone(ford_period_dues("ford", digest, day, cash - 1))
                self.assertIsNone(ford_period_dues("ford", digest, "2029-01-01", cash))
                self.assertIsNone(ford_period_dues("ford", "different rule", day, cash))
                self.assertIsNone(ford_period_dues("private-deal", digest, day, cash))
