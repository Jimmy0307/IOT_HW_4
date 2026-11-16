"""Lab-only MQTT/TLS probe (runs inside the `lab-probe` container, profile=test).

Every sub-command prints exactly one JSON object. Targets are restricted to this
lab's broker hostnames - the probe refuses anything else.

    python -m orchid.tools.probe connect --host central-broker --user historian
    python -m orchid.tools.probe connect --host central-broker --anonymous
    python -m orchid.tools.probe connect --host central-broker --user historian --wrong-password
    python -m orchid.tools.probe pub --host central-broker --user bridge-site01 --topic orchid/v1/site02/alerts
    python -m orchid.tools.probe sub --host central-broker --user bridge-site01 --topic orchid/v1/site02/#
    python -m orchid.tools.probe tls --host central-broker [--sni-name wrong-name]
    python -m orchid.tools.probe tcp --host central-broker --port 1883
    python -m orchid.tools.probe oversize --host site01-broker --user site01-temp01 --size 65536
    python -m orchid.tools.probe publish-many --host site01-broker --user site01-temp01 --topic ... --file payloads.json
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

SECRETS = Path("/run/orchid-secrets")
ALLOWED_HOST = re.compile(r"^(central-broker|site0[1-6]-broker)$")


def out(obj: dict[str, Any]) -> int:
    print(json.dumps(obj, default=str))
    return 0


def tls_ctx(check_hostname: bool = True) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(SECRETS / "ca.crt"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = check_hostname
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def make_client(args: argparse.Namespace) -> mqtt.Client:
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                         client_id=f"probe-{uuid.uuid4().hex[:10]}", protocol=mqtt.MQTTv5)
    if not args.anonymous:
        password = (SECRETS / "mqtt" / f"{args.user}.pw").read_text().strip()
        if args.wrong_password:
            password = "wrong-" + password[::-1]
        client.username_pw_set(args.user, password)
    client.tls_set_context(tls_ctx())
    return client


def connect(args: argparse.Namespace, client: mqtt.Client | None = None) -> tuple[mqtt.Client, dict[str, Any]]:
    client = client or make_client(args)
    result: dict[str, Any] = {"connected": False}
    done = threading.Event()

    def on_connect(c: Any, u: Any, flags: Any, rc: Any, props: Any) -> None:
        result.update(connected=not rc.is_failure, reason_code=int(rc.value), reason=str(rc))
        done.set()

    def on_disconnect(c: Any, u: Any, flags: Any, rc: Any, props: Any) -> None:
        result.setdefault("disconnect_reason", str(rc))
        result["disconnect_code"] = int(getattr(rc, "value", 0) or 0)
        done.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    try:
        client.connect(args.host, 8883, keepalive=20, clean_start=True)
    except (OSError, ssl.SSLError) as exc:
        result.update(error=type(exc).__name__, detail=str(exc)[:200])
        return client, result
    client.loop_start()
    done.wait(args.timeout)
    return client, result


def cmd_connect(args: argparse.Namespace) -> int:
    client, res = connect(args)
    client.disconnect()
    client.loop_stop()
    return out(res)


def cmd_pub(args: argparse.Namespace) -> int:
    client, res = connect(args)
    if not res.get("connected"):
        return out(res)
    codes: dict[int, int] = {}
    ev = threading.Event()

    def on_publish(c: Any, u: Any, mid: int, rc: Any, props: Any) -> None:
        codes[mid] = int(getattr(rc, "value", 0) or 0)
        ev.set()

    client.on_publish = on_publish
    props = None
    if args.expiry:
        props = Properties(PacketTypes.PUBLISH)
        props.MessageExpiryInterval = args.expiry
    payload = args.payload.encode() if args.payload is not None else b"{}"
    info = client.publish(args.topic, payload, qos=1, retain=args.retain, properties=props)
    ev.wait(args.timeout)
    code = codes.get(info.mid)
    res.update(topic=args.topic, puback_reason=code, denied=code is not None and code >= 0x80,
               acknowledged=code is not None)
    client.disconnect()
    client.loop_stop()
    return out(res)


def cmd_sub(args: argparse.Namespace) -> int:
    client, res = connect(args)
    if not res.get("connected"):
        return out(res)
    granted: list[int] = []
    messages: list[dict[str, Any]] = []
    ev = threading.Event()

    def on_subscribe(c: Any, u: Any, mid: int, rcs: Any, props: Any) -> None:
        granted.extend(int(getattr(r, "value", r)) for r in rcs)
        ev.set()

    def on_message(c: Any, u: Any, msg: Any) -> None:
        if len(messages) < args.max_messages:
            messages.append({"topic": msg.topic, "retain": bool(msg.retain),
                             "payload": msg.payload[:2048].decode(errors="replace")})

    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.subscribe(args.topic, qos=1)
    ev.wait(args.timeout)
    time.sleep(args.listen)
    res.update(topic=args.topic, suback=granted, denied=bool(granted) and all(g >= 0x80 for g in granted),
               messages=messages)
    client.disconnect()
    client.loop_stop()
    return out(res)


def cmd_tls(args: argparse.Namespace) -> int:
    name = args.sni_name or args.host
    res: dict[str, Any] = {"host": args.host, "server_name": name}
    try:
        with socket.create_connection((args.host, 8883), timeout=args.timeout) as sock:
            with tls_ctx().wrap_socket(sock, server_hostname=name) as tls:
                cert = tls.getpeercert()
                res.update(verified=True, version=tls.version(), cipher=tls.cipher()[0],
                           subject=dict(x[0] for x in cert.get("subject", ())),
                           san=[v for _, v in cert.get("subjectAltName", ())], not_after=cert.get("notAfter"))
    except ssl.SSLCertVerificationError as exc:
        res.update(verified=False, error="SSLCertVerificationError", detail=exc.verify_message)
    except (OSError, ssl.SSLError) as exc:
        res.update(verified=False, error=type(exc).__name__, detail=str(exc)[:200])
    return out(res)


def cmd_tcp(args: argparse.Namespace) -> int:
    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout):
            return out({"host": args.host, "port": args.port, "open": True})
    except OSError as exc:
        return out({"host": args.host, "port": args.port, "open": False, "error": type(exc).__name__})


def cmd_oversize(args: argparse.Namespace) -> int:
    client, res = connect(args)
    if not res.get("connected"):
        return out(res)
    info = client.publish(args.topic, b"x" * args.size, qos=1)
    time.sleep(3)
    res.update(size=args.size, still_connected=client.is_connected(), mid=info.mid, publish_rc=info.rc)
    client.disconnect()
    client.loop_stop()
    _, after = connect(argparse.Namespace(**{**vars(args), "anonymous": False, "wrong_password": False}))
    res["broker_alive_after"] = bool(after.get("connected"))
    return out(res)


def cmd_publish_many(args: argparse.Namespace) -> int:
    client, res = connect(args)
    if not res.get("connected"):
        return out(res)
    payloads = json.loads(Path(args.file).read_text()) if args.file else json.loads(sys.stdin.read())
    sent = 0
    for item in payloads:
        topic = item.get("topic", args.topic)
        body = item["payload"] if isinstance(item["payload"], str) else json.dumps(item["payload"])
        client.publish(topic, body.encode(), qos=int(item.get("qos", 0)))
        sent += 1
    time.sleep(2)
    res.update(sent=sent, still_connected=client.is_connected())
    client.disconnect()
    client.loop_stop()
    return out(res)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("connect", "pub", "sub", "tls", "tcp", "oversize", "publish-many"):
        p = sub.add_parser(name)
        p.add_argument("--host", required=True)
        p.add_argument("--user", default="health")
        p.add_argument("--anonymous", action="store_true")
        p.add_argument("--wrong-password", action="store_true")
        p.add_argument("--timeout", type=float, default=8.0)
        p.add_argument("--topic", default="orchid/v1/site01/events")
        p.add_argument("--payload")
        p.add_argument("--retain", action="store_true")
        p.add_argument("--expiry", type=int)
        p.add_argument("--listen", type=float, default=2.0)
        p.add_argument("--max-messages", type=int, default=50)
        p.add_argument("--sni-name")
        p.add_argument("--port", type=int, default=1883)
        p.add_argument("--size", type=int, default=65536)
        p.add_argument("--file")
    args = parser.parse_args(argv)
    if not ALLOWED_HOST.match(args.host):
        return out({"error": "host not allowed: probe only targets this lab's brokers"}) or 2
    return {"connect": cmd_connect, "pub": cmd_pub, "sub": cmd_sub, "tls": cmd_tls, "tcp": cmd_tcp,
            "oversize": cmd_oversize, "publish-many": cmd_publish_many}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
