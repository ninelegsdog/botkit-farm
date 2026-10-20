#!/usr/bin/env python3
"""Negative tests for check-coverage.py — the gate must be seen to fail.

A floors gate whose rules have never gone red is a green light with no bulb.
Each test below breaks one rule and requires exit 1; the "cannot verify" cases
require exit 2, because a gate that cannot reach its data must not pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "check-coverage.py"


def _report(modules: dict[str, float]) -> dict:
    return {"files": {k: {"summary": {"percent_covered": v}} for k, v in modules.items()}}


def _run(tmp: Path, report: dict, floors: dict) -> subprocess.CompletedProcess:
    (tmp / "coverage.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp / "floors.json").write_text(json.dumps(floors), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(tmp), str(tmp / "floors.json"), str(tmp / "coverage.json")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_passing_module_is_green() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        r = _run(
            tmp,
            _report({"src/store/handlers.py": 85.0}),
            {"modules": {"src/store/handlers.py": 80}},
        )
        assert r.returncode == 0, r.stderr + r.stdout


def test_below_floor_is_red() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        r = _run(
            tmp,
            _report({"src/store/handlers.py": 31.0}),
            {"modules": {"src/store/handlers.py": 60}},
        )
        assert r.returncode == 1, r.stderr
        assert "31%" in r.stdout and "< floor 60%" in r.stdout


def test_missing_module_is_red_not_skipped() -> None:
    """Absent module means renamed or never imported — silence would hide it."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        r = _run(tmp, _report({"src/other.py": 99.0}), {"modules": {"src/store/handlers.py": 60}})
        assert r.returncode == 1, r.stderr
        assert "absent from coverage report" in r.stdout


def test_no_report_is_cannot_verify() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "floors.json").write_text('{"modules": {"x.py": 1}}', encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                str(GATE),
                str(tmp),
                str(tmp / "floors.json"),
                str(tmp / "coverage.json"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 2, r.stderr


def test_empty_floors_is_cannot_verify_not_pass() -> None:
    """A floors file listing nothing would 'pass' everything. That is not a pass."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        r = _run(tmp, _report({"src/store/handlers.py": 10.0}), {"modules": {}})
        assert r.returncode == 1, r.stderr
        assert "lists no modules" in r.stdout


def test_exact_floor_passes() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        r = _run(
            tmp,
            _report({"src/store/handlers.py": 60.0}),
            {"modules": {"src/store/handlers.py": 60}},
        )
        assert r.returncode == 0, r.stderr


def test_backslash_paths_normalised() -> None:
    """Windows coverage writes backslashes; the floors file uses '/'."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        r = _run(
            tmp,
            _report({"src\\store\\handlers.py": 90.0}),
            {"modules": {"src/store/handlers.py": 80}},
        )
        assert r.returncode == 0, r.stderr + r.stdout


if __name__ == "__main__":
    import traceback

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception:
                failures += 1
                print(f"FAIL {name}: {traceback.format_exc()}")
    print(f"\n{'FAILED' if failures else 'passed'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)
