#!/bin/sh
# Supervises bma-daemon and the config web UI.
# The daemon only runs once /data/daemon.yaml exists (written by the web UI
# or pre-seeded via AGENT_ID / SECRET_KEY env vars).
set -eu

CONFIG=/data/daemon.yaml
mkdir -p /data
ln -sf /dev/stdout /var/log/bma.log

if [ ! -f "$CONFIG" ] && [ -n "${AGENT_ID:-}" ] && [ -n "${SECRET_KEY:-}" ]; then
    printf 'agent_id: %s\nsecret_key: %s\n' "$AGENT_ID" "$SECRET_KEY" > "$CONFIG"
fi

python3 /usr/lib/bma/webui.py &

DAEMON_PID=""
CONFIG_MTIME=""

term() {
    [ -n "$DAEMON_PID" ] && kill "$DAEMON_PID" 2>/dev/null || true
    exit 0
}
trap term TERM INT

while true; do
    if [ -f "$CONFIG" ]; then
        MTIME=$(stat -c %Y "$CONFIG")
        if [ -z "$DAEMON_PID" ] || ! kill -0 "$DAEMON_PID" 2>/dev/null; then
            /usr/bin/bma-daemon -c "$CONFIG" &
            DAEMON_PID=$!
            CONFIG_MTIME=$MTIME
        elif [ "$MTIME" != "$CONFIG_MTIME" ]; then
            kill "$DAEMON_PID" 2>/dev/null || true
            wait "$DAEMON_PID" 2>/dev/null || true
            DAEMON_PID=""
            continue
        fi
    fi
    sleep 5
done
