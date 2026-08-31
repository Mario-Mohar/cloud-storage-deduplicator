#!/usr/bin/env python3
"""Reduce a JUnit XML report to one line: passed, failed, skipped.

Every test runner in these repositories can emit JUnit XML -- pytest, vitest,
cargo-nextest, dotnet test -- so the pipeline reads one format instead of
parsing each runner's own output. Missing or unreadable input is not an error
here: the check that produced it has already reported its own failure, and this
only decorates the summary.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")

    total = failures = errors = skipped = 0
    for suite in suites:
        total += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))

    bad = failures + errors
    return total - bad - skipped, bad, skipped


def main():
    if len(sys.argv) < 2:
        print("no report")
        return 0

    path = Path(sys.argv[1])
    if not path.is_file():
        print("no report")
        return 0

    try:
        passed, failed, skipped = counts(path)
    except (ET.ParseError, ValueError) as exc:
        print("unreadable report (%s)" % exc)
        return 0

    parts = ["%d passed" % passed]
    if failed:
        parts.append("%d failed" % failed)
    if skipped:
        parts.append("%d skipped" % skipped)
    print(", ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
