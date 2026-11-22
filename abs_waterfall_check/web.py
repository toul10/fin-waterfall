"""Loopback analyst workspace and local HTTP transport."""
from contextlib import ExitStack
import base64
import hashlib
import json
import math
import os
import selectors
import secrets
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import access, draft_providers, engine, intake, local
from . import allocation_evidence
from .web_examples import EXAMPLE_COVERAGE

MAX_BODY = 16 * 1024 * 1024
MAX_TEXT = 2_000_000
STATIC = Path(__file__).with_name("web_static")
STATIC_ASSETS = {
    "index.html", "app.js", "app.css", "portfolio.css", "flow.css", "result.css",
    "guide.js", "guide.css", "releases.js", "release.css", "spreadsheet.js", "allocation.js",
    "ford-stress.csv", "mastr-original.csv", "mastr-amended.csv", "mastr-sample.pdf",
    "login.html", "login.css", "login.js",
}
STATIC_MEDIA = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                ".csv": "text/csv", ".pdf": "application/pdf"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def workspace_id(root):
    if hasattr(root, "database"):
        return root.database.workspace_id
    paths = [str(Path(__file__).resolve().parent), str(Path(root).resolve())]
    return sha(json.dumps(paths).encode())


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


def canonical_json(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def case_digest(case):
    return sha(canonical_json({k: v for k, v in case.items() if k != "case_sha256"}).encode())


def dashboard_run(report):
    summary = {k: v for k, v in report.items()
               if k not in ("rule", "search", "scenario", "case", "replay_case", "rows", "replay_cases", "submitted_inputs")}
    summary["largest_underpayment"] = None
    summary["review_scope"] = report["run_id"]
    if report.get("mode") == "release":
        rows = report.get("rows", [])
        summary["case_count"] = len(rows)
        summary["failed_case_count"] = sum(r.get("candidate", {}).get("status") == "BLOCK" for r in rows)
        summary["incomplete_case_count"] = len({i["case_id"] for i in summary.pop("incomplete", [])})
        summary["baseline_failure_count"] = len(summary.pop("baseline_failures", []))
        summary.setdefault("deal_name", report.get("rule", {}).get("deal", ""))
        return summary
    expected, proposed = report.get("expected"), report.get("candidate")
    if (report.get("status") not in ("PASS", "BLOCK")
            or not isinstance(expected, dict) or not isinstance(proposed, dict)):
        return summary
    scope = {key: report.get(key) for key in (
        "deal_id", "case_id", "mode", "scenario", "adapter", "adapter_spec_sha256",
        "origin_run_id", "rule_provenance", "candidate", "distribution_date", "payout_purpose")}
    if report.get("deal_id") and report.get("mode") in ("check", "replay", "search"):
        summary["review_scope"] = sha(canonical_json(scope).encode())
    # Accepted pro rata cent rounding is not an underpayment.
    rounded = set(report.get("rounding_accepted") or ())
    for key in sorted(expected):
        if key in rounded or type(expected[key]) is not int or type(proposed.get(key)) is not int:
            continue
        difference = expected[key] - proposed[key]
        largest = summary["largest_underpayment"]
        if difference > 0 and (largest is None or difference > largest["amount_cents"]):
            summary["largest_underpayment"] = {
                "tranche": key, "expected_cents": expected[key], "proposed_cents": proposed[key],
                "amount_cents": difference}
    rule = report.get("rule")
    if isinstance(rule, dict) and rule.get("schema") == 3:
        # Dashboard rows omit the rule, so they carry the names of the payments that differ.
        try:
            names = engine.waterfall_names(rule)
        except (KeyError, TypeError, AttributeError):
            names = {}
        summary["payment_names"] = {key: names[key] for key in sorted(expected) if key in names
                                    and key not in rounded and expected[key] != proposed.get(key)}
    return summary


def review_context(report, deal):
    """Attach filing evidence only when the saved case supplied the relevant inputs."""
    report = dict(report)
    report.setdefault("mode", "search" if "search" in report else "check")
    if not deal or deal.get("id") != "mastr":
        return report
    case_id = report.get("case_id")
    if report["mode"] == "replay":
        case_id = report.get("collection_case_id")
    case = next((c for c in deal.get("cases", []) if c["id"] == case_id), None)
    historical = bool(case and report["mode"] in ("check", "replay")
                      and report.get("scenario") == case["scenario"])
    filed = bool(historical and report.get("adapter") == "observed"
                 and report.get("candidate") == case["payouts"]
                 and (report["mode"] != "replay" or report.get("replayed_filed_payout")))
    report.update(filed_payout=filed, retrospective=None, filing_sources=[])
    if historical:
        report["distribution_date"] = "2006-06-26"
        report["collection_case_id"] = case_id
        report["retrospective"] = (deal["retrospective"] if filed else
            "The collection input comes from the amendment filed after the June 26 distribution.")
        report["filing_sources"] = [s for s in case["filing_sources"]
                                    if filed or s["label"] in ("Class P entitlement", "Corrected collection")]
    if case and report["mode"] == "check" and not filed:
        report["label"] = "Payout check"
    return report


def identifier(value):
    if not isinstance(value, str) or not local.ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid record ID")
    return value


def distribution_date(value):
    if value is None or value == "":
        return None
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except (TypeError, ValueError):
        pass
    raise ValueError("Enter a valid distribution date in YYYY-MM-DD format")


def waterfall_fields(rule):
    """Worksheet inputs and payments of a general priority of payments, or None."""
    try:
        engine.validate_rule({**rule, "approved_by": rule.get("approved_by") or "displayed draft"})
    except (ValueError, TypeError, AttributeError):
        return None
    if rule.get("schema") != 3:
        return None
    return (engine.FUNDS, *engine.waterfall_dues(rule)), tuple(engine.waterfall_payees(rule))


def held_check_inputs(inputs, kind, rule=None):
    fields = (("collected_prepayment_charges",), ("class_p",)) if (
        kind == engine.PREPAYMENT_CHARGES_TO_CLASS_P) else (("cash", *engine.STEPS), engine.STEPS)
    if kind == engine.PRIORITY_OF_PAYMENTS:
        fields = waterfall_fields(rule) if isinstance(rule, dict) else None
        fields = fields or ((engine.FUNDS,), ())
    retained = {}
    for source, target, keys in zip(("scenario", "payouts"), ("scenario", "submitted_payouts"), fields):
        values = inputs.get(source)
        if not isinstance(values, dict) or (source == "payouts" and inputs.get("adapter", "observed") != "observed"):
            continue
        retained[target] = {k: values[k] for k in keys if type(values.get(k)) is int and values[k] >= 0}
    return retained


def required(body, key, limit=500):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Provide {key} (up to {limit:,} characters)")
    return value.strip()


def pdf_text(raw):
    if not raw.startswith(b"%PDF-"):
        raise ValueError("The uploaded file has no PDF header")
    executable = shutil.which("pdftotext")
    if not executable:
        raise ValueError("PDF import requires Poppler pdftotext. Paste the clause or import UTF-8 text.")
    command = [executable, "-f", "1", "-l", "501", "-enc", "UTF-8", "-", "-"]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process, selectors.DefaultSelector() as streams:
        deadline = time.monotonic() + 40
        encoded = bytearray()
        remaining = memoryview(raw)
        streams.register(process.stdin, selectors.EVENT_WRITE)
        streams.register(process.stdout, selectors.EVENT_READ)
        try:
            while streams.get_map():
                for key, _ in streams.select(max(0, deadline - time.monotonic())):
                    if key.fileobj is process.stdin:
                        try:
                            remaining = remaining[os.write(key.fd, remaining[:4096]):]
                        except BrokenPipeError:
                            remaining = remaining[:0]
                        if not remaining:
                            streams.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            streams.unregister(process.stdout)
                        encoded.extend(chunk)
                        if len(encoded) > MAX_TEXT * 4:
                            raise ValueError("PDF text exceeds the import limit")
                if time.monotonic() >= deadline:
                    raise ValueError("PDF text extraction timed out")
            if process.wait(timeout=max(0, deadline - time.monotonic())):
                raise ValueError("PDF text extraction failed. Paste the clause or import UTF-8 text.")
        finally:
            if process.poll() is None:
                process.kill()
    value = encoded.decode("utf-8")
    if value.count("\f") > 500 or len(value) > MAX_TEXT:
        raise ValueError("Import a selection of up to 500 pages and 2 million characters")
    if not value.strip():
        raise ValueError("No selectable text found. Paste the clause or import an OCR text file.")
    return value


def import_contract(body, *, validate_extension):
    source_name = "Pasted text"
    file = body.get("file")
    if file:
        if not isinstance(file, dict):
            raise ValueError("Upload a named PDF or UTF-8 text file")
        source_name = Path(required(file, "name")).name
        if validate_extension and not source_name.lower().endswith((".pdf", ".txt")):
            raise ValueError("Upload a searchable PDF or UTF-8 text file.")
        encoded = file.get("content_base64")
        if not isinstance(encoded, str):
            raise ValueError("Choose the source document again.")
        raw = base64.b64decode(encoded, validate=True)
        source = pdf_text(raw) if source_name.lower().endswith(".pdf") else raw.decode("utf-8-sig")
    else:
        source = body.get("source_text")
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_TEXT:
        raise ValueError("Provide source text of up to 2 million characters")
    return {"source_name": source_name, "source_text": source.replace("\r\n", "\n").replace("\r", "\n"),
            "source_sha256": sha(raw if file else source.encode("utf-8"))}


class Handler(BaseHTTPRequestHandler):
    server_version = "WaterfallWorkspace/1"

    def log_message(self, fmt, *args):
        pass

    def respond(self, status, payload, mime="application/json", attachment=None, cookie=None):
        if mime == "application/json":
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'; object-src 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def dispatch(self, post):
        policy = self.server.access
        edition = getattr(policy, "edition", {"private": "self-hosted", "local": "development", "demo": "demo"}[policy.mode])
        # Platform probes can arrive with a different Host from the browser origin.
        if edition == "hosted" and not post and self.path in ("/healthz", "/readyz"):
            healthy = True
            if self.path == "/readyz":
                try:
                    healthy = policy.database.health()
                except Exception:
                    healthy = False
            return self.respond(200 if healthy else 503, {"status": "ok" if healthy else "unavailable"})
        port = self.server.server_port
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        allowed_hosts = ({urlsplit(policy.origin).netloc} if policy.origin
                         else {f"127.0.0.1:{port}", f"localhost:{port}"})
        # A link from another site may open the static landing page in a tab.
        # Subsequent requests for workspace data must originate in the workspace.
        landing_navigation = (not post and self.path == "/" and origin is None
                              and self.headers.get("Sec-Fetch-Mode") == "navigate"
                              and self.headers.get("Sec-Fetch-Dest") == "document")
        if (host not in allowed_hosts or (origin is not None and origin != (policy.origin or f"http://{host}"))
                or (self.headers.get("Sec-Fetch-Site") == "cross-site" and not landing_navigation)):
            return self.respond(403, {"error": "Invalid workspace origin"})
        path = urlsplit(self.path).path
        try:
            if not post and path == "/healthz":
                return self.respond(200, {"status": "ok"})
            cookie = self.headers.get("Cookie")
            identity = policy.identity(cookie)
            login = policy.mode == "private" and post and path == "/api/login"
            login_page = not post and path in ("/", "/login.js", "/login.css")
            if not identity and not (login or login_page):
                return self.respond(401, {"error": "Sign in to your workspace"})
            role = identity["role"] if identity else None
            parts = path.strip("/").split("/")
            if not post and path == "/api/session":
                return self.respond(200, {"mode": policy.mode, "edition": edition, "organization": policy.organization, **identity})
            if policy.mode == "demo" and (post or urlsplit(self.path).query):
                return self.respond(403, {"error": "Sample cases are read-only"})
            if policy.mode == "private" and role != "operator" and (
                    path.startswith("/api/releases") or path in ("/api/instance", "/api/operator")
                    or (parts[:2] == ["api", "runs"] and len(parts) >= 4 and parts[3] in ("replay", "cases", "audit"))):
                return self.respond(403, {"error": "Operator access required"})
            if not post and path == "/api/operator" and role in ("operator", "local"):
                return self.respond(200, {"mode": policy.mode, "organization": policy.organization,
                                          "local_drafting": self.server.app.local_drafting,
                                          "pricing_models": list(self.server.app.pricing_models)})
            if post:
                if self.headers.get_content_type() != "application/json":
                    return self.respond(415, {"error": "Send an application/json object"})
                length = int(self.headers.get("Content-Length", -1))
                if not 0 <= length <= (4096 if login else MAX_BODY):
                    return self.respond(413, {"error": "Request exceeds the size limit or has no length"})
                body = json.loads(self.rfile.read(length), parse_constant=finite_float, parse_float=finite_float)
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if login:
                    token = policy.login(body.get("username"), body.get("password"))
                    if not token:
                        return self.respond(401, {"error": "Invalid account credentials"})
                    return self.respond(200, {"ok": True}, cookie=policy.cookie(token))
                if path == "/api/logout" and policy.mode == "private":
                    policy.logout(cookie)
                    return self.respond(200, {"ok": True}, cookie=policy.cookie(""))
                if policy.mode == "private" and len(parts) == 4 and parts[:2] == ["api", "deals"]:
                    action = parts[-1]
                    if action == "approve":
                        if role not in ("reviewer", "operator"):
                            return self.respond(403, {"error": "Reviewer access required"})
                        try:
                            effective = distribution_date(body.get("effective_from"))
                        except ValueError:
                            effective = None
                        if not effective:
                            return self.respond(400, {"error": "Enter a valid effective date in YYYY-MM-DD format"})
                        body["reviewer"] = identity["username"]
                    if action == "draft-preview" and edition != "hosted":
                        return self.respond(403, {"error": "Gemini drafting is available only in the hosted workspace"})
                    if action == "draft" and edition == "hosted" and body.get("provider") != "gemini":
                        return self.respond(403, {"error": "Select Gemini and confirm the exact excerpt first"})
                    if action == "draft" and edition != "hosted" and body.get("provider", "local") != "local":
                        return self.respond(403, {"error": "Private documents require local drafting"})
                    if action == "draft" and edition != "hosted" and not self.server.app.local_drafting:
                        return self.respond(400, {"error": "Local drafting is unavailable. Contact the workspace operator."})
                    sample_ford = (parts[2] == "ford" and body.get("case_id") == "ford-stress"
                                   and body.get("adapter") == "faulty")
                    if action == "runs" and role != "operator" and (
                            body.get("mode") != "check" or (body.get("adapter", "observed") != "observed" and not sample_ford)):
                        return self.respond(403, {"error": "Operator access required"})
                if edition == "hosted":
                    result = self.server.app.post(path, body, actor=identity["username"])
                else:
                    result = self.server.app.post(path, body)
                if role in ("analyst", "reviewer") and parts[-1] == "runs":
                    result = distribution_view(result)
                return self.respond(200, result)
            if path.startswith("/api/"):
                report = len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "report"
                payload = self.server.app.get(path.removesuffix("/report") if report else path)
                if policy.mode == "private" and role != "operator" and parts[:2] == ["api", "runs"]:
                    if payload.get("mode") != "check":
                        return self.respond(403, {"error": "Operator access required"})
                if (role in ("analyst", "reviewer", "guest") and parts[:2] == ["api", "runs"]
                        and (len(parts) == 3 or report)):
                    payload = distribution_view(payload)
                if report:
                    from .report import distribution_pdf
                    return self.respond(200, distribution_pdf(payload), "application/pdf",
                                        f"distribution-review-{parts[2]}.pdf")
                if path == "/api/instance":
                    payload["mode"] = policy.mode
                if path == "/api/dashboard" and policy.mode == "private":
                    provider = "gemini" if edition == "hosted" else "local"
                    payload["draft_providers"] = [p for p in payload["draft_providers"] if p["id"] == provider]
                    if role != "operator":
                        payload["adapters"] = [a for a in payload["adapters"] if a["id"] == "observed"]
                        payload.pop("model", None)
                        payload["runs"] = [distribution_view(r) for r in payload["runs"] if r.get("mode") == "check"]
                attachment = None
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "cases":
                    attachment = f"failed-case-{parts[2]}-{parts[4]}.json"
                if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                    attachment = f"{parts[3]}-{parts[2]}.json"
                return self.respond(200, payload, attachment=attachment)
            name = "index.html" if path == "/" else path.lstrip("/")
            if not identity and path == "/":
                name = "login.html"
            if name not in STATIC_ASSETS:
                raise FileNotFoundError
            target = STATIC / name
            if not target.resolve().is_relative_to(STATIC.resolve()) or not target.is_file():
                raise FileNotFoundError
            self.respond(200, target.read_bytes(), STATIC_MEDIA[target.suffix], name if target.suffix == ".pdf" else None)
        except FileNotFoundError:
            self.respond(404, {"error": "Record or resource not found"})
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
            message = str(error) or "Invalid request"
            if policy.mode == "private" and identity and identity["role"] != "operator":
                if isinstance(error, (OSError, subprocess.SubprocessError)) or message.startswith("Could not prepare the payment rule"):
                    message = "The workspace could not process this request. Contact your operator."
            self.respond(400, {"error": message})
        except Exception:
            self.respond(500, {"error": "Local workspace operation failed"})

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)


