#!/bin/sh
# Supervises bma-daemon and the config web UI.
# The daemon only runs once /data/daemon.yaml exists (written by the web UI
# or pre-seeded via AGENT_ID / SECRET_KEY env vars).
set -eu

CONFIG=/data/daemon.yaml
# The daemon writes $LOG (pre-created in the image, writable for uid 1000);
# mirror it to stdout so `docker logs` still shows daemon output.
LOG=/var/log/bma.log
: > "$LOG"
tail -F "$LOG" &

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
    # Cap log growth (truncation is safe: daemon appends, tail -F follows)
    if [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 52428800 ]; then
        : > "$LOG"
    fi
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
