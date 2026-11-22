"""Locate proposed passages for local drafting. The reviewer decides applicability."""
from pathlib import Path
import re

from .engine import PAYMENT_PRIORITY, PREPAYMENT_CHARGES_TO_CLASS_P, PRIORITY_OF_PAYMENTS


# Split at clause boundaries while retaining character offsets into the imported source.
BOUNDARY = re.compile(r"(?<=;)|(?<=[.!?])(?=\s)|\n[ \t]*\n|(?<!\S)(?=\((?:[ivxlcdm]{1,6}|\d{1,2})\)\s)", re.I)
NUMBERED = re.compile(r"^\(([ivxlcdm]{1,6}|\d{1,2})\)\s*", re.I)
ROMAN = "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx".split()
SENIOR = re.compile(r"\b(?:first[ -]priority principal payment|class a (?:first[ -])?priority principal)\b", re.I)
JUNIOR = re.compile(r"\b(?:class b (?:accrued )?interest|(?:accrued (?:note )?interest|interest due)\b.{0,65}?\bclass b)\b", re.I)
SEQUENCE = re.compile(r"\b(?:before|after|then|followed by)\b", re.I)
ALL_CHARGES = re.compile(r"\ball (?:of (?:the )?)?(?:amounts representing )?(?:qualifying |collected |mortgage[- ]loan )*prepayment (?:charges|penalties)\b", re.I)
CLASS_P = re.compile(r"\b(?:to (?:the )?(?:holders of (?:the )?)?class p|class p (?:certificateholders|holders)?\s*(?:shall|will) receive)\b", re.I)
OTHER_FUNDS = re.compile(r"\b(?:principal|interest|fees?|expenses?|reserve|costs?|tax(?:es)?|deduct\w*|reimburse\w*)\b", re.I)
LIMITATION = re.compile(r"\b(?:unless|except\w*|if(?!\s+any\b)|subject to|provided that|remainder|remaining portion|default|however|not|portion|pro rata|up to|capped|less any|net of|otherwise|notwithstanding|only (?:when|while)|upon acceleration)\b|\d\s*%", re.I)
OTHER_CLASS = re.compile(r"\bclass\s+(?!a\b|b\b)[a-z]\b", re.I)
NEIGHBOR_BREAK = re.compile(r"(?<=[.;!?])\s+|\n[ \t]*\n")


def step_number(text):
    marker = NUMBERED.match(text)
    if not marker:
        return None
    mark = marker[1].lower()
    if mark.isdigit():
        return int(mark)
    return ROMAN.index(mark) + 1 if mark in ROMAN else None


def units(source):
    start = 0
    for boundary in BOUNDARY.finditer(source):
        if boundary.start() > start:
            yield start, boundary.start()
        start = boundary.end()
    if start < len(source):
        yield start, len(source)


def candidates(source):
    previous = None
    for start, end in units(source):
        raw = source[start:end]
        if not raw.strip():
            continue
        start += len(raw) - len(raw.lstrip())
        end -= len(raw) - len(raw.rstrip())
        text = " ".join(raw.split())
        if len(text) > 3000 or LIMITATION.search(text):
            previous = None
            continue
        if (ALL_CHARGES.search(text) and CLASS_P.search(text)
                and re.search(r"\b(?:collected|received)\b", text, re.I)
                and re.search(r"\b(?:distribut\w*|pay\w*|paid|remit\w*|allocat\w*|receive)\b", text, re.I)
                and not OTHER_FUNDS.search(text)
                and not re.search(r"\bclass (?!p\b)[a-z]\b", text, re.I)):
            yield PREPAYMENT_CHARGES_TO_CLASS_P, start, end
        senior, junior = bool(SENIOR.search(text)), bool(JUNIOR.search(text))
        if OTHER_CLASS.search(text):
            previous = None
            continue
        if senior and junior and SEQUENCE.search(text):
            yield PAYMENT_PRIORITY, start, end
        number = step_number(text)
        if number is not None and senior != junior:
            if previous and number == previous[0] + 1 and previous[1] != senior:
                yield PAYMENT_PRIORITY, previous[2], end
            previous = number, senior, start
        else:
            previous = None