class App:
    def __init__(self, root, model="qwen-local", model_port=8080, adapters=(), pricing_models=(), local_drafting=True,
                 *, model_token=None, gemini=None):
        self.root = local.artifact_path(root)
        if not isinstance(model, str) or not model.strip() or not 1 <= model_port <= 65535:
            raise ValueError("Configure a local model name and a valid inference port")
        self.gemini = gemini
        self.model = gemini.model if gemini else model
        self.local_drafting = local_drafting and gemini is None
        self.model_port = model_port
        self._model_token = model_token
        self.pricing_models = {}
        for entry in pricing_models:
            name, _, endpoint = entry.partition("=")
            model_id, _, version = name.rpartition("@")
            if name in self.pricing_models:
                raise ValueError("Register each pricing model version only once")
            from .releases import model_spec
            spec = {"id": model_id, "version": version, "endpoint": endpoint}
            model_spec(spec)
            self.pricing_models[name] = spec
        self.lock = threading.RLock()
        self.adapters = {"good": ("Principal first", "good"), "faulty": ("Interest first", "faulty")}
        for index, entry in enumerate(adapters, start=1):
            label, separator, spec = entry.partition("=")
            if not separator or not label.strip() or not spec.strip():
                raise ValueError("Configure each startup adapter as LABEL=SPEC")
            self.adapters[f"custom-{index}"] = (label.strip(), spec.strip())

    def deal_path(self, deal_id):
        if deal_id not in ("ford", "mastr"):
            identifier(deal_id)
        return self.root / "deals" / f"{deal_id}.json"

    def draft_path(self, deal_id):
        self.deal_path(deal_id)
        return self.root / "drafts" / f"{deal_id}.json"

    def run_path(self, run_id):
        return self.root / "runs" / f"{identifier(run_id)}.json"

    def saved_review(self, report, deal):
        if (report.get("mode") == "replay" and report.get("deal_id") == "mastr"
                and "collection_case_id" not in report and report.get("origin_run_id")):
            origin = self.run_path(report["origin_run_id"])
            if origin.exists():
                previous = review_context(local.load(origin), deal)
                report = {**report, "collection_case_id": previous.get("collection_case_id"),
                          "replayed_filed_payout": previous.get("filed_payout", False)}
            elif deal:
                for case in deal.get("cases", []):
                    if (report.get("scenario") == case["scenario"]
                            and report.get("filing_sources") == case["filing_sources"]):
                        report = {**report, "collection_case_id": case["id"]}
                        break
        return review_context(report, deal)

    def approval_versions(self, deal):
        versions = list(deal.get("approval_versions", []))
        current = deal.get("rule_provenance")
        if current and not any(v["rule_id"] == current["rule_id"] for v in versions):
            rule_id = current["rule_id"]
            versions.append({"rule_id": rule_id, "rule_provenance": current,
                             "effective_from": deal.get("effective_from", EXAMPLE_COVERAGE.get(rule_id)),
                             "rule_version": deal.get("draft", {}).get("rule_version"),
                             "reviewer": None})
        return versions

    def archive_approval(self, deal):
        if not deal.get("rule_provenance"):
            return
        rule, receipt = self.approval(deal)
        version = next(v for v in deal["approval_versions"] if v["rule_id"] == receipt["rule_id"])
        version["reviewer"] = rule["approved_by"]
        archive = self.root / "approved-drafts" / (identifier(receipt["rule_id"]) + ".json")
        if not archive.exists():
            local.save(archive, {"draft_text": self.draft_path(deal["id"]).read_bytes().decode("utf-8"),
                                 "effective_from": version["effective_from"]})

    def version_approval(self, deal, version):
        if version["rule_id"] == deal.get("rule_id"):
            return self.approval(deal)
        archive = local.load(self.root / "approved-drafts" / (identifier(version["rule_id"]) + ".json"))
        if archive["effective_from"] != version["effective_from"]:
            raise ValueError("Historical approval effective date differs from its archive")
        raw = archive["draft_text"].encode("utf-8")
        return self.verify_approval(deal, version["rule_provenance"], raw, json.loads(raw))

    @staticmethod
    def version_for_distribution(versions, payment_date):
        if not payment_date:
            raise ValueError("Enter the distribution date to identify the applicable approved terms.")
        selected = max((v for v in versions if v["effective_from"] and v["effective_from"] <= payment_date),
                       key=lambda v: v["effective_from"], default=None)
        if selected is None:
            raise ValueError("No dated approval covers this distribution. Ask a reviewer to approve terms with the applicable effective date.")
        if any(not v["effective_from"] for v in versions[versions.index(selected) + 1:]):
            raise ValueError("A later approval has no effective date. Have a reviewer confirm which terms apply on this distribution date.")
        return selected

    def verified_version(self, deal, selected):
        if selected is None:
            raise ValueError("The required approval is unavailable. Have a reviewer verify the approved terms.")
        try:
            rule, receipt = self.version_approval(deal, selected)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError("The saved approval for these terms is missing or unreadable. "
                             "Ask the operator to restore its reviewed draft, rule and receipt, then recheck.") from error
        if (selected["rule_id"] != receipt["rule_id"] or
                selected["effective_from"] != rule.get("effective_from", EXAMPLE_COVERAGE.get(receipt["rule_id"]))):
            raise ValueError("Approval date or rule identity differs from the verified approval")
        return rule, receipt

    def approval(self, deal):
        provenance = deal.get("rule_provenance") or {}
        raw = self.draft_path(deal["id"]).read_bytes()
        if deal.get("draft_sha256") != sha(raw):
            raise ValueError("Draft digest differs from the selected approval")
        return self.verify_approval(deal, provenance, raw, deal.get("draft"))

    def verify_approval(self, deal, provenance, raw, displayed):
        rule, receipt = local.load_approved_rule(self.root, provenance.get("rule_id"))
        if receipt != provenance:
            raise ValueError("Approval receipt differs from the selected deal version")
        if sha(raw) != receipt["draft_sha256"]:
            raise ValueError("Draft digest differs from the selected approval")
        draft = json.loads(raw)
        if (draft != displayed or any(rule.get(k) != v for k, v in draft.items())
                or draft["deal"] != deal["name"] or draft["clause"] != deal["clause"]
                or draft.get("type", engine.PAYMENT_PRIORITY) != deal["kind"]
                or not draft.get("support") or draft["support"] not in draft["contract_excerpt"]
                or (draft.get("schema") == 3 and not local.waterfall_evidence(draft))
                or draft["contract_excerpt"] not in deal["source_text"]):
            raise ValueError("Approved rule or evidence differs from the reviewed draft")
        return rule, receipt

    def detail(self, deal_id):
        deal = local.load(self.deal_path(deal_id))
        deal["approval_versions"] = self.approval_versions(deal)
        if deal.get("rule_provenance"):
            deal.setdefault("effective_from", EXAMPLE_COVERAGE.get(deal.get("rule_id")))
            try:
                self.approval(deal)
                deal["approved"] = True
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
                deal.update(approved=False, approval_error=str(error))
        else:
            deal["approved"] = False
        return deal

    def release_saved_cases(self, deal):
        cases = [{k: c[k] for k in ("id", "label", "scenario")} for c in deal.get("cases", [])]
        for path in sorted((self.root / "runs").glob("*.json")):
            run = local.load(path)
            if (run.get("deal_id") == deal["id"] and run.get("mode") == "check"
                    and not run.get("case_id") and isinstance(run.get("scenario"), dict)):
                label = run.get("distribution_date") or run.get("created_at", "")[:10]
                cases.append({"id": "saved-" + run["run_id"], "label": f"Saved payment · {label} · {run['run_id'][:6]}",
                              "scenario": run["scenario"]})
        partial = {key: 100_000 for key in engine.STEPS} if deal["kind"] == engine.PAYMENT_PRIORITY else {}
        missing_label = "Available cash missing" if deal["kind"] == engine.PAYMENT_PRIORITY else "Prepayment charges missing"
        cases.append({"id": "missing-input", "label": missing_label, "scenario": partial})
        return cases

    @staticmethod
    def release_failure(report, index):
        cases = report.get("replay_cases", [])
        if type(index) != int or not 0 <= index < len(cases):
            raise ValueError("Select a failed case from this comparison")
        return cases[index]

    @staticmethod
    def release_scope(deal):
        from .releases import SINGLE_CLAUSE_ONLY
        if deal.get("kind") == engine.PRIORITY_OF_PAYMENTS:
            raise ValueError(SINGLE_CLAUSE_ONLY)

    def release_compare(self, deal_id, action, body):
        from . import releases
        deal = self.detail(deal_id)
        self.release_scope(deal)
        candidate = self.pricing_models.get(body.get("candidate_id"))
        if not candidate:
            raise ValueError("Choose a configured candidate version")
        if action not in ("compare", "replay"):
            raise FileNotFoundError
        current = self.pricing_models.get(body.get("current_id"))
        receipt = deal.get("rule_provenance") or {}
        if (not deal["approved"] or ("rule_id" in body and body["rule_id"] != receipt.get("rule_id"))):
            report = {"run_id": uuid4().hex, "deal_id": deal_id, "deal_name": deal["name"],
                      "mode": "release", "created_at": now(), "status": "HOLD", "rows": [],
                      "requested_rule_id": body.get("rule_id"), "baseline": current,
                      "candidate_model": candidate,
                      "reason": deal.get("approval_error") or "The selected approval is missing or has changed.",
                      "action": "Review the deal’s contract terms and current approval, then start a new comparison."}
            local.save(self.run_path(report["run_id"]), report)
            return report
        if action == "replay":
            original = local.load(self.run_path(body.get("run_id")))
            if original.get("mode") != "release" or original.get("deal_id") != deal_id or not original.get("replay_case"):
                raise ValueError("Choose a failed release comparison for this deal")
            return releases.check_release(self.root, receipt["rule_id"], None, candidate,
                                          deal_id=deal_id, replay_case=self.release_failure(original, body.get("index", 0)))
        if not current:
            raise ValueError("Choose a configured current version")
        ids = body.get("case_ids") or []
        return releases.check_release(self.root, receipt["rule_id"], current, candidate,
                                      self.release_saved_cases(deal), deal_id=deal_id, case_ids=ids)

    def get(self, path):
        if path == "/api/instance":
            return {"workspace_id": workspace_id(self.root)}
        if path == "/api/releases":
            deals = (self.detail(p.stem) for p in sorted((self.root / "deals").glob("*.json")))
            return {"deals": [{"id": d["id"], "name": d["name"]} for d in deals
                              if d["approved"] and d.get("kind") != engine.PRIORITY_OF_PAYMENTS],
                    "models": [{"key": key, "id": m["id"], "version": m["version"]}
                               for key, m in self.pricing_models.items()]}
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "releases"]:
            from .releases import comparison_cases
            deal = self.detail(parts[2])
            self.release_scope(deal)
            rule, _ = self.approval(deal)
            return {"cases": comparison_cases(rule, self.release_saved_cases(deal)),
                    "rule_id": deal["rule_id"], "clause": rule["clause"], "support": rule["support"]}

        if path == "/api/dashboard":
            deals = []
            by_id = {}
            for p in sorted((self.root / "deals").glob("*.json")):
                d = self.detail(p.stem)
                by_id[d["id"]] = d
                deals.append({k: v for k, v in d.items() if k not in ("source_text", "draft", "cases")})
            runs = []
            for p in (self.root / "runs").glob("*.json"):
                row = local.load(p)
                deal = by_id.get(row.get("deal_id"))
                if deal and row.get("mode") == "release":
                    row.setdefault("deal_name", deal["name"])
                runs.append(dashboard_run(self.saved_review(row, deal)))
            return {"deals": deals, "runs": sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True),
                    "adapters": [{"id": "observed", "label": "Observed payouts"},
                                 *({"id": key, "label": config[0]} for key, config in self.adapters.items())],
                    "model": self.model, "draft_providers": (
                        [{"id": "gemini", "label": "Gemini on Google Cloud", "available": True}]
                        if self.gemini else draft_providers.choices(self.local_drafting))}
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "deals"]:
            return self.detail(parts[2])
        if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "cases":
            return self.release_failure(local.load(self.run_path(parts[2])), int(parts[4]))
        if len(parts) in (3, 4) and parts[:2] == ["api", "runs"]:
            report = local.load(self.run_path(parts[2]))
            if len(parts) == 3:
                deal = None
                if report.get("deal_id") == "mastr" and self.deal_path("mastr").exists():
                    deal = local.load(self.deal_path("mastr"))
                return self.saved_review(report, deal)
            if parts[3] == "audit":
                return report
            if parts[3] == "replay" and report.get("replay_available"):
                return report["replay_case"]
        raise FileNotFoundError

    def create_deal(self, body, *, discover):
        source = import_contract(body, validate_extension=discover)
        suggestion = {}
        initial = {}
        if discover:
            if "amount_cents" in body:
                value = body["amount_cents"]
                if type(value) is not int or not 0 <= value <= 2**53 - 1:
                    raise ValueError("Enter a nonnegative USD amount with at most two decimal places.")
                initial["amount_cents"] = value
            if "distribution_date" in body:
                day = distribution_date(body["distribution_date"])
                if day is None:
                    raise ValueError("Enter a distribution date or omit it.")
                initial["distribution_date"] = day
            if body.get("kind") == engine.PRIORITY_OF_PAYMENTS:
                # A general priority of payments starts from the analyst's own selection.
                if body.get("excerpt") is None:
                    return {"state": "select_excerpt", **source, "kind": engine.PRIORITY_OF_PAYMENTS,
                            "reason": "Select the complete priority of payments, from its opening "
                                      "sentence to the last numbered step."}
                suggestion = intake.suggest_waterfall(source["source_text"], source["source_name"], body["excerpt"])
            else:
                suggestion = intake.suggest(source["source_text"], source["source_name"], body.get("excerpt"))
            if "reason" in suggestion:
                return {"state": "select_excerpt", **source, "reason": suggestion["reason"]}

        metadata = {**suggestion, **body}
        name = required(metadata, "name")
        clause = required(metadata, "clause")
        kind = suggestion["kind"] if discover else required(body, "kind")
        if kind not in engine.RULE_KINDS:
            raise ValueError("Choose a supported rule kind")
        deal = {"id": uuid4().hex, "name": name, "clause": clause, "kind": kind,
                **source, "cases": [], "created_at": now(), "approved": False}
        if discover:
            deal["suggested_excerpt"] = suggestion["excerpt"]
        if initial:
            deal["initial_allocation"] = initial
        local.save(self.deal_path(deal["id"]), deal)
        if not discover:
            return deal
        if self.gemini:
            return {"state": "consent_needed", "deal": deal,
                    "reason": "Review the exact excerpt and confirm sending it to Gemini to draft a rule."}
        if not self.local_drafting:
            return {"state": "draft_needed", "deal": deal,
                    "reason": "The contract is saved. Ask your operator to start local Qwen, then prepare the payment rule."}
        try:
            drafted = self.post(f"/api/deals/{deal['id']}/draft",
                                {"provider": "local", "excerpt": suggestion["excerpt"]})
        except (ValueError, OSError):
            return {"state": "draft_needed", "deal": deal,
                    "reason": "Local Qwen could not draft these terms. Review the saved excerpt and try again."}
        return {"state": "drafted", "deal": drafted}

    def gemini_confirmation(self, deal, body, actor, *, preview):
        if self.gemini is None or not isinstance(actor, str) or not actor:
            raise ValueError("Sign in to the hosted workspace to draft with Gemini.")
        excerpt = body.get("excerpt")
        if (not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 32_000
                or excerpt not in deal["source_text"]):
            raise ValueError("Choose an exact excerpt of up to 32,000 characters from the saved contract.")
        path = self.root / "draft-consents" / f"{deal['id']}-{sha(actor.encode())}.json"
        if preview and (deal.get("suggested_excerpt") != excerpt or not deal.get("selection_id")):
            deal.update(suggested_excerpt=excerpt, selection_id=uuid4().hex)
            local.save(self.deal_path(deal["id"]), deal)
        binding = {"excerpt": sha(excerpt.encode()), "selection": deal.get("selection_id"),
                   "source": sha(deal["source_text"].encode()), "provider": self.gemini.resource,
                   "endpoint": self.gemini.fake_endpoint, "draft": deal.get("draft_sha256")}
        if preview:
            token = secrets.token_urlsafe(32)
            expires = int(time.time()) + 900
            local.save(path, {"token_hash": sha(token.encode()), "binding": binding, "expires": expires})
            return {"provider": "gemini", "model": self.model, "excerpt": excerpt, "consent_token": token,
                    "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}
        grant = local.load(path) if path.exists() else {}
        token = body.get("consent_token")
        if (body.get("provider") != "gemini" or body.get("confirmed") is not True
                or not isinstance(token, str) or grant.get("expires", 0) <= time.time()
                or grant.get("binding") != binding or deal.get("suggested_excerpt") != excerpt
                or not secrets.compare_digest(grant.get("token_hash", ""), sha(token.encode()))):
            raise ValueError("Preview the current excerpt and confirm sending this exact text to Gemini.")
        local.save(path, {})
        if hasattr(self.root, "database"):
            # Commit the one-time send before the external side effect, including when a worker dies.
            self.root.database.checkpoint()

    def post(self, path, body, *, actor=None):
        if path in ("/api/deals", "/api/intake"):
            return self.create_deal(body, discover=path == "/api/intake")
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "releases"]:
            with self.lock:
                return self.release_compare(parts[2], parts[3], body)
        if len(parts) != 4 or parts[:2] != ["api", "deals"]:
            raise FileNotFoundError
        deal_id, action = parts[2:]
        with self.lock:
            deal = self.detail(deal_id)
            if action == "allocation":
                return self.plan_allocation(deal, body)
            if action == "runs":
                return self.run(deal, body)
            if action == "draft-preview":
                return self.gemini_confirmation(deal, body, actor, preview=True)
            if action == "draft":
                provider = body.get("provider", "local")
                if self.gemini:
                    self.gemini_confirmation(deal, body, actor, preview=False)
                    backend, model, excerpt = "gemini", self.gemini.model, body["excerpt"]
                else:
                    hosted = provider in draft_providers.PROVIDERS
                    if provider != "local" and not hosted:
                        raise ValueError("Choose a configured drafting provider.")
                    if provider == "local" and not self.local_drafting:
                        raise ValueError("Enable Local Qwen at startup or choose a configured API provider.")
                    excerpt = body.get("excerpt") if hosted else body.get("excerpt", deal["source_text"])
                    if not isinstance(excerpt, str) or excerpt not in deal["source_text"]:
                        raise ValueError("Select an exact excerpt from the imported source")
                    if hosted and (body.get("consent_sha256") != sha(excerpt.encode()) or not excerpt.strip()):
                        raise ValueError("Confirm the selected provider and the exact excerpt to send for this draft.")
                    backend = provider if hosted else "llama.cpp"
                    model = draft_providers.model_name(provider) if hosted else self.model
                version = uuid4().hex
                try:
                    draft = local.extract(excerpt, deal["clause"], deal["name"], "1", version,
                                          model, self.model_port, backend, rule_kind=deal["kind"],
                                          model_token=self._model_token if provider == "local" else None,
                                          gemini=self.gemini)
                except (OSError, KeyError, TypeError, ValueError) as error:
                    if self.gemini:
                        return {"state": "draft_retry", "deal": deal,
                                "reason": f"{error} Your selection is saved. Preview it and confirm again to retry."}
                    raise ValueError(f"Could not prepare the payment rule ({backend}): {error}") from error
                self.archive_approval(deal)
                deal.pop("effective_from", None)
                raw = local.save(self.draft_path(deal_id), draft)
                deal.update(draft=draft, draft_sha256=sha(raw), approved=False)
                deal.pop("rule_id", None)
                deal.pop("rule_provenance", None)
                deal.pop("approval_error", None)
            elif action == "approve":
                reviewer = required(body, "reviewer")
                digest = required(body, "sha256")
                if body.get("confirmed") is not True or digest != deal.get("draft_sha256"):
                    raise ValueError("Confirm the exact reviewed draft and its SHA-256 digest")
                draft_path = self.draft_path(deal_id)
                if local.load(draft_path) != deal.get("draft"):
                    raise ValueError("Displayed draft differs from the saved draft")
                effective = distribution_date(body.get("effective_from"))
                if effective and any(v["effective_from"] == effective for v in deal["approval_versions"]):
                    raise ValueError("An approval already starts on that date. Choose a distinct effective date.")
                self.archive_approval(deal)
                rule_id = local.approve(self.root, draft_path, reviewer, digest, effective_from=effective)
                _, receipt = local.load_approved_rule(self.root, rule_id)
                deal.update(approved=True, rule_id=rule_id, rule_provenance=receipt, effective_from=effective)
                deal["approval_versions"].append({"rule_id": rule_id, "rule_provenance": receipt,
                    "effective_from": effective, "reviewer": reviewer, "rule_version": deal["draft"]["rule_version"]})
                deal.pop("approval_error", None)
                self.approval(deal)
            else:
                raise FileNotFoundError
            local.save(self.deal_path(deal_id), deal)
            return deal

    def plan_allocation(self, deal, body):
        """Calculate the covered payments without a submitted payout or verification verdict."""
        if body.keys() - {"distribution_date", "amount_cents", "dues"}:
            raise ValueError("Use the distribution date, available amount and applicable amounts due.")
        try:
            payment_date = distribution_date(body.get("distribution_date"))
            if not payment_date:
                raise ValueError("Which distribution date are you preparing?")
        except ValueError as error:
            return {"state": "needs_input", "missing_field": "distribution_date", "reason": str(error)}
        try:
            version = self.version_for_distribution(deal["approval_versions"], payment_date)
            rule, receipt = self.verified_version(deal, version)
        except ValueError as error:
            return {"state": "needs_approval", "distribution_date": payment_date, "reason": str(error)}
        result = {"distribution_date": payment_date, "rule": rule, "rule_provenance": receipt,
                  "approved_rule_id": receipt["rule_id"], "approval_effective_from": version["effective_from"]}
        amount = body.get("amount_cents")
        if type(amount) is not int or not 0 <= amount <= 2**53 - 1:
            return {**result, "state": "needs_input", "missing_field": "amount_cents",
                    "reason": "Enter a nonnegative USD amount accurate to the cent."}
        result["amount_cents"] = amount
        dues = body.get("dues", {})
        covered = engine.waterfall_dues(rule) if rule["schema"] == 3 else rule.get("order", [])
        if not isinstance(dues, dict) or dues.keys() - set(covered):
            raise ValueError("Supply only the amounts due for the payment steps covered by this rule.")
        if rule["schema"] == 3:
            names = engine.waterfall_names(rule)
            for field, value in dues.items():
                if type(value) is not int or not 0 <= value <= 2**53 - 1:
                    return {**result, "state": "needs_input", "missing_field": field, "missing_fields": [field],
                            "reason": f"Enter the amount due for {names[field]} as a nonnegative USD amount accurate to the cent."}
            payouts, missing = engine.allocate_waterfall(rule, amount, dues)
            result["scenario"] = {engine.FUNDS: amount, **dues}
            if missing:
                step = next(s for s in rule["steps"] if missing in (p["id"] for p in s["payees"]))
                needed = [p["id"] for p in step["payees"] if p["id"] not in dues]
                return {**result, "state": "needs_input", "missing_field": missing, "missing_fields": needed,
                        "reason": "Cash reaches this step. Enter the amount due for "
                                  + ", ".join(names[key] for key in needed) + " for this distribution."}
        elif rule["schema"] == 1:
            labels = {"class_a_principal": "Class A first-priority principal due",
                      "class_b_interest": "accrued Class B interest due"}
            for field, value in dues.items():
                if type(value) is not int or not 0 <= value <= 2**53 - 1:
                    return {**result, "state": "needs_input", "missing_field": field,
                            "reason": f"Enter {labels[field]} as a nonnegative USD amount accurate to the cent."}
            dues = dues.copy()
            payouts, missing = engine.allocate_priority(amount, rule["order"], dues)
            if missing:
                evidence = allocation_evidence.ford_period_dues(deal["id"], receipt["approved_rule_sha256"],
                                                              payment_date, amount)
                if evidence:
                    used_fields = []
                    while missing and missing in evidence["dues"]:
                        dues[missing] = engine.cents(evidence["dues"][missing])
                        used_fields.append(missing)
                        payouts, missing = engine.allocate_priority(amount, rule["order"], dues)
                    if used_fields:
                        result["period_evidence"] = {"label": evidence["label"], "source_url": evidence["source_url"],
                                                     "fields": used_fields}
            result["scenario"] = {"cash": amount, **dues}
            if missing:
                return {**result, "state": "needs_input", "missing_field": missing,
                        "reason": f"Cash reaches this step. Enter {labels[missing]} for this distribution."}
        else:
            result["scenario"] = {"collected_prepayment_charges": amount}
            payouts = engine.expected_payout(rule, result["scenario"])
        return {**result, "state": "ready", "payouts": payouts,
                "remaining_cents": amount - sum(payouts.values())}

    def run(self, deal, body):
        mode = body.get("mode")
        run_id = uuid4().hex
        labels = {"check": "Distribution check", "search": "Bounded stress search", "replay": "Regression replay"}
        label = labels.get(mode, "Distribution check") if isinstance(mode, str) else "Distribution check"
        report = {"run_id": run_id, "deal_id": deal["id"], "deal_name": deal["name"],
                  "label": label,
                  "created_at": now(), "status": "HOLD", "mode": mode,
                  "affected_tranche": "", "discrepancy_cents": None,
                  "retrospective": deal.get("retrospective"), "replay_available": False}
        inputs = body
        try:
            if mode == "check":
                report["submitted_inputs"] = {k: body[k] for k in (
                    "distribution_date", "payout_purpose", "case_id", "scenario", "payouts", "adapter") if k in body}
            case = {}
            if body.get("case_id"):
                case = next((c for c in deal["cases"] if c["id"] == body["case_id"]), None)
                if case is None:
                    raise ValueError("Selected case does not belong to this deal")
            if case:
                if mode == "check" and body.get("distribution_date"):
                    known_date = "2006-06-26" if deal["id"] == "mastr" else None
                    if body["distribution_date"] != known_date:
                        raise ValueError("Use custom inputs when changing a saved case date")
                for field in ("scenario", "payouts"):
                    if field in body and field in case and body[field] != case[field]:
                        raise ValueError("Use custom inputs when changing a saved case")
                report.update(label=case["label"] if mode == "check" else label, case_id=case["id"],
                              filing_sources=case.get("filing_sources", []),
                              synthetic=case.get("synthetic", False),
                              affected_tranche=case.get("affected_tranche", ""))
            inputs = {**case, **body}
            versions = deal.get("approval_versions", [])
            selected = next((v for v in versions if v["rule_id"] == deal.get("rule_id")), None)
            basis = "Selected approval for technical review"
            if mode == "check":
                purpose = body.get("payout_purpose", "actual" if case and deal["id"] == "mastr" else "proposed")
                if purpose not in ("proposed", "actual"):
                    raise ValueError("Choose proposed or actual for the submitted payout")
                report["payout_purpose"] = purpose
                payment_date = distribution_date(body.get("distribution_date"))
                if case and deal["id"] == "mastr":
                    payment_date = "2006-06-26"
                report["distribution_date"] = payment_date
                if case and case["id"] == "ford-stress" and deal["id"] == "ford":
                    selected = next((v for v in versions if v["rule_id"] == "c6d0c938c6244eea92bc5169cee68d80"), None)
                    basis = "Undated synthetic Ford stress case uses its bundled approved terms."
                else:
                    selected = self.version_for_distribution(versions, payment_date)
                    basis = "Latest approved effective date on or before the distribution date (inclusive)."
            elif mode == "replay" and isinstance(body.get("replay"), dict):
                selected = next((v for v in versions if v["rule_provenance"] ==
                                 body["replay"].get("rule_provenance")), None)
                basis = "Exact approval recorded in the original regression."
                report["payout_purpose"] = body["replay"].get("payout_purpose")
            rule, receipt = self.verified_version(deal, selected)
            report.update(rule=rule, rule_provenance=receipt, approved_rule_id=receipt["rule_id"],
                          approval_effective_from=selected["effective_from"], approval_date_basis=basis)
            if mode == "replay":
                replay = body.get("replay")
                if not isinstance(replay, dict):
                    raise ValueError("Import a regression case to replay")
                original_path = self.run_path(replay.get("origin_run_id"))
                if (original_path.exists() and canonical_json(local.load(original_path).get("replay_case"))
                        != canonical_json(replay)):
                    raise ValueError("Regression case differs from the originating local audit")
                if (type(replay.get("schema")) is not int or replay["schema"] != 1
                        or replay.get("case_sha256") != case_digest(replay)
                        or replay.get("deal_id") != deal["id"] or replay.get("rule_provenance") != receipt
                        or canonical_json(replay.get("rule")) != canonical_json(rule)):
                    raise ValueError("Regression case integrity or approved rule binding mismatch")
                captured = engine.compare(rule, replay["scenario"], lambda _: replay["payouts"])
                if (captured["status"] != "BLOCK"
                        or canonical_json(captured["expected"]) != canonical_json(replay["expected"])):
                    raise ValueError("Captured regression differs from the approved rule")
                # Replay the captured observation unless a caller explicitly selects a current adapter.
                report["distribution_date"] = distribution_date(replay.get("distribution_date"))
                inputs = {**replay, "adapter": body.get("adapter", "observed")}
                if "payouts" in body:
                    inputs["payouts"] = body["payouts"]
                report.update(synthetic=replay.get("synthetic", False),
                              collection_case_id=replay.get("collection_case_id"),
                              replayed_filed_payout=replay.get("filed_payout", False),
                              filing_sources=replay.get("filing_sources", []),
                              affected_tranche=replay.get("affected_tranche",""),
                              origin_run_id=replay["origin_run_id"])
                if original_path.exists() and "collection_case_id" not in replay:
                    origin = self.saved_review(local.load(original_path), deal)
                    report.update(collection_case_id=origin.get("collection_case_id"),
                                  replayed_filed_payout=origin.get("filed_payout", False))
                elif deal["id"] == "mastr" and "collection_case_id" not in replay:
                    for saved in deal["cases"]:
                        if (replay["scenario"] == saved["scenario"]
                                and replay.get("filing_sources") == saved["filing_sources"]):
                            report.update(collection_case_id=saved["id"], replayed_filed_payout=(
                                replay.get("adapter") == "observed" and replay["payouts"] == saved["payouts"]))
                            break
            adapter_name = inputs.get("adapter", "observed")
            report["adapter"] = adapter_name
            if adapter_name == "observed":
                if not isinstance(inputs.get("payouts"), dict):
                    raise ValueError("Provide observed candidate payouts in integer cents")
                candidate = lambda scenario: inputs["payouts"]
            elif adapter_name in self.adapters:
                label, spec = self.adapters[adapter_name]
                candidate = engine.adapter(spec)
                report["adapter_label"] = label
                report["adapter_spec_sha256"] = sha(spec.encode())
            else:
                raise ValueError("Unknown local candidate adapter")
            if mode == "search":
                if adapter_name == "observed":
                    raise ValueError("Bounded search requires a scenario-dependent candidate adapter")
                search = engine.search(rule, candidate)
                report.update(search=search, synthetic=True)
                if deal["id"] == "ford":
                    report["affected_tranche"] = "Class A-1 principal and Class B interest"
                result = search["regression"] or search["first_observed_divergence"]
                if not result:
                    raise ValueError("No divergence found within the bounded search")
            elif mode in ("check", "replay"):
                scenario = inputs.get("scenario")
                if not isinstance(scenario, dict):
                    raise ValueError("Provide the candidate scenario in integer cents")
                report["scenario"] = scenario
                result = engine.compare(rule, scenario, candidate)
                if mode == "replay" and result["expected"] != replay["expected"]:
                    raise ValueError("Regression expected payouts differ from the approved rule")
            else:
                raise ValueError("Choose check, search, or replay")
            report.update(result)
            rounded = set(result.get("rounding_accepted", ()))
            changes = {k: abs(v - result["candidate"][k]) for k, v in result["expected"].items() if k not in rounded}
            report["discrepancy_cents"] = max(changes.values(), default=0)
            if not report["affected_tranche"]:
                tranche_labels = {"class_a_principal": "Class A", "class_b_interest": "Class B",
                                  "class_p": "Class P"}
                if rule.get("schema") == 3:
                    tranche_labels = engine.waterfall_names(rule)
                report["affected_tranche"] = ", ".join(tranche_labels[k] for k, v in changes.items() if v)
            report = review_context(report, deal)
            if result["status"] == "BLOCK":
                report["replay_available"] = True
                report["replay_case"] = {
                    "schema": 1, "origin_run_id": run_id, "deal_id": deal["id"],
                    "rule_provenance": receipt, "rule": rule, "scenario": result["scenario"],
                    "expected": result["expected"], "payouts": result["candidate"], "adapter": adapter_name,
                    "payout_purpose": report.get("payout_purpose"),
                    "synthetic": report.get("synthetic", False),
                    "affected_tranche": report["affected_tranche"],
                    "filing_sources": report.get("filing_sources", [])}
                if report.get("collection_case_id"):
                    report["replay_case"].update(collection_case_id=report["collection_case_id"],
                                                 filed_payout=report["filed_payout"])
                if report.get("distribution_date"):
                    report["replay_case"]["distribution_date"] = report["distribution_date"]
                report["replay_case"]["case_sha256"] = case_digest(report["replay_case"])
        except Exception as error:
            report.update(status="HOLD", reason=str(error), replay_available=False)
            if mode == "check":
                report.pop("scenario", None)
                report.update(held_check_inputs(inputs, deal.get("kind"), report.get("rule") or deal.get("draft")))
            report = review_context(report, deal)
        local.save(self.run_path(run_id), report)
        return report


