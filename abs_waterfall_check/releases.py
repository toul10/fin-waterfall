"""Compare locally served cashflow model releases against a receipted rule."""
import hashlib
import json
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlsplit
from uuid import uuid4

from . import local
from .engine import STEPS, compare, expected_payout

TIMEOUT = 2
MAX_RESPONSE = 64 * 1024


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


SINGLE_CLAUSE_ONLY = ("Release comparisons cover the two single-clause rules. Check a general "
                      "priority of payments with a payment comparison.")


def comparison_cases(rule, cases):
    if rule.get("schema") not in (1, 2):
        raise ValueError(SINGLE_CLAUSE_ONLY)
    if not isinstance(cases, (list, tuple)) or any(not isinstance(c, dict)
            or not isinstance(c.get("id"), str) or not c["id"].strip() for c in cases):
        raise ValueError("Supply a JSON array of saved cases, each with a nonempty string ID")
    rows = [{"id": c["id"], "label": c.get("label", c["id"]), "scenario": c.get("scenario"),
             "source": "saved"} for c in cases]
    if rule["schema"] == 1:
        dues = dict(zip(STEPS, (10_000_000, 11_105_633)))
        priority = dues[rule["order"][0]]
        total = sum(dues.values())
        points = [("zero", 0), ("one-cent", 1), ("shortfall", 5_000_000),
                  ("priority-minus-one", priority - 1), ("priority-funded", priority),
                  ("priority-plus-one", priority + 1), ("total-minus-one", total - 1),
                  ("fully-funded", total), ("surplus", total + 1)]
        generated = [(name, {"cash": cash, **dues}) for name, cash in points]
    else:
        generated = [(name, {"collected_prepayment_charges": amount}) for name, amount in
                     [("zero", 0), ("one-cent", 1), ("one-dollar", 100), ("large-collection", 10_000_001)]]
    rows.extend({"id": "boundary-" + name, "label": name.replace("-", " ").capitalize(),
                 "scenario": scenario, "source": "boundary"} for name, scenario in generated)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Give each saved case a unique ID distinct from the generated boundaries")
    return rows


def model_spec(model):
    if not isinstance(model, dict) or any(not isinstance(model.get(k), str)
            or not model[k].strip() for k in ("id", "version", "endpoint")):
        raise ValueError("Supply a model ID, version and local HTTP endpoint")
    endpoint = urlsplit(model["endpoint"])
    if (endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "::1", "localhost")
            or endpoint.username is not None or endpoint.password is not None
            or endpoint.query or endpoint.fragment or not endpoint.port):
        raise ValueError("Configure an HTTP loopback endpoint with an explicit port and path")
    return endpoint


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"Duplicate JSON field {key}")
        obj[key] = value
    return obj


