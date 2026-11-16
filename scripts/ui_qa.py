#!/usr/bin/env python3
"""Run the Playwright browser QA against the running stack.

Computes the lab CA SPKI hash (public data) so Chromium trusts ONLY this lab CA
for the session; certificate verification is not disabled. Exit code mirrors
tests/ui/ui_qa.mjs (0 pass, 1 check failed, 2 runtime blocked).
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "tests" / "ui"


def ca_spki() -> str:
    cert = x509.load_pem_x509_certificate((ROOT / "secrets" / "pki" / "ca.crt").read_bytes())
    der = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


def main() -> int:
    npm = shutil.which("npm")
    if not npm:
        print("BROWSER_QA_BLOCKED: npm not found")
        return 2
    if not (UI / "node_modules" / "playwright").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=UI, check=True)
    env = {**os.environ, "ORCHID_CA_SPKI": ca_spki()}
    return subprocess.run([shutil.which("node") or "node", "ui_qa.mjs"], cwd=UI, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