def distribution_view(result):
    view = {k: v for k, v in result.items() if k not in (
        "replay_case", "replay_cases", "replay_available", "adapter_spec_sha256", "search")}
    return view


def sample_runs(app):
    existing = {(r.get("deal_id"), r.get("case_id")) for r in
                (local.load(path) for path in (app.root / "runs").glob("*.json")) if r.get("mode") == "check"}
    for deal_id in ("ford", "mastr"):
        for case in app.detail(deal_id)["cases"]:
            if (deal_id, case["id"]) not in existing:
                app.post(f"/api/deals/{deal_id}/runs", {"mode": "check", "case_id": case["id"]})


class Demo:
    """Snapshot public bundle into an explicit read-only route table."""
    def __init__(self):
        from tempfile import TemporaryDirectory
        from .web_examples import seed_examples
        with TemporaryDirectory() as tmp:
            seed_examples(tmp)
            app = App(tmp, local_drafting=False)
            sample_runs(app)
            dashboard = app.get("/api/dashboard")
            dashboard.update(adapters=[], draft_providers=[])
            self.pages = {"/api/dashboard": dashboard}
            for deal in dashboard["deals"]:
                path = "/api/deals/" + deal["id"]
                detail = app.get(path)
                self.pages[path] = detail
                if deal["id"] == "ford":
                    # Use the dated trustee statement in the quick example. The
                    # independent $50,000 stress case in the detailed review is undated.
                    from .web_examples import DATA
                    day = "2026-06-15"
                    amount = local.load(DATA / "ford" / "statement-dues.json")[day]["cash"]
                else:
                    day = "2006-06-26"
                    amount = detail["cases"][0]["scenario"]["collected_prepayment_charges"]
                example = app.plan_allocation(detail, {"distribution_date": day, "amount_cents": amount})
                if detail.get("retrospective"):
                    example["retrospective"] = detail["retrospective"]
                self.pages[path + "/allocation-example"] = example
            for run in dashboard["runs"]:
                source = app.get("/api/runs/" + run["run_id"])
                stable_id = uuid5(NAMESPACE_URL, "waterfall-demo/" + source["case_id"]).hex
                run["run_id"] = stable_id
                run.pop("replay_available", None)
                self.pages["/api/runs/" + stable_id] = distribution_view({**source, "run_id": stable_id})

    def get(self, path):
        if path not in self.pages:
            raise FileNotFoundError
        return self.pages[path]


