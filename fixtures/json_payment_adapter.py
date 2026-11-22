"""Standalone candidate for the two payment steps in the release demo."""
import json
import sys


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("good", "faulty"):
        raise SystemExit("Usage: json_payment_adapter.py good|faulty")

    scenario = json.load(sys.stdin)
    payment_names = ("class_a_principal", "class_b_interest")
    if not isinstance(scenario, dict) or set(scenario) != {"cash", *payment_names}:
        raise SystemExit("Expected cash and both amounts due")
    if any(type(amount) is not int or amount < 0 for amount in scenario.values()):
        raise SystemExit("Expected nonnegative integer cents")

    cash = scenario["cash"]
    principal_due = scenario["class_a_principal"]
    interest_due = scenario["class_b_interest"]
    if sys.argv[1] == "good":
        principal = min(principal_due, cash)
        interest = min(interest_due, cash - principal)
    else:
        interest = min(interest_due, cash)
        principal = min(principal_due, cash - interest)

    json.dump({"class_a_principal": principal, "class_b_interest": interest}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
