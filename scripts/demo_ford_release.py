"""Run the reviewed Qwen release demo against a loopback llama.cpp server."""
import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import mkdtemp

from demo_support import FIXTURES, ROOT, approve_reviewed_draft, cli, read_json, require, write_json

FORD_CLAUSE = "Indenture §8.2(c)(iv)–(v)"
REVERSE_CLAUSE = "Synthetic contract §4.2(i)–(ii)"
PRINCIPAL = "class_a_principal"
INTEREST = "class_b_interest"


def review_and_approve(args, output, excerpt, name, deal, version, clause, order, markers):
    draft_path = output / f"{name}-draft.json"
    draft_digest = cli(
        "extract", excerpt, "--backend", "llama.cpp", "--model", args.model, "--port", args.port,
        "--section", clause, "--deal", deal, "--deal-version", version, "--rule-version", "1.0.0",
        "--output", draft_path,
    )
    draft = read_json(draft_path)
    require(draft["order"] == order and all(marker in draft["support"] for marker in markers),
            f"The Qwen draft did not establish both priorities. Review {draft_path}")
    description = f"Order: {' then '.join(draft['order'])}"
    return approve_reviewed_draft(args.reviewer, output, draft_path, draft_digest, draft, description)


def demo(args, output):
    ford = review_and_approve(
        args, output, args.excerpt.resolve(), "ford", "Ford Credit Auto Owner Trust 2025-C",
        "2025-11-01", FORD_CLAUSE, [PRINCIPAL, INTEREST], ("(iv)", "(v)"),
    )
    ford_selection = ("--store", output, "--rule-id", ford["rule_id"])
    model_file = FIXTURES / "json_payment_adapter.py"
    adapters = {mode: "json:" + shlex.join([sys.executable, str(model_file), mode])
                for mode in ("good", "faulty")}

    case_path = output / "ford-regression.json"
    cli("search", *ford_selection, "--adapter", adapters["faulty"], "--output", case_path, exit_code=1)
    case = read_json(case_path)
    require(case["rule_provenance"] == ford, "Counterexample does not bind the Ford approval")
    ford_expected = {PRINCIPAL: 5_000_000, INTEREST: 0}
    reverse_expected = {PRINCIPAL: 0, INTEREST: 5_000_000}
    print("\nFord synthetic release case", flush=True)
    for mode, status, exit_code in (("good", "PASS", 0), ("faulty", "BLOCK", 1)):
        report_path = output / f"ford-{mode}.json"
        cli("check", case_path, *ford_selection, "--adapter", adapters[mode], "--output", report_path,
            exit_code=exit_code)
        report = read_json(report_path)
        expected_candidate = ford_expected if mode == "good" else reverse_expected
        require(report["rule_provenance"] == ford and report["status"] == status
                and report["expected"] == ford_expected and report["candidate"] == expected_candidate,
                f"Unexpected Ford replay result. Review {report_path}")
        print(f"{status} {mode} adapter, exit {exit_code}", flush=True)

    reverse = review_and_approve(
        args, output, FIXTURES / "synthetic-reversed-priority.txt", "reverse",
        "Synthetic reverse-priority deal", "1", REVERSE_CLAUSE, [INTEREST, PRINCIPAL],
        ("(i)", "(ii)"),
    )
    scenario_path = output / "synthetic-scenario.json"
    write_json(scenario_path, case["regression"]["scenario"])
    reverse_runs = {}
    print("\nSame synthetic inputs with reversed priorities", flush=True)
    for mode, status, exit_code in (("good", "BLOCK", 1), ("faulty", "PASS", 0)):
        receipt = json.loads(cli("register", scenario_path, "--store", output,
                                 "--rule-id", reverse["rule_id"], "--adapter", adapters[mode],
                                 exit_code=exit_code))
        report = read_json(output / "runs" / f"{receipt['run_id']}.json")
        expected_candidate = ford_expected if mode == "good" else reverse_expected
        require(report["rule_provenance"] == reverse and report["status"] == status
                and report["expected"] == reverse_expected and report["candidate"] == expected_candidate,
                "Reverse rule did not flip the expected payouts and release result")
        reverse_runs[mode] = receipt
        print(f"{status} {mode} adapter, exit {exit_code}", flush=True)

    print("\nRunning automated tests", flush=True)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                   cwd=ROOT, check=True)
    write_json(output / "summary.json", {
        "ford_rule": ford,
        "ford_regression": case_path.name,
        "ford_replays": {"good": "ford-good.json", "faulty": "ford-faulty.json"},
        "reverse_rule": reverse,
        "reverse_runs": reverse_runs,
        "automated_tests": "PASS",
    })
    print(f"\nVerified demo. Audit directory {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excerpt", type=Path, required=True, help="Public Ford §8.2(c)(iv)–(v) text")
    parser.add_argument("--model", default="qwen-local", help="Model alias on the loopback server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    try:
        output_root = ROOT / "out"
        output_root.mkdir(mode=0o700, exist_ok=True)
        output = Path(mkdtemp(prefix="ford-release-", dir=output_root))
        print(f"Audit directory {output}", flush=True)
        demo(args, output)
    except (EOFError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Demo incomplete. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
