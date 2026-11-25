#!/bin/sh
# Smoke test of a built Toolbox image: the binary runs, the web interface
# answers. Used by CI and by the release workflow.
#   image-braiins-toolbox/smoke.sh <image> [expected-toolbox-version]
set -eu
IMAGE=${1:?usage: smoke.sh <image> [expected-toolbox-version]}
EXPECT=${2:-$(sed -n 's/^ARG TOOLBOX_VERSION=//p' "$(dirname "$0")/Dockerfile")}
PORT=18888
NAME=smoke-toolbox-$$

wait_for() {
    t=$1; shift
    while ! "$@" >/dev/null 2>&1; do
        t=$((t - 1)); [ "$t" -gt 0 ] || { echo "timed out: $*" >&2; return 1; }
        sleep 1
    done
}
cleanup() { docker logs "$NAME" 2>&1 | tail -20; docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --rm --user 1000:1000 "$IMAGE" --version | grep -F "braiins-toolbox $EXPECT"
docker run -d --name "$NAME" -p "$PORT:8888" --user 1000:1000 --init "$IMAGE"
wait_for 60 curl -fs "http://localhost:$PORT/"
curl -fs "http://localhost:$PORT/" | grep -F '<title>Braiins Toolbox</title>'
docker logs "$NAME" 2>&1 | grep -F 'server listening'
echo "smoke ok: $IMAGE"
