# MQTT Contract (orchid/v1)

Protocol: **MQTT 5** over **TLS 1.2+** (TLS 1.3 negotiated in tests), port 8883 only. No
plaintext listener exists (1883 refused - G05). Brokers: Eclipse Mosquitto **2.1.2**.

## 1. Topics

| Topic | Publisher (principal) | Subscriber | QoS | Retain | Expiry |
|---|---|---|---|---|---|
| `orchid/v1/{site}/sensor/{device}/telemetry/{metric}` | `{site}-{device}` | `{site}-controller`; central (raw) via bridge | 0 | no | - |
| `orchid/v1/{site}/gateway/telemetry/{device}/{metric}` **[impl choice]** | `{site}-controller` | central historian via bridge | 1 | no | - |
| `orchid/v1/{site}/actuator/{device}/state` | `{site}-controller` | central | 1 | **yes** | - |
| `orchid/v1/{site}/control/fan_group/command` | `central-api` | `{site}-controller` via bridge | 1 | no | **Message Expiry = TTL** |
| `orchid/v1/{site}/control/dehumidifier_group/command` | `central-api` | `{site}-controller` | 1 | no | Message Expiry = TTL |
| `orchid/v1/{site}/control/mode/command` **[impl choice]** | `central-api` | `{site}-controller` | 1 | no | Message Expiry = TTL |
| `orchid/v1/{site}/control/{group\|mode}/ack` | `{site}-controller` | central | 1 | no | - |
| `orchid/v1/{site}/gateway/heartbeat` | `{site}-controller` (+ its LWT) | central | 1 | **yes** | - |
| `orchid/v1/{site}/gateway/bridge` | site broker bridge notifications (+ bridge LWT on central) | central | 1 | yes | - |
| `orchid/v1/{site}/alerts` | `{site}-controller` | central (persisted + audited) | 1 | no | - |
| `orchid/v1/{site}/events` | `{site}-controller` | central (persisted + audited) | 1 | no | - |
| `orchid/v1/central/audit` **[impl choice]** | `central-api` | historian | 1 | no | - |

`{site}` = `site01`..`site06`; `{device}` = `temp01`, `humidity01`, `fan01`, `fan02`,
`dehumidifier01`, `dehumidifier02`; `{metric}` = `temperature` | `humidity`.

Two topics were added to the V2.2 list because the spec requires the behaviour but names no topic:
gateway-validated telemetry (the spec requires `gateway_ingest_ts/quality/validation_result` to
reach central) and the mode/override command (spec section 5 "mode / override commands").

## 2. Schemas

### 2.1 Telemetry (device -> controller), strict, `extra=forbid`
```json
{"schema_version":"1.0","site_id":"site01","device_id":"temp01",
 "device_ts":"2026-11-17T00:00:00+08:00","seq":1042,"metric":"temperature","value":21.4,"unit":"C"}
```
`value` must be a JSON number (not string/bool/null), finite; `device_ts` must carry an offset;
`seq` is a non-negative integer that must strictly increase per device (the simulator seeds it
from epoch-ms so it survives restarts); `unit` is `C` for temperature and `%RH` for humidity.
Max 1024 bytes, max nesting depth 4.

### 2.2 Gateway telemetry (controller -> central)
Telemetry fields plus `gateway_ingest_ts`, `quality` (GOOD/SUSPECT/BAD/STALE),
`validation_result` (VALIDATED, SCHEMA_INVALID, MALFORMED_JSON, OVERSIZE, TOO_DEEP,
NON_FINITE, UNIT_INVALID, IDENTITY_MISMATCH, RANGE_FAIL, TIMESTAMP_FUTURE, STALE,
REPLAY_OR_DUPLICATE, SPIKE_PENDING, FROZEN), `stage` (REJECTED/NORMALIZED/VALIDATED),
`control_eligible`. Central adds `central_ingest_ts` and `late`.

### 2.3 Group command (central -> site)
```json
{"command_id":"<uuid>","issued_at":"...Z","expires_at":"...Z","desired_state":"ON",
 "operator":"operator","reason":"TEMP_HIGH"}
```
Rules: `extra=forbid`; `expires_at > issued_at`; `expires_at - issued_at <= 300 s`;
`issued_at <= now + 5 s`; `now > expires_at` -> **DROP + COMMAND_EXPIRED** (ACK result EXPIRED);
repeated `command_id` -> ACK result DUPLICATE, nothing re-applied.

### 2.4 Mode command
`{"command_id","issued_at","expires_at","mode":"AUTO|MANUAL_OVERRIDE|MAINTENANCE","operator","reason","ttl_seconds"?}`
FAILSAFE is never remotely settable.

### 2.5 ACK
```json
{"command_id":"...","site_id":"site01","group":"fan_group","requested_state":"ON","result":"APPLIED",
 "members":{"fan01":"ON","fan02":"ON"},"source":"REMOTE|AUTO_RULE|FAILSAFE|MAINTENANCE|HARD_SAFETY_INTERLOCK",
 "reason":"...","mode":"MANUAL_OVERRIDE","operator":"operator","acked_at":"..."}
```
`result` in APPLIED, PARTIAL_FAILURE, FAILED, REJECTED (+`detail`: INTERLOCK_ACTIVE,
MAINTENANCE_ACTIVE, FAILSAFE_ACTIVE), EXPIRED, DUPLICATE (+`original_result`), INVALID.

