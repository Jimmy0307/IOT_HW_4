#!/usr/bin/env python3
"""Verify the historian's HMAC audit chain (tamper EVIDENCE, not tamper prevention).

    # verify a DB file (e.g. a snapshot) with the lab key
    python scripts/verify_audit_chain.py --db evidence/generated/raw/audit-snapshot.db

    # take a consistent snapshot from the running historian, then verify it
    python scripts/verify_audit_chain.py --snapshot evidence/generated/raw/audit-snapshot.db

Exit code 0 = PASS, 1 = FAIL (chain broken), 2 = usage/IO error.
The key is read from secrets/app/audit_hmac_key (never printed). The output
includes the row count and a head prefix so an external record can also detect
truncation of the newest rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from orchid.core.audit_chain import verify_chain  # noqa: E402

SNAPSHOT_CODE = (
    "import sqlite3; s=sqlite3.connect('file:/data/historian.db?mode=ro', uri=True); "
    "d=sqlite3.connect('/data/audit-snapshot.db'); s.backup(d); d.close(); print('ok')"
)


def snapshot(dest: Path) -> None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    subprocess.run(["docker", "exec", "orchid-historian", "python", "-c", SNAPSHOT_CODE], check=True,
                   capture_output=True, env=env)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "cp", "orchid-historian:/data/audit-snapshot.db", str(dest)], check=True,
                   capture_output=True, env=env)
    subprocess.run(["docker", "exec", "orchid-historian", "rm", "-f", "/data/audit-snapshot.db"], check=False,
                   capture_output=True, env=env)


def verify(db_path: Path, key: bytes) -> dict:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    def rows():
        for r in con.execute("SELECT * FROM audit_log ORDER BY seq"):
            d = dict(r)
            d["details"] = json.loads(d["details"])
            yield d

    res = verify_chain(key, rows())
    con.close()
    return {"result": "PASS" if res.ok else "FAIL", "rows_checked": res.checked,
            "first_bad_index": res.first_bad_index, "reason": res.reason, "head_prefix": res.head[:16],
            "db": db_path.name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, help="SQLite DB to verify")
    parser.add_argument("--snapshot", type=Path, help="take a snapshot from orchid-historian to this path, then verify")
    parser.add_argument("--key-file", type=Path, default=ROOT / "secrets" / "app" / "audit_hmac_key")
    args = parser.parse_args(argv)
    if not args.db and not args.snapshot:
        parser.error("--db or --snapshot is required")
    try:
        key = args.key_file.read_text(encoding="utf-8").strip().encode()
        db_path = args.db
        if args.snapshot:
            snapshot(args.snapshot)
            db_path = args.snapshot
        out = verify(db_path, key)
    except (OSError, sqlite3.Error, subprocess.CalledProcessError, ValueError) as exc:
        print(json.dumps({"result": "ERROR", "error": type(exc).__name__, "detail": str(exc)[:200]}))
        return 2
    print(json.dumps(out))
    return 0 if out["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
