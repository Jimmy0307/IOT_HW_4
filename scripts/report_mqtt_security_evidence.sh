#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

pretty() {
  python3 -m json.tool 2>/dev/null || cat
}

probe() {
  docker compose exec -T lab-probe python -m orchid.tools.probe "$@"
}

printf '\n============================================================\n'
printf ' ORCHID MQTT / TLS / ACL RUNTIME EVIDENCE\n'
printf '============================================================\n'

central_health="$(docker inspect -f '{{.State.Health.Status}}' orchid-central-broker 2>/dev/null || true)"
site01_health="$(docker inspect -f '{{.State.Health.Status}}' orchid-site01-broker 2>/dev/null || true)"
if [[ "$central_health" != "healthy" || "$site01_health" != "healthy" ]]; then
  printf 'ERROR: brokers are not healthy. Start the stack first:\n'
  printf '  docker compose up -d\n'
  printf '  sleep 20\n'
  printf '  docker compose ps\n'
  exit 1
fi

docker compose --profile test up -d lab-probe >/dev/null

printf '\n[1] MQTT 5 TLS — CENTRAL BROKER :8883\n'
probe tls --host central-broker | pretty

printf '\n[2] MQTT 5 TLS — SITE01 BROKER :8883\n'
probe tls --host site01-broker | pretty

printf '\n[3] PLAINTEXT MQTT :1883 MUST BE CLOSED\n'
probe tcp --host central-broker --port 1883 | pretty
probe tcp --host site01-broker --port 1883 | pretty

printf '\n[4] AUTHENTICATION — ANONYMOUS CONNECT MUST BE DENIED (0x87 / 135)\n'
probe connect --host central-broker --anonymous | pretty

printf '\n[5] AUTHENTICATION — WRONG PASSWORD MUST BE DENIED (0x87 / 135)\n'
probe connect --host central-broker --user historian --wrong-password | pretty

printf '\n[6] AUTHORIZATION — VALID SUBSCRIBE TO RETAINED HEARTBEATS\n'
probe sub \
  --host central-broker \
  --user probe-historian \
  --topic 'orchid/v1/+/gateway/heartbeat' \
  --listen 1 \
  --max-messages 6 | pretty

printf '\n[7] AUTHORIZATION — VALID PUBLISH ON SITE01 ALLOWED TOPIC\n'
probe pub \
  --host site01-broker \
  --user probe-site01-controller \
  --topic 'orchid/v1/site01/events' \
  --payload '{"event_id":"report-runtime-proof","event_type":"DIAGNOSTIC","details":{"purpose":"security-report"}}' | pretty

printf '\n[8] ACL DENY — SENSOR PRINCIPAL CANNOT PUBLISH CONTROL COMMAND\n'
probe pub \
  --host site01-broker \
  --user probe-site01-temp01 \
  --topic 'orchid/v1/site01/control/fan_group/command' \
  --payload '{}' | pretty

printf '\n[9] CROSS-SITE ACL DENY — SITE01 BRIDGE CANNOT PUBLISH SITE02 DATA\n'
probe pub \
  --host central-broker \
  --user probe-bridge-site01 \
  --topic 'orchid/v1/site02/alerts' \
  --payload '{}' | pretty

printf '\n[10] MQTT BRIDGE — CENTRAL RECEIVES SITE01 BRIDGE STATUS\n'
probe sub \
  --host central-broker \
  --user probe-historian \
  --topic 'orchid/v1/site01/gateway/bridge' \
  --listen 1 \
  --max-messages 3 | pretty

printf '\n[11] NETWORK SEGMENTATION — SITE01 LAN MUST NOT REACH CENTRAL OR SITE02 DIRECTLY\n'
docker compose exec -T site01-controller python - <<'PY'
import json, socket
for target in ("central-broker", "site02-broker"):
    try:
        s = socket.create_connection((target, 8883), timeout=3)
        s.close()
        result = "REACHABLE"
    except Exception as exc:
        result = "ISOLATED:" + type(exc).__name__
    print(json.dumps({"source":"site01-controller","target":f"{target}:8883","result":result}))
PY

printf '\n[12] BROKER / BRIDGE PROTOCOL CONFIG — NO WILDCARD # BRIDGE\n'
printf 'site01 bridge configuration (selected security lines):\n'
docker compose exec -T site01-broker sh -lc \
  "grep -E '^(bridge_protocol_version|bridge_cafile|bridge_insecure|topic orchid/v1/)' /mosquitto/runtime/mosquitto.conf 2>/dev/null || true"

printf '\n============================================================\n'
printf ' EXPECTED SECURITY SIGNALS\n'
printf ' - TLS verified=true, version TLSv1.2 or TLSv1.3\n'
printf ' - TCP/1883 open=false\n'
printf ' - anonymous/wrong-password reason_code=135 (Not authorized)\n'
printf ' - valid subscribe suback < 128 and retained heartbeat messages present\n'
printf ' - valid publish puback_reason=0\n'
printf ' - forbidden publishes denied=true, puback_reason=135\n'
printf ' - site01-controller cannot directly reach central/site02 broker\n'
printf ' - bridge protocol mqttv50, CA verification enabled, explicit topic allowlist\n'
printf '============================================================\n'
