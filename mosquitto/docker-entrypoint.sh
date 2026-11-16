#!/bin/sh
# Orchid Mosquitto entrypoint: render config, install secrets privately, exec broker.
set -eu

SRC=/run/orchid-secrets
RT=/mosquitto/runtime
TEMPLATE=/orchid/conf/mosquitto.conf.tmpl
BROKER_HOST="${ORCHID_BROKER_HOST:?ORCHID_BROKER_HOST is required}"
SITE_ID="${ORCHID_SITE_ID:-}"

umask 077
rm -rf "$RT"/* 2>/dev/null || true
mkdir -p "$RT/xdg" /mosquitto/data

for f in ca.crt server.crt server.key dynsec.json health.pw; do
  [ -s "$SRC/$f" ] || { echo "missing secret $SRC/$f (run scripts/bootstrap_secrets.py)" >&2; exit 64; }
done

cp "$SRC/ca.crt" "$SRC/server.crt" "$SRC/server.key" "$RT/"
# Dynsec config is rendered from code (config-as-code); runtime changes are not persisted.
cp "$SRC/dynsec.json" /mosquitto/data/dynamic-security.json

BRIDGE_PW=""
if [ -n "$SITE_ID" ]; then
  echo "$SITE_ID" | grep -Eq '^site[0-9]{2}$' || { echo "invalid ORCHID_SITE_ID" >&2; exit 64; }
  [ -s "$SRC/bridge.pw" ] || { echo "missing bridge password" >&2; exit 64; }
  BRIDGE_PW=$(tr -d '\r\n' < "$SRC/bridge.pw")
  echo "$BRIDGE_PW" | grep -Eq '^[0-9a-f]{32,128}$' || { echo "bridge password must be hex" >&2; exit 64; }
fi

sed -e "s/@SITE_ID@/${SITE_ID}/g" -e "s/@BRIDGE_PASSWORD@/${BRIDGE_PW}/g" "$TEMPLATE" > "$RT/mosquitto.conf"

# mosquitto_sub options file for the container healthcheck (TLS + auth, read-only principal).
HEALTH_PW=$(tr -d '\r\n' < "$SRC/health.pw")
cat > "$RT/xdg/mosquitto_sub" <<EOF
-h $BROKER_HOST
-p 8883
--cafile $RT/ca.crt
-u health
-P $HEALTH_PW
-V mqttv5
-i health
EOF
unset BRIDGE_PW HEALTH_PW

chown -R mosquitto:mosquitto "$RT" /mosquitto/data
chmod 700 "$RT" "$RT/xdg"
chmod 600 "$RT"/* "$RT/xdg"/* /mosquitto/data/dynamic-security.json

exec /usr/sbin/mosquitto -c "$RT/mosquitto.conf"
