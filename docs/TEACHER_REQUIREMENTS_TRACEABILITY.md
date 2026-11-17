# Assignment #4 — Teacher Requirements Traceability

Source: **W10_Communication_Protocols.pptx**, slides/pages 20–28.

This document separates three things:

1. what the teacher explicitly requires;
2. how this repository satisfies the requirement;
3. what is an engineering extension beyond the minimum assignment scope.

## 1. Assignment objective

The teacher defines Assignment #4 as **MQTT practice and design**. The required learning objectives are:

- install an MQTT broker;
- configure username/password authentication;
- configure TLS encryption;
- use MQTT **publish** to control remote equipment;
- use MQTT **subscribe** to receive remote equipment information;
- design MQTT topics;
- submit a written report;
- due **2026-11-25 before class by email**.

## 2. Scenario requirements

The scenario is an orchid farm with **six sites**.

Per site:

- 1 temperature sensor;
- 1 humidity sensor;
- 2 fans;
- 2 dehumidifiers.

Targets:

- temperature target: **20 °C**;
- humidity target: **60 %RH**.

Required behavior:

- temperature too high → turn on **both fans**;
- humidity too high → turn on **both dehumidifiers**;
- subscribe to real-time temperature for all six sites;
- subscribe to real-time humidity for all six sites;
- publish commands that control the two devices as a group.

### Source inconsistency in slide 21

Slide 21 first says:

- humidity too high → turn on two dehumidifiers.

But the final bullet under Topic Design says:

- temperature too high → publish to turn on two dehumidifiers.

These two statements conflict. The implementation follows the semantically consistent scenario definition:

> **humidity high → dehumidifier group ON**

The inconsistency is documented rather than silently corrected.

## 3. Requirement → implementation → evidence

| Teacher requirement | Implementation | Evidence / verification | Scope |
|---|---|---|---|
| MQTT broker installation | 1 central + 6 site Mosquitto brokers, containerized with `eclipse-mosquitto:2.1.2-alpine` | G01 build, G02 health, architecture | Required |
| Username / password | Anonymous disabled; unique credentials; Mosquitto Dynamic Security | G06 anonymous denied, G07 wrong password denied, G08 correct identity accepted | Required + hardened |
| TLS | MQTT TLS on 8883; lab CA; hostname/SAN verification | G05 TLS verified | Required + hardened |
| Publish remote control | Central API / automatic rules publish site-scoped group commands | G14/G15 auto control, G17 command lifecycle, G26a operator E2E | Required |
| Subscribe remote information | Site controller subscribes telemetry; Historian receives bridged telemetry/state | G10 six-site telemetry; UI overview/detail | Required |
| Topic Design | Versioned site-scoped namespace `orchid/v1/{site}/...` | `docs/MQTT_CONTRACT.md` | Required |
| Six sites | `site01`–`site06`, each with broker/controller/simulator | 23-service deployment, six-site UI | Required |
| Temperature target 20 °C | Configuration uses 20 °C as target/setpoint | Dashboard + config | Required |
| Humidity target 60 %RH | Configuration uses 60 %RH as target/setpoint | Dashboard + config | Required |
| 2 fans per site | `fan_group` controls fan01 + fan02 together | G14, UI warning/fans-on | Required |
| 2 dehumidifiers per site | `dehumidifier_group` controls both together | G15, site06 evidence | Required |
| Written report | `docs/ASSIGNMENT4_REPORT.md` + team Word report | repository docs | Required |
| Due date | 2026-11-25 before class, email electronic file | teacher W10 slide 20 | Required |

## 4. Difference from the teacher's reference procedure

The teacher's reference slides show direct Ubuntu installation:

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl start mosquitto
```

and a reference TLS setup using OpenSSL.

This project satisfies the same functional requirements using a more reproducible containerized deployment:

- official Mosquitto container image instead of host-level apt installation;
- generated lab CA/certificates instead of manually calling OpenSSL;
- seven isolated broker instances instead of one host broker;
- explicit Docker networks and no host-exposed MQTT listener.

The reference procedure is treated as a **reference implementation**, not as a constraint that the solution must install Mosquitto directly into the host OS.

## 5. Target values vs. engineering control thresholds

The assignment target remains **20 °C / 60 %RH**.

To avoid actuator chatter around the exact setpoint, the engineering implementation adds hysteresis:

- temperature ON at 20.5 °C, OFF at 19.5 °C;
- humidity ON at 62 %RH, OFF at 58 %RH.

This is an engineering extension around the teacher's target values, not a replacement for them. If a strict threshold demonstration is required, the configurable thresholds can be set to the target values.

## 6. Engineering extensions beyond minimum assignment scope

The following are intentionally beyond the teacher's minimum requirements:

- 15 isolated Docker networks;
- six local brokers + central MQTT bridge topology;
- local autonomy during central outage;
- Dynamic Security default-deny ACL;
- MQTT 5 Message Expiry and application `expires_at`;
- command idempotency and ACK lifecycle;
- data validation / stale / replay / plausibility gates;
- viewer/operator RBAC;
- HMAC-chained audit evidence;
- log injection/XSS/flood handling;
- fault injection and 34 acceptance gates.

These extensions should be presented as **engineering hardening**, not as additional teacher requirements.
