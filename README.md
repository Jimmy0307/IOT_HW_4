# Secure Orchid IoT Control Platform

**Assignment #4 - MQTT 實習與設計** · Architecture V2.2 · local Docker lab

## What this is

A six-greenhouse orchid monitoring and control system built on **MQTT 5** with a classroom scope
but production-style engineering discipline:

* every site runs its own Mosquitto broker, controller, field-device simulator and relay
  outputs, and **keeps controlling locally when the central side or the WAN is down**;
* sites bridge to a central broker over **verified TLS** with per-site identities and explicit
  topic allowlists (never `#`);
* sensor data, commands and logs are all **untrusted input** until they pass their own trust gate;
* a central historian keeps telemetry, commands and an **HMAC-chained audit log**;
* a React dashboard (HTTPS, viewer/operator roles) shows six sites with freshness-aware colors.

Teacher's requirement: target 20 C / 60 %RH; temperature high -> turn on both fans; humidity
high -> turn on both dehumidifiers. Everything beyond that is an engineering extension
(see `docs/ASSIGNMENT4_REPORT.md`).

## Architecture

```
Browser --HTTPS 127.0.0.1:8443--> reverse-proxy --(DMZ)--> frontend, central-api
central-api / historian --MQTT5+TLS--> central-broker            (Central OT network)
central-broker <==bridge01_net..bridge06_net==> site01..06-broker (one bridge network per site)
siteNN-broker <--MQTT5+TLS--> siteNN-controller, siteNN-sim       (one isolated LAN per site)
```
Details: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`docs/MQTT_CONTRACT.md`](docs/MQTT_CONTRACT.md) ·
[`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md) · [`docs/RUNBOOK.md`](docs/RUNBOOK.md) ·
[`docs/TEST_MATRIX.md`](docs/TEST_MATRIX.md) · [`docs/FINAL_ACCEPTANCE_REPORT.md`](docs/FINAL_ACCEPTANCE_REPORT.md) ·
[`docs/TEACHER_REQUIREMENTS_TRACEABILITY.md`](docs/TEACHER_REQUIREMENTS_TRACEABILITY.md) · [`docs/NETWORK_SECURITY_REPORT_GUIDE.md`](docs/NETWORK_SECURITY_REPORT_GUIDE.md) · [`docs/TEAMMATE_DEMO_GUIDE.md`](docs/TEAMMATE_DEMO_GUIDE.md) ·
[`evidence/report/README.md`](evidence/report/README.md)

## Requirements

| Tool | Tested version |
|---|---|
| Docker Engine / Desktop | 29.6.1 (Compose v5.2.0) |
| Python (host, for scripts/tests) | 3.14.2 (>= 3.12) with `pip install -r requirements-dev.txt` |
| Node.js / npm (frontend build, browser QA) | 22.22 / 10.9 |
| Container images | eclipse-mosquitto 2.1.2-alpine, python 3.12.8-slim, nginx-unprivileged 1.27-alpine, node 22.12-alpine |

No host `openssl` is needed (certificates are generated with Python `cryptography`).

### Verified ARM64 classroom/demo host

The stack was also deployed successfully on a Seeed Studio / NVIDIA Tegra `aarch64` host:
Ubuntu 20.04.6 LTS, Linux 5.10.192-tegra, Docker Engine 24.0.5. The application runtime remains
containerized (Python 3.12.8 / Mosquitto 2.1.2 / Node 22 / nginx-unprivileged), so the host's
Python 3.8.10 is not the application runtime. See `docs/TEAMMATE_DEMO_GUIDE.md` for the Linux
bind-mount ACL note and the exact demo procedure.

## Quick start

```bash
pip install -r requirements-dev.txt
python scripts/bootstrap_secrets.py        # 1. bootstrap secrets (CA, certs, credentials, keys)
docker compose up -d --build               # 2. start (23 services)
docker compose ps                          #    wait until all are "healthy"
```

### Bootstrap secrets
`scripts/bootstrap_secrets.py` is idempotent and **never prints a secret**. It writes everything
into `./secrets/` (gitignored): lab CA and server certificates, one password file per MQTT
principal, Mosquitto Dynamic Security configs, JWT signing key, audit HMAC key, dashboard users.
`--rotate` regenerates all of it. `.env.example` lists only non-secret settings.

### Open the dashboard
<https://localhost:8443> - usernames `viewer` / `operator`; passwords are in
`secrets/web_credentials.local.txt`. Import `secrets/pki/ca.crt` as a trusted CA for a clean
padlock (or accept the warning for this local lab origin).

### Run verification
```bash
python scripts/verify_all.py --fresh       # or: make verify-fresh
```
Runs config validation, 192 unit tests, frontend lint/tests/build, compose build, health
wait, 31 stack tests covering the acceptance gates (integration, security, failure injection), Playwright browser QA, audit
chain verification and a secret scan, then writes `evidence/generated/gate_summary.json` and
`docs/TEST_MATRIX.md`. Unit tests alone: `python -m pytest tests/unit -q`.

### Stop / cleanup
```bash
docker compose --profile test down         # stop, keep historian data
docker compose --profile test down -v      # stop and delete all volumes
```

## Repository layout

```
config/platform.yaml          canonical thresholds (all lab assumptions flagged)
mosquitto/                    broker image, entrypoint, central + site config templates
services/orchid/core          config, topics, bounded JSON, validation, sanitize, audit chain, ACL model
services/orchid/site_service  engine (pure logic), controller runner, simulator, actuators, arbitration
services/orchid/historian     ingest pipeline, SQLite store, severity policy, rate limiter
services/orchid/central_api   FastAPI app, auth, read models, MQTT publisher
services/frontend             React + Vite dashboard
services/reverse-proxy        nginx TLS edge
scripts/                      bootstrap, certs, compose renderer, verify_all, audit verifier, secret scan, UI QA
tests/                        unit / integration / security / failure (+ ui Playwright driver)
evidence/generated/           per-gate evidence, UI screenshots, verify logs
docs/                         contract, security model, runbook, test matrix, reports
```

## Known limitations

* Lab CA is not in the OS trust store by default.
* Field devices and actuators are simulated; thresholds are lab assumptions.
* Central attribution stops at the bridge identity (no end-to-end message signing).
* Mosquitto 2.1.2 enforces but does not log ACL denials at info level.
* Single-node SQLite; the audit chain is tamper-evident, not tamper-proof.

Full list: `docs/SECURITY_MODEL.md` section 10 and `docs/FINAL_ACCEPTANCE_REPORT.md`.