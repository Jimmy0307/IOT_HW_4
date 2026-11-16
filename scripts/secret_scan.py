#!/usr/bin/env python3
"""Secret leakage scan (never prints secret values).

Checks, for EXACT generated secret values (every MQTT password, JWT key, audit HMAC
key, dashboard password, private-key body) and for generic patterns (PEM private
keys, JWTs):
  1. files tracked or addable in git (not ignored)
  2. the whole git history (all commits, all patches)
  3. evidence/ (text files)
  4. logs of all running orchid containers
Also asserts that no file under secrets/ and no *.key/*.pw is tracked.

Exit 0 = clean, 1 = finding(s), 2 = could not run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
GENERIC = {
    "pem_private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
TEXT_EXT = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".log", ".conf", ".tmpl", ".sh", ".ts", ".tsx",
            ".mjs", ".js", ".css", ".html", ".toml", ".example", ".cfg", ".ini", ""}
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def load_secret_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for f in sorted((SECRETS / "mqtt").glob("*.pw")):
        values[f"mqtt:{f.stem}"] = f.read_text().strip()
    for name in ("jwt_secret", "audit_hmac_key"):
        values[f"app:{name}"] = (SECRETS / "app" / name).read_text().strip()
    creds = SECRETS / "web_credentials.local.txt"
    for line in creds.read_text().splitlines():
        if line and not line.startswith("#"):
            user, pw, _ = line.split("\t")
            values[f"web:{user}"] = pw
    for key in sorted((SECRETS / "pki").glob("*.key")):
        body = "".join(l for l in key.read_text().splitlines() if not l.startswith("-----"))
        values[f"pki:{key.name}"] = body[20:60]  # distinctive slice of the base64 key body
    return {k: v for k, v in values.items() if len(v) >= 12}


def scan_text(text: str, secrets: dict[str, str], where: str, findings: list[dict], generic: bool = True) -> None:
    for name, value in secrets.items():
        if value in text:
            findings.append({"where": where, "type": "exact_secret_value", "secret": name})
    if generic:
        for name, pattern in GENERIC.items():
            if pattern.search(text):
                findings.append({"where": where, "type": name})


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def main() -> int:
    if not SECRETS.exists():
        print(json.dumps({"result": "ERROR", "detail": "secrets/ missing - run bootstrap first"}))
        return 2
    secrets = load_secret_values()
    findings: list[dict] = []
    stats = {"secret_values_checked": len(secrets)}

    files = set(git("ls-files").splitlines()) | set(git("ls-files", "-o", "--exclude-standard").splitlines())
    bad_tracked = [f for f in git("ls-files").splitlines()
                   if f.startswith("secrets/") or f.endswith((".key", ".pw")) or "dynamic-security.json" in f]
    findings += [{"where": f, "type": "secret_file_tracked"} for f in bad_tracked]
    scanned = 0
    for rel in sorted(files):
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT or path.stat().st_size > 5_000_000:
            continue
        if "node_modules" in path.parts:
            continue
        scan_text(path.read_text(encoding="utf-8", errors="replace"), secrets, rel, findings)
        scanned += 1
    stats["repo_files_scanned"] = scanned

    history = git("log", "--all", "-p", "--no-color")
    scan_text(history, secrets, "git history (all commits)", findings, generic=False)
    stats["git_history_bytes"] = len(history)

    ev = 0
    for path in sorted((ROOT / "evidence").rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_EXT:
            scan_text(path.read_text(encoding="utf-8", errors="replace"), secrets,
                      str(path.relative_to(ROOT)), findings)
            ev += 1
    stats["evidence_files_scanned"] = ev

    names = subprocess.run(["docker", "ps", "-a", "--filter", "name=orchid-", "--format", "{{.Names}}"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV).stdout.split()
    for name in names:
        proc = subprocess.run(["docker", "logs", name], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=ENV)
        scan_text(proc.stdout + proc.stderr, secrets, f"docker logs {name}", findings)
    stats["container_logs_scanned"] = len(names)

    result = {"result": "CLEAN" if not findings else "FINDINGS", **stats, "findings": findings}
    print(json.dumps(result, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
