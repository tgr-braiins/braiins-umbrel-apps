#!/bin/sh
# Smoke test of a built Bitcoin Data image. DEMO_MODE exercises the whole
# pipeline (epoch table, summary, widgets, exports) without a node; a second
# container without a node must wait, not crash. Used by CI and by the
# release workflow.
#   image-braiins-bitcoin-data/smoke.sh <image>
set -eu
IMAGE=${1:?usage: smoke.sh <image>}
NAME=smoke-bd-$$

wait_for() {
    t=$1; shift
    while ! "$@" >/dev/null 2>&1; do
        t=$((t - 1)); [ "$t" -gt 0 ] || { echo "timed out: $*" >&2; return 1; }
        sleep 1
    done
}
cleanup() {
    docker logs "$NAME" 2>&1 | tail -20
    docker rm -f "$NAME" "$NAME-nonode" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$NAME" -p 18090:8080 --user 1000:1000 --init -e DEMO_MODE=1 -e APP_VERSION=smoke-version "$IMAGE"
URL=http://localhost:18090
wait_for 30 curl -fs "$URL/api/summary"
curl -fs "$URL/api/summary" | grep '"status": "demo"'
curl -fs "$URL/api/epochs" | grep '"current": true'
curl -fs "$URL/widgets/status" | grep '"three-stats"'
curl -fs "$URL/widgets/status" | grep '"icon": "activity"'
curl -fs "$URL/widgets/halving" | grep '"text-with-progress"'
curl -fs "$URL/export.csv" | head -1 | grep '^epoch,'
curl -fs "$URL/" | grep 'Umbrel app smoke-version'

docker run -d --name "$NAME-nonode" -p 18091:8080 --user 1000:1000 --init "$IMAGE"
wait_for 30 curl -fs "http://localhost:18091/api/summary"
curl -fs "http://localhost:18091/api/summary" | grep '"status": "waiting"'
echo "smoke ok: $IMAGE"
