#!/usr/bin/env python3
"""Enforce per-module coverage floors from the project's own coverage data.

Why this exists
===============

``pytest --cov --cov-fail-under=N`` checks one number: the total. A bot whose
total sits at 70% passes while the module that decides what the bot *answers*
sits at 26%. The aggregate is diluted by well-covered infrastructure
(``service.py``, ``core/*``), so the gate goes green over the surface a customer
actually touches.

That is not hypothetical here. Measured on 2026-10-20, before this gate:

    botkit-store     total 75%   src/store/handlers.py     31%
    botkit-support   total 70%   src/support/handlers.py   26%
    botkit-leadgen   total 73%   src/leadgen/handlers.py   27%
    botkit-delivery  total 70%   src/delivery/handlers.py  40%

Three of them graze the 70% line exactly. The total was never the truth.

What it checks
==============

``scripts/coverage-floors.json`` lists per-module minimums. This script reads
``coverage.json`` (produced by ``--cov-report=json``) and fails if any listed
module is below its floor, or is missing from the report entirely.

It is a *floor per module*, not a second aggregate: raising ``service.py``
further must not buy permission to leave ``handlers.py`` untested.

Exit codes
==========

    0  every listed module meets its floor
    1  at least one module is below its floor, or is absent from the report
    2  the check could not verify anything (no coverage.json, unreadable
       floors file). This is NOT success: a gate that cannot reach its data
       must not report green.

Standard library only: it runs after pytest, but the policy should not depend
on the test environment to be readable.

The floors file has a negative test in ``check-coverage_test.py``; every rule
here has been seen to fail, because a rule that never failed is an assumption
wearing the costume of a gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_CANNOT_VERIFY = 0, 1, 2

# argv[0] + repo root, plus the two file names have defaults.
DEFAULT_FLOORS = "scripts/coverage-floors.json"
DEFAULT_REPORT = "coverage.json"


def load_json(path: Path) -> dict:
    """Read a JSON file or exit 2 — a gate without data is not a pass."""
    if not path.is_file():
        print(f"cannot verify: {path} not found", file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_VERIFY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot verify: {path}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_VERIFY) from exc
    if not isinstance(data, dict):
        print(f"cannot verify: {path}: top level is not an object", file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_VERIFY)
    return data


def normalise(path: str) -> str:
    """Coverage keys paths with the OS separator; the floors file uses '/'."""
    return path.replace("\\", "/")


def check(report: dict, floors: dict) -> list[str]:
    """Return every violation, not just the first: fix in one pass."""
    modules = floors.get("modules", {})
    if not isinstance(modules, dict) or not modules:
        return ["floors file lists no modules: nothing was checked"]

    files = report.get("files", {})
    if not isinstance(files, dict):
        return ["coverage report has no 'files' section"]

    by_module: dict[str, dict] = {normalise(k): v for k, v in files.items()}
    problems: list[str] = []
    for rel, floor in sorted(modules.items()):
        entry = by_module.get(normalise(rel))
        if entry is None:
            problems.append(f"{rel}: absent from coverage report (floor {floor}%)")
            problems.append(f"{rel}: module not imported, or renamed")
            continue
        got = entry.get("summary", {}).get("percent_covered")
        if not isinstance(got, (int, float)):
            problems.append(f"{rel}: no percent_covered in report")
            continue
        if got + 1e-9 < floor:
            problems.append(f"{rel}: {got:.0f}% < floor {floor}%")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    floors_path = Path(argv[2]) if len(argv) > 2 else root / DEFAULT_FLOORS
    report_path = Path(argv[3]) if len(argv) > 3 else root / DEFAULT_REPORT

    floors = load_json(floors_path)
    report = load_json(report_path)

    problems = check(report, floors)
    if problems:
        for p in problems:
            print(f"[coverage-floor] {p}")
        print(f"coverage floors violated: {len(problems)}", file=sys.stderr)
        return EXIT_FAIL

    listed = ", ".join(f"{k} ≥ {v}%" for k, v in sorted(floors.get("modules", {}).items()))
    print(f"coverage floors satisfied: {listed}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
