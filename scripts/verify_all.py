#!/usr/bin/env python3
"""One-command verification (cross-platform; `make verify` calls this).

    python scripts/verify_all.py            # full run against a (re)started stack
    python scripts/verify_all.py --fresh    # docker compose down -v first (clean volumes)
    python scripts/verify_all.py --skip-ui  # skip Playwright browser QA (recorded as BROWSER_QA_SKIPPED)

Steps: config validation -> secrets bootstrap -> unit tests -> frontend lint/test/build
-> compose build -> compose up + health -> stack tests (integration/security/failure)
-> browser QA -> audit chain verification -> secret scan -> gate summary.
Every step's command, timestamp, exit code and output tail go to evidence/generated/verify_all/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence" / "generated" / "verify_all"
PY = sys.executable
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1", "PYTHONUTF8": "1"}
NPM = shutil.which("npm") or "npm"
STACK_TESTS = [
    "tests/security/test_stack_mqtt_security.py",
    "tests/integration/test_stack_control.py",
    "tests/security/test_stack_web_and_logs.py",
    "tests/failure/test_stack_faults.py",
]
steps: dict[str, dict] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def step(name: str, cmd: list[str], *, cwd: Path = ROOT, timeout: int = 1800, env: dict | None = None,
         required: bool = True) -> int:
    print(f"\n=== [{name}] {' '.join(cmd)}", flush=True)
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env or ENV, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        code, output = proc.returncode, proc.stdout + proc.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        code, output = 124, f"{type(exc).__name__}: {exc}"
    duration = round(time.monotonic() - started, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.log").write_text(f"$ {' '.join(cmd)}\n# started {now()} duration {duration}s exit {code}\n\n"
                                     + output[-200_000:], encoding="utf-8")
    steps[name] = {"command": " ".join(cmd), "exit_code": code, "duration_s": duration, "finished": now(),
                   "log": f"evidence/generated/verify_all/{name}.log"}
    tail = "\n".join(output.strip().splitlines()[-6:])
    print(f"--- [{name}] exit={code} ({duration}s)\n{tail}", flush=True)
    save()
    if code != 0 and required:
        print(f"!!! step {name} failed", flush=True)
    return code


def save() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "steps.json").write_text(json.dumps({"updated": now(), "steps": steps}, indent=2), encoding="utf-8")


def wait_healthy(timeout: int = 300) -> int:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        proc = subprocess.run(["docker", "compose", "ps", "--format", "json"], cwd=ROOT, env=ENV,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        services = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        states = {s["Service"]: s.get("Health") or s.get("State") for s in services}
        last = json.dumps(states, indent=1)
        core = {k: v for k, v in states.items() if k != "lab-probe"}
        if len(core) == 23 and all(v == "healthy" for v in core.values()):
            break
        time.sleep(5)
    ok = len({k for k in json.loads(last) if k != "lab-probe"}) == 23 and \
        all(v == "healthy" for k, v in json.loads(last).items() if k != "lab-probe")
    (OUT / "services_healthy.log").write_text(f"# {now()}\n{last}\n", encoding="utf-8")
    steps["services_healthy"] = {"command": "docker compose ps (wait for 23 healthy services)",
                                 "exit_code": 0 if ok else 1, "finished": now(),
                                 "log": "evidence/generated/verify_all/services_healthy.log"}
    save()
    print(f"--- [services_healthy] {'OK' if ok else 'NOT HEALTHY'}", flush=True)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # e.g. cp950 consoles cannot print tool output like U+2713
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="remove containers AND volumes before starting")
    parser.add_argument("--skip-ui", action="store_true")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in (ROOT / "evidence" / "generated").glob("g[0-9]*"):
        shutil.rmtree(old, ignore_errors=True)  # never mix evidence from different runs

    step("config_validation", [PY, "-c", "import sys; sys.path.insert(0,'services'); "
                               "from orchid.core.config import load_config; c=load_config(); "
                               "print('sites', sorted(c.sites)); print('mqtt_version', c.platform.mqtt_version)"])
    step("compose_render_check", [PY, "scripts/render_compose.py", "--check"])
    step("bootstrap_secrets", [PY, "scripts/bootstrap_secrets.py"])
    step("compose_config", ["docker", "compose", "config", "--quiet"])
    step("unit_tests", [PY, "-m", "pytest", "tests/unit", "-q", "-p", "no:cacheprovider", "-W",
                        "ignore::DeprecationWarning", f"--junitxml={OUT / 'unit-junit.xml'}"])
    fe = ROOT / "services" / "frontend"
    if not (fe / "node_modules").exists():
        step("frontend_install", [NPM, "ci", "--no-audit", "--no-fund"], cwd=fe)
    fe_codes = [step("frontend_lint_security", [NPM, "run", "-s", "lint:security"], cwd=fe),
                step("frontend_tests", [NPM, "test", "--silent"], cwd=fe),
                step("frontend_build_only", [NPM, "run", "build"], cwd=fe)]
    steps["frontend_build"] = {"command": "lint:security + vitest + build", "exit_code": max(fe_codes),
                               "finished": now(), "log": "evidence/generated/verify_all/frontend_build_only.log"}
    save()

    if args.fresh:
        step("compose_down_fresh", ["docker", "compose", "--profile", "test", "down", "-v", "--remove-orphans"],
             required=False)
    step("compose_build", ["docker", "compose", "build"], timeout=3600)
    step("compose_up", ["docker", "compose", "up", "-d", "--remove-orphans"], timeout=900)
    step("probe_up", ["docker", "compose", "--profile", "test", "up", "-d", "lab-probe"], timeout=300)
    wait_healthy()
    time.sleep(20)  # let all six sites accumulate N-of-M samples and heartbeats

    stack_env = {**ENV, "ORCHID_STACK": "1"}
    step("stack_tests", [PY, "-m", "pytest", *STACK_TESTS, "-p", "no:cacheprovider", "-v",
                         f"--junitxml={OUT / 'stack-junit.xml'}"], env=stack_env, timeout=3600)
    if args.skip_ui:
        (ROOT / "evidence" / "generated" / "ui").mkdir(parents=True, exist_ok=True)
        steps["browser_qa"] = {"command": "skipped (--skip-ui)", "exit_code": None, "finished": now()}
    else:
        step("browser_qa", [PY, "scripts/ui_qa.py"], timeout=900, required=False)
    step("audit_verify", [PY, "scripts/verify_audit_chain.py", "--snapshot",
                          str(ROOT / "evidence" / "generated" / "raw" / "final-audit-snapshot.db")])
    step("secret_scan", [PY, "scripts/secret_scan.py"], timeout=900)
    code = step("collect_evidence", [PY, "scripts/collect_evidence.py"])
    summary = json.loads((ROOT / "evidence" / "generated" / "gate_summary.json").read_text())
    print("\n================ VERIFY SUMMARY ================")
    for name, st in steps.items():
        print(f"{name:24s} exit={st['exit_code']}")
    print(f"GATES: PASS {summary['passed']} / FAIL {summary['failed']} / INCONCLUSIVE {summary['inconclusive']}"
          f" | BROWSER_QA {summary['browser_qa']}")
    hard = [n for n, s in steps.items() if s["exit_code"] not in (0, None) and n not in ("compose_down_fresh",)]
    return 0 if code == 0 and not hard else 1


if __name__ == "__main__":
    sys.exit(main())
