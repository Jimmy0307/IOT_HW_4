#!/usr/bin/env python3
"""Aggregate gate evidence into evidence/generated/gate_summary.json and docs/TEST_MATRIX.md.

A gate is PASS only if every evidence source it depends on reports PASS.
Missing evidence => INCONCLUSIVE (never assumed PASS).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "evidence" / "generated"

# id, requirement, method, evidence sources ("pytest:<folder>", "step:<name>", "ui:<check substring>")
GATES = [
    ("G01", "docker compose build", "`docker compose build` (all images)", ["step:compose_build"]),
    ("G02", "all required services healthy", "Docker healthchecks (TLS+auth MQTT probe, HTTP, heartbeat file)", ["step:services_healthy"]),
    ("G03", "HTTPS dashboard reachable", "CA-verified HTTPS GET / and API (Python ssl, hostname checked)", ["pytest:g03-"]),
    ("G04", "no host-exposed MQTT ports", "`docker compose ps` publishers + host connect 1883/8883", ["pytest:g04-"]),
    ("G05", "MQTT TLS verified", "TLS handshake + SAN on every broker; wrong SNI rejected; 1883 closed", ["pytest:g05-"]),
    ("G06", "anonymous denied", "MQTT5 CONNECT without credentials -> 0x87", ["pytest:g06-"]),
    ("G07", "wrong password denied", "wrong password / foreign identity -> 0x87", ["pytest:g07-"]),
    ("G08", "correct identity accepted", "valid principal connects; allowed publish PUBACK 0x00", ["pytest:g08-"]),
    ("G09", "cross-site ACL denied", "PUBACK/SUBACK 0x87 for 11 cross-scope attempts; LAN isolation", ["pytest:g09-"]),
    ("G10", "6 sites telemetry available", "fresh VALIDATED series x12 at central; heartbeats ONLINE", ["pytest:g10-"]),
    ("G11", "invalid sensor sample cannot control", "12 injected invalid classes while value=30 C; fans stay OFF", ["pytest:g11-"]),
    ("G12", "stale sample cannot control", "stale/future device_ts with value=30 C; fans stay OFF", ["pytest:g12-"]),
    ("G13", "spike does not immediately actuate", "+15 C one-shot spike and 200 C sample; fans stay OFF", ["pytest:g13-"]),
    ("G14", "temperature high -> fan group", "ramp to 23 C; both fans ON; AUTO ACK TEMP_HIGH at central", ["pytest:g14-"]),
    ("G15", "humidity high -> dehumidifier group", "ramp to 68 %RH; both dehumidifiers ON; ACK", ["pytest:g15-"]),
    ("G16", "group partial failure detected", "fan02 STUCK_OFF; ACK PARTIAL_FAILURE; CRITICAL alert; DEGRADED", ["pytest:g16-", "ui:partial actuator failure"]),
    ("G17", "command ACK/state lifecycle", "central command -> bridge -> site -> ACK + retained observed state", ["pytest:g17-", "pytest:g26a-"]),
    ("G18", "expired queued command never executes", "consumer offline; broker expiry + app expires_at variants", ["pytest:g18-"]),
    ("G19", "duplicate command idempotent", "same command_id x3 -> 1 APPLIED, 2 DUPLICATE", ["pytest:g19-"]),
    ("G20", "manual override arbitration", "override beats AUTO; HARD_SAFETY_INTERLOCK beats override", ["pytest:g20-"]),
    ("G21", "override TTL returns AUTO", "ttl 8 s -> AUTO; transitions in HMAC audit chain", ["pytest:g21-"]),
    ("G22", "central outage local control survives", "central broker stopped + site03 bridge net cut; local AUTO acts", ["pytest:g22-"]),
    ("G23", "recovery/store-forward works", "outage-window telemetry delivered after recovery (late=1)", ["pytest:g23-"]),
    ("G24", "retained stale state not shown fresh", "controller frozen/killed; API+UI show STALE/OFFLINE", ["pytest:g24-", "ui:retained state shown STALE"]),
    ("G25", "log CRLF injection neutralized", "CR/LF + forged JSON line + ANSI -> one record, visible escapes", ["pytest:g25-"]),
    ("G26", "stored XSS neutralized", "API returns inert text; browser renders text, no script/dialog", ["pytest:g26-", "ui:XSS payload"]),
    ("G27", "fake principal/severity rejected", "claims dropped; server principal/site/severity/ingest_ts", ["pytest:g27-"]),
    ("G28", "log flood bounded", "3000 events -> rate limited, 5/5 critical kept, disk bounded", ["pytest:g28-"]),
    ("G29", "logging outage does not stop control", "historian stopped + local log sink failing; control continues", ["pytest:g29-"]),
    ("G30", "audit tampering detected", "verify_audit_chain.py PASS on snapshot; FAIL on edited/deleted copy", ["pytest:g30-", "step:audit_verify"]),
    ("G31", "secret scan clean", "exact-value + pattern scan: repo, git history, evidence, container logs", ["step:secret_scan"]),
    ("G32", "oversized MQTT payload bounded", "64 KiB publish vs max_packet_size 16384 -> disconnect, broker alive", ["pytest:g32-"]),
    ("G33", "frontend production build passes", "`npm run build` (tsc + vite) + vitest + lint:security", ["step:frontend_build"]),
    ("G34", "test suite passes", "unit + integration + security + failure suites", ["step:unit_tests", "step:stack_tests"]),
]


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def source_result(source: str, steps: dict, ui: dict) -> tuple[str, str]:
    kind, key = source.split(":", 1)
    if kind == "pytest":
        folders = sorted(p for p in GEN.glob(f"{key}*") if (p / "result.json").exists())
        if not folders:
            return "INCONCLUSIVE", f"evidence/generated/{key}* (missing)"
        res = load_json(folders[0] / "result.json").get("result", "INCONCLUSIVE")
        return res, f"evidence/generated/{folders[0].name}/"
    if kind == "step":
        step = steps.get(key)
        if not step:
            return "INCONCLUSIVE", f"verify_all step {key} (not run)"
        return ("PASS" if step.get("exit_code") == 0 else "FAIL"), f"evidence/generated/verify_all/{key}.log"
    if kind == "ui":
        checks = [c for c in ui.get("results", []) if key.lower() in c["check"].lower()]
        if not checks:
            return "INCONCLUSIVE", "evidence/generated/ui/ (check missing)"
        return checks[0]["result"], "evidence/generated/ui/ui_qa_result.json"
    return "INCONCLUSIVE", source


def main() -> int:
    steps = load_json(GEN / "verify_all" / "steps.json").get("steps", {})
    ui = load_json(GEN / "ui" / "ui_qa_result.json")
    rows = []
    for gid, req, method, sources in GATES:
        results = [source_result(s, steps, ui) for s in sources]
        values = [r for r, _ in results]
        result = "PASS" if all(v == "PASS" for v in values) else ("FAIL" if "FAIL" in values else "INCONCLUSIVE")
        rows.append({"gate": gid, "requirement": req, "method": method, "result": result,
                     "evidence": [e for _, e in results]})
    summary = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "passed": sum(r["result"] == "PASS" for r in rows),
               "failed": sum(r["result"] == "FAIL" for r in rows),
               "inconclusive": sum(r["result"] == "INCONCLUSIVE" for r in rows),
               "browser_qa": ("PASS" if ui.get("failed") == 0 and ui.get("results") else
                              ("BROWSER_QA_BLOCKED" if not ui.get("results") else "FAIL")),
               "gates": rows}
    (GEN / "gate_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = ["# Test Matrix - Acceptance Gates", "",
             f"Generated by `scripts/collect_evidence.py` at {summary['generated']} from evidence produced by "
             "`python scripts/verify_all.py`. Do not edit by hand.", "",
             f"**PASS {summary['passed']} / FAIL {summary['failed']} / INCONCLUSIVE {summary['inconclusive']}** "
             f"(of {len(rows)}) - Browser QA: **{summary['browser_qa']}**", "",
             "| Gate | Requirement | Method | Result | Evidence | Notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        ev = "<br>".join(f"`{e}`" for e in r["evidence"])
        note = "UI + API" if any("ui" in e for e in r["evidence"]) else ""
        lines.append(f"| {r['gate']} | {r['requirement']} | {r['method']} | **{r['result']}** | {ev} | {note} |")
    extra = []
    for folder in sorted(GEN.glob("g*")):
        doc = load_json(folder / "result.json")
        gate = str(doc.get("gate", ""))
        if doc and gate and gate not in {g[0] for g in GATES}:
            extra.append(f"| {gate} | {doc.get('title')} | **{doc.get('result')}** | `evidence/generated/{folder.name}/` |")
    if extra:
        lines += ["", "## Supplementary checks (beyond the 34 core gates)", "",
                  "| Check | Title | Result | Evidence |", "|---|---|---|---|", *extra]
    lines += ["", "## Automated suites", "",
              "| Suite | Location | Runs in |", "|---|---|---|",
              "| Unit (pure logic, no Docker) | `tests/unit/` | `pytest tests/unit` |",
              "| Integration / control plane | `tests/integration/` | live stack (`ORCHID_STACK=1`) |",
              "| Security (MQTT, web, logs, audit) | `tests/security/` | live stack |",
              "| Failure / fault injection | `tests/failure/` | live stack (stops/starts containers, cuts networks) |",
              "| Frontend (vitest, XSS, static scan) | `services/frontend/src/__tests__/` | `npm test` |",
              "| Browser QA (Playwright) | `tests/ui/ui_qa.mjs` | `python scripts/ui_qa.py` |", ""]
    (ROOT / "docs" / "TEST_MATRIX.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(json.dumps({k: summary[k] for k in ("passed", "failed", "inconclusive", "browser_qa")}))
    return 0 if summary["failed"] == 0 and summary["inconclusive"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
