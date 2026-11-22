"""General priority of payments (schema 3): validation, allocation and comparison."""
import copy
import random
import unittest

from abs_waterfall_check import engine


def make_rule(steps, funds_label="Available Funds"):
    """steps: [(limit, [(payee id, label), ...]), ...]"""
    return {
        "schema": 3, "type": "priority_of_payments", "rounding": "largest_remainder",
        "deal": "Synthetic Auto Trust 2026-1", "deal_version": "1", "rule_version": "test",
        "clause": "Section 8.2(c)", "approved_by": "reviewer", "funds_label": funds_label,
        "steps": [{"payees": [{"id": key, "label": label} for key, label in payees],
                   "limit": limit, "evidence": f"({index}) synthetic step"}
                  for index, (limit, payees) in enumerate(steps, 1)],
    }


# The worked example: eight steps, two pro rata groups and a residual.
AUTO = make_rule([
    ("due", [("servicing_fee", "Servicing Fee")]),
    ("due", [("indenture_trustee_fees", "Indenture Trustee fees"), ("owner_trustee_fees", "Owner Trustee fees")]),
    ("due", [("class_a_1_interest", "Class A-1 interest"), ("class_a_2_interest", "Class A-2 interest"),
             ("class_a_3_interest", "Class A-3 interest")]),
    ("due", [("first_priority_principal", "First Priority Principal Payment")]),
    ("due", [("class_b_interest", "Class B interest")]),
    ("due", [("reserve_deposit", "Reserve Account deposit")]),
    ("due", [("regular_principal", "Regular Principal Payment")]),
    ("remainder", [("certificateholders", "Certificateholders")]),
])
DUES = {"servicing_fee": 2_000_000, "indenture_trustee_fees": 300_000, "owner_trustee_fees": 100_000,
        "class_a_1_interest": 4_000_000, "class_a_2_interest": 6_000_000, "class_a_3_interest": 10_000_000,
        "first_priority_principal": 0, "class_b_interest": 2_500_000, "reserve_deposit": 5_000_000,
        "regular_principal": 90_000_000}


class WorkedExample(unittest.TestCase):
    def test_full_distribution(self):
        expected = engine.expected_payout(AUTO, {"cash": 100_000_000, **DUES})
        self.assertEqual(expected, {**DUES, "regular_principal": 70_100_000, "certificateholders": 0})
        self.assertEqual(sum(expected.values()), 100_000_000)

    def test_reported_payments_pass_and_misdirected_cash_blocks(self):
        scenario = {"cash": 100_000_000, **DUES}
        filed = engine.expected_payout(AUTO, scenario)
        self.assertEqual(engine.compare(AUTO, scenario, lambda _: dict(filed))["status"], "PASS")
        misdirected = {**filed, "regular_principal": 69_100_000, "certificateholders": 1_000_000}
        result = engine.compare(AUTO, scenario, lambda _: misdirected)
        self.assertEqual(result["status"], "BLOCK")
        self.assertEqual(result["delta_dollars"]["regular_principal"], "-10000.00")
        self.assertEqual(result["delta_dollars"]["certificateholders"], "10000.00")
        self.assertEqual(result["expected_total_cents"], result["candidate_total_cents"])
        self.assertNotIn("rounding_accepted", result)

    def test_stress_shares_class_a_interest_and_needs_no_later_dues(self):
        dues = {key: DUES[key] for key in ("servicing_fee", "indenture_trustee_fees", "owner_trustee_fees",
                                           "class_a_1_interest", "class_a_2_interest", "class_a_3_interest")}
        expected = engine.expected_payout(AUTO, {"cash": 15_000_000, **dues})
        self.assertEqual([expected[k] for k in ("class_a_1_interest", "class_a_2_interest", "class_a_3_interest")],
                         [2_520_000, 3_780_000, 6_300_000])
        self.assertEqual(sum(expected[k] for k in ("first_priority_principal", "class_b_interest", "reserve_deposit",
                                                   "regular_principal", "certificateholders")), 0)

    def test_planning_asks_only_for_amounts_that_change_the_result(self):
        payouts, missing = engine.allocate_waterfall(AUTO, 100_000_000, {"servicing_fee": 2_000_000})
        self.assertEqual((payouts, missing), ({"servicing_fee": 2_000_000}, "indenture_trustee_fees"))
        # A pro rata step needs every amount due in the step once cash reaches it.
        _, missing = engine.allocate_waterfall(AUTO, 100_000_000, {"servicing_fee": 2_000_000,
                                                                   "indenture_trustee_fees": 300_000})
        self.assertEqual(missing, "owner_trustee_fees")
        payouts, missing = engine.allocate_waterfall(AUTO, 1_000_000, {"servicing_fee": 2_000_000})
        self.assertIsNone(missing)
        self.assertEqual(payouts["servicing_fee"], 1_000_000)
        self.assertEqual(sum(payouts.values()), 1_000_000)

    def test_scenario_errors_name_the_fields(self):
        with self.assertRaisesRegex(ValueError, "Supply missing integer-cent fields: cash"):
            engine.expected_payout(AUTO, dict(DUES))
        with self.assertRaisesRegex(ValueError, "Remove unexpected scenario fields: certificateholders"):
            engine.expected_payout(AUTO, {"cash": 1, "certificateholders": 1, **DUES})
        with self.assertRaisesRegex(ValueError, "Set servicing_fee to nonnegative integer cents"):
            engine.expected_payout(AUTO, {"cash": 1, "servicing_fee": -1})
        with self.assertRaisesRegex(ValueError, "Set servicing_fee to nonnegative integer cents"):
            engine.expected_payout(AUTO, {"cash": 1, "servicing_fee": 1.5})
        with self.assertRaisesRegex(ValueError, "Supply missing integer-cent fields: class_a_1_interest, "
                                                "class_a_2_interest, class_a_3_interest"):
            engine.expected_payout(AUTO, {"cash": 100_000_000, "servicing_fee": 1, "indenture_trustee_fees": 1,
                                          "owner_trustee_fees": 1})

    def test_candidate_must_report_every_payment(self):
        scenario = {"cash": 100_000_000, **DUES}
        partial = engine.expected_payout(AUTO, scenario)
        partial.pop("certificateholders")
        with self.assertRaisesRegex(ValueError, "Candidate must return the payout fields"):
            engine.compare(AUTO, scenario, lambda _: partial)

    def test_stress_search_remains_specific_to_the_two_step_rule(self):
        with self.assertRaisesRegex(ValueError, "Synthetic search supports payment-priority rules"):
            engine.search(AUTO, lambda scenario: {})


