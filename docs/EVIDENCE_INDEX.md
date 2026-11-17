# Evidence Index — Assignment #4

Canonical verification status: **34/34 core gates PASS**, Browser QA **PASS**.

Report-ready screenshots are stored under `docs/evidence/report-ready/`.

| File | Evidence purpose |
|---|---|
| `01_live_arm64_all_services_healthy.jpg` | Seeed Studio / NVIDIA Tegra ARM64: 23 Docker services healthy |
| `02_live_arm64_global_overview.jpg` | Six-site live dashboard overview |
| `03_live_arm64_login_rbac.jpg` | HTTPS dashboard login and RBAC entry point |
| `04_viewer_global_overview.jpg` | Viewer read-only overview |
| `05_greenhouse_01_detail.jpg` | Greenhouse 01 site detail |
| `06_greenhouse_02_temp_warning_fans_on.jpg` | Temperature warning and two-fan automatic actuation |
| `07_greenhouse_03_warning_detail.jpg` | Greenhouse 03 warning/detail state |
| `08_greenhouse_04_detail.jpg` | Greenhouse 04 detail |
| `09_greenhouse_05_temp_humidity_warning.jpg` | Simultaneous temperature + humidity warning |
| `10_greenhouse_06_humidity_warning_dehumidifiers_on.jpg` | Humidity warning and two-dehumidifier automatic actuation |
| `11_system_security_operator.jpg` | System & Security page as operator |
| `12_system_security_viewer.jpg` | System & Security page as viewer |
| `13_ci_partial_failure_degraded.jpg` | Partial actuator failure → DEGRADED |
| `14_ci_stale_sensor.jpg` | STALE/freshness handling |
| `15_ci_offline_stale_degraded.jpg` | OFFLINE / STALE / DEGRADED overview |
| `16_ci_xss_neutralized.jpg` | Stored XSS payload rendered as inert text |
| `17_ci_telemetry_trend.jpg` | Site telemetry trend/detail |
| `18_ci_operator_command_ack.jpg` | Operator command → ACK → observed-state lifecycle |
| `19_ci_temperature_high_fans_on.jpg` | CI-verified temperature-high → fan group ON |

## Raw evidence

The canonical raw logs, per-gate evidence, screenshots and verification outputs are preserved as GitHub Actions artifacts from the successful full verification run. Repository summaries:

- `docs/TEST_MATRIX.md`
- `docs/FINAL_ACCEPTANCE_REPORT.md`
- `docs/ASSIGNMENT4_REPORT.md`

## Evidence classes

1. **Live ARM64 deployment evidence** — same stack running on Seeed Studio / Tegra Linux.
2. **Saved browser-state evidence** — pages reconstructed from Firefox DOM snapshots saved during the live session.
3. **CI / Playwright evidence** — automated scenarios from the canonical 34/34 acceptance run.
