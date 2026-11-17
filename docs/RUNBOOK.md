# Runbook

All commands run from the repository root. On Windows use the Python commands directly
(`make` targets are thin wrappers). In Git Bash, prefix `docker exec` commands with
`MSYS_NO_PATHCONV=1` so paths are not rewritten.

## 1. First start

```bash
python scripts/bootstrap_secrets.py      # CA, certs, 27 MQTT credentials, dynsec, JWT + audit keys
docker compose up -d --build             # 23 services (lab-probe is test-profile only)
docker compose ps                        # all should become (healthy) within ~60 s
```
Dashboard: <https://localhost:8443> - logins are in `secrets/web_credentials.local.txt`
(local file, never printed). Import `secrets/pki/ca.crt` into your browser/OS trust store, or
accept the warning for this lab origin only.

## 2. Everyday operations

| Task | Command |
|---|---|
| Status of one site controller | `docker exec orchid-site01-controller python -m orchid.tools.faultctl GET /status` |
| Simulator status | `docker exec orchid-site01-sim python -m orchid.tools.faultctl GET /status` |
| Pin temperature (ramped) | `docker exec orchid-site02-sim python -m orchid.tools.faultctl POST /set '{"metric":"temperature","value":23}'` |
| Resume normal profile | `... POST /profile '{}'` |
| Inject sensor fault | `docker exec orchid-site05-sim python -m orchid.tools.faultctl POST /fault '{"device":"temp01","fault":"frozen"}'` (`null` clears) |
| Inject actuator fault | `docker exec orchid-site06-controller python -m orchid.tools.faultctl POST /fault/actuator '{"member":"fan02","fault":"STUCK_OFF"}'` |
| Inject local log failure | `... POST /fault/log '{"fail":true}'` ; clear all: `POST /fault/clear '{}'` |
| Verify audit chain | `python scripts/verify_audit_chain.py --snapshot evidence/generated/raw/audit-snapshot.db` |
| Secret scan | `python scripts/secret_scan.py` |
| Logs (structured JSON) | `docker logs orchid-site01-controller`, `docker logs orchid-historian` |

Sensor faults: `malformed_json invalid_unit null_value string_value nan_value infinity_value
stale_ts future_ts naive_ts duplicate_seq backward_seq spike frozen offline oversize deep_json
identity_spoof`. Actuator faults: `STUCK_OFF STUCK_ON NO_FEEDBACK`. The fault interface listens
on 127.0.0.1 inside the container only.

## 3. Failure drills

| Drill | Do | Expect |
|---|---|---|
| Central outage | `docker stop orchid-central-broker` | sites keep controlling; dashboard sites go UNKNOWN; restart -> queued data arrives (late) |
| WAN cut one site | `docker network disconnect orchid_bridge03_net orchid-site03-broker` | site03 local control continues; reconnect with `docker network connect --alias site03-broker orchid_bridge03_net orchid-site03-broker` |
| Historian down | `docker stop orchid-historian` | control unaffected; dashboard shows historian unavailable / stale; restart catches up |
| Controller crash | `docker kill -s KILL orchid-site04-controller` | LWT -> site04 OFFLINE, actuators STALE; `docker start` -> ONLINE |

## 4. Verification

```bash
python scripts/verify_all.py --fresh     # full acceptance run from empty volumes (~25 min)
python scripts/verify_all.py             # same, reusing volumes
python -m pytest tests/unit -q           # unit tests only (no Docker)
python scripts/ui_qa.py                  # browser QA only (stack must be up)
```
Results: `evidence/generated/gate_summary.json`, `docs/TEST_MATRIX.md`,
`evidence/generated/<gate>/evidence.log`, screenshots in `evidence/generated/ui/`.

## 5. Rotation and reset

```bash
python scripts/bootstrap_secrets.py --rotate   # new CA, certs, passwords, keys
docker compose up -d --force-recreate          # brokers re-install dynsec on start
```
Rotating `audit_hmac_key` starts a new chain only on an empty DB; for an existing DB keep the
key, or archive the DB (`docker compose down -v` deletes it).

## 6. Stop / cleanup

```bash
docker compose --profile test down        # stop, keep data
docker compose --profile test down -v     # stop and delete volumes
```

## 7. Linux / ARM64 / Seeed Studio deployment

The same stack has been verified on a Seeed Studio / NVIDIA Tegra ARM64 host.

Linux bind mounts preserve host UID/mode. The Python application containers run as UID `10001`
and nginx-unprivileged uses UID `101`. If the generated secrets remain mode `0600`, grant
those container UIDs read-only ACL access instead of making the files world-readable:

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
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
curl -k https://localhost:8443/api/health
```

Expected health response:

```json
{"status":"ok","mqtt_connected":true}
```

Do **not** solve this by making private keys/password files world-readable. The ACL keeps
host-side access restricted while allowing the non-root container UIDs to read the bind mounts.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Broker container exits with `missing secret` | run `python scripts/bootstrap_secrets.py` before `docker compose up` |
| A secret path became a directory | Docker created it because the file was missing at first start: delete it, bootstrap, restart |
| `curl` on Windows: "revocation status is unknown" | schannel cannot check revocation for the lab CA (no CRL); use `curl --ssl-no-revoke --cacert secrets/pki/ca.crt ...` (verification stays on) or Python |
| Unexpected 1883 listener in a custom config | in Mosquitto 2.1 listener-scoped options (e.g. `max_qos`) placed before any `listener` create an implicit 1883 listener - keep them under `listener 8883` |
| Dashboard all gray | historian or central broker down, or heartbeats older than 20 s - by design nothing stale is shown as current |
