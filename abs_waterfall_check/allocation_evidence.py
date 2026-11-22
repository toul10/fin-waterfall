"""Dated upstream figures from the five bundled Ford trustee statement replays."""
from .local import load
from .web_examples import DATA


def ford_period_dues(deal_id, approved_rule_sha256, distribution_date, amount_cents):
    """Match an approved deal, date and the cash reported at the two-step window."""
    if deal_id != "ford":
        return None
    folder = DATA / "ford"
    if load(folder / "approval.json")["approved_rule_sha256"] != approved_rule_sha256:
        return None
    row = load(folder / "statement-dues.json").get(distribution_date)
    if row is None or row["cash"] != amount_cents:
        return None
    return {
        "dues": {field: row[field] for field in ("class_a_principal", "class_b_interest")},
        "label": f"Ford trustee statement for the {distribution_date} distribution, sections III–V",
        "source_url": row["source"],
    }
