"""Ford 2025-C pre-maturity, full Class A interest replay."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .engine import STEPS, compare

CLASSES = ("A-1", "A-2a", "A-2b", "A-3", "A-4", "B", "C")


def round_cent(value):
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def replay(row, rule, candidate):
    data = row["input"]
    opening = data["opening"]
    payment_date = date.fromisoformat(data["payment_date"])
    days = (payment_date - date.fromisoformat(data["prior_payment_date"])).days
    if not 28 <= days <= 33 or payment_date >= date(2026, 12, 15):
        raise ValueError("Payment date outside supported replay scope")
    interest = [
        round_cent(Decimal(opening[0]) * Decimal(".03993") * days / 360),
        round_cent(Decimal(opening[1]) * Decimal(".0399") / 12),
        data["floating_interest_due"],
        round_cent(Decimal(opening[3]) * Decimal(".039") / 12),
        round_cent(Decimal(opening[4]) * Decimal(".0398") / 12),
        round_cent(Decimal(opening[5]) * Decimal(".0422") / 12),
        0,
    ]
    fees = round_cent(Decimal(data["pool_open"]) / 1200) + data["trustee_due"]
    cash = sum(data[key] for key in ("collections", "purchases", "cleanup", "reserve_draw"))
    cash -= fees + sum(interest[:5])
    first = max(0, sum(opening[:5]) - data["pool_adjusted_close"])
    second = max(0, sum(opening[:6]) - data["pool_adjusted_close"] - first)
    reserve = max(0, data["reserve_target"] - data["reserve_open"] + data["reserve_draw"])
    if cash < 0 or first or second or reserve or data["cleanup"] or data["reserve_draw"]:
        raise ValueError("Historical period outside supported replay scope")
    window = compare(rule, dict(zip(("cash", *STEPS), (cash, first, interest[5]))), candidate)
    payouts = window["candidate"]
    interest[5] = payouts[STEPS[1]]
    cash -= sum(payouts.values())
    if cash < 0:
        raise ValueError("Candidate exceeded cash available")
    regular_due = max(opening[0], sum(opening) - data["pool_close"] + data["target_oc"])
    principal = [0] * len(CLASSES)
    remaining = min(cash, max(0, regular_due - payouts[STEPS[0]])) + payouts[STEPS[0]]
    for group in ((0,), (1, 2), (3,), (4,), (5,), (6,)):
        balance = sum(opening[index] for index in group)
        amount = min(remaining, balance)
        for index in group[:-1]:
            principal[index] = round_cent(Decimal(amount) * opening[index] / balance) if balance else 0
        principal[group[-1]] = amount - sum(principal[index] for index in group[:-1])
        remaining -= amount
    computed = {"principal": principal, "interest": interest}
    comparisons = [
        {"class": note, "payment": payment, "actual": computed[payment][i],
         "expected": row["expected"][payment][i]}
        for payment in ("principal", "interest") for i, note in enumerate(CLASSES)
    ]
    return {"period": row["period"], "source": row["source"],
            "input": data, "first_priority_due_cents": first,
            "comparisons": comparisons, "window": window}
