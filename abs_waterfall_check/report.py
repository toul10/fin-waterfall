"""Render an analyst's distribution report from the saved verification record."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import engine


NAMES = {"class_a_principal": "Class A principal", "class_b_interest": "Class B interest",
         "class_p": "Class P"}
INPUTS = {"cash": "Cash available for distribution", "class_a_principal": "Class A principal due",
          "class_b_interest": "Class B interest due", "collected_prepayment_charges": "Prepayment charges collected"}
PAYMENTS = {"class_a_principal": "Class A principal payment", "class_b_interest": "Class B interest payment",
            "class_p": "Class P payment"}


def labels(rule):
    """Payment names, input names and submitted-payment names for the rule in a saved review."""
    if isinstance(rule, dict) and rule.get("schema") == 3:
        try:
            names = engine.waterfall_names(rule)
            dues = engine.waterfall_dues(rule)
        except (KeyError, TypeError, AttributeError):
            return {}, {"cash": "Available funds"}, {}
        inputs = {"cash": rule.get("funds_label") or "Available funds", **{k: names[k] + " due" for k in dues}}
        return names, inputs, {k: v + " payment" for k, v in names.items()}
    return NAMES, INPUTS, PAYMENTS
INK = colors.HexColor("#193c35")
MUTED = colors.HexColor("#496159")
RULE = colors.HexColor("#c3d3ca")


def dollars(cents, signed=False):
    if type(cents) is not int:
        return "Unavailable"
    whole, fraction = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else "+" if signed and cents else ""
    return f"{sign}${whole:,}.{fraction:02d}"


def incomplete_reason(report):
    reason = report.get("reason") or "Complete the worksheet and review the approval."
    rule = report.get("rule") or {}
    names, inputs, payments = labels(rule)
    if reason.startswith(("Supply missing integer-cent fields: ", "Supply scenario fields in integer cents: ")):
        keys = reason.partition(": ")[2].split(", ")
        return "Complete these distribution fields in USD. " + ", ".join(inputs.get(key, key) for key in keys) + "."
    if (reason == "Candidate must return the payout fields required by the rule"
            and report.get("submitted_inputs", {}).get("adapter", "observed") == "observed"):
        required = (tuple(payments) if rule.get("schema") == 3 else
                    ("class_p",) if rule.get("schema") == 2 else ("class_a_principal", "class_b_interest"))
        submitted = report.get("submitted_payouts") or {}
        missing = [payments[key] for key in required if key not in submitted]
        if missing:
            return "Complete these submitted payments in USD. " + ", ".join(missing) + "."
    return reason


def distribution_pdf(report):
    if report.get("mode", "check") not in {"check", "replay", "search"}:
        raise ValueError("Choose a single distribution report for this download.")
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=42, rightMargin=42,
                            topMargin=48, bottomMargin=46, title="Distribution review",
                            author="Waterfall")
    normal = ParagraphStyle("Body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK)
    heading = ParagraphStyle("Section", parent=normal, fontName="Helvetica-Bold",
                             fontSize=13, leading=16, spaceBefore=12, spaceAfter=5, keepWithNext=True)
    title = ParagraphStyle("Title", parent=heading, fontSize=21, leading=25, spaceBefore=0)
    note = ParagraphStyle("Note", parent=normal, fontSize=8.5, leading=12, textColor=MUTED)
    number = ParagraphStyle("Amount", parent=normal, alignment=TA_RIGHT)
    label = ParagraphStyle("Label", parent=normal, fontName="Helvetica-Bold")
    story = []

    def para(value, style=normal):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    status = report["status"]
    rule = report.get("rule") or {}
    receipt = report.get("rule_provenance") or {}
    purpose = "Actual payment" if report.get("filed_payout") or report.get("payout_purpose") == "actual" else "Proposed payment"
    period = report.get("distribution_date") or ("Undated synthetic stress" if report.get("synthetic") else "Date unavailable")
    story.extend([para("DISTRIBUTION REVIEW", note), para(report.get("deal_name") or rule.get("deal") or "Payment review", title),
                  para(f"{period}   |   {purpose}   |   USD", note), Spacer(1, 10)])
    verdict = {"PASS": "Submitted payments match the approved terms.",
               "BLOCK": "Submitted payments differ from the approved terms.",
               "HOLD": "The review needs more information."}[status]
    story.append(para(f"{status}   {verdict}", label))
    expected = report.get("expected") or {}
    submitted = report.get("candidate") or report.get("submitted_payouts") or {}
    names, inputs, _ = labels(rule)
    keys = [key for key in names if key in expected or key in submitted]
    if keys:
        story.append(para("Payments in scope", heading))
        rows = [[para(h, note) for h in ("Tranche", "Expected", "Submitted", "Difference")]]
        def row(name, due, paid):
            delta = paid - due if type(due) is int and type(paid) is int else None
            return [para(name), para(dollars(due), number), para(dollars(paid), number), para(dollars(delta, signed=True), number)]
        for key in keys:
            name = names[key]
            if (rule.get("schema") != 3 and key == "class_a_principal"
                    and "Class A-1" in (report.get("affected_tranche") or "")):
                name = "Class A-1 principal"
            rows.append(row(name, expected.get(key), submitted.get(key)))
        if type(report.get("expected_total_cents")) is int and type(report.get("candidate_total_cents")) is int:
            rows.append(row("Total reviewed", report["expected_total_cents"], report["candidate_total_cents"]))
        table = Table(rows, colWidths=[153, 108, 108, 120], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#edf3ed")),
                                   ("LINEBELOW",(0,0),(-1,-1),0.4,RULE),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                   ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]))
        story.extend([table, para("Difference is submitted less expected, for these payments only.", note)])
        rounded = [names.get(key, key) for key in report.get("rounding_accepted") or ()]
        if rounded:
            story.append(para("Pro rata cent rounding accepted for " + ", ".join(rounded) + ". Each amount is the "
                              "exact pro rata share rounded down or up, and the step total matches the approved "
                              "allocation.", note))
    if status == "HOLD":
        story.append(para(incomplete_reason(report)))
    next_step = ("Attach this report to the distribution review." if status == "PASS" else
                 "Investigate the payment with the servicer and record any corrective distribution." if status == "BLOCK" and purpose == "Actual payment" else
                 "Request a corrected allocation before payment and recheck the revised amounts." if status == "BLOCK" else
                 "Resolve the missing information or approval, then recheck the saved worksheet.")
    story.extend([Spacer(1, 6), para(next_step)])
    scenario = report.get("scenario") or {}
    if scenario:
        story.append(para("Distribution inputs", heading))
        for key, name in inputs.items():
            if key in scenario:
                story.append(para(f"{name}   {dollars(scenario[key])}"))
    if report.get("retrospective"):
        story.extend([para("Timing of the evidence", heading), para(report["retrospective"])])
    story.extend([para("Governing terms", heading), para(rule.get("clause") or receipt.get("clause") or "Approval unavailable", label)])
    if rule.get("support"):
        story.append(para(rule["support"]))
    if receipt:
        story.append(Spacer(1, 4))
        for name, item in (("Effective from", report.get("approval_effective_from")),
                           ("Approved by", rule.get("approved_by")), ("Approval", receipt.get("rule_id")),
                           ("Approved rule SHA-256", receipt.get("approved_rule_sha256"))):
            if item:
                story.append(para(f"{name}   {item}", note))
    if report.get("filing_sources"):
        story.append(para("Filing references", heading))
        for source in report["filing_sources"]:
            story.append(para(str(source.get("label", "")) + " · " + str(source.get("section", "")), note))
            if source.get("url"):
                story.append(para(source["url"], note))

    def footer(canvas, document):
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(42, 24, f"Waterfall   |   Review {report['run_id']}")
        canvas.drawRightString(A4[0] - 42, 24, f"Page {document.page}")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
