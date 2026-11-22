"""Start the dashboard and its local Qwen server from this checkout."""
import argparse
import errno
from http.client import HTTPConnection, HTTPException
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".local-runtime"
sys.path.insert(0, str(ROOT))
from abs_waterfall_check import web


def probe(port, path, token=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", path, headers={"Authorization": "Bearer " + token} if token else {})
        response = connection.getresponse()
        server = response.getheader("Server", "").split()
        if response.status != 200:
            return server, {}
        data = json.loads(response.read(65537))
        return server, data if isinstance(data, dict) else {}
    except (OSError, HTTPException, ValueError):
        return [], {}
    finally:
        connection.close()


def dashboard_in_use(port, store):
    url = f"http://127.0.0.1:{port}/"
    server, info = probe(port, "/api/instance")
    if "WaterfallWorkspace/1" in server:
        if info.get("mode") == "local" and info.get("workspace_id") == web.workspace_id(store):
            print(f"This workspace is already running. Open {url}")
            print("To apply new startup settings, stop the running launcher with Ctrl+C first.")
            return 0
        if "workspace_id" in info:
            print(f"A different Waterfall workspace is running at {url}", file=sys.stderr)
        else:
            print(f"Waterfall is already running at {url} Open it in your browser.", file=sys.stderr)
            print("This older dashboard cannot identify its store. To apply your settings, stop its launcher with Ctrl+C first.", file=sys.stderr)
    else:
        print(f"Dashboard port {port} is occupied. The listener did not identify itself as Waterfall.", file=sys.stderr)
    print("To launch on a free dashboard port, use --port 0.", file=sys.stderr)
    return 1


def select_model_port(requested):
    port = 8080 if requested is None else requested
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            if requested is not None:
                raise RuntimeError(f"Qwen port {port} is occupied. Use --model-port 0 to start Qwen on a free port, "
                                   "or --use-running-model to reuse a known local model.") from error
            sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def model_ready(port, alias, token=None):
    _, health = probe(port, "/health")
    if health.get("status") != "ok":
        return False
    _, models = probe(port, "/v1/models", token)
    entries = models.get("data")
    if not isinstance(entries, list) or not any(isinstance(entry, dict) and entry.get("id") == alias for entry in entries):
        return False
    if token:
        # Public health and model metadata can be readable even on a protected server.
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("POST", "/v1/chat/completions", "{}", {"Content-Type": "application/json"})
            if connection.getresponse().status != 401:
                raise RuntimeError("Private inference did not require authentication")
        finally:
            connection.close()
    return True


def port_number(value):
    number = int(value)
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("choose a port from 0 to 65535")
    return number


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=port_number, default=8765, help="Dashboard port (default 8765, 0 for a free port).")
    parser.add_argument("--store", type=Path, default=ROOT / "private" / "web")
    parser.add_argument("--model-port", type=port_number, help="Qwen port (prefers 8080, 0 for a free port).")
    parser.add_argument("--model", default="qwen-local")
    parser.add_argument("--llama-server", type=Path, default=RUNTIME / "llama" / "llama-server")
    parser.add_argument("--model-file", type=Path,
                        default=RUNTIME / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--use-running-model", action="store_true",
                        help="Use an existing loopback llama.cpp endpoint.")
    parser.add_argument("--adapter", action="append", default=[], metavar="LABEL=SPEC",
                        help="Configure a trusted local candidate adapter. Repeat to add more.")
    parser.add_argument("--pricing-model", action="append", default=[], metavar="ID@VERSION=URL",
                        help="Register a versioned loopback pricing model (repeatable)")
    mode.add_argument("--without-qwen", action="store_true",
                        help="Run saved approvals and optional API drafting without a local model.")
    web.access.options(parser)
    args = parser.parse_args()

    if args.mode == "private" and args.use_running_model:
        parser.error("Private drafting requires launcher-managed Qwen. Omit --use-running-model or use --without-qwen.")
    if args.use_running_model and args.model_port == 0:
        parser.error("--use-running-model requires the listening port of your model")

    store = args.store if args.mode == "demo" else args.store.resolve()
    model_process = None
    token = None
    try:
        policy = web.access.Access(args.mode, args.users, args.origin, args.bind)
        if args.mode != "demo":
            policy.check_store(store)
        try:
            dashboard = ThreadingHTTPServer((args.bind, args.port), web.Handler)
        except OSError as error:
            if error.errno == errno.EADDRINUSE and args.mode == "local":
                return dashboard_in_use(args.port, store)
            raise
        with dashboard:
            model_port = args.model_port or 8080
            if args.mode == "demo":
                pass
            elif args.use_running_model:
                if not model_ready(model_port, args.model):
                    raise RuntimeError(f"Expected a healthy llama.cpp model named {args.model!r} on port {model_port}. "
                                       "Check --model and --model-port, or omit --use-running-model to start local Qwen.")
            elif not args.without_qwen:
                model_port = select_model_port(args.model_port)
                if not args.llama_server.is_file() or not args.model_file.is_file():
                    parser.error("llama-server or GGUF is missing. Supply --llama-server and --model-file.")
                print(f"Starting local Qwen on port {model_port}…", flush=True)
                child_options = {}
                if args.mode == "private":
                    token = secrets.token_urlsafe(32)
                    child_env = {**os.environ, "LLAMA_API_KEY": token}
                    child_env.pop("LLAMA_ARG_API_KEY_FILE", None)
                    child_env.pop("LLAMA_ARG_LOG_FILE", None)
                    # llama.cpp versions differ in how much authentication configuration they print.
                    child_options = {"env": child_env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                model_process = subprocess.Popen([
                    str(args.llama_server.resolve()), "--host", "127.0.0.1", "--port", str(model_port),
                    *(["--log-disable"] if token else []),
                    "--ctx-size", "4096", "--model", str(args.model_file.resolve()), "--alias", args.model,
                ], cwd=ROOT, **child_options)
                deadline = time.monotonic() + 75
                while not model_ready(model_port, args.model, token):
                    if model_process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Qwen did not become ready on port {model_port}. Check the model log above.")
                    time.sleep(0.3)
                if model_process.poll() is not None:
                    raise RuntimeError("Qwen exited during startup. Retry with --model-port 0.")
            os.chdir(ROOT)
            web.serve_workspace(dashboard, store, args.model, model_port,
                                args.adapter, args.pricing_model, not args.without_qwen, policy, model_token=token)
            return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        stop(model_process)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    sys.exit(main())
