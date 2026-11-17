# Final Acceptance Report — Assignment #4 (Architecture V2.2)

```text
FINAL_STATUS        = COMPLETE
BRANCH              = feat/assignment4-orchid-mqtt
VERIFIED_SHA        = ce7dc14b8e6b4daa0a7365abfdc0e831dbe92c86
VERIFY_RUN          = GitHub Actions run 43998817412, 2026-11-17 01:52–02:03 UTC
VERIFY_COMMAND      = python scripts/verify_all.py --fresh

CORE_GATES          = 34/34 PASS
FAILED              = 0
INCONCLUSIVE        = 0
BROWSER_QA          = PASS

UNIT_TESTS          = PASS
FRONTEND_TESTS      = PASS
FRONTEND_BUILD      = PASS
STACK_TESTS         = PASS
AUDIT_CHAIN         = PASS
SECRET_SCAN         = CLEAN
```

## 1. Closure decision

The full canonical fresh verification completed successfully on a clean GitHub-hosted Linux runner.

Final result:

- **34 / 34 core acceptance gates PASS**
- **0 FAIL**
- **0 INCONCLUSIVE**
- Browser QA: **PASS**
- Stack integration/security/failure suite: **PASS**
- Audit-chain verification: **PASS**
- Secret scan: **CLEAN**

The previous G34 blocker is closed. The whitespace-only operator-command regression fixed in `0836663` is covered by supplementary check **G19b**, which now passes in the full stack.

The first GitHub Actions attempt exposed a Linux-only bind-mount permission issue: generated secret files were mode `0600` on the host while the Python containers intentionally run as non-root UID 10001. This was a CI portability issue, not a control-system defect. The repository now uses the existing opt-in ephemeral-runner compatibility path (`ORCHID_EPHEMERAL_BIND_SECRET_MODE=1`) for disposable CI hosts. The final fresh run passed after that correction.

## 2. Final verified evidence

GitHub Actions:

- Workflow: **Full Assignment4 Verification**
- Run ID: **43998817412**
- Verified commit: `ce7dc14b8e6b4daa0a7365abfdc0e831dbe92c86`
- Artifact: `assignment4-verification-43998817412-1`
- Artifact digest: `sha256:8507f2397cf3050cc9e88bac25a76e2943752b1d2c4bf8b68631ebb35205abbf`

Canonical summary produced by the run:

```json
{"passed": 34, "failed": 0, "inconclusive": 0, "browser_qa": "PASS"}
```

Major verification steps all returned exit code 0:

- configuration validation
- compose render/config validation
- secret/bootstrap generation
- unit tests
- frontend security lint
- frontend tests
- production frontend build
- Docker compose build
- 23-service health verification
- integration/security/failure stack tests
- Playwright browser QA
- audit-chain verification
- secret scan
- evidence collection

See `docs/TEST_MATRIX.md` for the gate-by-gate result and artifact paths.

## 3. Acceptance coverage

The verified system includes:

- six isolated greenhouse site LANs
- one local Mosquitto broker per site
- central broker with explicit bridge topic allowlists
- MQTT 5 over verified TLS
- authentication and least-privilege ACLs
- no host-exposed MQTT ports
- six-site validated telemetry aggregation
- sensor schema/type/unit/range/freshness/plausibility validation
- spike/debounce protection
- local AUTO control
- two-member fan/dehumidifier group actuation
- command ACK and observed-state lifecycle
- MQTT command expiry and application-level expiry
- duplicate-command idempotency
- AUTO / MANUAL_OVERRIDE / MAINTENANCE / FAILSAFE arbitration
- local control survival during central outage
- bridge recovery / store-and-forward
- retained-state freshness handling
- CRLF log-injection neutralization
- stored-XSS neutralization
- authoritative principal/severity handling
- bounded log flooding with critical-event preservation
- logging-outage isolation from control
- HMAC audit tamper detection
- oversized MQTT payload bounding
- viewer/operator RBAC
- visual six-site dashboard and browser QA

## 4. Independent review fixes

Independent security and Python-correctness reviews identified real issues before closure. HIGH and MEDIUM findings were fixed, including:

- whitespace-only operator input crash path
- MQTT client-id/session takeover risk
- ACK provenance forgery
- over-broad historian ACL
- unregistered device identities
- sensor publication outside allowed scope
- audit-growth and verification cost issues
- operator-audit starvation
- non-atomic event/audit writes
- retry ACK storms
- future-dated freshness handling
- unbounded MAINTENANCE
- writable broker root filesystems

The final full run includes those fixes.

## 5. Known limitations / residual risks

This remains a **production-like academic/local lab**, not a production deployment.

Known limitations:

- Field devices and actuators are simulated.
- Sensor thresholds and plausibility limits are lab assumptions.
- The lab CA is locally generated and not trusted by operating systems by default.
- Central attribution terminates at the bridge identity; there is no end-to-end device message signing.
- Single-node SQLite is used for the lab historian.
- The audit chain is tamper-evident, not tamper-proof.
- Login JWTs are not revocable during their short lifetime.
- Viewer access to the security view is accepted for this course lab.
- A peer already inside the web-facing Docker network could bypass reverse-proxy rate limiting; network membership is treated as a trusted deployment boundary.
- Test principals exist in the lab security configuration; use the documented no-test-principal mode outside lab verification.

These are documented design limitations, not failed acceptance gates.

## 6. How to run locally

```bash
pip install -r requirements-dev.txt
python scripts/bootstrap_secrets.py
docker compose up -d --build
```

Dashboard:

```text
https://localhost:8443
```

Local credentials are generated into the gitignored `secrets/` directory.

## 7. How to verify

Full canonical verification:

```bash
python scripts/verify_all.py --fresh
```

GitHub Actions can also execute the same fresh verification through:

```text
.github/workflows/full-assignment4-verification.yml
```

The canonical acceptance status for this version is:

```text
ASSIGNMENT4_IMPLEMENTATION_COMPLETE
CORE_GATES = 34/34 PASS
```
