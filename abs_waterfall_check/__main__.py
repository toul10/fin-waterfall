"""Command-line release gates and local analyst workflow."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from .engine import adapter, compare, search
from .history import replay
from .releases import check_release
from .local import approve, extract, load, load_approved_rule, register, save

EXIT_CODES = {"PASS": 0, "BLOCK": 1, "HOLD": 2}
FORD_RULE = "fixtures/ford-rule.json"


def run(args):
    if args.command == "release-cases":
        from .web import App
        app = App(args.store)
        app.deal_path(args.deal)
        print(json.dumps(app.get("/api/releases/" + args.deal), indent=2))
        return "PASS"
    if args.command in ("release-check", "release-replay"):
        def model(role):
            return {key: getattr(args, f"{role}_{key}") for key in ("id", "version", "endpoint")}
        checking = args.command == "release-check"
        if checking and args.deal and args.cases:
            raise ValueError("Use --cases with --rule-id. A workspace --deal loads its own saved cases.")
        report = check_release(args.store, args.rule_id, model("baseline") if checking else None,
                               model("candidate"), load(args.cases) if checking and args.cases else (),
                               replay_case=None if checking else load(args.case),
                               deal_id=args.deal, case_ids=args.case_id if checking else None)
        if args.output:
            save(args.output, report)
        if checking and args.export_case and "replay_case" in report:
            save(args.export_case, report["replay_case"])
        print(json.dumps(report, indent=2))
        return report["status"]
    if args.command == "extract":
        excerpt = Path(args.excerpt).read_text(encoding="utf-8")
        draft = extract(excerpt, args.section, args.deal, args.deal_version,
                        args.rule_version, args.model, args.port, args.backend,
                        rule_kind=args.rule_kind)
        save(args.output, draft)
        print(hashlib.sha256(Path(args.output).read_bytes()).hexdigest())
        return "PASS"
    if args.command == "approve":
        print(approve(args.store, args.draft, args.reviewer, args.sha256))
        return "PASS"
    if args.command == "register":
        run_id, status = register(args.store, args.rule_id, args.scenario, args.adapter)
        print(json.dumps({"run_id": run_id, "status": status}))
        return status

    if (args.store is None) != (args.rule_id is None):
        raise ValueError("Use --store and --rule-id together")
    provenance = {}
    if args.rule_id is None:
        rule = load(args.rule or FORD_RULE)
    else:
        rule, receipt = load_approved_rule(args.store, args.rule_id)
        provenance = {"rule_provenance": receipt}
    candidate = adapter(args.adapter)
    if args.command == "history":
        rows = load(args.history)
        if [row["period"] for row in rows] != [f"2026-0{month}" for month in range(5, 10)]:
            raise ValueError("Expected all five Ford collection periods in order")
        reports = [replay(row, rule, candidate) for row in rows]
        comparisons = [item for report in reports for item in report["comparisons"]]
        matches = sum(item["actual"] == item["expected"] for item in comparisons)
        status = "PASS" if matches == len(comparisons) and all(
            report["window"]["status"] == "PASS" for report in reports
        ) else "BLOCK"
        if args.output:
            save(args.output, {"status": status, "periods": reports, **provenance})
        print(f"{status}: {matches}/{len(comparisons)} class-level comparisons")
        return status
    if args.command == "search":
        evidence = search(rule, candidate)
        save(args.output, {**evidence, **provenance})
        first = evidence["first_observed_divergence"]
        return "BLOCK" if first else "HOLD"
    evidence = load(args.case)
    if evidence["synthetic"] is not True or not evidence["regression"]:
        raise ValueError("Missing synthetic regression")
    regression = evidence["regression"]
    if regression["rule"] != rule:
        raise ValueError("Review regression against the selected rule version")
    if evidence.get("rule_provenance") != provenance.get("rule_provenance"):
        raise ValueError("Regression was generated under a different rule approval")
    result = compare(rule, regression["scenario"], candidate)
    if result["expected"] != regression["expected"]:
        raise ValueError("Regression expected payouts differ from the approved rule")
    result.update(provenance)
    if args.output:
        save(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["status"]


def main():
    parser = argparse.ArgumentParser(prog="abs-waterfall-check")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("history", "search", "check"):
        command = commands.add_parser(name)
        rules = command.add_mutually_exclusive_group()
        rules.add_argument("--rule", help="Path to a standalone rule. Defaults to the bundled Ford rule.")
        rules.add_argument("--rule-id", help="ID of a rule approved in --store.")
        command.add_argument("--store", help="Local approval store for --rule-id.")
        command.add_argument("--adapter", default="good" if name != "search" else "faulty")
        command.add_argument("--output", required=name == "search")
        if name == "history":
            command.add_argument("--history", default="fixtures/ford-history.json")
        if name == "check":
            command.add_argument("case")

    command = commands.add_parser("release-cases", help="List available workspace release cases")
    command.add_argument("--deal", required=True)
    command.add_argument("--store", required=True)
    for name in ("release-check", "release-replay"):
        command = commands.add_parser(name)
        command.add_argument("--store", required=True)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--rule-id", help="Use an approved rule in the store")
        source.add_argument("--deal", help="Use a workspace deal and its approved terms and saved cases")
        command.add_argument("--output")
        roles = ("baseline", "candidate") if name == "release-check" else ("candidate",)
        for role in roles:
            for field in ("id", "version", "endpoint"):
                command.add_argument(f"--{role}-{field}", required=True)
        if name == "release-check":
            command.add_argument("--cases", help="JSON array of saved cases with id, label and scenario")
            command.add_argument("--case-id", action="append", help="Select a saved case or generated boundary")
            command.add_argument("--export-case", help="Write the first candidate failure for replay")
        else:
            command.add_argument("case")
    command = commands.add_parser("extract")
    command.add_argument("excerpt")
    for field in ("section", "deal", "deal-version", "rule-version", "output"):
        command.add_argument("--" + field, required=True)
    command.add_argument("--model", help="Local model name. Omit for the deterministic stub.")
    command.add_argument("--backend", choices=("ollama", "llama.cpp"), default="ollama")
    command.add_argument("--port", type=int, help="Loopback port (Ollama 11434, llama.cpp 8080).")
    command.add_argument("--rule-kind", choices=("payment_priority", "prepayment_charges_to_class_p",
                                                 "priority_of_payments"),
                         default="payment_priority",
                         help="priority_of_payments drafts every step of a selected clause and requires --model.")

    command = commands.add_parser("approve")
    command.add_argument("draft")
    for field in ("store", "reviewer", "sha256"):
        command.add_argument("--" + field, required=True)

    command = commands.add_parser("register")
    command.add_argument("scenario")
    command.add_argument("--store", required=True)
    command.add_argument("--rule-id", required=True)
    command.add_argument("--adapter", default="good")

    args = parser.parse_args()
    try:
        status = run(args)
        if args.command == "search":
            print(f"{status}: search audit saved to {args.output}")
        return EXIT_CODES[status]
    except Exception as error:
        print(f"HOLD: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
