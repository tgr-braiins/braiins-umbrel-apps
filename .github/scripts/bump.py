#!/usr/bin/env python3
"""Version bump helper for the release workflows.

Serves both apps in this store; select with --app (default: agent, so
pre-existing agent workflow invocations keep working unchanged).

Subcommands:
  check           Fetch the app's public release feed; if a newer upstream
                  version exists, rewrite its image Dockerfile (version +
                  asset checksums), umbrel-app.yml (version + releaseNotes
                  from the feed description), and prepend a changelog entry.
                  Emits `changed=true|false` and `version=...` to
                  $GITHUB_OUTPUT (or stdout when run locally). Used by
                  agent-update.yml / toolbox-update.yml.
  release         Wrapper-only release (no new upstream version): set
                  umbrel-app.yml version + releaseNotes and prepend a
                  changelog entry from a notes file. Does NOT touch the
                  Dockerfile — the bundled upstream binary is unchanged.
                  Used by wrapper-release.yml / toolbox-wrapper-release.yml.
                  release --version X.Y[.Z][-N] --notes-file notes.md
  pin             Rewrite the compose image reference to a tag@digest. Used
                  after the multi-arch image is pushed:
                  pin --version X.Y[.Z][-N] --digest sha256:...
  current-version Print the manifest version and exit (no changes).

Wrapper-suffix ordering differs per app, on purpose:
  - agent: wrapper versions are a pre-release of the NEXT patch
    ("4.11.1-1" while the agent is 4.11.0), so per semver they sort BELOW a
    real upstream 4.11.1 — upstream wins when it ships.
  - toolbox: upstream is CalVer (26.06, sometimes 26.06.1), so a fake next
    patch would read like a real upstream hotfix. Wrapper versions are a
    debian-style revision of the CURRENT version ("26.06-1"), sorting ABOVE
    plain 26.06 and BELOW upstream 26.06.1 / 26.07.
  This ordering only matters for `check` deciding whether the feed is newer
  than the manifest. umbrelOS itself shows the Update badge on plain version
  INEQUALITY (ui/src/hooks/use-apps-with-updates.ts: `version !== version`),
  not ordering.

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

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY_OWNER = os.environ.get("REGISTRY_OWNER", "tgr-braiins")

APPS = {
    "agent": {
        "display": "Braiins Manager Agent",
        "feed": "https://downloads.braiins.com/braiins-manager-agent/index.json",
        "feed_version_key": "bma_version",
        "dockerfile": "image-braiins-manager-agent/Dockerfile",
        "version_arg": "BMA_VERSION",
        "manifest": "braiins-manager-agent/umbrel-app.yml",
        "compose": "braiins-manager-agent/docker-compose.yml",
        "changelog": "CHANGELOG-braiins-manager-agent.md",
        "image": "braiins-manager-agent",
        # strict semver core; suffix = pre-release of the next patch, sorts below
        "version_re": r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?",
        "suffix_above_core": False,
        # webui.py footer reads APP_VERSION from compose; keep it in sync on pin
        "compose_app_version": True,
    },
    "braiins-bitcoin-data": {
        "display": "Bitcoin Data",
        # No upstream: the app IS this repo's webui.py, so there is no feed
        # and `check` must not be run for it — only release / pin.
        "feed": None,
        "feed_version_key": None,
        "dockerfile": "image-braiins-bitcoin-data/Dockerfile",
        "version_arg": None,
        "manifest": "braiins-bitcoin-data/umbrel-app.yml",
        "compose": "braiins-bitcoin-data/docker-compose.yml",
        "changelog": "CHANGELOG-braiins-bitcoin-data.md",
        "image": "braiins-bitcoin-data",
        # plain semver; a -N suffix would be meaningless with no upstream
        "version_re": r"(\d+)\.(\d+)\.(\d+)",
        "suffix_above_core": False,
        "compose_app_version": True,
    },
    "toolbox": {
        "display": "Braiins Toolbox",
        "feed": "https://downloads.braiins.com/braiins-toolbox/index.json",
        "feed_version_key": "toolbox_version",
        "dockerfile": "image-braiins-toolbox/Dockerfile",
        "version_arg": "TOOLBOX_VERSION",
        "manifest": "braiins-toolbox/umbrel-app.yml",
        "compose": "braiins-toolbox/docker-compose.yml",
        "changelog": "CHANGELOG-braiins-toolbox.md",
        "image": "braiins-toolbox",
        # CalVer core, patch optional; suffix = revision of this version, sorts above
        "version_re": r"(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?",
        "suffix_above_core": True,
        "compose_app_version": False,
    },
}


def out(key, value):
    dest = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}"
    if dest:
        with open(dest, "a") as f:
            f.write(line + "\n")
    print(line)


def parse_version(app, v):
    """Order versions with an optional wrapper suffix. See module docstring
    for why suffix ordering differs between the two apps."""
    m = re.fullmatch(app["version_re"], v)
    if not m:
        sys.exit(f"unparseable version: {v!r}")
    major, minor, patch, suffix = m.groups()
    core = (int(major), int(minor), int(patch or 0))
    n = int(suffix) if suffix and suffix.isdigit() else 0
    if app["suffix_above_core"]:
        pre = (1, n) if suffix is not None else (0, 0)
    else:
        pre = (0, n) if suffix is not None else (1, 0)
    return core + pre


def latest_release(app):
    # the CDN rejects urllib's default User-Agent
    req = urllib.request.Request(app["feed"], headers={"User-Agent": "braiins-umbrel-update-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        feed = json.load(r)
    key = app["feed_version_key"]
    return max(feed["releases"], key=lambda rel: parse_version(app, rel["metadata"][key]))


def current_version(app):
    m = re.search(r'^version: "([^"]+)"', (ROOT / app["manifest"]).read_text(), re.M)
    return m.group(1)


def prepend_changelog(app, version, desc):
    """Insert a `## [version] - date` section right below the marker, unless
    one already exists for this version. Body is the feed description with
    Umbrel's `&nbsp;` spacer lines dropped and blank runs collapsed, so it
    reads as plain Markdown."""
    changelog = ROOT / app["changelog"]
    marker = "<!-- new entries are inserted directly below this line -->"
    if not changelog.exists():
        return
    text = changelog.read_text()
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.M):
        return  # already recorded (e.g. re-run of the same bump)

    body_lines = [ln for ln in desc.splitlines() if ln.strip().lower() != "&nbsp;"]
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    today = datetime.date.today().isoformat()
    entry = f"\n\n## [{version}] - {today}\n\n{body}"

    if marker in text:
        text = text.replace(marker, marker + entry, 1)
    else:  # marker gone: fall back to inserting before the newest entry
        text = re.sub(r"(?=^## \[)", entry.lstrip("\n") + "\n\n", text, count=1, flags=re.M)
    changelog.write_text(text)


def write_manifest_release(app, version, desc):
    """Set the manifest `version` and `releaseNotes` block, and prepend a
    matching changelog entry. Shared by `check` (feed-driven) and `release`
    (wrapper-only)."""
    manifest = ROOT / app["manifest"]
    desc = desc.replace("\r", "").strip() or f"{app['display']} {version}."
    t = manifest.read_text()
    t = re.sub(r'^version: ".*"', f'version: "{version}"', t, count=1, flags=re.M)
    block = "\n".join(("  " + line).rstrip() for line in desc.splitlines())
    t = re.sub(r"releaseNotes: .*?\n\ndeveloper:", f"releaseNotes: |-\n{block}\n\ndeveloper:", t, count=1, flags=re.S)
    manifest.write_text(t)
    prepend_changelog(app, version, desc)


def check(app):
    rel = latest_release(app)
    meta = rel["metadata"]
    new = meta[app["feed_version_key"]]
    cur = current_version(app)
    if parse_version(app, new) <= parse_version(app, cur):
        out("changed", "false")
        out("version", cur)
        return

    assets = meta["assets"]
    missing = [a for a in ("linux_x86_64", "linux_aarch64") if not assets.get(a)]
    if missing:
        sys.exit(f"release {new} is missing required assets: {missing}")

    dockerfile = ROOT / app["dockerfile"]
    t = dockerfile.read_text()
    t = re.sub(rf"ARG {app['version_arg']}=.*", f"ARG {app['version_arg']}={new}", t)
    t = re.sub(r"ARG SHA256_AMD64=.*", f"ARG SHA256_AMD64={assets['linux_x86_64']['integrity']['checksum']}", t)
    t = re.sub(r"ARG SHA256_ARM64=.*", f"ARG SHA256_ARM64={assets['linux_aarch64']['integrity']['checksum']}", t)
    dockerfile.write_text(t)

    write_manifest_release(app, new, meta.get("description", ""))

    out("changed", "true")
    out("version", new)


def release(app, version, notes_file):
    # Accept the app's core version format with an optional wrapper suffix
    # (agent "4.11.1-1" next-patch pre-release, toolbox "26.06-1" revision —
    # see module docstring for the ordering rationale).
    if not re.fullmatch(app["version_re"], version):
        sys.exit(f"not a valid version: {version}")
    if version == current_version(app):
        sys.exit(f"version {version} is unchanged; existing installs get no "
                 "Update badge. Pass a higher version, or run a digest-only "
                 "re-pin instead (empty version input).")
    notes = pathlib.Path(notes_file).read_text() if notes_file else ""
    write_manifest_release(app, version, notes)
    out("version", version)


def pin(app, version, digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit(f"not a digest: {digest}")
    compose = ROOT / app["compose"]
    t = compose.read_text()
    t, n = re.subn(
        r"image: ghcr\.io/\S+",
        f"image: ghcr.io/{REGISTRY_OWNER}/{app['image']}:{version}@{digest}",
        t,
    )
    if n != 1:
        sys.exit(f"expected exactly one image line in compose, found {n}")
    if app["compose_app_version"]:
        # keep the footer version (webui.py reads APP_VERSION) in sync with the tag
        t, n = re.subn(r'APP_VERSION: ".*"', f'APP_VERSION: "{version}"', t)
        if n != 1:
            sys.exit(f"expected exactly one APP_VERSION line in compose, found {n}")
    compose.write_text(t)
    print(f"pinned {version}@{digest}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) >= 2 else ""
    args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    app_name = args.get("--app", "agent")
    if app_name not in APPS:
        sys.exit(f"unknown app {app_name!r}; expected one of {sorted(APPS)}")
    app = APPS[app_name]
    if cmd == "check":
        check(app)
    elif cmd == "release":
        release(app, args["--version"], args.get("--notes-file"))
    elif cmd == "pin":
        pin(app, args["--version"], args["--digest"])
    elif cmd == "current-version":
        print(current_version(app))
    else:
        sys.exit(__doc__)
