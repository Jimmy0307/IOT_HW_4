# Final Acceptance Report — Assignment #4 (Architecture V2.2)

```text
FINAL_STATUS        = PARTIAL   (33/34 core gates PASS on the last complete run; G34 needs one re-run)
BRANCH              = feat/assignment4-orchid-mqtt
HEAD_SHA            = see `git rev-parse HEAD` (report committed on top of 0836663)
LAST_COMPLETE_RUN   = verify_all --fresh "run 4", 2026-11-16 20:22-20:34 UTC, code at 6db74e0

DOCKER_COMPOSE      = PASS  (build + 23 services healthy; 15 segmented networks; only 127.0.0.1:8443 published)
UNIT_TESTS          = PASS  (192/192 at HEAD 0836663; 192/192 in run 4)
INTEGRATION_TESTS   = 7/8 PASS in run 4 (G10 G14 G15 G17 G19 G20 G21 PASS; supplementary G19b FAIL -> fixed, see below)
SECURITY_TESTS      = 15/15 PASS in run 4 (G03-G09, G24-G28, G30, G32, RBAC end-to-end)
FAILURE_TESTS       = 8/8 PASS in run 4 (G11 G12 G13 G16 G18 G22 G23 G29)
FRONTEND_BUILD      = PASS  (tsc + vite build; 74/74 vitest; lint:security 0 findings)
SECRET_SCAN         = CLEAN (49 exact secret values x 211 repo files, full git history, 80 evidence files, 24 container logs)
BROWSER_QA          = PASS  (12/12 Playwright checks against the live stack; 9 screenshots)
AUDIT_CHAIN         = PASS  (207 rows; tampered copy FAIL at index 5, deleted-row copy FAIL at index 3)

CORE_GATES_PASSED   = 33  (G01-G33)
CORE_GATES_FAILED   = 1   (G34 "test suite passes" - caused only by supplementary check G19b, now fixed)
INCONCLUSIVE        = 0
```

## 1. Open blocker (why the status is PARTIAL, not BUILD_READY)

| Item | Detail |
|---|---|
| What failed | Supplementary stack check **G19b** ("malformed operator input cannot crash the controller"). The controller **did not crash** (same start time, 0 restarts, `loop_errors 0`, AUTO, fans OFF - verified live), but the INVALID ACK carried `command_id: null`, so the central command row stayed `SENT` and the test's correlation assertion timed out. |
| Fix | Commit `0836663`: the command gate extracts a well-formed UUID best-effort from an invalid command, so INVALID ACKs (and `COMMAND_INVALID` events) are correlated end to end. Covered by unit tests (192/192 pass). |
| Why not re-verified | The required full re-run (`verify_all --fresh`, "run 5") **was stopped by Claude Code because the host was critically low on memory** (~1.3 GB free of 32 GB). Run 5 stopped during the frontend step and never touched the stack. Per that stop notice the run was not restarted automatically. |
| Exact resume command | `python scripts/verify_all.py --fresh` (≈25 min; needs roughly 4 GB free RAM for 24 containers + Playwright) |
| Expected outcome | G19b PASS -> G34 PASS -> 34/34 -> FINAL_STATUS = BUILD_READY |
| Minimal human step | Free memory (close large apps, or `docker compose --profile test down` first), then run the command above or ask Claude to run it. |

Evidence note: before it was stopped, run 5's first step deleted run 4's per-gate folders
(`evidence/generated/gNN-*`). The surviving run-4 record is complete at
the per-test level: `evidence/generated/verify_all/stack-junit.xml` (31 test cases with times),
`stack_tests.log` (full pytest output), `audit_verify.log`, `secret_scan.log`, `browser_qa.log`,
`gate_summary.json`, and all screenshots in `evidence/generated/ui/`. The early-step logs `config_validation`, `compose_render_check`, `bootstrap_secrets`,
`compose_config`, `unit_tests`, `frontend_lint_security`, `frontend_tests` and `steps.json` were
overwritten by the interrupted run 5 (all exit 0 there too, on code including `0836663`).
`scripts/verify_all.py`
has since been changed to **archive** the previous run to `evidence/generated/_previous_run/`
instead of deleting it.

## 2. Gate results (last complete run: run 4)

