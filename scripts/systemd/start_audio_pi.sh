#!/bin/bash
set -euo pipefail

REPO_DIR="${AUDIO_PI_REPO_DIR:-$(pwd)}"
PREFERRED_PORT="${FLASK_PORT:-80}"
FALLBACK_PORT="${AUDIO_PI_FALLBACK_PORT:-8080}"
ACTIVE_PORT_FILE="${AUDIO_PI_ACTIVE_PORT_FILE:-/run/audio-pi/active_port}"

port_is_available() {
    local port="$1"
    python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

SELECTED_PORT="$PREFERRED_PORT"
if ! port_is_available "$SELECTED_PORT"; then
    if [ "$SELECTED_PORT" = "80" ] && port_is_available "$FALLBACK_PORT"; then
        SELECTED_PORT="$FALLBACK_PORT"
        echo "Port 80 ist bereits belegt, verwende automatisch Port ${SELECTED_PORT}." >&2
    else
        echo "Port ${SELECTED_PORT} ist bereits belegt und kein automatischer Fallback ist möglich." >&2
        exit 1
    fi
fi

export FLASK_PORT="$SELECTED_PORT"
printf '%s\n' "$SELECTED_PORT" > "$ACTIVE_PORT_FILE"

cd "$REPO_DIR"
exec "$REPO_DIR/venv/bin/gunicorn" --config "$REPO_DIR/gunicorn.conf.py" app:app
