import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from abs_waterfall_check import access, local, web
from abs_waterfall_check.web_examples import seed_examples


class WebAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        hashed = access.password_hash("test passphrase")
        config = self.root / "accounts.json"
        config.write_text(json.dumps({"organization": "Servicing", "users": {
            role: {"role": role, "password_hash": hashed} for role in ("analyst", "reviewer", "operator")
        }}))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.server.access = access.Access("private", config, "https://review.example")
        seed_examples(self.root)
        self.server.app = web.App(self.root)
        web.sample_runs(self.server.app)

    def request(self, path, body=None, cookie="", origin="https://review.example", extra_headers=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            headers = {"Host": "review.example", "Cookie": cookie, "Content-Type": "application/json"}
            if origin is not None:
                headers["Origin"] = origin
            headers.update(extra_headers or {})
            conn.request("POST" if body is not None else "GET", path, headers=headers,
                         body=json.dumps(body) if body is not None else None)
            response = conn.getresponse()
            payload = response.read()
            if response.getheader("Content-Type") == "application/json":
                payload = json.loads(payload)
            return response.status, payload, response.getheader("Set-Cookie", "")
        finally:
            conn.close()

    def login(self, role):
        status, _, cookie = self.request("/api/login", {"username": role, "password": "test passphrase"})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        return cookie.split(";")[0]

    def test_private_reads_and_posts_require_session_and_role(self):
        self.assertEqual(self.request("/healthz")[:2], (200, {"status": "ok"}))
        self.assertIn(b"Sign in", self.request("/")[1])
        self.assertEqual(self.request("/api/dashboard")[0], 401)
        allocate = "/api/deals/mastr/allocation"
        worksheet = {"distribution_date": "2006-06-26", "amount_cents": 91442}
        self.assertEqual(self.request(allocate, worksheet)[0], 401)
        self.assertEqual(self.request("/api/login", {"username": "analyst", "password": "wrong"})[0], 401)
        cookie = self.login("analyst")
        response = self.request(allocate, worksheet, cookie)
        self.assertEqual(response[0], 200)
        self.assertEqual(response[1]["payouts"], {"class_p": 91442})
        operator_run = {"run_id": "a" * 32, "deal_id": "ford", "mode": "release", "rows": [],
                        "candidate_model": {"endpoint": "technical-details"}}
        local.save(self.server.app.run_path(operator_run["run_id"]), operator_run)
        for role_cookie in (cookie, self.login("reviewer")):
            response = self.request("/api/dashboard", cookie=role_cookie)[1]
            self.assertNotIn("technical-details", json.dumps(response))
            self.assertNotIn(operator_run["run_id"], [r["run_id"] for r in response["runs"]])
            for suffix in ("", "/report", "/audit"):
                self.assertEqual(self.request("/api/runs/" + operator_run["run_id"] + suffix, cookie=role_cookie)[0], 403)
        rows = response["runs"]
        run = "/api/runs/" + next(r["run_id"] for r in rows if r["status"] == "BLOCK")
        self.assertEqual(self.request(run + "/report")[0], 401)
        self.assertNotIn("replay_case", self.request(run, cookie=cookie)[1])
        for path in (run + "/audit", run + "/replay", "/api/operator", "/api/releases"):
            self.assertEqual(self.request(path, cookie=cookie)[0], 403)
        self.assertEqual(self.request("/api/deals", {}, cookie, "https://unrelated.example")[0], 403)
        self.assertEqual(self.request("/api/deals/ford/approve", {"reviewer": "operator"}, cookie)[0], 403)
        self.assertEqual(self.request("/api/deals/ford/draft", {"provider": "openai"}, cookie)[0], 403)
        self.assertEqual(self.request("/api/deals/ford/runs", {"mode": "search"}, cookie)[0], 403)
        self.assertEqual(self.request("/api/logout", {}, cookie)[0], 200)
        self.assertEqual(self.request(run, cookie=cookie)[0], 401)
        operator = self.login("operator")
        self.assertEqual(self.request(run + "/audit", cookie=operator)[0], 200)
        self.assertEqual(self.request("/api/operator", cookie=operator)[0], 200)

    def test_approval_uses_signed_in_account(self):
        cookie = self.login("reviewer")
        deal = self.server.app.detail("ford")
        # Start from an unapproved copy of the bundled draft.
        draft = deal["draft"]
        new = self.server.app.post("/api/deals", {"name": deal["name"], "kind": deal["kind"],
                    "clause": deal["clause"], "source_text": deal["source_text"]})
        raw = local.save(self.server.app.draft_path(new["id"]), draft)
        new.update(draft=draft, draft_sha256=web.sha(raw))
        local.save(self.server.app.deal_path(new["id"]), new)
        endpoint = f"/api/deals/{new['id']}/approve"
        for fields in ({}, {"effective_from": ""}, {"effective_from": "2020-02-30"}):
            status, error, _ = self.request(endpoint, {"confirmed": True, "sha256": new["draft_sha256"], **fields}, cookie)
            self.assertEqual(status, 400)
            self.assertIn("effective date", error["error"])
            self.assertFalse(self.server.app.detail(new["id"])["approved"])
        status, approved, _ = self.request(endpoint,
            {"reviewer": "someone else", "confirmed": True, "sha256": new["draft_sha256"],
             "effective_from": "2020-01-01"}, cookie)
        self.assertEqual(status, 200, approved)
        rule, _ = local.load_approved_rule(self.root, approved["rule_id"])
        self.assertEqual(rule["approved_by"], "reviewer")

    def test_demo_serves_only_bundled_cases(self):
        private_run = self.server.app.get("/api/dashboard")["runs"][0]["run_id"]
        self.server.access = access.Access("demo", origin="https://review.example")
        self.server.app = web.Demo()
        _, dashboard, _ = self.request("/api/dashboard")
        self.assertEqual({d["id"] for d in dashboard["deals"]}, {"ford", "mastr"})
        self.assertEqual({(r["case_id"], r["status"]) for r in dashboard["runs"]},
                         {("ford-stress", "BLOCK"), ("mastr-original", "BLOCK"), ("mastr-amended", "PASS")})
        sample = "/api/runs/" + dashboard["runs"][0]["run_id"]
        self.assertEqual(self.request(sample)[0], 200)
        _, ford, _ = self.request("/api/deals/ford/allocation-example")
        self.assertEqual(ford["state"], "ready")
        self.assertEqual(ford["payouts"], {"class_a_principal": 0, "class_b_interest": 11105633})
        _, mastr, _ = self.request("/api/deals/mastr/allocation-example")
        self.assertEqual(mastr["payouts"], {"class_p": 24625394})
        self.assertTrue(mastr["retrospective"])
        self.assertEqual(self.request("/api/deals/ford/allocation", {"amount_cents": 123})[0], 403)
        self.assertEqual(self.request("/api/deals/private/allocation-example")[0], 404)
        for path in (sample + "/audit", sample + "/replay", "/api/runs/" + private_run, "/api/instance"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/deals", {"name": "private"})[0], 403)
        self.assertEqual(self.request("/api/deals/mastr/runs", {"mode": "check", "case_id": "mastr-original"})[0], 403)
        self.assertEqual(self.request("/api/deals/mastr/allocation", {"amount_cents": 25})[0], 403)

    def test_static_files_are_allowlisted_by_name(self):
        self.assertEqual(self.request("/mastr-sample.pdf")[0], 401)
        self.assertTrue(self.request("/mastr-sample.pdf", cookie=self.login("analyst"))[1].startswith(b"%PDF"))
        self.server.access = access.Access("demo", origin="https://review.example")
        self.server.app = web.Demo()
        self.assertTrue(self.request("/mastr-sample.pdf")[1].startswith(b"%PDF"))
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "other.csv").write_text("private")
        (assets / "ford-stress.csv").symlink_to(self.root / "accounts.json")
        with patch.object(web, "STATIC", assets):
            for path in ("/other.csv", "/ford-stress.csv", "/private/web/runs/data.json"):
                self.assertEqual(self.request(path)[0], 404)

    def test_provisioning_preserves_accounts_and_organization(self):
        path = self.root / "org.json"
        access.add_user(path, "Trust Ops", "amy", "analyst", "test-password")
        access.add_user(path, "Trust Ops", "bill", "reviewer", "test-password")
        expected = path.read_bytes()
        for org, account in (("Trust Ops", "bill"), ("Other", "chris")):
            with self.assertRaises(ValueError):
                access.add_user(path, org, account, "operator", "test-password")
        self.assertEqual(path.read_bytes(), expected)
        self.assertNotIn(b"test-password", expected)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(access.Access("private", path).accounts), {"amy", "bill"})

    def test_store_binding_persists_and_rejects_other_modes_and_organizations(self):
        store = self.root / "customer"
        private = access.Access("private", self.root / "accounts.json")
        local_policy = access.Access()
        with local_policy.open_store(store):
            with self.assertRaisesRegex(ValueError, "already served"):
                with private.open_store(store):
                    self.fail("Active unsigned workspace accepted a conflicting binding")
        with private.open_store(store):
            self.assertTrue(private.check_store(store))
        restarted = access.Access("private", self.root / "accounts.json")
        with restarted.open_store(store):
            self.assertEqual(json.loads((store / "organization.json").read_text())["organization"], "Servicing")
        other_file = self.root / "other.json"
        access.add_user(other_file, "Different Org", "analyst", "analyst", "pw")
        for policy in (local_policy, access.Access("private", other_file)):
            with self.assertRaises(ValueError):
                with policy.open_store(store):
                    self.fail("Bound workspace opened by an unauthorized instance")

    def test_external_navigation_opens_only_the_landing_page(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                      "Sec-Fetch-Dest": "document"}
        def visit(path, **kwargs):
            return self.request(path, origin=None, extra_headers=navigation, **kwargs)
        status, page, _ = visit("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Sign in", page)
        self.assertEqual(visit("/api/dashboard", cookie=self.login("analyst"))[0], 403)
        self.assertEqual(visit("/api/login", body={})[0], 403)

    def test_origin_and_modes_fail_closed(self):
        for args in [("local", None, None, "0.0.0.0"), ("private", None), ("demo", None, "http://remote.example"),
                     ("demo", None, "https://remote.example", "0.0.0.0")]:
            with self.assertRaises(ValueError):
                access.Access(*args)
