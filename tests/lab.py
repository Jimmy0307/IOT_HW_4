"""Helpers for stack (docker compose) tests: exec, probes, DB queries, evidence capture.

Evidence rules: every gate test records the commands it ran, timestamps, exit codes
and sanitized outputs under evidence/generated/<gate>/. Outputs never contain
secrets (probes print reason codes only; passwords are read inside containers).
"""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "generated"
SECRET_PATTERN = re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[0-9a-f]{48}\b|\b[0-9a-f]{64}\b|eyJ[\w-]{10,}\.[\w-]{10,})")
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
API_BASE = os.environ.get("ORCHID_API_BASE", "https://localhost:8443")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def redact(text: str) -> str:
    return SECRET_PATTERN.sub("[REDACTED]", text)


class Evidence:
    """Collects steps for one gate; written as JSON + a readable log."""

    def __init__(self, gate: str, title: str) -> None:
        self.gate = gate
        self.title = title
        self.steps: list[dict[str, Any]] = []
        self.started = iso(now_utc())
        self.notes: list[str] = []

    def add(self, name: str, command: str, exit_code: int | None, output: Any) -> None:
        text = output if isinstance(output, str) else json.dumps(output, default=str)
        self.steps.append({"step": name, "ts": iso(now_utc()), "command": redact(command), "exit_code": exit_code,
                           "output": redact(text)[:4000]})

    def note(self, text: str) -> None:
        self.notes.append(redact(text))

    def folder_name(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:48]
        return f"{self.gate.lower()}-{slug}"

    def write(self, result: str) -> Path:
        folder = EVIDENCE / self.folder_name()
        folder.mkdir(parents=True, exist_ok=True)
        doc = {"gate": self.gate, "title": self.title, "result": result, "started": self.started,
               "finished": iso(now_utc()), "steps": self.steps, "notes": self.notes}
        (folder / "result.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        lines = [f"# {self.gate} - {self.title}", f"RESULT: {result}", f"started: {doc['started']}",
                 f"finished: {doc['finished']}", ""]
        for s in self.steps:
            lines += [f"## {s['step']}  ({s['ts']})", f"$ {s['command']}", f"exit={s['exit_code']}", s["output"], ""]
        lines += [f"NOTE: {n}" for n in self.notes]
        (folder / "evidence.log").write_text("\n".join(lines), encoding="utf-8")
        return folder


_current: Evidence | None = None


def set_current(ev: Evidence | None) -> None:
    global _current
    _current = ev


def run(cmd: list[str], *, timeout: float = 120, check: bool = False, record: str | None = None,
        input: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV, encoding="utf-8",
                          errors="replace", input=input)
    if record and _current is not None:
        _current.add(record, " ".join(cmd), proc.returncode, (proc.stdout + proc.stderr).strip())
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}")
    return proc