class Rounding(unittest.TestCase):
    RULE = make_rule([("due", [("a", "Class A"), ("b", "Class B"), ("c", "Class C")]),
                      ("remainder", [("residual", "Residual")])])

    def test_largest_remainder_with_listed_order_for_ties(self):
        self.assertEqual(engine.pro_rata(2, [1, 1, 1]), [1, 1, 0])
        self.assertEqual(engine.pro_rata(100, [100, 100, 100]), [34, 33, 33])
        self.assertEqual(engine.pro_rata(10, [0, 5, 5]), [0, 5, 5])
        self.assertEqual(engine.pro_rata(0, [0, 0]), [0, 0])
        with self.assertRaises(ValueError):
            engine.pro_rata(3, [1, 1])

    def test_other_cent_rounding_within_a_shortfall_step_passes_with_a_record(self):
        scenario = {"cash": 2, "a": 1, "b": 1, "c": 1}
        self.assertEqual(engine.expected_payout(self.RULE, scenario), {"a": 1, "b": 1, "c": 0, "residual": 0})
        result = engine.compare(self.RULE, scenario, lambda _: {"a": 0, "b": 1, "c": 1, "residual": 0})
        self.assertEqual((result["status"], result["rounding_accepted"]), ("PASS", ["a", "c"]))

    def test_rounding_tolerance_is_narrow(self):
        scenario = {"cash": 2, "a": 1, "b": 1, "c": 1}
        for submitted in ({"a": 2, "b": 0, "c": 0, "residual": 0},   # more than the exact share rounded up
                          {"a": 1, "b": 0, "c": 0, "residual": 1},   # moves cash to another step
                          {"a": 1, "b": 1, "c": 1, "residual": 0}):  # exceeds the cash available
            self.assertEqual(engine.compare(self.RULE, scenario, lambda _: submitted)["status"], "BLOCK")
        # A fully funded step has exact amounts, so any difference blocks.
        funded = {"cash": 10, "a": 1, "b": 1, "c": 1}
        self.assertEqual(engine.compare(self.RULE, funded, lambda _: {"a": 0, "b": 2, "c": 1, "residual": 7})["status"],
                         "BLOCK")


