"""Tests for .github/scripts/bump.py. Standard library only:

    python3 -m unittest discover -s tests

Each test runs against a temporary copy of the app files, never the repo.
"""
import contextlib
import importlib.util
import io
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bump", REPO / ".github/scripts/bump.py")
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)

AGENT, TOOLBOX, DATA = bump.APPS["agent"], bump.APPS["toolbox"], bump.APPS["braiins-bitcoin-data"]
SUM_A, SUM_B = "a" * 64, "b" * 64


def feed_entry(app, version, sums=(SUM_A, SUM_B), description="Notes.", date="2026-11-24"):
    assets = {}
    if sums:
        assets = {"linux_x86_64": {"integrity": {"checksum": sums[0]}},
                  "linux_aarch64": {"integrity": {"checksum": sums[1]}}}
    return {"metadata": {app["feed_version_key"]: version, "assets": assets,
                         "description": description, "release_date": date}}


class RepoCopy(unittest.TestCase):
    """Point bump.ROOT at a scratch copy of every file the script edits."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        for app in bump.APPS.values():
            for key in ("dockerfile", "manifest", "compose", "changelog"):
                dest = self.tmp / app[key]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(REPO / app[key], dest)
        patcher = mock.patch.object(bump, "ROOT", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp)
        self.addCleanup(lambda: bump.os.environ.pop("GITHUB_OUTPUT", None))

    def read(self, app, key):
        return (self.tmp / app[key]).read_text()

    def quiet(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*args)

    def assert_exits(self, fn, *args):
        with self.assertRaises(SystemExit) as cm:
            self.quiet(fn, *args)
        self.assertNotIn(cm.exception.code, (0, None))

    def release_notes(self, app):
        """The releaseNotes value lines, up to the next top-level key."""
        lines = self.read(app, "manifest").splitlines()
        i = next(i for i, ln in enumerate(lines) if ln.startswith("releaseNotes:"))
        body = []
        for ln in lines[i + 1:]:
            if ln and not ln.startswith(" "):
                break
            body.append(ln)
        return lines[i], body, lines[i + 1 + len(body)]


class VersionOrder(unittest.TestCase):
    def test_agent_wrapper_sorts_between_versions(self):
        v = lambda s: bump.parse_version(AGENT, s)
        self.assertLess(v("4.15.0"), v("4.15.1-1"))
        self.assertLess(v("4.15.1-1"), v("4.15.1-2"))
        self.assertLess(v("4.15.1-2"), v("4.15.1"))
        self.assertLess(v("4.15.0-1"), v("4.15.0"))

    def test_toolbox_revision_sorts_between_versions(self):
        v = lambda s: bump.parse_version(TOOLBOX, s)
        self.assertLess(v("26.06"), v("26.06-1"))
        self.assertLess(v("26.06-1"), v("26.06-2"))
        self.assertLess(v("26.06-2"), v("26.06.1"))
        self.assertLess(v("26.06.1"), v("26.07"))

    def test_bitcoin_data_is_plain_semver(self):
        self.assertLess(bump.parse_version(DATA, "1.13.1"), bump.parse_version(DATA, "1.14.0"))
        with self.assertRaises(ValueError):
            bump.parse_version(DATA, "1.13.1-1")

    def test_garbage_is_rejected(self):
        for s in ("", "4.15", "v4.15.0", "4.15.0\nRUN x", "26.09 "):
            with self.assertRaises(ValueError, msg=s):
                bump.parse_version(AGENT, s)


class Release(RepoCopy):
    def notes(self, text):
        f = self.tmp / "notes.md"
        f.write_text(text)
        return str(f)

    def test_bitcoin_data_release_notes_reach_the_manifest(self):
        # 1.13.1 shipped with empty notes: `developer:` directly followed
        # `releaseNotes: ""` and the old pattern needed a blank line.
        self.quiet(bump.release, DATA, "9.0.0", self.notes("Fixed the first blocks of an epoch."))
        key, body, nxt = self.release_notes(DATA)
        self.assertEqual(key, "releaseNotes: |-")
        self.assertIn("  Fixed the first blocks of an epoch.", body)
        self.assertEqual(nxt, "developer: Braiins")
        self.assertIn('version: "9.0.0"', self.read(DATA, "manifest"))
        self.assertIn("## [9.0.0]", self.read(DATA, "changelog"))

    def test_existing_block_is_replaced_not_appended(self):
        self.quiet(bump.release, AGENT, "4.15.1-1", self.notes("First."))
        self.quiet(bump.release, AGENT, "4.15.1-2", self.notes("Second."))
        key, body, nxt = self.release_notes(AGENT)
        self.assertEqual([ln for ln in body if ln.strip()], ["  Second."])
        self.assertEqual(nxt, "developer: Braiins")

    def test_backslashes_are_kept_literally(self):
        text = "Path C:\\data and a literal \\n stay as typed."
        self.quiet(bump.release, TOOLBOX, "26.09-2", self.notes(text))
        _, body, _ = self.release_notes(TOOLBOX)
        self.assertIn("  " + text, body)
        self.assertIn(text, self.read(TOOLBOX, "changelog"))

    def test_lower_or_equal_versions_are_refused(self):
        cur = bump.current_version(AGENT)
        self.assert_exits(bump.release, AGENT, cur, self.notes("x"))
        self.assert_exits(bump.release, AGENT, cur + "-1", self.notes("x"))  # sorts below cur
        self.assert_exits(bump.release, AGENT, "4.0.0", self.notes("x"))
        self.assert_exits(bump.release, AGENT, "not-a-version", self.notes("x"))
        self.assertEqual(bump.current_version(AGENT), cur)


class Check(RepoCopy):
    def run_check(self, app, entries):
        with mock.patch.object(bump, "fetch_feed", return_value={"releases": entries}):
            self.quiet(bump.check, app)

    def test_newer_feed_version_is_written(self):
        self.run_check(TOOLBOX, [feed_entry(TOOLBOX, "26.09"), feed_entry(TOOLBOX, "99.01")])
        dockerfile = self.read(TOOLBOX, "dockerfile")
        self.assertIn("ARG TOOLBOX_VERSION=99.01", dockerfile)
        self.assertIn(f"ARG SHA256_AMD64={SUM_A}", dockerfile)
        self.assertIn(f"ARG SHA256_ARM64={SUM_B}", dockerfile)
        self.assertEqual(bump.current_version(TOOLBOX), "99.01")

    def test_skipped_versions_get_changelog_entries(self):
        self.run_check(AGENT, [feed_entry(AGENT, "98.0.0", description="Older."),
                               feed_entry(AGENT, "99.0.0", description="Newer.")])
        log = self.read(AGENT, "changelog")
        self.assertLess(log.index("## [99.0.0]"), log.index("## [98.0.0]"))
        self.assertEqual(bump.current_version(AGENT), "99.0.0")

    def test_malformed_checksum_is_refused(self):
        before = self.read(AGENT, "dockerfile")
        for bad in ("aa\nRUN echo pwned > /pwn", "A" * 64, "a" * 63, None):
            with self.subTest(bad=bad):
                self.assertRaises(SystemExit, self.run_check, AGENT,
                                  [feed_entry(AGENT, "99.0.0", sums=(bad, SUM_B))])
                self.assertEqual(self.read(AGENT, "dockerfile"), before)

    def test_missing_assets_exit_cleanly(self):
        self.assertRaises(SystemExit, self.run_check, AGENT, [feed_entry(AGENT, "99.0.0", sums=None)])

    def test_pre_releases_and_odd_entries_are_skipped(self):
        cur = bump.current_version(TOOLBOX)
        self.run_check(TOOLBOX, [feed_entry(TOOLBOX, "99.01-rc1"), feed_entry(TOOLBOX, "junk"),
                                 {"metadata": {}}, {}])
        self.assertEqual(bump.current_version(TOOLBOX), cur)

    def test_nothing_newer_changes_nothing(self):
        before = self.read(AGENT, "manifest")
        self.run_check(AGENT, [feed_entry(AGENT, "4.0.0")])
        self.assertEqual(self.read(AGENT, "manifest"), before)


class Pin(RepoCopy):
    def test_pin_sets_image_and_app_version(self):
        digest = "sha256:" + "c" * 64
        self.quiet(bump.pin, AGENT, "4.15.1-1", digest)
        compose = self.read(AGENT, "compose")
        self.assertIn(f"braiins-manager-agent:4.15.1-1@{digest}", compose)
        self.assertIn('APP_VERSION: "4.15.1-1"', compose)

    def test_pin_refuses_bad_input(self):
        self.assert_exits(bump.pin, AGENT, "4.15.1-1", "sha256:xyz")
        self.assert_exits(bump.pin, AGENT, "4.15.1\nimage: evil", "sha256:" + "c" * 64)


if __name__ == "__main__":
    unittest.main()
