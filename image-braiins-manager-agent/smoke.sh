#!/bin/sh
# Smoke test of a built Manager Agent image: setup flow, daemon start, status
# and widget endpoints. Used by CI and by the release workflow.
#   image-braiins-manager-agent/smoke.sh <image> [expected-agent-version]
set -eu
IMAGE=${1:?usage: smoke.sh <image> [expected-agent-version]}
EXPECT=${2:-$(sed -n 's/^ARG BMA_VERSION=//p' "$(dirname "$0")/Dockerfile")}
PORT=18080
NAME=smoke-agent-$$

# wait_for <seconds> <command...>: retry until the command succeeds
wait_for() {
    t=$1; shift
    while ! "$@" >/dev/null 2>&1; do
        t=$((t - 1)); [ "$t" -gt 0 ] || { echo "timed out: $*" >&2; return 1; }
        sleep 1
    done
}
cleanup() { docker logs "$NAME" 2>&1 | tail -20; docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --rm --entrypoint /usr/bin/bma-daemon "$IMAGE" --version | grep -F " $EXPECT"

docker run -d --name "$NAME" -p "$PORT:8080" --user 1000:1000 --init -e APP_VERSION=smoke-version "$IMAGE"
URL=http://localhost:$PORT
wait_for 30 curl -fs "$URL/status"
curl -fs "$URL/status" | grep '"configured": false'
curl -fs "$URL/" | grep 'Umbrel app smoke-version'

# a save without the page's header (a plain form from another site) is refused
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST --data "agent_id=123e4567-e89b-42d3-a456-426614174000&secret_key=123e4567-e89b-42d3-a456-426614174001" "$URL/")
[ "$code" = 403 ]
curl -fs -X POST -H "X-BMA-Setup: 1" --data "agent_id=123e4567-e89b-42d3-a456-426614174000&secret_key=123e4567-e89b-42d3-a456-426614174001" "$URL/" | grep '"ok": true'

running() { curl -fs "$URL/status" | grep -q '"running": true'; }
wait_for 30 running
curl -fs "$URL/widgets/status" | grep '"three-stats"'
curl -fs "$URL/widgets/status" | grep '"icon": "plug-connected"'

# docker stop must let the daemon exit cleanly well within the grace period
start=$(date +%s)
docker stop -t 20 "$NAME" >/dev/null
[ $(( $(date +%s) - start )) -lt 15 ]
echo "smoke ok: $IMAGE"
