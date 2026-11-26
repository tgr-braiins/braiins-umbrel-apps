#!/bin/sh
# Smoke test of a built Braiins Proxy image: the binary runs, the web
# interface and the status API answer, the history goes to /data, and the
# container stops cleanly. Used by CI and by the release workflow.
#   image-braiins-proxy/smoke.sh <image> [expected-version]
set -eu
IMAGE=${1:?usage: smoke.sh <image> [expected-version]}
EXPECT=${2:-$(sed -n 's/^ARG BRAIINS_PROXY_VERSION=//p' "$(dirname "$0")/Dockerfile")}
PORT=18550
NAME=smoke-proxy-$$

wait_for() {
    t=$1; shift
    while ! "$@" >/dev/null 2>&1; do
        t=$((t - 1)); [ "$t" -gt 0 ] || { echo "timed out: $*" >&2; return 1; }
        sleep 1
    done
}
cleanup() { docker logs "$NAME" 2>&1 | tail -20; docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --rm --user 1000:1000 "$IMAGE" --version | grep -F "Braiins Proxy $EXPECT,"
docker run --rm --user 1000:1000 --entrypoint socat "$IMAGE" -V | grep -F 'socat version'
docker run -d --name "$NAME" -p "$PORT:8080" --user 1000:1000 --init "$IMAGE"
wait_for 60 curl -fs "http://localhost:$PORT/"
curl -fs "http://localhost:$PORT/" | grep -F '<title>Braiins Proxy</title>'
curl -fs "http://localhost:$PORT/api/v1/status" | grep -F '"miners"'
curl -fs "http://localhost:$PORT/metrics" >/dev/null
docker exec "$NAME" test -f /data/state/braiins-proxy/history.db

start=$(date +%s)
docker stop "$NAME" >/dev/null
took=$(( $(date +%s) - start ))
[ "$took" -lt 15 ] || { echo "docker stop took ${took}s" >&2; exit 1; }
echo "smoke ok: $IMAGE"
