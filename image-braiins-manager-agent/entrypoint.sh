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
TAIL_PID=$!

UUID='[0-9a-fA-F]\{8\}-[0-9a-fA-F]\{4\}-[1-5][0-9a-fA-F]\{3\}-[89abAB][0-9a-fA-F]\{3\}-[0-9a-fA-F]\{12\}'
is_uuid() { printf '%s' "$1" | grep -qx "$UUID"; }

if [ ! -f "$CONFIG" ] && [ -n "${AGENT_ID:-}" ] && [ -n "${SECRET_KEY:-}" ]; then
    if is_uuid "$AGENT_ID" && is_uuid "$SECRET_KEY"; then
        # same as the web UI: mode 0600, written to a temp file, then renamed
        (umask 077 && printf 'agent_id: %s\nsecret_key: %s\n' "$AGENT_ID" "$SECRET_KEY" > "$CONFIG.env.tmp")
        mv "$CONFIG.env.tmp" "$CONFIG"
    else
        echo "AGENT_ID / SECRET_KEY are not UUIDs; ignoring them" >&2
    fi
fi

python3 /usr/lib/bma/webui.py &
WEBUI_PID=$!

DAEMON_PID=""
CONFIG_MTIME=""

# docker stop: let the daemon finish its shutdown before this script exits.
# With init: true this shell is the container's main process; once it exits,
# everything left in the container is killed at once.
term() {
    kill "$WEBUI_PID" 2>/dev/null || true
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
    kill "$TAIL_PID" 2>/dev/null || true
    exit 0
}
trap term TERM INT

while true; do
    # Cap log growth (truncation is safe: daemon appends, tail -F follows)
    if [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 52428800 ]; then
        : > "$LOG"
    fi
    # the config UI is the only way to (re)configure the agent: keep it up
    if ! kill -0 "$WEBUI_PID" 2>/dev/null; then
        python3 /usr/lib/bma/webui.py &
        WEBUI_PID=$!
    fi
    if [ -f "$CONFIG" ]; then
        # inode + mtime: webui.py replaces the file atomically (new inode), so
        # two saves within the same second still count as a change
        MTIME=$(stat -c '%i %Y' "$CONFIG" 2>/dev/null || echo gone)
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
    # sleep in the background and wait for it, so a TERM runs the trap at
    # once instead of after the sleep
    sleep 5 &
    wait $! || true
done
