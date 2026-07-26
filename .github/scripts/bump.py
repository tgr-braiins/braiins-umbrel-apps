#!/usr/bin/env python3
"""Version bump helper for the release workflows.

Subcommands:
  check           Fetch the public release feed; if a newer agent version
                  exists, rewrite image/Dockerfile (version + .deb checksums),
                  umbrel-app.yml (version + releaseNotes from the feed
                  description), and prepend a CHANGELOG.md entry.
                  Emits `changed=true|false` and `version=X.Y.Z` to
                  $GITHUB_OUTPUT (or stdout when run locally). Used by
                  agent-update.yml.
  release         Wrapper-only release (no new agent version): set umbrel-app.yml
                  version + releaseNotes and prepend a CHANGELOG.md entry from a
                  notes file. Does NOT touch the Dockerfile — the bundled agent
                  is unchanged. Used by wrapper-release.yml.
                  release --version X.Y.Z --notes-file notes.md
  pin             Rewrite the compose image reference to a tag@digest. Used
                  after the multi-arch image is pushed:
                  pin --version X.Y.Z --digest sha256:...
  current-version Print the manifest version and exit (no changes).

Only touches the same fields a human release bump touches (see README
"Release flow"). No third-party dependencies.
"""
import datetime
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
CHANGELOG = ROOT / "CHANGELOG.md"
CHANGELOG_MARKER = "<!-- new entries are inserted directly below this line -->"
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


def prepend_changelog(version, desc):
    """Insert a `## [version] - date` section right below the marker, unless
    one already exists for this version. Body is the feed description with
    Umbrel's `&nbsp;` spacer lines dropped and blank runs collapsed, so it
    reads as plain Markdown."""
    if not CHANGELOG.exists():
        return
    text = CHANGELOG.read_text()
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.M):
        return  # already recorded (e.g. re-run of the same bump)

    body_lines = [ln for ln in desc.splitlines() if ln.strip().lower() != "&nbsp;"]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    today = datetime.date.today().isoformat()
    entry = f"\n\n## [{version}] - {today}\n\n{body}"

    if CHANGELOG_MARKER in text:
        text = text.replace(CHANGELOG_MARKER, CHANGELOG_MARKER + entry, 1)
    else:  # marker gone: fall back to inserting before the newest entry
        text = re.sub(r"(?=^## \[)", entry.lstrip("\n") + "\n\n", text, count=1, flags=re.M)
    CHANGELOG.write_text(text)


def write_manifest_release(version, desc):
    """Set the manifest `version` and `releaseNotes` block, and prepend a
    matching CHANGELOG.md entry. Shared by `check` (feed-driven) and `release`
    (wrapper-only)."""
    desc = desc.replace("\r", "").strip() or f"Braiins Manager Agent {version}."
    t = MANIFEST.read_text()
    t = re.sub(r'^version: ".*"', f'version: "{version}"', t, count=1, flags=re.M)
    block = "\n".join(("  " + line).rstrip() for line in desc.splitlines())
    t = re.sub(r"releaseNotes: .*?\n\ndeveloper:", f"releaseNotes: |-\n{block}\n\ndeveloper:", t, count=1, flags=re.S)
    MANIFEST.write_text(t)
    prepend_changelog(version, desc)


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

    write_manifest_release(new, meta.get("description", ""))

    out("changed", "true")
    out("version", new)


def release(version, notes_file):
    # Accept a semver core with an optional pre-release/build suffix, so a
    # wrapper revision can sort above the agent version without colliding with a
    # future upstream release (e.g. agent 4.11.0 -> wrapper "4.11.1-1").
    if not re.fullmatch(r"\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?", version):
        sys.exit(f"not a valid version: {version}")
    if version == current_version():
        sys.exit(f"version {version} is unchanged; existing installs get no "
                 "Update badge. Pass a higher version, or run a digest-only "
                 "re-pin instead (empty version input).")
    notes = pathlib.Path(notes_file).read_text() if notes_file else ""
    write_manifest_release(version, notes)
    out("version", version)


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
    cmd = sys.argv[1] if len(sys.argv) >= 2 else ""
    args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    if cmd == "check":
        check()
    elif cmd == "release":
        release(args["--version"], args.get("--notes-file"))
    elif cmd == "pin":
        pin(args["--version"], args["--digest"])
    elif cmd == "current-version":
        print(current_version())
    else:
        sys.exit(__doc__)
