# Teammate Demo & Environment Guide

This page is for teammates who need to run, demonstrate, or explain Assignment #4.

## 1. Canonical repository

- Repository: `Jimmy0307/IOT_HW_4`
- Branch: `feat/assignment4-orchid-mqtt`
- Acceptance: **34/34 PASS**
- Dashboard: `https://localhost:8443`

Read first:

- `docs/TEACHER_REQUIREMENTS_TRACEABILITY.md`
- `docs/MQTT_CONTRACT.md`
- `docs/SECURITY_MODEL.md`
- `docs/TEST_MATRIX.md`

## 2. Verified environments

### A. Live ARM64 demo host

Physical platform:

- Seeed Studio edge computer using NVIDIA Tegra ARM64;
- architecture: `aarch64`;
- exact board model was not recorded in the evidence, so this repository does not invent one.

Host OS:

- Ubuntu **20.04.6 LTS**;
- Linux kernel **5.10.192-tegra**;
- Docker Engine **24.0.5**;
- browser: Firefox;
- host Python: **3.8.10**.

Important: host Python 3.8 is **not** the application runtime.

### B. Container runtime

| Component | Runtime |
|---|---|
| Python services | `python:3.12.8-slim-bookworm` |
| MQTT brokers | `eclipse-mosquitto:2.1.2-alpine` |
| Frontend build | Node 22 Alpine |
| Frontend runtime | `nginxinc/nginx-unprivileged:1.27-alpine` |
| Protocol | MQTT 5 + TLS |
| Web edge | HTTPS |

### C. Service topology

Normal stack: **23 services**

- 7 Mosquitto brokers: 1 central + 6 site brokers;
- 6 site controllers;
- 6 site simulators;
- 1 central API;
- 1 historian;
- 1 frontend;
- 1 reverse proxy.

### D. Docker network topology

The architecture uses **15 Docker networks**:

- 1 WAN network;
- 1 DMZ network;
- 1 central OT network;
- 6 site LAN networks;
- 6 bridge networks.

Only the reverse proxy is host-published:

```text
127.0.0.1:8443 -> reverse-proxy:8443
```

MQTT ports are **not** published to the host. MQTT 8883 is used inside the isolated topology.

### E. CI environment

GitHub Actions is the canonical acceptance environment for the full gate suite.

Current final result:

- core gates: **34/34 PASS**;
- Browser QA: PASS;
- secret scan: CLEAN;
- audit-chain verification: PASS.

The CI environment and the ARM64 host together demonstrate portability across amd64 CI and aarch64 deployment.

## 3. Credentials

Public GitHub intentionally does not contain active passwords/private keys.

Generate local secrets:

```bash
python scripts/bootstrap_secrets.py
```

Read the local dashboard credentials:

```bash
cat secrets/web_credentials.local.txt
```

Roles:

- `viewer`: read-only;
- `operator`: can send control commands.

The credentials file and the whole `secrets/` directory are gitignored.

## 4. Start the demo

```bash
git clone --branch feat/assignment4-orchid-mqtt --single-branch \
  https://github.com/Jimmy0307/IOT_HW_4.git
cd IOT_HW_4

python scripts/bootstrap_secrets.py
docker compose up -d --build
docker compose ps
```

Expected: all 23 services become healthy.

Open:

```text
https://localhost:8443
```

## 5. ARM64 / Linux secret permission fix

On the verified Seed/Tegra host, host-generated secrets were mode 0600 while Python containers run as UID 10001 and nginx as UID 101.

Symptom:

```text
PermissionError: [Errno 13] Permission denied: /run/orchid-secrets/...
```

Safe fix:

```bash
docker run --rm \
  -v "$PWD/secrets:/secrets" \
  alpine:3.20 \
  sh -lc '
    apk add --no-cache acl >/dev/null &&
    setfacl -R -m u:10001:rX,u:101:rX /secrets
  '

docker compose restart
sleep 20
docker compose ps
```

This is preferred over making secrets world-readable.

## 6. Health check

```bash
curl -khttps://localhost:8443/api/health
```

Expected live result:

```json
{"status":"ok","mqtt_connected":true}
```

## 7. What to show the teacher

Recommended sequence:

1. six-site Overview;
2. Topic Design;
3. username/password + TLS;
4. temperature warning → both fans ON;
5. humidity warning → both dehumidifiers ON;
6. operator command → ACK;
7. STALE / OFFLINE / DEGRADED handling;
8. final 34/34 acceptance matrix.

## 8. Remote dashboard over SSH

When the demo host is reachable:

```bash
ssh -L 8443:127.0.0.1:8443 <user>@<demo-host>
```

Then open locally:

```text
https://localhost:8443
```

## 9. Stop

Keep data:

```bash
docker compose down
```

Delete persistent volumes only when intentional:

```bash
docker compose down -v
```

## 10. Common teacher questions

### Why Docker instead of `apt install mosquitto`?
The teacher slide gives a reference setup. Containerizing the broker still installs and runs Mosquitto, but makes seven broker instances reproducible and isolated.

### Where is publish/subscribe?

- sensors publish telemetry;
- site controllers subscribe telemetry;
- site controllers publish validated state/ACK/events;
- Historian subscribes central bridged data;
- Central API publishes group commands.

### Why group command topics?
The assignment explicitly asks both fans / both dehumidifiers to turn on together. A group command gives one command ID and one aggregate ACK instead of two unrelated commands.

### Why are thresholds not exactly 20 / 60?
20 °C / 60 %RH are the assignment targets. Hysteresis around those targets prevents rapid ON/OFF chatter.

### What happens if central MQTT fails?
Each site continues local automatic control. Central outage affects monitoring/remote control, not local safety control.
