#!/usr/bin/env python3
"""Tests for check-contract-sync.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VALIDATOR = HERE / "check-contract-sync.py"
CONTRACT = HERE / "repo-contract.json"


def run(root: Path, contract_path: Path, env: dict | None = None) -> tuple[int, str]:
    e = os.environ.copy()
    if env:
        e.update(env)
    p = subprocess.run(
        [sys.executable, str(VALIDATOR), str(root), str(contract_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=e,
    )
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    if not VALIDATOR.is_file() or not CONTRACT.is_file():
        print("cannot verify: validator or contract missing", file=sys.stderr)
        return 2

    # Control
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        code, out = run(root, CONTRACT)
        if code != 0:
            print("FAIL control: expected exit 0")
            print(out)
            return 1
        print("ok   control: contract sync passes")

    # Case 1: modified local copy -> exit 1
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = Path(td) / "repo-contract.json"
        d = json.loads(CONTRACT.read_text())
        d["version"] = "9.9.9"
        p.write_text(json.dumps(d, indent=2) + "\n")
        code, out = run(root, p)
        if code != 1:
            print(f"FAIL modified copy: expected exit 1, got {code}")
            print(out)
            return 1
        print("ok   modified copy -> exit 1")

    # Case 2: canonical unavailable -> exit 2 (not green)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # copy contract
        import shutil
        p = Path(td) / "repo-contract.json"
        shutil.copy(CONTRACT, p)
        code, out = run(root, p, env={"BOTKIT_FARM_QUALITYFORGE_CONTRACT": "/nonexistent/repo-contract.json"})
        if code != 2:
            print(f"FAIL missing canonical: expected exit 2, got {code}")
            print(out)
            return 1
        print("ok   missing canonical -> exit 2")

    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
