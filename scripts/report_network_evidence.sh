#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

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

printf '\n=== API HEALTH ===\n'
curl -ksS https://localhost:8443/api/health || true
printf '\n'
