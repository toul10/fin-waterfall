import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from abs_waterfall_check.local import extract, load

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
MARKER = "PRIVATE-STIPULATION-7E941C"


def cli(*args):
    return subprocess.run([sys.executable, "-m", "abs_waterfall_check", *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, timeout=15)


class ReleaseGateTests(unittest.TestCase):
    def test_both_adapters_replay_published_payments(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "history.json"
            for candidate in ("good", "faulty"):
                result = cli("history", "--adapter", candidate, "--output", report)
                self.assertEqual((result.returncode, result.stdout.strip()),
                                 (0, "PASS: 70/70 class-level comparisons"), result.stderr)
                self.assertEqual([p["first_priority_due_cents"] for p in load(report)["periods"]],
                                 [0] * 5)

    def test_generated_case_gates_both_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp) / "generated.json"
            result = cli("search", "--adapter", "faulty", "--output", case)
            self.assertEqual(result.returncode, 1, result.stderr)
            evidence = load(case)
            self.assertEqual(evidence, load(FIXTURES / "ford-synthetic-regression.json"))
            first = evidence["first_observed_divergence"]
            self.assertEqual(first["scenario"]["cash"], 1)
            self.assertEqual(first["delta_dollars"],
                             {"class_a_principal": "-0.01", "class_b_interest": "0.01"})
            self.assertEqual(evidence["fully_funded"]["status"], "PASS")
            self.assertEqual(evidence["one_cent_shortfall"]["status"], "BLOCK")
            self.assertEqual(evidence["one_cent_shortfall"]["scenario"]["cash"], 21_105_632)
            self.assertEqual(evidence["affected_class_by_payment"],
                             {"class_a_principal": "A-1", "class_b_interest": "B"})
            regression = evidence["regression"]
            self.assertEqual(regression["delta_dollars"],
                             {"class_a_principal": "-50000.00", "class_b_interest": "50000.00"})
            self.assertEqual(regression["expected_total_cents"], 5_000_000)
            self.assertEqual(regression["candidate_total_cents"], 5_000_000)
            for candidate, status, code in (("good", "PASS", 0), ("faulty", "BLOCK", 1),
                                            ("missing:adapter", "HOLD", 2)):
                with self.subTest(candidate=candidate):
                    check = cli("check", case, "--adapter", candidate)
                    self.assertEqual(check.returncode, code, check.stderr)
                    if code != 2:
                        self.assertEqual(json.loads(check.stdout)["status"], status)


class PrivacyTests(unittest.TestCase):
    @patch("abs_waterfall_check.local.HTTPConnection")
    def test_model_draft_preserves_evidence_for_both_payments(self, connect):
        principal = "(iv) Fourth, pay the Class A priority principal."
        interest = "(v) Fifth, pay the Class B interest."
        source = f"{principal}\n \n{interest}"
        proposed = {
            "order": ["class_a_principal", "class_b_interest"],
            "evidence": {"class_a_principal": "P1", "class_b_interest": "P2"},
        }
        response = connect.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": json.dumps(proposed)}}],
        }).encode()

        draft = extract(source, "Section 4.2", "Synthetic deal", "1", "1",
                        model="qwen-local", backend="llama.cpp")
        self.assertEqual(draft["order"], proposed["order"])
        self.assertEqual(draft["support"], source)
        self.assertNotIn("approved_by", draft)
        connect.assert_called_once_with("127.0.0.1", 8080, timeout=90)

    def test_approval_run_and_real_stdio_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft = root / "draft.json"
            result = cli("extract", FIXTURES / "synthetic-private-contract.txt",
                         "--section", "Section 4.2", "--deal", "Synthetic private deal",
                         "--deal-version", "1", "--rule-version", "1", "--output", draft)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), hashlib.sha256(draft.read_bytes()).hexdigest())
            self.assertIn(MARKER, load(draft)["support"])
            scenario = FIXTURES / "synthetic-private-scenario.json"
            missing = cli("register", scenario, "--store", root, "--rule-id", "0" * 32)
            self.assertEqual(missing.returncode, 2)
            bad_approval = cli("approve", draft, "--store", root, "--reviewer", "Analyst",
                               "--sha256", "0" * 64)
            self.assertEqual(bad_approval.returncode, 2)
            approval = cli("approve", draft, "--store", root, "--reviewer", "Analyst",
                           "--sha256", result.stdout.strip())
            self.assertEqual(approval.returncode, 0, approval.stderr)
            rule_id = approval.stdout.strip()
            cases = [(json.loads(missing.stdout)["run_id"], "HOLD")]
            for candidate, status, code in (("good", "PASS", 0), ("faulty", "BLOCK", 1)):
                result = cli("register", scenario, "--store", root, "--rule-id", rule_id,
                             "--adapter", candidate)
                self.assertEqual(result.returncode, code, result.stderr)
                run_id = json.loads(result.stdout)["run_id"]
                report = load(root / "runs" / f"{run_id}.json")
                self.assertIn(MARKER, report["rule"]["support"])
                self.assertEqual(report["scenario"]["class_a_principal"], 8_734_567)
                self.assertEqual(report["scenario"]["class_b_interest"], 9_456_789)
                cases.append((run_id, status))
            asyncio.run(self.check_mcp(root, cases))

    async def check_mcp(self, root, cases):
        server = StdioServerParameters(
            command=sys.executable, args=["-m", "abs_waterfall_check.mcp_server", "--store", str(root)],
            cwd=ROOT,
        )
        async with asyncio.timeout(15):
            async with stdio_client(server) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    self.assertEqual([tool.name for tool in tools], ["verify_distribution"])
                    calls = [({"run_id": run_id}, status) for run_id, status in cases]
                    calls += [
                        ({"run_id": MARKER}, "HOLD"),
                        ({"run_id": "f" * 32}, "HOLD"),
                        ({"run_id": 8_734_567}, "HOLD"),
                        ({"run_id": cases[-1][0], "excerpt": MARKER}, "HOLD"),
                        ({}, "HOLD"),
                    ]
                    for arguments, status in calls:
                        result = await session.call_tool("verify_distribution", arguments)
                        captured = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                        self.assertEqual(captured, {"content": [{"type": "text", "text": status}],
                                                    "isError": False})
                        for secret in (MARKER, "87,345.67", "94,567.89", "8734567", "9456789"):
                            self.assertNotIn(secret, json.dumps(captured))

    def test_malformed_wire_requests_do_not_echo_input(self):
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(self.check_malformed_requests(temp))

    async def check_malformed_requests(self, root):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "abs_waterfall_check.mcp_server", "--store", root,
            cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def send(method, params=None, request_id=None):
            message = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                message["params"] = params
            if request_id is not None:
                message["id"] = request_id
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()
            if request_id is not None:
                return json.loads(await process.stdout.readline())

        try:
            async with asyncio.timeout(15):
                initialized = await send("initialize", {
                    "protocolVersion": types.LATEST_PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "privacy test", "version": "1"},
                }, request_id=1)
                self.assertIn("result", initialized)
                await send("notifications/initialized")
                malformed = await send("tools/call", {
                    "name": "verify_distribution", "arguments": MARKER,
                }, request_id=2)
                self.assertEqual(malformed["error"], {
                    "code": types.INVALID_PARAMS, "message": "Invalid request parameters", "data": "",
                })
                unknown = await send("tools/call", {
                    "name": MARKER, "arguments": {"run_id": "8734567"},
                }, request_id=3)
                self.assertEqual(unknown["result"], {
                    "content": [{"type": "text", "text": "HOLD"}], "isError": False,
                })
                remaining_stdout, stderr = await process.communicate()
                transcript = json.dumps([initialized, malformed, unknown]).encode()
                for secret in (MARKER, "8734567"):
                    self.assertNotIn(secret.encode(), transcript + remaining_stdout + stderr)
                self.assertEqual(remaining_stdout + stderr, b"")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