def calculate(model, scenario):
    endpoint = model_spec(model)
    host = "::1" if endpoint.hostname == "::1" else "127.0.0.1"
    conn = HTTPConnection(host, endpoint.port, timeout=TIMEOUT)
    try:
        conn.request("POST", endpoint.path or "/", json.dumps(scenario), {"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"Model returned HTTP {response.status}")
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("Model response exceeds 64 KiB")
        data = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {"model_id", "version", "payouts"}:
            raise ValueError("Return a JSON object with model_id, version and payouts")
        if (data.get("model_id"), data.get("version")) != (model["id"], model["version"]):
            raise ValueError(f"Expected {model['id']} {model['version']}, service reported "
                             f"{data.get('model_id')} {data.get('version')}")
        return data["payouts"]
    except TimeoutError as error:
        raise ValueError(f"{model['id']} {model['version']} timed out after {TIMEOUT}s; "
                         "restart or repair the local service and retry") from error
    finally:
        conn.close()


def observation(rule, scenario, model):
    try:
        outcome = compare(rule, scenario, lambda inputs: calculate(model, inputs))
        return {"status": outcome["status"], "payouts": outcome["candidate"]}
    except (OSError, ValueError, KeyError, TypeError, HTTPException) as error:
        return {"status": "HOLD", "reason": str(error) or type(error).__name__, "payouts": None}


def captured_case(report, row):
    case = {"format": "waterfall-release-case-v1", "origin_run_id": report["run_id"],
            "deal_id": report["deal_id"], "rule_provenance": report["rule_provenance"],
            "baseline_model": report["baseline"], "candidate_model": report["candidate_model"], "row": row}
    return {**case, "case_sha256": digest(case)}


def replay_row(root, receipt, case, deal_id, rule):
    if not isinstance(case, dict) or case.get("format") != "waterfall-release-case-v1":
        raise ValueError("Import a waterfall release regression case")
    if digest({k: v for k, v in case.items() if k != "case_sha256"}) != case.get("case_sha256"):
        raise ValueError("Re-export the case from its original audit; its checksum differs")
    if case["rule_provenance"] != receipt or (deal_id is not None and case["deal_id"] != deal_id):
        raise ValueError("Select the same deal and approved rule version that captured the case")
    if not local.ID_PATTERN.fullmatch(case["origin_run_id"]):
        raise ValueError("Invalid originating run ID")
    path = local.artifact_path(root) / "runs" / (case["origin_run_id"] + ".json")
    if path.exists() and case not in local.load(path).get("replay_cases", []):
        raise ValueError("Re-export the original case from the local audit")
    row = case["row"]
    if (not isinstance(row, dict) or not isinstance(row.get("candidate"), dict)
            or not isinstance(row.get("baseline"), dict)
            or row["candidate"].get("status") != "BLOCK"):
        raise ValueError("Import a case containing the captured candidate failure and baseline observation")
    model_spec(case["candidate_model"])
    observed = row["candidate"]["payouts"]
    check = compare(rule, row["scenario"], lambda _: observed)
    if check["status"] != "BLOCK" or digest(row["expected"]) != digest(check["expected"]):
        raise ValueError("Import a captured candidate failure matching this approved rule")
    original = row["baseline"]
    if original.get("status") != "HOLD":
        baseline = compare(rule, row["scenario"], lambda _: original["payouts"])
        if baseline["status"] != original.get("status"):
            raise ValueError("Captured baseline status differs from the approved rule")
    elif not original.get("reason") or original.get("payouts") is not None:
        raise ValueError("Captured incomplete baseline is missing its failure reason")
    return {k: row[k] for k in ("id", "label", "source", "scenario")}


def check_release(root, rule_id, baseline, candidate, cases=(), *, deal_id=None, replay_case=None,
                  case_ids=None):
    report = {"run_id": uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
              "mode": "release", "deal_id": deal_id, "approved_rule_id": rule_id,
              "baseline": baseline, "candidate_model": candidate, "rows": [], "status": "HOLD"}
    try:
        if deal_id is not None and rule_id is None:
            from .web import App
            app = App(root)
            deal = local.load(app.deal_path(deal_id))
            if deal.get("id") != deal_id:
                raise ValueError("Restore the workspace deal record matching the selected deal ID")
            report["deal_name"] = deal["name"]
            rule, receipt = app.approval(deal)
            report["approved_rule_id"] = receipt["rule_id"]
            if replay_case is None:
                cases = app.release_saved_cases(deal)
                if case_ids is None:
                    cases = [case for case in cases if case["id"] != "missing-input"]
        else:
            rule, receipt = local.load_approved_rule(root, rule_id)
        report.update(rule=rule, rule_provenance=receipt, deal_name=rule["deal"])
        if rule.get("schema") not in (1, 2):
            raise ValueError(SINGLE_CLAUSE_ONLY)
        if (replay_case is None and isinstance(baseline, dict) and isinstance(candidate, dict)
                and (baseline.get("id"), baseline.get("version")) == (candidate.get("id"), candidate.get("version"))):
            raise ValueError("Select different current and candidate model versions")
        if replay_case is not None:
            selected = [replay_row(root, receipt, replay_case, deal_id, rule)]
            report["origin_run_id"] = replay_case["origin_run_id"]
            report["deal_id"] = replay_case["deal_id"]
            if baseline is None:
                report["baseline"] = replay_case["baseline_model"]
        else:
            selected = comparison_cases(rule, cases)
        if case_ids is not None:
            if (not isinstance(case_ids, (list, tuple)) or not case_ids or
                    any(not isinstance(case_id, str) for case_id in case_ids)
                    or len(case_ids) != len(set(case_ids))):
                raise ValueError("Select at least one payment case and include each case only once")
            selected = [row for row in selected if row["id"] in case_ids]
            if not selected or {row["id"] for row in selected} != set(case_ids):
                raise ValueError("Select at least one available case and remove unknown case IDs")
        for row in selected:
            scenario = row["scenario"]
            try:
                row["expected"] = expected_payout(rule, scenario)
            except (TypeError, ValueError, KeyError) as error:
                missing = {"status": "HOLD", "payouts": None, "reason": str(error)}
                row.update(expected=None, baseline=missing, candidate=missing, delta_cents={},
                           candidate_minus_current_cents={}, input_error=str(error))
                report["rows"].append(row)
                continue
            row["baseline"] = (observation(rule, scenario, baseline) if baseline is not None or replay_case is None
                               else replay_case["row"]["baseline"])
            row["candidate"] = observation(rule, scenario, candidate)
            proposed = row["candidate"]["payouts"]
            current = row["baseline"]["payouts"]
            row["delta_cents"] = {}
            row["candidate_minus_current_cents"] = {}
            if proposed is not None:
                row["delta_cents"] = {k: proposed[k] - expected for k, expected in row["expected"].items()}
                if current is not None:
                    row["candidate_minus_current_cents"] = {k: proposed[k] - current[k] for k in proposed}
            report["rows"].append(row)
        failed = next((r for r in report["rows"] if r["candidate"]["status"] == "BLOCK"), None)
        held = next(((r, m) for r in report["rows"] for m in ("baseline", "candidate")
                     if r[m]["status"] == "HOLD"), None)
        if failed:
            report["replay_cases"] = [captured_case(report, row) for row in report["rows"]
                                      if row["candidate"]["status"] == "BLOCK"]
            report["replay_case"] = report["replay_cases"][0]
        report["incomplete"] = [{"case_id": row["id"], "role": role, "reason": row[role]["reason"]}
                                for row in report["rows"] for role in ("baseline", "candidate")
                                if row[role]["status"] == "HOLD"]
        report["baseline_failures"] = [r["id"] for r in report["rows"] if r["baseline"]["status"] == "BLOCK"]
        if failed:
            report.update(status="BLOCK", reason=f"Candidate payout differs on {failed['label']}.",
                          action="Correct the candidate payout and replay the exported case.")
            if held:
                report["action"] += " Resolve the listed incomplete checks and rerun the full selection."
        elif held:
            row, role = held
            if "input_error" in row:
                report.update(reason=f"{row['label']}: {row['input_error']}",
                              action=f"Correct the inputs for {row['label']} ({row['id']}): "
                                     f"{row['input_error']}. Rerun the case.")
            else:
                model = report["baseline" if role == "baseline" else "candidate_model"]
                name = model.get("id") if isinstance(model, dict) else "unconfigured model"
                reason = row[role]["reason"]
                report.update(reason=f"{role} {name} / {row['label']}: {reason}",
                              action=f"Check the {role} service {name}: {reason}. Rerun {row['label']}.")
        else:
            report.update(status="PASS", reason="Candidate matched the approved rule on every selected case.",
                          action="Review the recorded comparison for this release.")
    except (ValueError, KeyError, TypeError, OSError) as error:
        report.update(reason=str(error), action=f"Resolve the following input or approval issue and rerun: {error}")
    local.save(local.artifact_path(root) / "runs" / (report["run_id"] + ".json"), report)
    return report
