"""Static checks of the image Dockerfiles.

    python3 -m unittest discover -s tests
"""
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILES = sorted(REPO.glob("image-braiins-*/Dockerfile"))


class Dockerfiles(unittest.TestCase):
    def test_found(self):
        self.assertGreaterEqual(len(DOCKERFILES), 3)

    def test_platform_args_have_no_default(self):
        # `ARG TARGETARCH=amd64` overrides the value BuildKit sets per
        # platform, so the arm64 image got the amd64 binary (agent and
        # Toolbox until Nov 2026).
        for path in DOCKERFILES:
            for line in path.read_text().splitlines():
                with self.subTest(dockerfile=path.parent.name, line=line):
                    self.assertIsNone(re.match(r"\s*ARG\s+(TARGET|BUILD)(ARCH|OS|PLATFORM|VARIANT)\s*=", line))

    def test_runs_as_uid_1000_or_compose_sets_it(self):
        # compose sets user: "1000:1000" for every app; keep that true
        for compose in REPO.glob("braiins-*/docker-compose.yml"):
            with self.subTest(app=compose.parent.name):
                self.assertIn('user: "1000:1000"', compose.read_text())


class EveryAppIsWired(unittest.TestCase):
    """A new app needs the same wiring as the others; missing one piece
    means it is silently not built, linted or updated."""

    def test_ci_dependabot_and_bump_know_every_app(self):
        ci = (REPO / ".github/workflows/ci.yml").read_text()
        dependabot = (REPO / ".github/dependabot.yml").read_text()
        bump = (REPO / ".github/scripts/bump.py").read_text()
        for manifest in REPO.glob("braiins-*/umbrel-app.yml"):
            app = manifest.parent.name
            with self.subTest(app=app):
                self.assertTrue((REPO / f"image-{app}/smoke.sh").exists())
                self.assertRegex(ci, rf"app: \[[^\]]*\b{app}\b")
                self.assertRegex(ci, rf"set -- [^\n]*\b{app}\b")
                self.assertIn(f"/image-{app}\n", dependabot)
                self.assertIn(f'"manifest": "{app}/umbrel-app.yml"', bump)


if __name__ == "__main__":
    unittest.main()
