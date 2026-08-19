#!/usr/bin/env python3
"""Write shields.io endpoint JSON for the test and coverage badges.

The README's badges are drawn by shields.io from these files, so every badge
in the row shares one renderer and one visual style. Locally-drawn SVGs never
quite matched, and their numbers were whatever CI last committed; these carry
data only, and the drawing is not ours to get wrong.

    python scripts/make_badges.py junit.xml coverage.xml

Writes .github/badges/tests.json and .github/badges/coverage.json.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    junit, coverage = Path(argv[1]), Path(argv[2])
    out = Path(".github/badges")
    out.mkdir(parents=True, exist_ok=True)

    suite = ET.parse(junit).getroot()
    if suite.tag == "testsuites":
        suite = suite[0]
    tests = int(suite.get("tests", 0))
    bad = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    tests_payload = {
        "schemaVersion": 1,
        "label": "tests",
        "message": f"{tests} passed" if bad == 0 else f"{bad} of {tests} failing",
        "color": "brightgreen" if bad == 0 else "red",
    }
    (out / "tests.json").write_text(json.dumps(tests_payload) + "\n", encoding="utf-8")

    rate = float(ET.parse(coverage).getroot().get("line-rate", 0)) * 100
    if rate >= 90:
        color = "brightgreen"
    elif rate >= 80:
        color = "green"
    elif rate >= 70:
        color = "yellow"
    else:
        color = "red"
    coverage_payload = {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{rate:.0f}%",
        "color": color,
    }
    (out / "coverage.json").write_text(json.dumps(coverage_payload) + "\n", encoding="utf-8")

    print(f"tests: {tests} ({bad} failing), coverage: {rate:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
