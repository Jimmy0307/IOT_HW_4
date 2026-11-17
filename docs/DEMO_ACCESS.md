# Demo Access Guide — Secure Orchid IoT Control Platform

This file is intentionally **public-safe**. It explains how to obtain and use the local demo credentials without committing active passwords to GitHub.

## Roles

| Username | Role | Permission |
|---|---|---|
| `viewer` | read-only | View global overview, site detail, telemetry, alarms, system/security status |
| `operator` | control | All viewer functions plus manual control commands and operator workflows |

## Where the password is

Passwords are generated per installation by `scripts/bootstrap_secrets.py` and stored only in the gitignored local file:

```bash
cat secrets/web_credentials.local.txt
```

The file has this format:

```text
viewer   <generated-password>   role=viewer
operator <generated-password>   role=operator
```

Dashboard:

```text
https://localhost:8443
```

For a remote Linux/Jetson demo host whose HTTPS listener is intentionally bound to `127.0.0.1`, use an SSH tunnel:

```bash
ssh -L 8443:127.0.0.1:8443 <user>@<demo-host>
```

Then browse to `https://localhost:8443` on the client.

## First start

```bash
python scripts/bootstrap_secrets.py
docker compose up -d --build
docker compose ps
```

All 23 services should become healthy.

## Linux / ARM64 / Jetson note

The Python application containers run as UID `10001` and nginx-unprivileged uses UID `101`. Linux bind mounts preserve host permissions. If local secrets are mode `0600`, grant read-only ACL access to those container UIDs instead of making secrets world-readable:

```bash
docker run --rm \
  -v "$PWD/secrets:/secrets" \
  alpine:3.20 \
  sh -lc '
    apk add --no-cache acl >/dev/null &&
    setfacl -R -m u:10001:rX,u:101:rX /secrets
  '

docker compose restart
```

Verify:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
curl -k https://localhost:8443/api/health
```

Expected API health:

```json
{"status":"ok","mqtt_connected":true}
```

## Rotate demo credentials

```bash
python scripts/bootstrap_secrets.py --rotate
docker compose up -d --force-recreate
```

Never commit `secrets/` or `web_credentials.local.txt`.