def dexec_json(container: str, args: list[str], *, record: str | None = None, timeout: float = 60,
               input: str | None = None) -> dict:
    proc = run(["docker", "exec", "-i", container, *args], timeout=timeout, record=record, input=input)
    line = (proc.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        return json.loads(line)
    except ValueError as exc:
        raise AssertionError(f"non-JSON output from {container}: {proc.stdout[-500:]} {proc.stderr[-500:]}") from exc


def probe(*args: str, record: str | None = None) -> dict:
    return dexec_json("orchid-lab-probe", ["python", "-m", "orchid.tools.probe", *args], record=record)


def ctl(site: str, method: str, path: str, body: dict | None = None, record: str | None = None) -> dict:
    cmd = ["python", "-m", "orchid.tools.faultctl", method, path] + ([json.dumps(body)] if body is not None else [])
    return dexec_json(f"orchid-{site}-controller", cmd, record=record)


def sim(site: str, method: str, path: str, body: dict | None = None, record: str | None = None) -> dict:
    cmd = ["python", "-m", "orchid.tools.faultctl", method, path] + ([json.dumps(body)] if body is not None else [])
    return dexec_json(f"orchid-{site}-sim", cmd, record=record)


DB_SNIPPET = (
    "import sqlite3,json,sys; a=json.loads(sys.argv[2]);"
    "c=sqlite3.connect('file:/data/historian.db?mode=ro', uri=True, timeout=10); c.row_factory=sqlite3.Row;"
    "print(json.dumps([dict(r) for r in c.execute(sys.argv[1], a).fetchall()]))"
)


def db(sql: str, args: tuple = (), *, record: str | None = None, container: str = "orchid-historian") -> list[dict]:
    proc = run(["docker", "exec", container, "python", "-c", DB_SNIPPET, sql, json.dumps(list(args))],
               timeout=60, record=record)
    if proc.returncode != 0:
        raise AssertionError(f"db query failed: {proc.stderr[-800:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def wait_until(fn: Callable[[], Any], timeout: float, interval: float = 1.0, desc: str = "") -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = fn()
            if last:
                return last
        except (AssertionError, KeyError, IndexError, ValueError) as exc:
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {desc or fn}: last={last!r}"[:2000])


def command_body(desired: str | None = None, *, ttl: float = 30, issued_offset: float = 0, mode: str | None = None,
                 cid: str | None = None, ttl_seconds: int | None = None, reason: str = "STACK_TEST") -> dict:
    issued = now_utc() + timedelta(seconds=issued_offset)
    body: dict[str, Any] = {"command_id": cid or str(uuid.uuid4()), "issued_at": iso(issued),
                            "expires_at": iso(issued + timedelta(seconds=ttl)), "operator": "stack-test",
                            "reason": reason}
    if mode:
        body["mode"] = mode
        if ttl_seconds:
            body["ttl_seconds"] = ttl_seconds
    else:
        body["desired_state"] = desired
    return body


def send_command(site: str, group: str, body: dict, *, expiry: int | None = None, record: str | None = None) -> dict:
    """Publish as the central-api principal on the central broker (real central -> bridge -> site path)."""
    args = ["pub", "--host", "central-broker", "--user", "central-api",
            "--topic", f"orchid/v1/{site}/control/{group}/command", "--payload", json.dumps(body)]
    if expiry:
        args += ["--expiry", str(expiry)]
    res = probe(*args, record=record)
    assert res.get("puback_reason") == 0, res
    return res


def reset_site(site: str) -> None:
    """Return a site to a clean AUTO baseline after a test."""
    ctl(site, "POST", "/fault/clear", {})
    for dev in ("temp01", "humidity01"):
        sim(site, "POST", "/fault", {"device": dev, "fault": None})
    sim(site, "POST", "/profile", {})
    send_command(site, "mode", command_body(mode="AUTO", reason="TEST_RESET"))


def status(site: str) -> dict:
    return ctl(site, "GET", "/status")


def members(site: str, group: str) -> dict:
    return status(site)["groups"][group]["members"]


def compose(*args: str, timeout: float = 300, record: str | None = None) -> subprocess.CompletedProcess:
    return run(["docker", "compose", *args], timeout=timeout, record=record, check=False)


# ---------------------------------------------------------------- HTTPS API (through the reverse proxy)
def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(ROOT / "secrets" / "pki" / "ca.crt"))
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def web_credentials() -> dict[str, str]:
    creds = {}
    for line in (ROOT / "secrets" / "web_credentials.local.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            user, pw, _ = line.split("\t")
            creds[user] = pw
    return creds


def api(method: str, path: str, *, token: str | None = None, body: dict | None = None,
        record: str | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, context=_ctx(), timeout=15) as resp:  # noqa: S310 - lab URL
            code, payload = resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        code, payload = exc.code, json.loads(exc.read() or b"null")
    if record and _current is not None:
        shown = {k: v for k, v in payload.items() if k != "access_token"} if isinstance(payload, dict) else payload
        _current.add(record, f"{method} {API_BASE}{path}", code, shown)
    return code, payload


def login(role: str = "viewer") -> str:
    code, payload = api("POST", "/api/auth/login", body={"username": role, "password": web_credentials()[role]})
    assert code == 200, payload
    return payload["access_token"]


def set_env(site: str, *, temp: float | None = None, hum: float | None = None, jump: bool = False) -> None:
    if temp is not None:
        sim(site, "POST", "/set", {"metric": "temperature", "value": temp, "jump": jump})
    if hum is not None:
        sim(site, "POST", "/set", {"metric": "humidity", "value": hum, "jump": jump})


def wait_group(site: str, group: str, state: str, timeout: float = 40) -> dict:
    return wait_until(lambda: (m := members(site, group)) and all(v == state for v in m.values()) and m,
                      timeout, desc=f"{site} {group} == {state}")


def baseline(site: str, timeout: float = 60) -> None:
    """Pin a normal environment and wait until both groups are OFF in AUTO."""
    set_env(site, temp=19.0, hum=56.0)
    wait_until(lambda: status(site)["mode"]["mode"] == "AUTO", timeout, desc=f"{site} AUTO")
    wait_group(site, "fan_group", "OFF", timeout)
    wait_group(site, "dehumidifier_group", "OFF", timeout)
