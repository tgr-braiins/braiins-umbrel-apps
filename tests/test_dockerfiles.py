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


if __name__ == "__main__":
    unittest.main()