| Gate | Requirement | Result | Run-4 evidence |
|---|---|---|---|
| G01 | docker compose build | PASS | `verify_all/compose_build.log` |
| G02 | all required services healthy | PASS | `verify_all/services_healthy.log` (23/23 healthy) |
| G03 | HTTPS dashboard reachable | PASS | junit `test_g03_https_dashboard_and_api` |
| G04 | no host-exposed MQTT ports | PASS | junit `test_g04_*` |
| G05 | MQTT TLS verified | PASS | junit `test_g05_*` (7 brokers, SAN, wrong SNI rejected, 1883 closed) |
| G06 | anonymous denied | PASS | junit `test_g06_*` (CONNACK 0x87) |
| G07 | wrong password denied | PASS | junit `test_g07_*` (+ correct password with foreign client id refused) |
| G08 | correct identity accepted | PASS | junit `test_g08_*` |
| G09 | cross-site ACL denied | PASS | junit `test_g09_*` (15 denied attempts, PUBACK/SUBACK 0x87; LAN isolation) |
| G10 | 6 sites telemetry available | PASS | junit `test_g10_*` |
| G11 | invalid sensor sample cannot control | PASS | junit `test_g11_*` (12 invalid classes at 30 C) |
| G12 | stale sample cannot control | PASS | junit `test_g12_*` |
| G13 | spike does not immediately actuate | PASS | junit `test_g13_*` |
| G14 | temperature high -> fan group | PASS | junit `test_g14_*` |
| G15 | humidity high -> dehumidifier group | PASS | junit `test_g15_*` |
| G16 | group partial failure detected | PASS | junit `test_g16_*` + UI `03-site06-partial-failure-degraded.png` |
| G17 | command ACK/state lifecycle | PASS | junit `test_g17_*` + RBAC end-to-end |
| G18 | expired queued command never executes | PASS | junit `test_g18_*` (broker expiry + app expiry variants) |
| G19 | duplicate command idempotent | PASS | junit `test_g19_*` |
| G20 | manual override arbitration | PASS | junit `test_g20_*` (override > AUTO; interlock > override) |
| G21 | override TTL returns AUTO | PASS | junit `test_g21_*` |
| G22 | central outage local control survives | PASS | junit `test_g22_*` (central broker stopped + bridge net cut) |
| G23 | recovery/store-forward works | PASS | junit `test_g23_*` (late=1 rows from the outage window) |
| G24 | retained stale state not shown fresh | PASS | junit `test_g24_*` + UI `04-*`, `05-*` |
| G25 | log CRLF injection neutralized | PASS | junit `test_g25_*` |
| G26 | stored XSS neutralized | PASS | junit `test_g26_*` + UI `06-system-security-xss-as-text.png` |
| G27 | fake principal/severity rejected | PASS | junit `test_g27_*` |
| G28 | log flood bounded | PASS | junit `test_g28_*` (5/5 critical kept) |
| G29 | logging outage does not stop control | PASS | junit `test_g29_*` |
| G30 | audit tampering detected | PASS | junit `test_g30_*`, `verify_all/audit_verify.log` |
| G31 | secret scan clean | PASS | `verify_all/secret_scan.log` |
| G32 | oversized MQTT payload bounded | PASS | junit `test_g32_*` |
| G33 | frontend production build passes | PASS | `verify_all/frontend_build_only.log` |
| G34 | test suite passes | **FAIL** | junit: 30/31 (G19b) - fixed in `0836663`, re-run pending |

## 3. Independent reviews (performed, findings fixed)

Two independent reviewers (security; Python correctness) read the code without my conclusions.
All HIGH and MEDIUM findings were fixed in `6db74e0` and verified by run 4 + new unit tests:
controller crash on whitespace-only `reason` (now INVALID, loop errors contained), MQTT
client-id takeover (every production principal bound to its client id), unbounded audit
growth / O(n) verification on the writer thread (incremental verification + lower server
lane), operator-audit starvation (principal-keyed buckets), ACK provenance forgery, unregistered
device names, over-broad historian ACL, sensors able to publish site events, misclassified
historian lanes, non-atomic event+audit writes, truncated-heartbeat JSON breaking the overview,
dedup persistence when the cache is full, retry ACK storms, login timing and limiter reset,
unbounded MAINTENANCE, writable broker root filesystems. Accepted and documented: JWT not
revocable within 15 min, viewer can read the security view, a DMZ peer could bypass nginx
rate limits.

## 4. Known limitations

* Field devices and actuators are simulated; every threshold is a lab assumption.
* The lab CA is not in the OS trust store (import `secrets/pki/ca.crt`); Windows `curl`
  needs `--ssl-no-revoke` because the lab CA has no CRL (verification stays on).
* Mosquitto 2.1.2 enforces ACL denials but does not log them at info level.
* Commands and events are not end-to-end signed; central attribution stops at the bridge.
* Single-node SQLite; tamper-evident, not tamper-proof.
* Version deviations from the suggested baseline: none functional. Host Python is 3.14.2
  (containers 3.12.8); Mosquitto 2.1.2 is the current stable image and its Dynamic Security
  plugin was used (no fallback needed).

## 5. Residual risks

See `docs/SECURITY_MODEL.md` section 10 (15 items), notably: no per-device attribution across
the bridge, no command signing (bounded by expiry, mode rules and the local interlock),
`probe-*` test principals present in lab dynsec configs (use `--no-test-principals` outside the
lab), clock dependence of command expiry.

## 6. How to run

```bash
pip install -r requirements-dev.txt
python scripts/bootstrap_secrets.py
docker compose up -d --build
# dashboard: https://localhost:8443  (logins in secrets/web_credentials.local.txt)
```

## 7. How to verify

```bash
python scripts/verify_all.py --fresh      # full acceptance run -> evidence/generated/, docs/TEST_MATRIX.md
python -m pytest tests/unit -q            # 192 unit tests, no Docker
python scripts/verify_audit_chain.py --snapshot evidence/generated/raw/audit-snapshot.db
python scripts/secret_scan.py
```
