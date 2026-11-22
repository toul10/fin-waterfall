"""Deterministic verification for supported payout rules."""
import json
import re
import shlex
import subprocess
from decimal import Decimal
from importlib import import_module
from pathlib import Path

STEPS = ("class_a_principal", "class_b_interest")
STATUSES = ("PASS", "BLOCK", "HOLD")
PAYMENT_PRIORITY = "payment_priority"
PREPAYMENT_CHARGES_TO_CLASS_P = "prepayment_charges_to_class_p"
PRIORITY_OF_PAYMENTS = "priority_of_payments"
RULE_KINDS = (PAYMENT_PRIORITY, PREPAYMENT_CHARGES_TO_CLASS_P, PRIORITY_OF_PAYMENTS)

# A general priority of payments (schema 3) applies one amount of funds through
# ordered steps. A "due" step pays each payee up to its amount due and shares a
# shortfall pro rata by those amounts. A final "remainder" step pays one payee
# everything left. Amounts due come from the statement or upstream worksheet.
FUNDS = "cash"
WATERFALL_LIMITS = ("due", "remainder")
WATERFALL_ROUNDING = "largest_remainder"
PAYEE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
MAX_WATERFALL_STEPS = 40
MAX_STEP_PAYEES = 12
MAX_WATERFALL_PAYEES = 60
MAX_LABEL = 120
MAX_STEP_EVIDENCE = 6000


