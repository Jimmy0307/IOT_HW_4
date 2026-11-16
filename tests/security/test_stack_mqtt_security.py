"""Stack security gates: exposure, TLS, authN, authZ (ACL), segmentation, packet limits."""
from __future__ import annotations

import json
import socket

import pytest

from tests import lab

pytestmark = pytest.mark.stack
BROKERS = ["central-broker"] + [f"site0{i}-broker" for i in range(1, 7)]


@pytest.mark.gate("G04", "no host exposed mqtt ports")
def test_g04_no_host_exposed_mqtt_ports(evidence):
    proc = lab.compose("ps", "--format", "json", record="docker compose ps (published ports)")
    published = []
    for line in proc.stdout.strip().splitlines():
        svc = json.loads(line)
        for pub in svc.get("Publishers") or []:
            if pub.get("PublishedPort"):
                published.append((svc["Service"], pub.get("URL"), pub["PublishedPort"], pub["TargetPort"]))
    evidence.add("published ports summary", "parsed", 0, published)
    assert published and all(svc == "reverse-proxy" for svc, *_ in published), published
    assert all(url == "127.0.0.1" for _, url, *_ in published), published
    for port in (1883, 8883):
        with socket.socket() as s:
            s.settimeout(2)
            refused = s.connect_ex(("127.0.0.1", port)) != 0
        evidence.add(f"host tcp connect 127.0.0.1:{port}", f"socket.connect_ex(127.0.0.1:{port})", 0,
                     {"refused": refused})
        assert refused


@pytest.mark.gate("G05", "mqtt tls verified")
def test_g05_tls_verified_on_every_broker(evidence):
    for host in BROKERS:
        # Each site broker is only reachable from its own LAN: probe from that site's controller container.
        where = "orchid-lab-probe" if host == "central-broker" else f"orchid-{host.replace('-broker', '')}-controller"

        def run_probe(*args: str, record: str) -> dict:
            return lab.dexec_json(where, ["python", "-m", "orchid.tools.probe", *args], record=f"[{where}] {record}")

        res = run_probe("tls", "--host", host, record=f"TLS handshake {host}")
        assert res["verified"] is True and res["version"] in ("TLSv1.2", "TLSv1.3"), res
        assert host in res["san"]
        bad = run_probe("tls", "--host", host, "--sni-name", "attacker.invalid", record=f"hostname mismatch {host}")
        assert bad["verified"] is False and bad["error"] == "SSLCertVerificationError", bad
        plain = run_probe("tcp", "--host", host, "--port", "1883", record=f"plaintext 1883 {host}")
        assert plain["open"] is False, plain


@pytest.mark.gate("G06", "anonymous denied")
def test_g06_anonymous_denied(evidence):
    for host in ("central-broker", "site01-broker", "site02-broker"):
        res = lab.probe("connect", "--host", host, "--anonymous", record=f"anonymous connect {host}")
        assert res["connected"] is False and res["reason_code"] == 135, res


@pytest.mark.gate("G07", "wrong password denied")
def test_g07_wrong_password_denied(evidence):
    for host, user in (("central-broker", "historian"), ("site01-broker", "site01-temp01"),
                       ("central-broker", "bridge-site01")):
        res = lab.probe("connect", "--host", host, "--user", user, "--wrong-password",
                        record=f"wrong password {user}@{host}")
        assert res["connected"] is False and res["reason_code"] == 135, res
    # an identity valid on one broker is unknown on another broker
    res = lab.probe("connect", "--host", "site02-broker", "--user", "probe-site01-temp01",
                    record="site01 identity on site02")
    assert res["connected"] is False, res
    # client-id binding: the CORRECT password of a production principal is refused with a foreign client id,
    # so a stolen credential cannot take over another principal's session
    for host, user in (("central-broker", "historian"), ("central-broker", "bridge-site01"),
                       ("site01-broker", "site01-controller")):
        res = lab.probe("connect", "--host", host, "--user", user,
                        record=f"correct password, foreign client id: {user}@{host}")
        assert res["connected"] is False, res
    health = lab.db("SELECT site, bridge_state, hb_status FROM site_status WHERE site='site01'",
                    record="real bridge/controller sessions unaffected")
    assert health[0]["bridge_state"] == "1"


