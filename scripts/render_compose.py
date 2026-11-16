#!/usr/bin/env python3
"""Render docker-compose.yml from config/platform.yaml (deterministic).

Segmentation (all networks internal except wan_net):
  wan_net        reverse-proxy only (host port 127.0.0.1:8443 -> 8443)
  dmz_net        reverse-proxy, frontend, central-api
  central_ot_net central-api, central-broker, historian
  bridgeNN_net   central-broker <-> siteNN-broker   (one per site)
  siteNN_net     siteNN-broker, siteNN-controller, siteNN-sim

    python scripts/render_compose.py          # write docker-compose.yml
    python scripts/render_compose.py --check  # exit 1 if file is out of date
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
from orchid.core.config import load_config  # noqa: E402

OUT = ROOT / "docker-compose.yml"
S = "./secrets"
SEC = "/run/orchid-secrets"
PY_IMAGE = "orchid-python:local"
MQ_IMAGE = "orchid-mosquitto:local"
LOGGING = {"driver": "json-file", "options": {"max-size": "5m", "max-file": "3"}}
PY_HEALTH = None  # defined in services/Dockerfile


def ro(src: str, dst: str) -> str:
    return f"{src}:{dst}:ro"


def hardened(mem: str, pids: int = 128) -> dict:
    return {"restart": "unless-stopped", "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
            "mem_limit": mem, "pids_limit": pids, "logging": LOGGING}


def python_service(name: str, command: list[str], networks: list[str], volumes: list[str],
                   env: dict[str, str] | None = None, depends: dict | None = None, mem: str = "192m") -> dict:
    svc = {
        "image": PY_IMAGE,
        "container_name": f"orchid-{name}",
        "hostname": name,
        "command": command,
        "read_only": True,
        "tmpfs": ["/tmp:size=16m"],
        "environment": {"ORCHID_CONFIG": "/app/config/platform.yaml", **(env or {})},
        "volumes": [ro("./config", "/app/config"), *volumes],
        "networks": networks,
        **hardened(mem),
    }
    if depends:
        svc["depends_on"] = depends
    return svc


def broker_service(name: str, template: str, networks: list[str], secrets: list[str], env: dict[str, str],
                   volume: str) -> dict:
    return {
        "build": {"context": "./mosquitto"},
        "image": MQ_IMAGE,
        "container_name": f"orchid-{name}",
        "hostname": name,
        "environment": env,
        "volumes": [ro(template, "/orchid/conf/mosquitto.conf.tmpl"), *secrets, f"{volume}:/mosquitto/data"],
        "networks": networks,
        "tmpfs": ["/mosquitto/runtime:size=4m,uid=1883,gid=1883,mode=0700"],
        **{**hardened("128m"), "cap_drop": ["ALL"], "cap_add": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER"]},
    }


def render() -> dict:
    cfg = load_config()
    sites = sorted(cfg.sites)
    networks: dict[str, dict] = {"wan_net": {"name": "orchid_wan_net"},
                                 "dmz_net": {"name": "orchid_dmz_net", "internal": True},
                                 "central_ot_net": {"name": "orchid_central_ot_net", "internal": True}}
    volumes: dict[str, dict] = {"historian_data": {}, "central_broker_data": {}}
    healthy = {"condition": "service_healthy"}
    services: dict[str, dict] = {}

    services["reverse-proxy"] = {
        "image": "nginxinc/nginx-unprivileged:1.27-alpine",
        "container_name": "orchid-reverse-proxy",
        "hostname": "reverse-proxy",
        "ports": ["${ORCHID_HTTPS_BIND:-127.0.0.1}:${ORCHID_HTTPS_PORT:-8443}:8443"],
        "volumes": [ro("./services/reverse-proxy/nginx.conf", "/etc/nginx/conf.d/default.conf"),
                    ro("./services/reverse-proxy/orchid_proxy_headers.conf", "/etc/nginx/orchid_proxy_headers.conf"),
                    ro(f"{S}/pki/reverse-proxy.crt", f"{SEC}/tls.crt"), ro(f"{S}/pki/reverse-proxy.key", f"{SEC}/tls.key"),
                    ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt")],
        "networks": ["wan_net", "dmz_net"],
        "read_only": True,
        "tmpfs": ["/tmp:size=16m"],
        "depends_on": {"frontend": healthy, "central-api": healthy},
        "healthcheck": {"test": ["CMD", "curl", "-fsS", "--cacert", f"{SEC}/ca.crt", "https://localhost:8443/api/health"],
                        "interval": "10s", "timeout": "5s", "retries": 6, "start_period": "5s"},
        **hardened("64m"),
    }
    services["frontend"] = {
        "build": {"context": "./services/frontend"},
        "image": "orchid-frontend:local",
        "container_name": "orchid-frontend",
        "hostname": "frontend",
        "networks": ["dmz_net"],
        "read_only": True,
        "tmpfs": ["/tmp:size=16m"],
        "healthcheck": {"test": ["CMD", "curl", "-fsS", "http://localhost:8080/"], "interval": "10s",
                        "timeout": "5s", "retries": 6},
        **hardened("64m"),
    }
    services["central-api"] = python_service(
        "central-api",
        ["uvicorn", "--factory", "orchid.central_api.app:build_production_app", "--host", "0.0.0.0",
         "--port", "8000", "--no-server-header", "--no-access-log"],
        ["dmz_net", "central_ot_net"],
        ["historian_data:/data", ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"), ro(f"{S}/mqtt/central-api.pw", f"{SEC}/mqtt.pw"),
         ro(f"{S}/app/jwt_secret", f"{SEC}/jwt_secret"), ro(f"{S}/app/web_users.json", f"{SEC}/web_users.json")],
        depends={"central-broker": healthy})
    services["central-api"]["build"] = {"context": "./services"}
    services["central-api"]["healthcheck"] = {
        "test": ["CMD", "python", "-c",
                 "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"],
        "interval": "10s", "timeout": "5s", "retries": 6, "start_period": "10s"}
    services["historian"] = python_service(
        "historian", ["python", "-m", "orchid.historian.main"], ["central_ot_net"],
        ["historian_data:/data", ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"), ro(f"{S}/mqtt/historian.pw", f"{SEC}/mqtt.pw"),
         ro(f"{S}/app/audit_hmac_key", f"{SEC}/audit_hmac_key")],
        depends={"central-broker": healthy}, mem="256m")

    bridge_nets = []
    for site in sites:
        bnet, snet = f"bridge{site[-2:]}_net", f"{site}_net"
        bridge_nets.append(bnet)
        networks[bnet] = {"name": f"orchid_{bnet}", "internal": True}
        networks[snet] = {"name": f"orchid_{snet}", "internal": True}
        volumes[f"{site}_broker_data"] = {}
        volumes[f"{site}_controller_data"] = {}
        services[f"{site}-broker"] = broker_service(
            f"{site}-broker", "./mosquitto/site-template/mosquitto.conf.tmpl", [bnet, snet],
            [ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"), ro(f"{S}/pki/{site}-broker.crt", f"{SEC}/server.crt"),
             ro(f"{S}/pki/{site}-broker.key", f"{SEC}/server.key"), ro(f"{S}/dynsec/{site}.json", f"{SEC}/dynsec.json"),
             ro(f"{S}/mqtt/health.pw", f"{SEC}/health.pw"), ro(f"{S}/mqtt/bridge-{site}.pw", f"{SEC}/bridge.pw")],
            {"ORCHID_BROKER_HOST": f"{site}-broker", "ORCHID_SITE_ID": site}, f"{site}_broker_data")
        services[f"{site}-controller"] = python_service(
            f"{site}-controller", ["python", "-m", "orchid.site_service.controller"], [snet],
            [f"{site}_controller_data:/data", ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"),
             ro(f"{S}/mqtt/{site}-controller.pw", f"{SEC}/mqtt.pw")],
            env={"ORCHID_SITE_ID": site}, depends={f"{site}-broker": healthy})
        sim_secrets = [ro(f"{S}/mqtt/{site}-{dev}.pw", f"{SEC}/{dev}.pw") for dev in cfg.sites[site].sensors]
        services[f"{site}-sim"] = python_service(
            f"{site}-sim", ["python", "-m", "orchid.site_service.simulator"], [snet],
            [ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"), *sim_secrets],
            env={"ORCHID_SITE_ID": site}, depends={f"{site}-broker": healthy}, mem="128m")

    services["central-broker"] = broker_service(
        "central-broker", "./mosquitto/central/mosquitto.conf.tmpl", ["central_ot_net", *bridge_nets],
        [ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"), ro(f"{S}/pki/central-broker.crt", f"{SEC}/server.crt"),
         ro(f"{S}/pki/central-broker.key", f"{SEC}/server.key"), ro(f"{S}/dynsec/central.json", f"{SEC}/dynsec.json"),
         ro(f"{S}/mqtt/health.pw", f"{SEC}/health.pw")],
        {"ORCHID_BROKER_HOST": "central-broker"}, "central_broker_data")

    # Test-only probe (profile "test"): sits on the central OT net and two site LANs to run
    # authN/authZ/TLS/ACL probes. It is never started by a plain `docker compose up`.
    services["lab-probe"] = python_service(
        "lab-probe", ["sleep", "infinity"], ["central_ot_net", "site01_net", "site02_net"],
        [ro(f"{S}/pki/ca.crt", f"{SEC}/ca.crt"), ro(f"{S}/mqtt", f"{SEC}/mqtt")], mem="128m")
    services["lab-probe"]["profiles"] = ["test"]
    services["lab-probe"]["restart"] = "no"
    services["lab-probe"]["healthcheck"] = {"disable": True}

    ordered = {k: services[k] for k in ["reverse-proxy", "frontend", "central-api", "historian", "central-broker"]}
    ordered.update({k: v for k, v in services.items() if k not in ordered})
    return {"name": "orchid", "services": ordered, "networks": networks, "volumes": volumes}


HEADER = """# =====================================================================
# GENERATED by scripts/render_compose.py from config/platform.yaml.
# Do not edit by hand: edit the generator and re-run it.
# Secrets are bind-mounted read-only from ./secrets (gitignored).
# Only reverse-proxy publishes a host port (127.0.0.1:8443). No MQTT port
# (1883/8883) is published to the host.
# =====================================================================
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = HEADER + yaml.safe_dump(render(), sort_keys=False, width=120)
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print("docker-compose.yml is out of date; run scripts/render_compose.py")
            return 1
        print("docker-compose.yml is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.name}: {len(render()['services'])} services, {len(render()['networks'])} networks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
