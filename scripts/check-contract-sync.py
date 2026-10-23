#!/usr/bin/env python3
"""Check that the vendored repo-contract.json matches the canonical one in qualityforge."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_CANNOT_VERIFY = 0, 1, 2
ARITY = 3


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_contract_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"contract unreadable: {path}: {exc}") from exc


def find_canonical() -> Path | None:
    env = os.environ.get("BOTKIT_FARM_QUALITYFORGE_CONTRACT")
    if env is not None:
        p = Path(env)
        if p.is_file():
            return p.resolve()
        # If explicitly forced to bad path, return None (cannot verify)
        return None
    candidates = [
        Path("/home/algtro/code/qualityforge/scripts/repo-contract.json"),
        Path("/home/algtro/code/qualityforge/repo-contract.json"),
        Path("/tmp/opencode/qf-t5/scripts/repo-contract.json"),
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def main() -> int:
    if len(sys.argv) < ARITY:
        print(f"usage: {Path(sys.argv[0]).name} <repo-root> <contract.json>", file=sys.stderr)
        return EXIT_CANNOT_VERIFY
    root = Path(sys.argv[1]).resolve()
    contract_path = Path(sys.argv[2]).resolve()
    if not root.is_dir():
        print(f"repository root not found: {root}", file=sys.stderr)
        return EXIT_CANNOT_VERIFY

    contract = load_contract_json(contract_path)
    expected_sha = contract.get("canonical_sha256") or contract.get("canonical_sha") or contract.get("qualityforge_sha256")
    if not expected_sha or not isinstance(expected_sha, str):
        print("cannot verify: canonical_sha256 not pinned in contract", file=sys.stderr)
        return EXIT_CANNOT_VERIFY

    canonical = find_canonical()
    if canonical is None:
        print("cannot verify: canonical qualityforge contract not available", file=sys.stderr)
        return EXIT_CANNOT_VERIFY

    try:
        canonical_bytes = canonical.read_bytes()
        actual_sha = sha256_bytes(canonical_bytes)
    except Exception as exc:
        print(f"cannot verify: cannot read canonical: {exc}", file=sys.stderr)
        return EXIT_CANNOT_VERIFY

    if actual_sha != expected_sha:
        print(f"contract sync divergence: canonical sha {actual_sha} != pinned {expected_sha}", file=sys.stderr)
        print(f"  local: {contract_path}", file=sys.stderr)
        print(f"  canonical: {canonical}", file=sys.stderr)
        return EXIT_FAIL

    try:
        local_bytes = contract_path.read_bytes()
        local_sha = sha256_bytes(local_bytes)
    except Exception as exc:
        print(f"cannot verify: cannot read local contract: {exc}", file=sys.stderr)
        return EXIT_CANNOT_VERIFY

    if local_sha == actual_sha:
        print(f"contract sync OK: {contract_path.name} matches qualityforge canonical ({actual_sha[:12]})")
        return EXIT_OK

    # Compare ignoring pin fields
    try:
        local_json = json.loads(local_bytes)
        local_cmp = dict(local_json)
        for k in list(local_cmp.keys()):
            if k in ("canonical_sha256", "canonical_sha", "qualityforge_sha256", "pinned_canonical_sha"):
                local_cmp.pop(k, None)
        canonical_json = json.loads(canonical_bytes)
        canonical_cmp = dict(canonical_json)
        for k in list(canonical_cmp.keys()):
            if k in ("canonical_sha256", "canonical_sha", "qualityforge_sha256", "pinned_canonical_sha"):
                canonical_cmp.pop(k, None)
        if json.dumps(local_cmp, sort_keys=True, ensure_ascii=False) == json.dumps(canonical_cmp, sort_keys=True, ensure_ascii=False):
            print(f"contract sync OK: {contract_path.name} matches qualityforge canonical ({actual_sha[:12]})")
            return EXIT_OK
    except Exception:
        pass

    print(f"contract sync divergence: local {contract_path} differs from canonical {canonical}", file=sys.stderr)
    print(f"  local sha256: {local_sha}", file=sys.stderr)
    print(f"  canonical sha256: {actual_sha} (pinned: {expected_sha})", file=sys.stderr)
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
