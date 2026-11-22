#!/bin/sh
# Local single-organization example, with a synthetic Gemini recorder.
set -eu
cd "$(dirname "$0")/.."
if ! docker info >/dev/null 2>&1 && docker --context colima info >/dev/null 2>&1; then
    export DOCKER_CONTEXT=colima
fi
context=$(docker context show)
endpoint=$(docker context inspect "$context" --format '{{.Endpoints.docker.Host}}')
case "${DOCKER_HOST:-$endpoint}" in
    unix://*|npipe://*) ;;
    *) echo "Choose a local Docker engine for this example." >&2; exit 1 ;;
esac

state=private/hosted-local
umask 077
mkdir -p "$state"
if [ ! -f "$state/compose.env" ]; then
    python3 - "$state/compose.env" <<'PY'
import os, secrets, sys
from tempfile import NamedTemporaryFile
path = sys.argv[1]
with NamedTemporaryFile(mode="w", dir=os.path.dirname(path)) as staged:
    staged.write("POSTGRES_PASSWORD=" + secrets.token_hex(32) + "\n")
    staged.flush()
    try:
        os.link(staged.name, path)
    except FileExistsError:
        pass
PY
fi
compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose --env-file "$state/compose.env" -f deploy/compose.hosted.yaml "$@"
    elif command -v docker-compose >/dev/null; then
        docker-compose --env-file "$state/compose.env" -f deploy/compose.hosted.yaml "$@"
    elif [ "$(uname -sm)" = "Darwin arm64" ] && [ -x out/hosted-tools/docker-compose-darwin-aarch64 ]; then
        out/hosted-tools/docker-compose-darwin-aarch64 --env-file "$state/compose.env" -f deploy/compose.hosted.yaml "$@"
    else
        echo "Install Docker Compose and run this command again." >&2; return 1
    fi
}
case "${1:-up}" in
    up)
        docker build -t fin-waterfall:local .
        compose up --wait -d
        echo "Hosted workspace http://127.0.0.1:${HOSTED_PORT:-8820}/"
        ;;
    add-user|reset-password|disable-user)
        if [ -t 0 ]; then
            compose exec app python -m abs_waterfall_check.postgres "$@"
        else
            compose exec -T app python -m abs_waterfall_check.postgres "$@"
        fi
        ;;
    restart)
        compose restart app
        compose up --wait -d
        ;;
    down) compose down ;;
    *) echo "Usage: sh scripts/hosted-local.sh {up|add-user|reset-password|disable-user|restart|down}" >&2; exit 2 ;;
esac