@pytest.mark.gate("G08", "correct identity accepted")
def test_g08_correct_identity_accepted(evidence):
    res = lab.probe("connect", "--host", "central-broker", "--user", "probe-historian",
                    record="historian-equivalent principal @central")
    assert res["connected"] is True
    res = lab.probe("pub", "--host", "site01-broker", "--user", "probe-site01-controller",
                    "--topic", "orchid/v1/site01/events",
                    "--payload", json.dumps({"event_id": "g08-ok", "event_type": "DIAGNOSTIC", "details": {}}),
                    record="controller-equivalent principal publishes to an allowed topic")
    assert res["connected"] and res["puback_reason"] == 0, res
    res = lab.probe("sub", "--host", "central-broker", "--user", "probe-historian", "--topic",
                    "orchid/v1/+/gateway/heartbeat", "--listen", "1",
                    record="subscribes to allowed filter (retained heartbeats)")
    assert res["suback"] and res["suback"][0] < 0x80 and res["messages"], res


@pytest.mark.gate("G09", "cross site acl denied")
def test_g09_cross_site_acl_denied(evidence):
    cases = [
        ("central-broker", "probe-bridge-site01", "pub", "orchid/v1/site02/alerts"),
        ("central-broker", "probe-bridge-site01", "pub", "orchid/v1/site02/sensor/temp01/telemetry/temperature"),
        ("central-broker", "probe-bridge-site01", "pub", "orchid/v1/site01/control/fan_group/command"),
        ("central-broker", "probe-bridge-site01", "pub", "orchid/v1/site01/actuator/rogue99/state"),
        ("central-broker", "probe-bridge-site01", "sub", "orchid/v1/site02/control/+/command"),
        ("central-broker", "probe-bridge-site01", "sub", "orchid/v1/#"),
        ("central-broker", "probe-central-api", "sub", "orchid/v1/#"),
        ("central-broker", "probe-historian", "pub", "orchid/v1/site01/control/fan_group/command"),
        ("site01-broker", "probe-site01-temp01", "pub", "orchid/v1/site01/control/fan_group/command"),
        ("site01-broker", "probe-site01-temp01", "pub", "orchid/v1/site01/sensor/humidity01/telemetry/humidity"),
        ("site01-broker", "probe-site01-temp01", "pub", "orchid/v1/site01/alerts"),
        ("site01-broker", "probe-site01-temp01", "pub", "orchid/v1/site01/events"),
        ("site01-broker", "probe-site01-temp01", "pub", "orchid/v1/site02/events"),
        ("site01-broker", "probe-site01-temp01", "sub", "orchid/v1/site01/control/+/command"),
        ("site01-broker", "probe-site01-controller", "pub", "orchid/v1/site01/control/fan_group/command"),
    ]
    for host, user, op, topic in cases:
        args = [op, "--host", host, "--user", user, "--topic", topic, "--listen", "0.3"]
        if op == "pub":
            args += ["--payload", "{}"]
        res = lab.probe(*args, record=f"{op} {topic} as {user}@{host}")
        assert res["connected"] is True and res["denied"] is True, res
    # Network segmentation: a site LAN cannot reach another site or the central OT network directly.
    for target in ("site02-broker", "central-broker"):
        code = ("import socket,json\ntry:\n socket.create_connection(('%s',8883),timeout=3); r='REACHABLE'\n"
                "except Exception as e: r=type(e).__name__\nprint(json.dumps({'target':'%s','result':r}))") % (target, target)
        res = lab.dexec_json("orchid-site01-controller", ["python", "-c", code],
                             record=f"site01 LAN -> {target}:8883 reachability")
        assert res["result"] != "REACHABLE", res


@pytest.mark.gate("G32", "oversized mqtt payload bounded")
def test_g32_oversized_payload_bounded(evidence):
    for host, user, topic in (("site01-broker", "probe-site01-temp01",
                               "orchid/v1/site01/sensor/temp01/telemetry/temperature"),
                              ("central-broker", "probe-bridge-site01", "orchid/v1/site01/events")):
        res = lab.probe("oversize", "--host", host, "--user", user, "--topic", topic, "--size", "65536",
                        record=f"64 KiB publish to {host} (max_packet_size 16384)")
        assert res["still_connected"] is False, res
        assert res["broker_alive_after"] is True, res
    lab.run(["docker", "logs", "--tail", "20", "orchid-site01-broker"], record="site01-broker log tail (evidence)")
    health = lab.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", "orchid-site01-broker",
                      "orchid-central-broker"], record="brokers still healthy")
    assert health.stdout.split() == ["healthy", "healthy"]
