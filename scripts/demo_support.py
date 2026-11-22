"""Shared CLI and review steps for the local verification demos."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def cli(*arguments, exit_code=0):
    result = subprocess.run(
        [sys.executable, "-m", "abs_waterfall_check", *map(str, arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != exit_code:
        raise RuntimeError(
            f"{arguments[0]} exited {result.returncode}, expected {exit_code}. "
            f"{result.stderr.strip()} {result.stdout.strip()}"
        )
    return result.stdout.strip()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def approve_reviewed_draft(reviewer, output, draft_path, digest, draft, description):
    print(f"\nDraft for {draft['clause']}\n{description}\n", flush=True)
    print(draft["support"], flush=True)
    print(f"\nSHA-256 {digest}\nDraft file {draft_path}", flush=True)
    if input(f"{reviewer}, type approve to approve this exact draft: ").strip() != "approve":
        raise RuntimeError("Approval was declined")
    rule_id = cli("approve", draft_path, "--sha256", digest, "--reviewer", reviewer, "--store", output)
    approved_path = output / "rules" / f"{rule_id}.json"
    return {
        "rule_id": rule_id,
        "approved_rule_sha256": hashlib.sha256(approved_path.read_bytes()).hexdigest(),
        "draft_sha256": digest,
        "clause": draft["clause"],
    }
