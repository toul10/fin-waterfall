"""Run against an authorized local PostgreSQL database with WATERFALL_TEST_DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
import sys
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from abs_waterfall_check import access, local
from abs_waterfall_check.gemini import Gemini
from abs_waterfall_check.postgres import Database, PostgresAccess
from abs_waterfall_check.web import App, Demo, Handler, sample_runs
from abs_waterfall_check.web_examples import DATA, seed_examples


@unittest.skipUnless(os.environ.get("WATERFALL_TEST_DATABASE_URL"), "set WATERFALL_TEST_DATABASE_URL for PostgreSQL")
class HostedPersistenceTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        # Every test owns a unique schema, even if it shares a Postgres server
        # with a running demonstration. Never touch existing workspace tables.
        dsn = os.environ["WATERFALL_TEST_DATABASE_URL"]
        namespace = sql.Identifier("waterfall_test_" + uuid4().hex)
        self.namespace = namespace
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(namespace))
        def cleanup():
            with psycopg.connect(dsn) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(namespace))
        self.addCleanup(cleanup)
        self.dsn = make_conninfo(dsn, options="-c search_path=" + namespace.as_string())
        self.db, self.app = self.restart()

    def restart(self):
        db = Database(self.dsn, "Test organization")
        db.initialize()
        app = App(db.root, local_drafting=False)
        with db.transaction(write=True):
            seed_examples(db.root)
            sample_runs(app)
        return db, db.wrap(app)

    def test_restart_preserves_exact_bytes_and_shared_engine_reports(self):
        again, restarted = self.restart()
        self.assertTrue(again.health())
        self.assertEqual(again.workspace_id, self.db.workspace_id)
        with self.assertRaisesRegex(ValueError, "different organization"):
            Database(self.dsn, "Another organization").initialize()
        rows = self.app.get("/api/dashboard")["runs"]
        self.assertEqual(len(rows), 3)
        cases = {"ford-stress": ("BLOCK", 5_000_000), "mastr-original": ("BLOCK", 5_586_112),
                 "mastr-amended": ("PASS", 0)}
        self.assertEqual(set(cases), {r["case_id"] for r in rows})
        for row in rows:
            report = restarted.get("/api/runs/" + row["run_id"] + "/audit")
            self.assertEqual(report, self.app.get("/api/runs/" + row["run_id"] + "/audit"))
            self.assertEqual((report["status"], report["discrepancy_cents"]), cases[row["case_id"]])
            source = DATA / row["deal_id"]
            receipt = report["rule_provenance"]
            for collection, key, fixture in (
                ("drafts", row["deal_id"], "draft.json"),
                ("rules", receipt["rule_id"], "rule.json"),
                ("approvals", receipt["rule_id"], "approval.json"),
            ):
                self.assertEqual((again.root / collection / (key + ".json")).read_bytes(),
                                 (source / fixture).read_bytes())
            self.assertEqual(hashlib.sha256((source / "rule.json").read_bytes()).hexdigest(),
                             receipt["approved_rule_sha256"])
            self.assertEqual(local.status_for(again.root, row["run_id"]), report["status"])

    def test_concurrent_workers_cannot_approve_two_versions_for_the_same_date(self):
        _, other = self.restart()
        barrier = threading.Barrier(2)
        digest = self.app.get("/api/deals/ford")["draft_sha256"]
        def approve(app):
            barrier.wait(4)
            try:
                return app.post("/api/deals/ford/approve", {
                    "sha256": digest, "reviewer": "reviewer", "confirmed": True,
                    "effective_from": "2026-02-01",
                })["rule_id"]
            except ValueError as error:
                self.assertIn("already starts", str(error))
                return None
        with ThreadPoolExecutor(2) as workers:
            outcomes = list(workers.map(approve, [self.app, other]))
        self.assertEqual(outcomes.count(None), 1)
        deal = self.app.get("/api/deals/ford")
        self.assertEqual(len(deal["approval_versions"]), 2)
        self.assertIn(deal["rule_id"], outcomes)
        self.assertEqual(len((self.db.root / "approvals").glob("*.json")), 3)
        self.assertEqual(len((self.db.root / "rules").glob("*.json")), 3)

    def test_rollback_and_checkpoint(self):
        record = self.db.root / "draft-consents" / "trial.json"
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with self.db.transaction(write=True):
                local.save(record, {"token": "consumed"})
                self.db.checkpoint()
                local.save(record, {"partial": True})
                raise RuntimeError("stop the write after checkpoint")
        self.assertEqual(local.load(record), {"token": "consumed"})

    def test_one_confirmed_send_across_two_workers_and_provider_failure(self):
        model = Gemini("local-pilot", "us-central1", "gemini-test", "http://localhost:8089")
        other = Database(self.dsn, self.db.organization)
        apps = [db.wrap(App(db.root, gemini=model)) for db in (self.db, other)]
        source = local.load(DATA / "ford" / "draft.json")
        excerpt = source["contract_excerpt"]
        created = apps[0].post("/api/deals", {"source_text": excerpt, "name": "Pilot test",
                                             "clause": source["clause"], "kind": "payment_priority"})
        path = "/api/deals/" + created["id"]
        grant = apps[0].post(path + "/draft-preview", {"excerpt": excerpt}, actor="reviewer")
        request = {"provider": "gemini", "confirmed": True, "excerpt": excerpt,
                   "consent_token": grant["consent_token"]}
        barrier = threading.Barrier(2)
        def send(app):
            barrier.wait(4)
            try:
                return app.post(path + "/draft", request, actor="reviewer")["state"]
            except ValueError as error:
                self.assertIn("confirm", str(error))
                return "needs_confirmation"
        with patch.object(Gemini, "proposal", side_effect=ValueError("test outage")) as provider:
            with ThreadPoolExecutor(2) as workers:
                self.assertCountEqual(list(workers.map(send, apps)), ["draft_retry", "needs_confirmation"])
            provider.assert_called_once()
        self.assertEqual(apps[1].get(path)["suggested_excerpt"], excerpt)

    def test_general_priority_of_payments_survives_restart(self):
        from scripts.fake_gemini import FakeVertex
        from tests.test_waterfall_drafting import EXCERPT, SOURCE
        from tests.test_waterfall_web import DUES, PAID
        server = FakeVertex(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        model = Gemini("local-pilot", "us-central1", "gemini-test", f"http://127.0.0.1:{server.server_port}")
        app = self.db.wrap(App(self.db.root, gemini=model))
        saved = app.post("/api/intake", {"source_text": SOURCE, "kind": "priority_of_payments", "excerpt": EXCERPT,
                                         "amount_cents": 100_000_000, "distribution_date": "2026-06-15"},
                         actor="reviewer")
        path = "/api/deals/" + saved["deal"]["id"]
        grant = app.post(path + "/draft-preview", {"excerpt": EXCERPT}, actor="reviewer")
        drafted = app.post(path + "/draft", {"provider": "gemini", "confirmed": True, "excerpt": EXCERPT,
                                             "consent_token": grant["consent_token"]}, actor="reviewer")
        self.assertEqual(len(drafted["draft"]["steps"]), 8)
        app.post(path + "/approve", {"reviewer": "reviewer", "sha256": drafted["draft_sha256"],
                                     "effective_from": "2026-06-01", "confirmed": True}, actor="reviewer")
        allocation = app.post(path + "/allocation", {"distribution_date": "2026-06-15",
                                                     "amount_cents": 100_000_000, "dues": DUES}, actor="reviewer")
        self.assertEqual((allocation["state"], allocation["payouts"]), ("ready", PAID))
        misdirected = {**PAID, "regular_principal_payment": 69_100_000, "certificateholders": 1_000_000}
        run = app.post(path + "/runs", {"mode": "check", "distribution_date": "2026-06-15", "payout_purpose": "actual",
                                        "adapter": "observed", "scenario": {"cash": 100_000_000, **DUES},
                                        "payouts": misdirected}, actor="reviewer")
        self.assertEqual((run["status"], run["discrepancy_cents"]), ("BLOCK", 1_000_000))
        # Another worker reads the same approval and saved review from the database.
        _, restarted = self.restart()
        self.assertTrue(restarted.get(path)["approved"])
        self.assertEqual(restarted.get("/api/runs/" + run["run_id"] + "/audit"), run)

    def test_hosted_mcp_stdio_returns_only_status(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        rows = self.app.get("/api/dashboard")["runs"]
        self.assertEqual(Database(self.dsn, "Wrong organization").run_status(rows[0]["run_id"]), "HOLD")
        status_dsn = self.dsn
        if account := os.environ.get("WATERFALL_TEST_MCP_USER"):
            role = sql.Identifier(account)
            with self.db.connect() as connection:
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(self.namespace, role))
                connection.execute(sql.SQL("GRANT SELECT ON wf_mcp_status TO {}").format(role))
                for table in ("wf_documents", "wf_accounts", "wf_sessions", "wf_workspace"):
                    self.assertFalse(connection.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                                                        (account, table)).fetchone()[0])
            status_dsn = make_conninfo(self.dsn, user=account, password=os.getenv("WATERFALL_TEST_MCP_PASSWORD", ""))
        server = StdioServerParameters(command=sys.executable,
            args=["-m", "abs_waterfall_check.mcp_server", "--hosted"],
            env={"DATABASE_URL": status_dsn, "WATERFALL_ORGANIZATION": self.db.organization})
        async def verify():
            async with asyncio.timeout(15), stdio_client(server) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    self.assertEqual([t.name for t in (await client.list_tools()).tools], ["verify_distribution"])
                    cases = [({"run_id": r["run_id"]}, r["status"]) for r in rows]
                    cases.extend([({"run_id": "0" * 32}, "HOLD"),
                                  ({"run_id": "Ford Class A", "expose": True}, "HOLD")])
                    for inputs, expected in cases:
                        response = await client.call_tool("verify_distribution", inputs)
                        self.assertEqual(response.model_dump(mode="json", by_alias=True, exclude_none=True),
                                         {"content": [{"type": "text", "text": expected}], "isError": False})
        asyncio.run(verify())

    def test_hosted_roles_and_separate_demo_http_server(self):
        def start(policy, app):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.access, server.app = policy, app
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            return server

        def request(server, path, body=None, cookie=""):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=4)
            try:
                connection.request("POST" if body is not None else "GET", path,
                    body=json.dumps(body) if body is not None else None,
                    headers={"Origin": "https://pilot.example", "Host": "pilot.example",
                             "Content-Type": "application/json", "Cookie": cookie})
                response = connection.getresponse()
                return response.status, json.loads(response.read()), response.getheader("Set-Cookie", "").split(";")[0]
            finally:
                connection.close()

        policy = PostgresAccess(self.db, "https://pilot.example")
        private = start(policy, self.app)
        public = start(access.Access("demo", origin=policy.origin), Demo())
        self.assertEqual(request(private, "/api/dashboard")[0], 401)
        for role in ("analyst", "reviewer", "operator"):
            policy.manage_user("add-user", role, role=role, password="local test")
            status, _, cookie = request(private, "/api/login", {"username": role, "password": "local test"})
            self.assertEqual(status, 200)
            self.assertEqual(request(private, "/api/operator", cookie=cookie)[0], 200 if role == "operator" else 403)
            self.assertEqual(request(private, "/api/releases", cookie=cookie)[0], 200 if role == "operator" else 403)
            if role == "analyst":
                self.assertEqual(request(private, "/api/deals/ford/approve", {"confirmed": True}, cookie)[0], 403)
                status, result, _ = request(private, "/api/deals/mastr/runs",
                                            {"mode": "check", "case_id": "mastr-amended"}, cookie)
                self.assertEqual((status, result["status"]), (200, "PASS"))
                private_run = "/api/runs/" + result["run_id"]
            self.assertEqual(request(private, private_run + "/audit", cookie=cookie)[0],
                             200 if role == "operator" else 403)
        status, demo, _ = request(public, "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual({d["id"] for d in demo["deals"]}, {"ford", "mastr"})
        self.assertEqual(request(public, private_run, cookie=cookie)[0], 404)
        for endpoint in ("/api/intake", "/api/deals", "/api/deals/ford/draft-preview", "/api/deals/ford/draft"):
            self.assertEqual(request(public, endpoint, {"source_text": "Local test excerpt"}, cookie)[0], 403)

    def test_durable_sessions_password_rotation_and_revocation(self):
        access = PostgresAccess(self.db, "https://pilot.example")
        access.manage_user("add-user", "analyst", role="analyst", password="local test password")
        self.assertIsNone(access.login("analyst", "incorrect"))
        session = access.login("analyst", "local test password")
        cookie = access.cookie(session)
        self.assertIn("Secure", cookie)
        db, _ = self.restart()
        reopened = PostgresAccess(db, access.origin)
        self.assertEqual(reopened.identity(cookie), {"username": "analyst", "role": "analyst"})
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT digest FROM wf_sessions").fetchone()[0],
                             hashlib.sha256(session.encode()).digest())
        reopened.manage_user("reset-password", "analyst", password="changed")
        self.assertIsNone(access.identity(cookie))
        self.assertIsNone(access.login("analyst", "local test password"))
        token = reopened.login("analyst", "changed")
        access.logout(reopened.cookie(token))
        self.assertIsNone(reopened.identity(reopened.cookie(token)))
        token = reopened.login("analyst", "changed")
        access.manage_user("disable-user", "analyst")
        self.assertIsNone(reopened.identity(reopened.cookie(token)))
        self.assertIsNone(reopened.login("analyst", "changed"))