### 2.6 Actuator state (retained)
`{"device_id","group","state":"ON|OFF|UNKNOWN","commanded","observed_at","quality":"GOOD|BAD","source":"site01-controller"}`
Republished every 10 s so `observed_at` stays fresh while the controller is alive.

### 2.7 Heartbeat (retained) and LWT
Birth/heartbeat every 5 s: `{"status":"ONLINE","health":"OK|DEGRADED","issues":[...],"mode","ts","seq","sensors":{...}}`.
LWT (retained, QoS1): `{"status":"OFFLINE","reason":"LWT"}`; graceful stop publishes `reason":"SHUTDOWN"`.

### 2.8 Events / alerts (untrusted at central)
`{"event_id":"[A-Za-z0-9_.:-]{1,64}","event_type":"<allowlisted>","ts":"<claimed>","details":{...scalars}}`
Central drops claimed `principal/severity/ingest_ts/site`, assigns them server-side, max 4096 bytes.

## 3. Session semantics

| Client | client_id | clean_start | Session Expiry |
|---|---|---|---|
| site controller | `{site}-controller` | false | **120 s** (queued commands survive a short restart, never forever) |
| historian | `historian` | false | 3600 s (catch-up after outage) |
| central-api | `central-api` | true | 0 |
| simulators / probe | per device / random | true | 0 |
| bridge | `bridge-{site}` | cleansession false, local_cleansession false | `bridge_session_expiry_interval 600` |

Brokers additionally enforce `persistent_client_expiration 2h`.

**Stale command resurrection** is prevented in two independent layers (G18): MQTT 5 Message Expiry
(broker discards the queued message) and the application `expires_at` check at the consumer
(`COMMAND_EXPIRED`, ACK `EXPIRED`). `retain=false` alone is not relied upon.

## 4. ACL (Dynamic Security)

Default ACL access: `publishClientSend deny, publishClientReceive deny, subscribe deny,
unsubscribe deny`. No dynsec admin client exists. Every production principal is **bound to a
fixed client id** (dynsec `clientid`), so a stolen credential cannot connect as another
principal's client id and take over its session (verified in G07). Grants (generated from
`services/orchid/core/acl_model.py`):

| Broker | Principal | Publish | Subscribe/receive |
|---|---|---|---|
| central | `central-api` | `orchid/v1/+/control/+/command`, `orchid/v1/central/audit` | - |
| central | `historian` | - | exactly its 16 subscription filters (10 `orchid/v1/+/...` filters, `central/audit`, `$SYS/broker/log/#`, uptime and 4 load/client metrics) |
| central | `bridge-siteNN` | **concrete** registered topics of siteNN only: 2 raw + 2 gateway telemetry, 4 actuator states, 3 acks, heartbeat, bridge, alerts, events (no `+`) | the 3 concrete command topics of siteNN |
| central/site | `health` | - | `$SYS/broker/uptime` (container healthcheck) |
| site | `siteNN-controller` | gateway telemetry, state, ack, heartbeat, alerts, events of siteNN | siteNN telemetry, siteNN commands |
| site | `siteNN-temp01` | its own telemetry topic only | - |
| site | `siteNN-humidity01` | its own telemetry topic only | - |
| central, site01, site02 | `probe-*` (test only) | same ACL as the principal they stand in for, no client-id binding; credentials mounted only into the test-profile `lab-probe`; omit with `bootstrap_secrets.py --no-test-principals` | |

Denied publishes get PUBACK `0x87`, denied subscriptions SUBACK `0x87` (verified G09).

## 5. Bridge directions (site broker config)

```
topic orchid/v1/@SITE@/sensor/+/telemetry/+ out 1
topic orchid/v1/@SITE@/gateway/telemetry/+/+ out 1
topic orchid/v1/@SITE@/actuator/+/state out 1
topic orchid/v1/@SITE@/control/+/ack out 1
topic orchid/v1/@SITE@/gateway/heartbeat out 1
topic orchid/v1/@SITE@/alerts out 1
topic orchid/v1/@SITE@/events out 1
topic orchid/v1/@SITE@/control/fan_group/command in 1
topic orchid/v1/@SITE@/control/dehumidifier_group/command in 1
topic orchid/v1/@SITE@/control/mode/command in 1
```
`#` is never bridged. TLS to `central-broker:8883` with `bridge_cafile` and
`bridge_insecure false`; `bridge_protocol_version mqttv50`.

## 6. Broker resource limits

| Setting | Central | Site | Why |
|---|---|---|---|
| `max_packet_size` | 16384 | 16384 | largest legit payload is an event (4 KiB app limit) |
| `max_queued_messages` | 20000 | 5000 | historian catch-up / ~10 min WAN outage at site |
| `max_queued_bytes` | 16 MiB | 8 MiB | memory bound |
| `max_inflight_messages` | 20 | 20 | QoS1 window |
| `max_keepalive` | 120 | 120 | bound dead-peer detection |
| `max_qos` (listener) | 1 | 1 | QoS2 not used |
| `max_connections` (listener) | 64 | 32 | lab principals + headroom |

`message_size_limit` is deprecated in Mosquitto 2.x and superseded by `max_packet_size`.
