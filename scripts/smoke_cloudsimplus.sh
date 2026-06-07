#!/usr/bin/env sh
set -eu

MAVEN_PATH=${MAVEN_PATH:-mvn}
RUN_EXAMPLE=0
SERVER=${SERVER:-http://127.0.0.1:8024}
SCENARIO=${SCENARIO:-normal}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --maven-path)
            MAVEN_PATH=$2
            shift 2
            ;;
        --run-example)
            RUN_EXAMPLE=1
            shift
            ;;
        --server)
            SERVER=$2
            shift 2
            ;;
        --scenario)
            SCENARIO=$2
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

echo "Checking Java runtime..."
java -version

echo "Checking Maven runtime: $MAVEN_PATH"
"$MAVEN_PATH" -version

cd "$REPO_ROOT/examples/cloudsimplus"
"$MAVEN_PATH" clean compile

if [ "$RUN_EXAMPLE" -eq 1 ]; then
    if command -v python >/dev/null 2>&1; then
        python - "$SERVER" <<'PY'
import json
import sys
import urllib.request

server = sys.argv[1].rstrip("/")
try:
    with urllib.request.urlopen(f"{server}/health", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    raise SystemExit(f"Tianjun HTTP server is not reachable at {server}. Start it before using --run-example. Original error: {exc}")

if payload.get("status") != "ok":
    raise SystemExit(f"Unexpected /health status at {server}: {payload.get('status')}")
PY
    else
        echo "python is required for the /health preflight when --run-example is used." >&2
        exit 1
    fi
    "$MAVEN_PATH" exec:java -Dexec.args="$SERVER $SCENARIO"
fi
