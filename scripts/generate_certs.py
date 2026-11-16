#!/usr/bin/env python3
"""Generate the local lab PKI (CA + server certificates with correct SANs).

Output: secrets/pki/ (gitignored). Private keys are never printed.
Idempotent: existing material is kept unless --force is given.

    python scripts/generate_certs.py [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[1]
PKI = ROOT / "secrets" / "pki"
SITES = [f"site{i:02d}" for i in range(1, 7)]
CA_DAYS = 365
LEAF_DAYS = 180


def server_identities() -> dict[str, dict[str, list[str]]]:
    ids: dict[str, dict[str, list[str]]] = {"central-broker": {"dns": ["central-broker"], "ip": []}}
    for site in SITES:
        ids[f"{site}-broker"] = {"dns": [f"{site}-broker"], "ip": []}
    ids["reverse-proxy"] = {"dns": ["localhost", "reverse-proxy"], "ip": ["127.0.0.1"]}
    return ids


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _name(cn: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Orchid IoT Lab (LOCAL ONLY)"),
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
    ])


def make_ca(force: bool) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate, bool]:
    key_path, crt_path = PKI / "ca.key", PKI / "ca.crt"
    if key_path.exists() and crt_path.exists() and not force:
        key = serialization.load_pem_private_key(key_path.read_bytes(), None)
        return key, x509.load_pem_x509_certificate(crt_path.read_bytes()), False  # type: ignore[return-value]
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Orchid Lab Root CA"))
        .issuer_name(_name("Orchid Lab Root CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private(key_path, _key_pem(key))
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert, True


def make_leaf(name: str, sans: dict[str, list[str]], ca_key, ca_cert, force: bool) -> bool:
    key_path, crt_path = PKI / f"{name}.key", PKI / f"{name}.crt"
    if key_path.exists() and crt_path.exists() and not force:
        return False
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    alt = [x509.DNSName(d) for d in sans["dns"]] + [x509.IPAddress(ipaddress.ip_address(i)) for i in sans["ip"]]
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(key_path, _key_pem(key))
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="regenerate CA and all certificates")
    args = parser.parse_args(argv)
    PKI.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert, ca_new = make_ca(args.force)
    reissue = args.force or ca_new  # leaves must chain to the current CA
    created = [n for n, s in server_identities().items() if make_leaf(n, s, ca_key, ca_cert, reissue)]
    ca_pem = (PKI / "ca.crt").read_bytes()
    for name in server_identities():  # leaf + CA, so clients can match the presented chain
        (PKI / f"{name}-fullchain.crt").write_bytes((PKI / f"{name}.crt").read_bytes() + ca_pem)
    print(f"[pki] CA: {ca_cert.subject.rfc4514_string()} (valid until {ca_cert.not_valid_after_utc.date()})")
    print(f"[pki] server certificates created: {len(created)}; kept: {len(server_identities()) - len(created)}")
    print(f"[pki] output directory (gitignored): {PKI.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
