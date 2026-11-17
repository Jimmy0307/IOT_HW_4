#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

wait_healthy() {
  local deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    local n
    n="$(docker compose ps --format json 2>/dev/null | python3 -c '
import json,sys
rows=[json.loads(x) for x in sys.stdin if x.strip()]
core=[r for r in rows if r.get("Service")!="lab-probe"]
print(sum(1 for r in core if (r.get("Health") or r.get("State"))=="healthy"))
' 2>/dev/null || echo 0)"
    [[ "$n" == "23" ]] && return 0
    sleep 3
  done
  return 1
}

printf '\n=== WAIT FOR 23 HEALTHY SERVICES ===\n'
if wait_healthy; then
  printf 'health_gate: PASS (23/23 healthy)\n'
else
  printf 'health_gate: NOT_READY after 120s\n'
fi

printf '\n=== HOST / RUNTIME ===\n'
uname -a
printf 'architecture: '; uname -m
printf 'docker: '; docker version --format '{{.Client.Version}}' 2>/dev/null || docker --version
printf 'compose: '; docker compose version --short 2>/dev/null || true

printf '\n=== SERVICE HEALTH ===\n'
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

printf '\n=== ORCHID DOCKER NETWORKS ===\n'
docker network ls --format '{{.Name}}' | grep '^orchid_' | sort
printf 'network_count: '
docker network ls --format '{{.Name}}' | grep -c '^orchid_' || true

printf '\n=== HOST PORT BINDINGS ===\n'
printf 'reverse-proxy: '
docker inspect orchid-reverse-proxy --format '{{json .HostConfig.PortBindings}}'
printf 'central-broker: '
docker inspect orchid-central-broker --format '{{json .HostConfig.PortBindings}}'
printf 'site01-broker: '
docker inspect orchid-site01-broker --format '{{json .HostConfig.PortBindings}}'

printf '\n=== NETWORK MEMBERSHIP (SAMPLE) ===\n'
printf 'reverse-proxy: '
docker inspect orchid-reverse-proxy --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
printf '\ncentral-api: '
docker inspect orchid-central-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
printf '\ncentral-broker: '
docker inspect orchid-central-broker --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
printf '\nsite01-broker: '
docker inspect orchid-site01-broker --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
printf '\nsite01-controller: '
docker inspect orchid-site01-controller --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
printf '\n'

printf '\n=== DEFAULT ROUTE ===\n'
ip route show default

printf '\n=== API HEALTH ===\n'
ok=0
for _ in $(seq 1 20); do
  if out="$(curl -ksS --max-time 3 https://localhost:8443/api/health 2>/dev/null)"; then
    printf '%s\n' "$out"
    ok=1
    break
  fi
  sleep 2
done
[[ "$ok" == "1" ]] || printf 'API_HEALTH_NOT_READY\n'