def cents(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected nonnegative integer cents")
    return value


def validate_rule(rule):
    metadata = ("deal", "deal_version", "rule_version", "clause", "approved_by")
    if not isinstance(rule, dict) or not all(
        isinstance(rule.get(key), str) and rule[key].strip() for key in metadata
    ):
        raise ValueError("Unsupported or unapproved rule")
    schema = rule.get("schema")
    if type(schema) is int and schema == 1:
        order = rule.get("order")
        if isinstance(order, list) and tuple(order) in (STEPS, STEPS[::-1]):
            return rule
    if (type(schema) is int and schema == 2
            and rule.get("type") == PREPAYMENT_CHARGES_TO_CLASS_P
            and rule.get("recipient") == "class_p"):
        return rule
    if type(schema) is int and schema == 3 and _valid_waterfall(rule):
        return rule
    raise ValueError("Unsupported or unapproved rule")


def waterfall_label(value):
    """Accept a single-line display name that cannot start a spreadsheet formula."""
    return (isinstance(value, str) and 0 < len(value) <= MAX_LABEL
            and value == " ".join(value.split()) and value[0] not in "=+-@"
            and not re.search(r"[\x00-\x1f\x7f]", value))


def worksheet_labels(label):
    """The display name, its amount-due input and its payment in a worksheet."""
    return label, label + " due", label + " payment"


def _valid_waterfall(rule):
    if (rule.get("type") != PRIORITY_OF_PAYMENTS or rule.get("rounding") != WATERFALL_ROUNDING
            or not waterfall_label(rule.get("funds_label"))):
        return False
    steps = rule.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_WATERFALL_STEPS:
        return False
    ids, names, count = set(), {rule["funds_label"].casefold()}, 0
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {"payees", "limit", "evidence"}:
            return False
        payees, evidence = step["payees"], step["evidence"]
        if (step["limit"] not in WATERFALL_LIMITS or not isinstance(evidence, str)
                or not evidence.strip() or len(evidence) > MAX_STEP_EVIDENCE
                or not isinstance(payees, list) or not 1 <= len(payees) <= MAX_STEP_PAYEES):
            return False
        # Funds left after a remainder step would have no recipient.
        if step["limit"] == "remainder" and (len(payees) != 1 or index != len(steps) - 1):
            return False
        for payee in payees:
            if not isinstance(payee, dict) or set(payee) != {"id", "label"}:
                return False
            key, label = payee["id"], payee["label"]
            if (not isinstance(key, str) or not PAYEE_ID.fullmatch(key) or key == FUNDS
                    or key in ids or not waterfall_label(label)):
                return False
            ids.add(key)
            # Worksheets and spreadsheets identify each amount by its label.
            for name in worksheet_labels(label):
                if name.casefold() in names:
                    return False
                names.add(name.casefold())
            count += 1
    return count <= MAX_WATERFALL_PAYEES


def waterfall_payees(rule):
    return [payee["id"] for step in rule["steps"] for payee in step["payees"]]


def waterfall_dues(rule):
    """Payees whose amount due limits their payment."""
    return [payee["id"] for step in rule["steps"] if step["limit"] == "due" for payee in step["payees"]]


def waterfall_names(rule):
    return {payee["id"]: payee["label"] for step in rule["steps"] for payee in step["payees"]}


def pro_rata(amount, dues):
    """Share amount by dues with the largest remainder method. Ties follow the listed order."""
    total = sum(dues)
    if type(amount) is not int or not 0 <= amount <= total:
        raise ValueError("A pro rata amount must be between zero and the amounts due")
    if not total:
        return [0] * len(dues)
    shares = [amount * due // total for due in dues]
    remainders = [amount * due % total for due in dues]
    for index in sorted(range(len(dues)), key=lambda i: (-remainders[i], i))[:amount - sum(shares)]:
        shares[index] += 1
    return shares


def waterfall_steps(rule, cash, dues):
    """Apply cash through the approved steps and stop at the first amount due still needed."""
    records = []
    for step in rule["steps"]:
        ids = [payee["id"] for payee in step["payees"]]
        if step["limit"] == "remainder":
            records.append({"ids": ids, "owed": None, "paid": [cash], "shortfall": False})
            cash = 0
            continue
        if not cash:
            # Once all cash is paid, later payments are known even if their dues are unknown.
            records.append({"ids": ids, "owed": None, "paid": [0] * len(ids), "shortfall": False})
            continue
        missing = next((key for key in ids if key not in dues), None)
        if missing:
            return records, missing
        owed = [cents(dues[key]) for key in ids]
        shortfall = cash < sum(owed)
        paid = pro_rata(cash, owed) if shortfall else owed
        records.append({"ids": ids, "owed": owed, "paid": paid, "shortfall": shortfall})
        cash -= sum(paid)
    return records, None


def allocate_waterfall(rule, cash, dues):
    """Return the determined payments and the first amount due still needed."""
    records, missing = waterfall_steps(rule, cents(cash), dues)
    return {key: amount for record in records for key, amount in zip(record["ids"], record["paid"])}, missing


def waterfall_expected(rule, scenario):
    if not isinstance(scenario, dict) or FUNDS not in scenario:
        raise ValueError("Supply missing integer-cent fields: " + FUNDS)
    extra = set(scenario).difference({FUNDS, *waterfall_dues(rule)})
    if extra:
        raise ValueError("Remove unexpected scenario fields: " + ", ".join(sorted(extra)))
    for field in sorted(scenario):
        if type(scenario[field]) is not int or scenario[field] < 0:
            raise ValueError(f"Set {field} to nonnegative integer cents")
    records, missing = waterfall_steps(rule, scenario[FUNDS], scenario)
    if missing:
        step = next(s for s in rule["steps"] if missing in (p["id"] for p in s["payees"]))
        absent = [p["id"] for p in step["payees"] if p["id"] not in scenario]
        raise ValueError("Supply missing integer-cent fields: " + ", ".join(absent))
    payouts = {key: amount for record in records for key, amount in zip(record["ids"], record["paid"])}
    return payouts, records


def waterfall_rounding(rule, scenario, actual):
    """List payees whose differences are another cent rounding of an exact pro rata share.

    The approved terms share a shortfall pro rata but rarely state a cent rounding
    convention. Within such a step, a submitted amount is accepted when it is the
    exact share rounded down or up and the step total equals the approved total.
    """
    _, records = waterfall_expected(rule, scenario)
    accepted = []
    for record in records:
        submitted = [actual[key] for key in record["ids"]]
        if submitted == record["paid"]:
            continue
        if not record["shortfall"] or len(submitted) < 2 or sum(submitted) != sum(record["paid"]):
            return []
        total, available = sum(record["owed"]), sum(record["paid"])
        for amount, due in zip(submitted, record["owed"]):
            low = available * due // total
            if not low <= amount <= low + (available * due % total > 0):
                return []
        accepted.extend(key for key, amount, due in zip(record["ids"], submitted, record["paid"])
                        if amount != due)
    return accepted


def allocate_priority(cash, order, dues):
    """Return the determined payments and the first due amount still needed."""
    payouts = {}
    for step in order:
        if cash and step not in dues:
            return payouts, step
        # Once all cash is paid, later payments are known even if their dues are unknown.
        payouts[step] = min(cash, dues[step]) if cash else 0
        cash -= payouts[step]
    return payouts, None


def allocate(scenario, order):
    if set(scenario) != {"cash", *STEPS}:
        raise ValueError("Unexpected scenario fields")
    dues = {step: cents(scenario[step]) for step in STEPS}
    payouts, _ = allocate_priority(cents(scenario["cash"]), order, dues)
    return payouts


def good(scenario):
    principal = min(scenario["cash"], scenario[STEPS[0]])
    return {STEPS[0]: principal, STEPS[1]: min(scenario["cash"] - principal, scenario[STEPS[1]])}


def faulty(scenario):
    interest = min(scenario["cash"], scenario[STEPS[1]])
    return {STEPS[0]: min(scenario["cash"] - interest, scenario[STEPS[0]]), STEPS[1]: interest}


def adapter(spec):
    if spec in ("good", "faulty"):
        return {"good": good, "faulty": faulty}[spec]
    if spec.startswith("observed:"):
        payout = json.loads(Path(spec.removeprefix("observed:")).read_text(encoding="utf-8"))
        return lambda _scenario: payout
    if spec.startswith("json:"):
        command = shlex.split(spec.removeprefix("json:"))
        if not command:
            raise ValueError("JSON adapter requires a command")

        def calculate(scenario):
            try:
                result = subprocess.run(
                    command,
                    input=json.dumps(scenario),
                    capture_output=True,
                    encoding="utf-8",
                    timeout=5,
                    check=True,
                )
            except subprocess.TimeoutExpired as error:
                raise ValueError("JSON adapter timed out") from error
            except subprocess.CalledProcessError as error:
                raise ValueError(f"JSON adapter exited with status {error.returncode}") from error
            return json.loads(result.stdout)

        return calculate
    module, name = spec.split(":")
    return getattr(import_module(module), name)


def expected_payout(rule, scenario):
    validate_rule(rule)
    if rule["schema"] == 3:
        return waterfall_expected(rule, scenario)[0]
    required = {"cash", *STEPS} if rule["schema"] == 1 else {"collected_prepayment_charges"}
    if not isinstance(scenario, dict):
        raise ValueError("Supply scenario fields in integer cents: " + ", ".join(sorted(required)))
    missing = required.difference(scenario)
    extra = set(scenario).difference(required)
    if missing:
        raise ValueError("Supply missing integer-cent fields: " + ", ".join(sorted(missing)))
    if extra:
        raise ValueError("Remove unexpected scenario fields: " + ", ".join(sorted(extra)))
    for field in sorted(required):
        if type(scenario[field]) is not int or scenario[field] < 0:
            raise ValueError(f"Set {field} to nonnegative integer cents")
    if rule["schema"] == 1:
        return allocate(scenario, rule["order"])
    return {rule["recipient"]: cents(scenario["collected_prepayment_charges"])}


def compare(rule, scenario, candidate):
    expected = expected_payout(rule, scenario)
    payout_fields = STEPS if rule["schema"] == 1 else expected.keys()
    actual = candidate(scenario.copy())
    if not isinstance(actual, dict) or actual.keys() != expected.keys():
        raise ValueError("Candidate must return the payout fields required by the rule")
    for amount in actual.values():
        cents(amount)
    result = {
        "status": "PASS" if expected == actual else "BLOCK",
        "rule": rule,
        "scenario": scenario,
        "expected": expected,
        "candidate": actual,
        "delta_dollars": {
            step: f"{Decimal(actual[step] - expected[step]) / 100:.2f}" for step in payout_fields
        },
        "expected_total_cents": sum(expected.values()),
        "candidate_total_cents": sum(actual.values()),
    }
    if rule["schema"] == 3 and result["status"] == "BLOCK":
        accepted = waterfall_rounding(rule, scenario, actual)
        if accepted:
            result.update(status="PASS", rounding_accepted=accepted)
    return result


def search(rule, candidate):
    if validate_rule(rule)["schema"] != 1:
        raise ValueError("Synthetic search supports payment-priority rules")
    dues = dict(zip(STEPS, (10_000_000, 11_105_633)))
    # Exhaust both ends of the shortfall range to one cent, and sample the
    # interior at $100 increments, including the $50,000 cash point.
    total = sum(dues.values())
    cash_points = sorted({0, 1, total - 1, total, *range(10_000, total, 10_000)})
    first = stress = None
    for cash in cash_points:
        result = compare(rule, {"cash": cash, **dues}, candidate)
        if result["status"] != "BLOCK":
            continue
        if first is None:
            first = result
        if (result["expected_total_cents"] == result["candidate_total_cents"]
                and abs(result["expected"][STEPS[0]] - result["candidate"][STEPS[0]]) == 5_000_000):
            stress = result
            # Keep the lowest cash point that produces the target transfer.
            break
    near_full = compare(rule, {"cash": total - 1, **dues}, candidate)
    full = compare(rule, {"cash": total, **dues}, candidate)
    return {
        "synthetic": True,
        "class_a_1_opening_balance_cents": dues[STEPS[0]],
        "affected_class_by_payment": {STEPS[0]: "A-1", STEPS[1]: "B"},
        "first_observed_divergence": first,
        "regression": stress,
        "one_cent_shortfall": near_full,
        "fully_funded": full,
    }
