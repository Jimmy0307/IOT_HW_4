#!/usr/bin/env python3
"""One-shot secret bootstrap for the Orchid lab (idempotent).

Generates into ./secrets/ (gitignored, never committed):
  pki/                 local CA + server certificates (via generate_certs.py)
  mqtt/<user>.pw       one random password file per MQTT principal
  dynsec/<broker>.json Mosquitto Dynamic Security config (PBKDF2-SHA512 hashes)
  app/jwt_secret       JWT signing key (central-api only)
  app/audit_hmac_key   audit HMAC chain key (historian + verifier only)
  app/web_users.json   dashboard users (viewer/operator), PBKDF2 hashes
  web_credentials.local.txt   human-readable dashboard logins (local file only)

Secrets are NEVER printed. Use --rotate to regenerate all passwords/keys.

    python scripts/bootstrap_secrets.py [--rotate]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from orchid.core import acl_model  # noqa: E402
from orchid.core.config import load_config  # noqa: E402

SECRETS = ROOT / "secrets"
MQTT_PBKDF2_ITERATIONS = 20000
WEB_PBKDF2_ITERATIONS = 210000
WEB_USERS = {"viewer": "viewer", "operator": "operator"}
PROBE_SITES = ("site01", "site02")


def _write_private(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _apply_ephemeral_bind_mount_compatibility() -> None:
    """Make bind-mounted secrets readable by non-root containers on an ephemeral POSIX CI host.

    Normal/local behavior remains mode 0600. This opt-in exists because Linux bind mounts
    preserve host UID/mode, while the Python runtime intentionally runs as UID 10001.
    Use only on disposable single-tenant runners; secret values are still gitignored and
    must never be uploaded as artifacts.
    """
    if os.environ.get("ORCHID_EPHEMERAL_BIND_SECRET_MODE") != "1" or os.name != "posix":
        return
    for path in SECRETS.rglob("*"):
        try:
            if path.is_dir():
                os.chmod(path, 0o755)
            elif path.is_file():
                os.chmod(path, 0o644)
        except OSError as exc:
            raise RuntimeError(f"failed to prepare ephemeral bind-mounted secret: {path}") from exc
    print("[secrets] ephemeral POSIX bind-mount compatibility enabled (permissions only; values not printed)")


def _ensure(path: Path, factory, rotate: bool) -> tuple[str, bool]:
    if path.exists() and not rotate:
        return path.read_text(encoding="utf-8").strip(), False
    value = factory()
    _write_private(path, value + "\n")
    return value, True


def mosquitto_encoded_password(password: str) -> str:
    """Mosquitto `$7$` format: PBKDF2-HMAC-SHA512, base64 salt and hash."""
    salt = secrets.token_bytes(64)  # Mosquitto expects a 64-byte salt for $7$
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, MQTT_PBKDF2_ITERATIONS, dklen=64)
    return f"$7${MQTT_PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def web_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, WEB_PBKDF2_ITERATIONS)
    return (f"pbkdf2_sha256${WEB_PBKDF2_ITERATIONS}$"
            f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="store_true", help="regenerate all passwords, keys and certificates")
    parser.add_argument("--no-test-principals", action="store_true",
                        help="omit probe-* principals used only by the test-profile lab probe")
    args = parser.parse_args(argv)
    cfg = load_config()
    sites = sorted(cfg.sites)

    cert_args = [sys.executable, str(ROOT / "scripts" / "generate_certs.py")] + (["--force"] if args.rotate else [])
    subprocess.run(cert_args, check=True)

    with_probe = not args.no_test_principals
    specs = {s: acl_model.SiteSpec(cfg.sites[s].sensors, cfg.sites[s].actuator_groups) for s in sites}
    brokers: dict[str, list[acl_model.Principal]] = {
        "central": acl_model.central_principals(specs, test_principals=with_probe)}
    for site in sites:  # the lab probe sits on the site01/site02 LANs only
        brokers[site] = acl_model.site_principals(site, cfg.sites[site].sensors,
                                                  test_principals=with_probe and site in PROBE_SITES)

    created = 0
    passwords: dict[str, str] = {}
    for principals in brokers.values():
        for p in principals:
            if p.username in passwords:
                continue  # 'health' is shared by name but each broker hashes it independently
            value, new = _ensure(SECRETS / "mqtt" / f"{p.username}.pw", lambda: secrets.token_hex(24), args.rotate)
            passwords[p.username] = value
            created += int(new)

    for broker, principals in brokers.items():
        encoded = {p.username: mosquitto_encoded_password(passwords[p.username]) for p in principals}
        doc = acl_model.dynsec_document(principals, encoded)
        _write_private(SECRETS / "dynsec" / f"{broker}.json", json.dumps(doc, indent=2))

    _, jwt_new = _ensure(SECRETS / "app" / "jwt_secret", lambda: secrets.token_hex(32), args.rotate)
    _, hmac_new = _ensure(SECRETS / "app" / "audit_hmac_key", lambda: secrets.token_hex(32), args.rotate)

    users_path = SECRETS / "app" / "web_users.json"
    creds_path = SECRETS / "web_credentials.local.txt"
    if args.rotate or not users_path.exists() or not creds_path.exists():
        plain = {u: secrets.token_urlsafe(12) for u in WEB_USERS}
        users = {u: {"role": role, "password_hash": web_password_hash(plain[u])} for u, role in WEB_USERS.items()}
        _write_private(users_path, json.dumps(users, indent=2))
        lines = ["# Orchid lab dashboard logins - LOCAL ONLY, gitignored. Do not share or commit."]
        lines += [f"{u}\t{plain[u]}\trole={WEB_USERS[u]}" for u in WEB_USERS]
        _write_private(creds_path, "\n".join(lines) + "\n")
        web_new = True
    else:
        web_new = False

    manifest = {
        "brokers": {b: [p.username for p in ps] for b, ps in brokers.items()},
        "mqtt_principals": len(passwords),
        "mqtt_password_hash": f"PBKDF2-SHA512 x{MQTT_PBKDF2_ITERATIONS} ($7$)",
        "test_principals": with_probe,
        "web_users": list(WEB_USERS),
        "note": "Manifest lists names only. No secret values.",
    }
    (SECRETS / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    print(f"[secrets] MQTT principals: {len(passwords)} (new password files: {created})")
    print(f"[secrets] dynsec configs: {', '.join(sorted(brokers))}")
    print(f"[secrets] jwt_secret: {'created' if jwt_new else 'kept'}; audit_hmac_key: "
          f"{'created' if hmac_new else 'kept'}; web users: {'created' if web_new else 'kept'}")
    print(f"[secrets] dashboard logins written to {creds_path.relative_to(ROOT)} (not printed)")
    _apply_ephemeral_bind_mount_compatibility()
    return 0


if __name__ == "__main__":
    sys.exit(main())