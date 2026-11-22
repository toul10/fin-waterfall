"""Run nginx in the foreground in front of one loopback Waterfall instance."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess


def port(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("Use a port between 1 and 65535")
    return number


def nginx_file(path):
    path = path.resolve(strict=True)
    if not path.is_file() or any(char in str(path) for char in '\n\r"$\\'):
        raise ValueError(f"Choose an ordinary certificate or key file with a simple path: {path}")
    return f'"{path}"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--listen", type=ipaddress.IPv4Address, default="127.0.0.1")
    parser.add_argument("--port", type=port, default=8443)
    parser.add_argument("--upstream-port", type=port, required=True)
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", args.hostname):
        parser.error("Use the DNS name or IPv4 address on the certificate")
    nginx = shutil.which("nginx")
    if not nginx:
        parser.error("Install nginx with the HTTP SSL module")
    os.umask(0o077)
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    template = (Path(__file__).resolve().parents[1] / "deploy" / "nginx.conf.in").read_text()
    try:
        values = {"LISTEN": f"{args.listen}:{args.port}", "HOST": args.hostname,
                  "CERT": nginx_file(args.cert), "KEY": nginx_file(args.key), "UPSTREAM": str(args.upstream_port)}
    except (OSError, ValueError) as error:
        parser.error(str(error))
    conf = runtime / "nginx.conf"
    conf.write_text(re.sub(r"@([A-Z]+)@", lambda m: values[m[1]], template))
    command = [nginx, "-e", "stderr", "-p", str(runtime) + "/", "-c", str(conf)]
    subprocess.run([*command, "-t"], check=True)
    os.execv(nginx, [*command, "-g", "daemon off;"])


if __name__ == "__main__":
    main()