class Validation(unittest.TestCase):
    def assertRejected(self, rule):
        with self.assertRaisesRegex(ValueError, "Unsupported or unapproved rule"):
            engine.validate_rule(rule)

    def test_accepts_the_worked_example_and_a_single_pass_through(self):
        self.assertIs(engine.validate_rule(AUTO), AUTO)
        engine.validate_rule(make_rule([("remainder", [("class_p", "Class P")])], "Prepayment charges collected"))

    def test_rejects_malformed_rules(self):
        mutations = {
            "remainder not last": lambda r: r["steps"].insert(0, copy.deepcopy(r["steps"][-1])),
            "two remainder payees": lambda r: r["steps"][-1]["payees"].append({"id": "x", "label": "X"}),
            "duplicate id": lambda r: r["steps"][1]["payees"][1].update(id="indenture_trustee_fees"),
            "funds id": lambda r: r["steps"][0]["payees"][0].update(id="cash"),
            "id pattern": lambda r: r["steps"][0]["payees"][0].update(id="Servicing Fee"),
            "formula label": lambda r: r["steps"][0]["payees"][0].update(label="=HYPERLINK(1)"),
            "signed label": lambda r: r["steps"][0]["payees"][0].update(label="-1"),
            "multiline label": lambda r: r["steps"][0]["payees"][0].update(label="Servicing\nFee"),
            "padded label": lambda r: r["steps"][0]["payees"][0].update(label=" Servicing Fee"),
            "control label": lambda r: r["steps"][0]["payees"][0].update(label="Fee\x07"),
            "long label": lambda r: r["steps"][0]["payees"][0].update(label="F" * 121),
            "duplicate label": lambda r: r["steps"][1]["payees"][0].update(label="servicing fee"),
            "worksheet label clash": lambda r: r["steps"][1]["payees"][0].update(label="Servicing Fee due"),
            "funds label clash": lambda r: r.update(funds_label="Class B interest"),
            "missing evidence": lambda r: r["steps"][0].update(evidence=" "),
            "extra step key": lambda r: r["steps"][0].update(share="50%"),
            "unknown limit": lambda r: r["steps"][0].update(limit="percent"),
            "empty payees": lambda r: r["steps"][0].update(payees=[]),
            "empty steps": lambda r: r.update(steps=[]),
            "rounding": lambda r: r.update(rounding="half_up"),
            "type": lambda r: r.update(type="payment_priority"),
            "schema text": lambda r: r.update(schema="3"),
            "unapproved": lambda r: r.pop("approved_by"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                rule = copy.deepcopy(AUTO)
                mutate(rule)
                self.assertRejected(rule)

    def test_size_limits(self):
        many_steps = make_rule([("due", [(f"p{i}", f"Payee {i}")]) for i in range(41)])
        self.assertRejected(many_steps)
        wide_step = make_rule([("due", [(f"p{i}", f"Payee {i}") for i in range(13)])])
        self.assertRejected(wide_step)
        too_many = make_rule([("due", [(f"p{s}_{i}", f"Payee {s}.{i}") for i in range(10)]) for s in range(7)])
        self.assertRejected(too_many)
        engine.validate_rule(make_rule([("due", [(f"p{s}_{i}", f"Payee {s}.{i}") for i in range(10)]) for s in range(6)]))


class Properties(unittest.TestCase):
    def test_random_waterfalls_keep_the_invariants(self):
        rng = random.Random(20261119)
        for trial in range(600):
            steps = []
            for s in range(rng.randint(1, 8)):
                steps.append(("due", [(f"p{s}_{i}", f"Payee {s}.{i}") for i in range(rng.randint(1, 4))]))
            residual = rng.random() < 0.5
            if residual:
                steps.append(("remainder", [("residual", "Residual")]))
            rule = make_rule(steps)
            engine.validate_rule(rule)
            dues = {key: rng.choice((0, rng.randint(0, 999), rng.randint(0, 10**9)))
                    for key in engine.waterfall_dues(rule)}
            owed = sum(dues.values())
            cash = rng.choice((0, 1, owed, max(0, owed - 1), owed + 1, rng.randint(0, 2 * owed + 1)))
            with self.subTest(trial=trial):
                payouts = engine.expected_payout(rule, {"cash": cash, **dues})
                self.assertEqual(list(payouts), engine.waterfall_payees(rule))
                self.assertEqual(sum(payouts.values()), cash if residual else min(cash, owed))
                remaining, funded = cash, True
                for limit, payees in steps:
                    ids = [key for key, _ in payees]
                    paid = sum(payouts[key] for key in ids)
                    if not funded:
                        self.assertEqual(paid, 0, "a later step was paid before an earlier one was funded")
                    if limit == "due":
                        due = sum(dues[key] for key in ids)
                        self.assertEqual(paid, min(remaining, due))
                        for key in ids:
                            self.assertLessEqual(payouts[key], dues[key])
                            # Each share is within one cent of the exact pro rata share.
                            if due:
                                self.assertLess(abs(payouts[key] * due - paid * dues[key]), due)
                        funded = funded and paid == due
                    remaining -= paid
                self.assertEqual(engine.compare(rule, {"cash": cash, **dues}, lambda _: dict(payouts))["status"], "PASS")
                # Moving one cent from a paid payee to the first payee of another step always blocks.
                donors = [key for key, amount in payouts.items() if amount]
                if donors:
                    donor = rng.choice(donors)
                    step_of = {key: index for index, (_, payees) in enumerate(steps) for key, _ in payees}
                    others = [key for key in payouts if step_of[key] != step_of[donor]]
                    if others:
                        moved = {**payouts, donor: payouts[donor] - 1}
                        target = rng.choice(others)
                        moved[target] += 1
                        result = engine.compare(rule, {"cash": cash, **dues}, lambda _: moved)
                        self.assertEqual(result["status"], "BLOCK")

    def test_determinism(self):
        scenario = {"cash": 12_345_678, **DUES}
        first = engine.compare(AUTO, scenario, lambda s: engine.expected_payout(AUTO, s))
        for _ in range(3):
            self.assertEqual(engine.compare(AUTO, scenario, lambda s: engine.expected_payout(AUTO, s)), first)


if __name__ == "__main__":
    unittest.main()
