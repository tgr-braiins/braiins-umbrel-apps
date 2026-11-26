#!/usr/bin/env python3
"""Judge the official Umbrel linter's JSON output for this community store.

    node .tools/lint-apps.mjs <apps...> --check-images --format json > lint.json
    python3 umbrel_lint.py lint.json

Prints every issue. Fails on any error except the ones a community store has
by design (see README "Path to the official App Store"). Warnings never fail.
"""
import json
import sys

# Errors that are expected until the official-store submission.
ACCEPTED_ERRORS = {
    "manifest.submission",  # set to the submission PR URL at submission time
}
# Errors accepted for one file only, as (rule, file).
ACCEPTED_IN_FILE = {
    # Braiins Proxy runs on the host network, so the app proxy points at the
    # Docker gateway address instead of a service (DECISIONS, "Host network").
    ("app_proxy.host", "braiins-proxy/docker-compose.yml"),
}


def main(path):
    with open(path) as f:
        report = json.load(f)
    failed = 0
    for issue in report.get("issues", []):
        severity, rule = issue.get("severity"), issue.get("rule")
        where = issue.get("file") or "?"
        if issue.get("line"):
            where += ":%s" % issue["line"]
        accepted = severity == "error" and (
            rule in ACCEPTED_ERRORS or (rule, issue.get("file")) in ACCEPTED_IN_FILE)
        tag = "accepted" if accepted else severity
        print("%-8s %-28s %s  %s" % (tag, rule, where, issue.get("message", "")))
        if severity == "error" and not accepted:
            failed += 1
    print("%d app(s) linted, %d unexpected error(s)" % (len(report.get("apps", [])), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
