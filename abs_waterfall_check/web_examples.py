"""Install the two public review examples from their preserved local artifacts."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import local


DATA = Path(__file__).with_name("web_examples_data")
FORD_INDENTURE = (
    "https://www.sec.gov/Archives/edgar/data/1129987/000110465925116294/"
    "tm2530629d11_ex4-1.htm"
)


# The public review examples establish coverage from the indenture version and
# the documented MASTR distribution respectively. Neither date is a review timestamp.
EXAMPLE_COVERAGE = {
    "c6d0c938c6244eea92bc5169cee68d80": "2025-11-01",
    "eee241c908534ec6adfe4dbbd25860bb": "2006-06-26",
}


def _install(path, content):
    """Publish a complete file once, including when two workspaces start together."""
    if not isinstance(path, Path):
        path.install(content)
        return
    path.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
    with NamedTemporaryFile(dir=path.parent) as temporary:
        temporary.write(content)
        temporary.flush()
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(f"Existing example artifact differs: {path.name}")


def _source(label, citation):
    return {"label": label, "url": citation["url"], "section": citation["section"]}


def _ford(folder):
    replay = local.load(folder / "ford-faulty.json")
    return {
        "kind": "payment_priority",
        "source_name": "Ford execution indenture",
        "source_url": FORD_INDENTURE,
        "description": "Check first-priority Class A principal and Class B interest under limited cash.",
        "cases": [{
            "id": "ford-stress",
            "label": "$50,000 cash stress",
            "description": (
                "Synthetic two-step stress. Both models distribute $50,000. The approved order "
                "pays Class A-1 principal. The reversed adapter pays Class B interest."
            ),
            "scenario": replay["scenario"],
            "adapter": "faulty",
            "synthetic": True,
            "affected_tranche": "Class A-1 principal and Class B interest",
            "filing_sources": [{"label": "Execution indenture", "url": FORD_INDENTURE,
                                "section": "§8.2(c)(iv)–(v) and §8.2(d)"}],
        }],
    }


def _mastr(folder):
    summary = local.load(folder / "summary.json")
    scenario = local.load(folder / "scenario.json")
    cases = []
    for version, label in (("original", "Original distribution"), ("amended", "Amended distribution")):
        observed = summary["results"][version]["observed"]
        cases.append({
            "id": f"mastr-{version}",
            "label": label,
            "description": f"June 26, 2006 Class P distribution, as reported in the {version} filing.",
            "scenario": scenario,
            "payouts": local.load(folder / f"{version}-observed.json"),
            "affected_tranche": "Class P",
            "filing_sources": [
                _source("Class P entitlement", summary["rule_source"]),
                _source("Corrected collection", summary["collection"]),
                _source(label, observed),
                _source("Corrective payment", summary["correction"]),
            ],
        })
    return {
        "kind": "prepayment_charges_to_class_p",
        "source_name": "MASTR prospectus supplement",
        "source_url": summary["rule_source"]["url"],
        "description": "Reconcile collected prepayment charges to the reported Class P distribution.",
        "retrospective": summary["correction"]["input_timing"],
        "cases": cases,
    }


def seed_examples(store):
    store = local.artifact_path(store)
    for deal_id, details in (("ford", _ford), ("mastr", _mastr)):
        deal_path = store / "deals" / f"{deal_id}.json"
        if deal_path.exists():
            continue
        folder = DATA / deal_id
        draft_bytes = (folder / "draft.json").read_bytes()
        rule_bytes = (folder / "rule.json").read_bytes()
        approval_bytes = (folder / "approval.json").read_bytes()
        receipt = json.loads(approval_bytes)
        draft = json.loads(draft_bytes)
        rule_id = receipt["rule_id"]
        if (hashlib.sha256(draft_bytes).hexdigest() != receipt["draft_sha256"]
                or hashlib.sha256(rule_bytes).hexdigest() != receipt["approved_rule_sha256"]):
            raise ValueError("Bundled example digest mismatch")
        _install(store / "drafts" / f"{deal_id}.json", draft_bytes)
        _install(store / "rules" / f"{rule_id}.json", rule_bytes)
        _install(store / "approvals" / f"{rule_id}.json", approval_bytes)
        local.load_approved_rule(store, rule_id)
        deal = {
            "id": deal_id,
            "name": draft["deal"],
            "clause": draft["clause"],
            "source_text": draft["contract_excerpt"],
            "approved": True,
            "effective_from": EXAMPLE_COVERAGE[rule_id],
            "rule_provenance": receipt,
            "draft": draft,
            "draft_sha256": receipt["draft_sha256"],
            "rule_id": rule_id,
            **details(folder),
        }
        # Use the approval's creation as the public example's local installation time.
        receipt_path = store / "approvals" / f"{rule_id}.json"
        timestamp = receipt_path.stat().st_mtime if isinstance(receipt_path, Path) else receipt_path.modified_time()
        deal["created_at"] = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        _install(deal_path, (json.dumps(deal, ensure_ascii=False, indent=2) + "\n").encode())
