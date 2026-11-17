# Report Evidence Index

Canonical UI screenshots are already tracked under:

`evidence/generated/ui/`

| Evidence | File |
|---|---|
| Login / RBAC | `00-login.png` |
| Six-site Overview | `01-overview.png` |
| Temperature warning → fans ON | `02-site02-warning-fans-on.png` |
| Partial actuator failure → DEGRADED | `03-site06-partial-failure-degraded.png` |
| STALE sensor | `04-site05-stale.png` |
| OFFLINE / STALE / DEGRADED Overview | `05-overview-offline-stale-degraded.png` |
| Stored XSS shown as inert text | `06-system-security-xss-as-text.png` |
| Telemetry trend | `07-site02-detail-trend.png` |
| Operator command → ACK | `08-operator-control-ack.png` |

## Teacher-requirement mapping

See:

- `docs/TEACHER_REQUIREMENTS_TRACEABILITY.md`
- `docs/TEAMMATE_DEMO_GUIDE.md`
- `docs/TEST_MATRIX.md`
- `docs/FINAL_ACCEPTANCE_REPORT.md`

## Live ARM64 evidence

The final classroom/demo package also contains live Seeed Studio / NVIDIA Tegra ARM64 screenshots proving:

- all 23 services healthy;
- live API `mqtt_connected=true`;
- live six-site overview.

Those host screenshots are kept in the team-share evidence package. The public repository intentionally excludes any screenshot that reveals active local credentials.
