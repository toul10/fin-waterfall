"""Run a standalone example release model on loopback."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from abs_waterfall_check.model_service import make_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--behavior", choices=("good", "faulty"), default="good")
    parser.add_argument("--delay", type=float, default=0)
    args = parser.parse_args()
    with make_server(args.port, args.model_id, args.version, args.behavior, args.delay) as server:
        print(f"Serving {args.model_id} {args.version} at http://127.0.0.1:{server.server_port}/calculate", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
