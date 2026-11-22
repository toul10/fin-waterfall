"""Verify MASTR 2006-HE1 Class P using a reviewed local Qwen draft."""
import argparse
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp

from demo_support import FIXTURES, ROOT, approve_reviewed_draft, cli, read_json, require, write_json

RULE_KIND = "prepayment_charges_to_class_p"


def demo(args, output):
    case = read_json(FIXTURES / "mastr-2006-he1-class-p.json")
    draft_path = output / "draft.json"
    digest = cli(
        "extract", args.excerpt.resolve(), "--backend", "llama.cpp", "--model", args.model,
        "--port", args.port, "--rule-kind", RULE_KIND, "--section", case["rule_source"]["section"],
        "--deal", case["deal"], "--deal-version", case["deal_version"],
        "--rule-version", case["rule_version"], "--output", draft_path,
    )
    draft = read_json(draft_path)
    require(draft["type"] == RULE_KIND and draft["recipient"] == "class_p",
            f"The Qwen draft does not establish the Class P rule. Review {draft_path}")
    approval = approve_reviewed_draft(
        args.reviewer, output, draft_path, digest, draft, "Recipient of all collected charges: Class P",
    )

    collected = case["collection"]["amount_cents"]
    scenario = output / "scenario.json"
    write_json(scenario, {"collected_prepayment_charges": collected})
    results = {}
    print(f"\nRetrospective check for the {case['distribution_date']} distribution", flush=True)
    for label, status, exit_code in (("original", "BLOCK", 1), ("amended", "PASS", 0)):
        filing = case["observed"][label]
        paid = filing["amount_cents"]
        observed = output / f"{label}-observed.json"
        write_json(observed, {"class_p": paid})
        receipt = json.loads(cli(
            "register", scenario, "--store", output, "--rule-id", approval["rule_id"],
            "--adapter", f"observed:{observed}", exit_code=exit_code,
        ))
        audit_path = Path("runs") / f"{receipt['run_id']}.json"
        audit = read_json(output / audit_path)
        delta = f"{Decimal(paid - collected) / 100:.2f}"
        require(
            audit["rule_provenance"] == approval and audit["status"] == status
            and audit["scenario"] == {"collected_prepayment_charges": collected}
            and audit["expected"] == {"class_p": collected} and audit["candidate"] == {"class_p": paid}
            and audit["delta_dollars"] == {"class_p": delta},
            f"Unexpected result. Review {output / audit_path}",
        )
        results[label] = {"status": status, "exit_code": exit_code, "class_p_delta_usd": delta,
                          "observed": filing, "audit": str(audit_path)}
        print(f"{label}: {status}, exit {exit_code}, Class P delta USD {delta}", flush=True)

    require(collected - case["observed"]["original"]["amount_cents"]
            == case["correction"]["class_p_corrective_payment_cents"],
            "Payout shortfall disagrees with the amendment's correction")
    write_json(output / "summary.json", {
        "deal": case["deal"],
        "distribution_date": case["distribution_date"],
        "rule_source": case["rule_source"],
        "rule_provenance": approval,
        "collection": case["collection"],
        "correction": case["correction"],
        "results": results,
    })
    print(f"\nVerified cases. Audit summary {output / 'summary.json'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excerpt", type=Path, required=True, help="Public prospectus lines 4630–4633")
    parser.add_argument("--model", default="qwen-local", help="Model alias on the loopback server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    try:
        output_root = ROOT / "out"
        output_root.mkdir(mode=0o700, exist_ok=True)
        demo(args, Path(mkdtemp(prefix="mastr-class-p-", dir=output_root)))
    except (EOFError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Demo incomplete. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
