#!/usr/bin/env python3
"""Version bump helper for the agent-update workflow.

Subcommands:
  check   Fetch the public release feed; if a newer agent version exists,
          rewrite image/Dockerfile (version + .deb checksums) and
          umbrel-app.yml (version + releaseNotes from the feed description).
          Emits `changed=true|false` and `version=X.Y.Z` to $GITHUB_OUTPUT
          (or stdout when run locally).
  pin     Rewrite the compose image reference to a tag@digest. Used after the
          multi-arch image is pushed: pin --version X.Y.Z --digest sha256:...

Only touches the same fields a human release bump touches (see README
"Release flow"). No third-party dependencies.
"""
import json
import os
import pathlib
import re
import sys
import urllib.request

FEED = "https://downloads.braiins.com/braiins-manager-agent/index.json"
ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "image/Dockerfile"
MANIFEST = ROOT / "braiins-braiins-manager-agent/umbrel-app.yml"
COMPOSE = ROOT / "braiins-braiins-manager-agent/docker-compose.yml"
REGISTRY_OWNER = os.environ.get("REGISTRY_OWNER", "tgr-braiins")


def out(key, value):
    dest = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}"
    if dest:
        with open(dest, "a") as f:
            f.write(line + "\n")
    print(line)


def parse_version(v):
    return tuple(int(x) for x in v.split("."))


def latest_release():
    # the CDN rejects urllib's default User-Agent
    req = urllib.request.Request(FEED, headers={"User-Agent": "bma-umbrel-update-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        feed = json.load(r)
    return max(feed["releases"], key=lambda rel: parse_version(rel["metadata"]["bma_version"]))


def current_version():
    m = re.search(r'^version: "([^"]+)"', MANIFEST.read_text(), re.M)
    return m.group(1)


def check():
    rel = latest_release()
    meta = rel["metadata"]
    new = meta["bma_version"]
    cur = current_version()
    if parse_version(new) <= parse_version(cur):
        out("changed", "false")
        out("version", cur)
        return

    assets = meta["assets"]
    missing = [a for a in ("linux_x86_64", "linux_aarch64") if not assets.get(a)]
    if missing:
        sys.exit(f"release {new} is missing required assets: {missing}")

    t = DOCKERFILE.read_text()
    t = re.sub(r"ARG BMA_VERSION=.*", f"ARG BMA_VERSION={new}", t)
    t = re.sub(r"ARG SHA256_AMD64=.*", f"ARG SHA256_AMD64={assets['linux_x86_64']['integrity']['checksum']}", t)
    t = re.sub(r"ARG SHA256_ARM64=.*", f"ARG SHA256_ARM64={assets['linux_aarch64']['integrity']['checksum']}", t)
    DOCKERFILE.write_text(t)

    t = MANIFEST.read_text()
    t = re.sub(r'^version: ".*"', f'version: "{new}"', t, count=1, flags=re.M)
    desc = meta.get("description", "").replace("\r", "").strip() or f"Braiins Manager Agent {new}."
    block = "\n".join(("  " + line).rstrip() for line in desc.splitlines())
    t = re.sub(r"releaseNotes: .*?\n\ndeveloper:", f"releaseNotes: |-\n{block}\n\ndeveloper:", t, count=1, flags=re.S)
    MANIFEST.write_text(t)

    out("changed", "true")
    out("version", new)


def pin(version, digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit(f"not a digest: {digest}")
    t = COMPOSE.read_text()
    t, n = re.subn(
        r"image: ghcr\.io/\S+",
        f"image: ghcr.io/{REGISTRY_OWNER}/braiins-manager-agent:{version}@{digest}",
        t,
    )
    if n != 1:
        sys.exit(f"expected exactly one image line in compose, found {n}")
    COMPOSE.write_text(t)
    print(f"pinned {version}@{digest}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "check":
        check()
    elif len(sys.argv) >= 2 and sys.argv[1] == "pin":
        args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
        pin(args["--version"], args["--digest"])
    else:
        sys.exit(__doc__)