def qualified_context(source, start, end):
    """Inspect adjoining prose in the original document, even after a manual selection."""
    before = source[max(0, start - 900):start].strip()
    after = source[end:end + 900].strip()
    preceding = NEIGHBOR_BREAK.split(before)[-1]
    following = NEIGHBOR_BREAK.split(after, maxsplit=1)[0]
    # Another numbered obligation carries its own qualifications.
    return any(part and not NUMBERED.match(part) and LIMITATION.search(part)
               for part in (preceding, following))


def suggest(source, filename, selection=None):
    if selection is not None:
        if not isinstance(selection, str) or not selection.strip() or len(selection) > 32000:
            raise ValueError("Select up to 32,000 characters from the contract.")
        offset = source.find(selection)
        if offset < 0 or source.find(selection, offset + 1) >= 0:
            raise ValueError("Select an exact, unique passage from the imported contract. Include the section heading if the wording repeats.")
        last = offset + len(selection)
    else:
        offset, last = 0, len(source)

    found = []
    for kind, start, end in candidates(source):
        if start >= offset and end <= last:
            if qualified_context(source, start, end):
                return {"reason": "An adjoining qualification may change who is paid. Review the complete provision and its conditions separately before using a payment calculation."}
            found.append((kind, start, end))
            if len(found) == 2:
                return {"reason": "This document contains more than one possible payment rule. Select the complete operative paragraph or the two consecutive priority steps for this distribution."}
    if not found:
        return {"reason": "Select the complete clause assigning all collected prepayment charges to Class P, or both consecutive steps for Class A first-priority principal and Class B interest. Terms with extra recipients, conditions or exceptions require separate review."}
    return describe(source, filename, *found[0])


def suggest_waterfall(source, filename, selection):
    """Use a reviewer's selection of a complete priority of payments. Nothing is detected automatically."""
    if not isinstance(selection, str) or not selection.strip() or len(selection) > 32000:
        raise ValueError("Select the complete priority of payments, up to 32,000 characters.")
    offset = source.find(selection)
    if offset < 0 or source.find(selection, offset + 1) >= 0:
        raise ValueError("Select an exact, unique passage from the imported contract. Include the section heading if the wording repeats.")
    start = offset + len(selection) - len(selection.lstrip())
    return describe(source, filename, PRIORITY_OF_PAYMENTS, start, offset + len(selection.rstrip()))


def describe(source, filename, kind, start, end):
    first_line = 1 + source.count("\n", 0, start)
    last_line = first_line + source.count("\n", start, end)
    locator = f"Source line {first_line}" if first_line == last_line else f"Source lines {first_line}–{last_line}"
    if filename.lower().endswith(".pdf"):
        locator = f"PDF page {1 + source.count(chr(12), 0, start)} · {locator}"
    prior_lines = source[max(0, start - 300):start].rstrip().splitlines()
    if prior_lines:
        heading = prior_lines[-1].strip()
        if len(heading) <= 110 and re.fullmatch(
                r"(?:Indenture\s+)?(?:§\s*|Section\s+)\d+(?:\.\d+)*(?:\([a-z0-9]+\))*(?:[–-]\([a-z0-9]+\))?[.:]?",
                heading, re.I):
            locator = heading + " · " + locator
    name = Path(filename).stem if filename != "Pasted text" else "Pasted contract"
    # Use an explicit title line. Bare operative language cannot establish a legal issuer.
    for line in source[:2000].splitlines()[:20]:
        clean = line.strip()
        if (len(clean) <= 130 and re.fullmatch(r"[\w &'’.,\-–]+", clean)
                and re.search(r"\btrust\b.*\b20\d\d[-–][\w-]+\b", clean, re.I)):
            name = clean
            break
    return {"kind": kind, "name": name or "Uploaded contract", "clause": locator,
            "excerpt": source[start:end]}