def serve(root, port=8765, model="qwen-local", model_port=8080, adapters=(), pricing_models=(), local_drafting=True,
          *, mode="local", users=None, origin=None, bind="127.0.0.1"):
    policy = access.Access(mode, users, origin, bind)
    with ThreadingHTTPServer((bind, port), Handler) as server:
        serve_workspace(server, root, model, model_port, adapters, pricing_models, local_drafting, policy)


def workspace_pricing_models(stack, configured=()):
    if configured:
        return configured
    from .model_service import make_server
    models = []
    for version, behavior in (("1.0", "good"), ("1.1", "faulty"), ("1.2", "good")):
        service = stack.enter_context(make_server(0, "example-pricing", version, behavior))
        threading.Thread(target=service.serve_forever, daemon=True).start()
        stack.callback(service.shutdown)
        models.append(f"example-pricing@{version}=http://127.0.0.1:{service.server_port}/calculate")
    return models


def serve_workspace(server, root, model, model_port, adapters, pricing_models, local_drafting, policy=None, *, model_token=None):
    from .web_examples import seed_examples
    server.access = policy or access.Access()
    if server.access.mode == "private" and local_drafting and not model_token:
        raise ValueError("Use scripts/start_app.py for authenticated private Qwen or select --without-qwen.")
    with ExitStack() as stack:
        if server.access.mode == "demo":
            server.app = Demo()
        else:
            stack.enter_context(server.access.open_store(root))
            seed_examples(Path(root))
            pricing_models = workspace_pricing_models(stack, pricing_models)
            server.app = App(root, model, model_port, adapters, pricing_models, local_drafting=local_drafting, model_token=model_token)
            if server.access.mode == "private":
                sample_runs(server.app)
        origin = server.access.origin or f"http://127.0.0.1:{server.server_port}"
        print(f"Waterfall workspace: {origin}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Local waterfall review workspace")
    parser.add_argument("--store", default="private/web")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--model", default="qwen-local")
    parser.add_argument("--model-port", default=8080, type=int)
    parser.add_argument("--adapter", action="append", default=[], metavar="LABEL=SPEC",
                        help="Register a trusted local candidate adapter, repeatable")
    parser.add_argument("--pricing-model", action="append", default=[], metavar="ID@VERSION=URL",
                        help="Register a loopback pricing model endpoint for release comparisons (repeatable)")
    parser.add_argument("--without-qwen", action="store_true", help="Disable local Qwen drafting.")
    access.options(parser)
    args = parser.parse_args(argv)
    serve(args.store, args.port, args.model, args.model_port, args.adapter, args.pricing_model,
          local_drafting=not args.without_qwen, mode=args.mode, users=args.users, origin=args.origin, bind=args.bind)


if __name__ == "__main__":
    main()
