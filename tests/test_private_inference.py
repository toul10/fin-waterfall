from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from abs_waterfall_check import local
from scripts import start_app


class PrivateInferenceTests(unittest.TestCase):
    @patch("abs_waterfall_check.local.HTTPConnection")
    def test_authentication_header_is_limited_to_loopback_llama(self, dial):
        for backend, key, expected in [
            ("llama.cpp", "test-token", "Bearer test-token"),
            ("llama.cpp", None, None), ("ollama", "test-token", None),
        ]:
            response = dial.return_value.getresponse.return_value
            response.status = 401
            with self.assertRaisesRegex(ValueError, "HTTP 401"):
                local._local_proposal("qa", 43000, backend, "a", "b", {}, key)
            dial.assert_called_with("127.0.0.1", 43000, timeout=90)
            headers = dial.return_value.request.call_args.args[3]
            self.assertEqual(headers.get("Authorization"), expected)

    @patch("scripts.start_app.HTTPConnection")
    @patch("scripts.start_app.probe")
    def test_private_readiness_requires_anonymous_inference_rejection(self, probe, dial):
        probe.side_effect = [([], {"status": "ok"}), ([], {"data": [{"id": "qa"}]})] * 2
        response = dial.return_value.getresponse.return_value
        response.status = 200
        with self.assertRaisesRegex(RuntimeError, "require authentication"):
            start_app.model_ready(43001, "qa", "test-token")
        response.status = 401
        self.assertTrue(start_app.model_ready(43001, "qa", "test-token"))
        self.assertNotIn("Authorization", dial.return_value.request.call_args.args[3])

    def test_manager_shares_transient_token_without_command_line_or_log_output(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            fake_binary = Path(directory) / "fixture"
            fake_binary.touch()
            argv = ["start_app", "--mode", "private", "--store", directory,
                    "--llama-server", str(fake_binary), "--model-file", str(fake_binary)]
            stack.enter_context(patch("sys.argv", argv))
            for name in ("ThreadingHTTPServer", "web.access.Access", "stop", "os.chdir"):
                stack.enter_context(patch("scripts.start_app." + name))
            stack.enter_context(patch("scripts.start_app.select_model_port", return_value=43001))
            spawn = stack.enter_context(patch("scripts.start_app.subprocess.Popen"))
            spawn.return_value.poll.return_value = None
            ready = stack.enter_context(patch("scripts.start_app.model_ready", return_value=True))
            serve = stack.enter_context(patch("scripts.start_app.web.serve_workspace"))
            log = stack.enter_context(redirect_stdout(StringIO()))
            self.assertEqual(start_app.main(), 0)
            arguments, options = spawn.call_args
            token = options["env"]["LLAMA_API_KEY"]
            self.assertGreaterEqual(len(token), 40)
            self.assertNotIn(token, " ".join(arguments[0]))
            self.assertNotIn(token, log.getvalue())
            self.assertEqual(options["stdout"], start_app.subprocess.DEVNULL)
            self.assertEqual(options["stderr"], start_app.subprocess.DEVNULL)
            self.assertEqual(ready.call_args.args[2], token)
            self.assertEqual(serve.call_args.kwargs["model_token"], token)
