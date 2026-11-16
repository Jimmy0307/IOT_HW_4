"""Server-side event policy: the ONLY source of severity.

Device-claimed severity is never trusted. Event types not in the allowlist are
rejected at ingress (and the rejection itself is recorded as SECURITY).
"""
from __future__ import annotations

SEVERITY_POLICY: dict[str, str] = {
    # control lifecycle
    "COMMAND_APPLIED": "INFO",
    "COMMAND_EXPIRED": "WARNING",
    "COMMAND_REJECTED": "WARNING",
    "COMMAND_DUPLICATE": "NOTICE",
    "COMMAND_INVALID": "SECURITY",
    "MODE_CHANGED": "NOTICE",
    "OVERRIDE_EXPIRED": "NOTICE",
    "INTERLOCK_ACTIVE": "CRITICAL",
    "INTERLOCK_CLEARED": "NOTICE",
    "FAILSAFE_ENTERED": "CRITICAL",
    "FAILSAFE_EXITED": "NOTICE",
    # actuators
    "ACTUATOR_PARTIAL_FAILURE": "CRITICAL",
    "ACTUATOR_FAILURE": "CRITICAL",
    "ACTUATOR_RECOVERED": "NOTICE",
    # data quality
    "TELEMETRY_REJECTED": "WARNING",
    "SENSOR_STALE": "WARNING",
    "SENSOR_FROZEN": "WARNING",
    "SENSOR_RECOVERED": "NOTICE",
    "PLAUSIBILITY_FAIL": "WARNING",
    "REPLAY_DETECTED": "SECURITY",
    "IDENTITY_MISMATCH": "SECURITY",
    # site lifecycle / diagnostics
    "CONTROLLER_STARTED": "NOTICE",
    "LOCAL_LOG_DEGRADED": "WARNING",
    "DIAGNOSTIC": "INFO",
    # central-api audit
    "LOGIN_SUCCESS": "NOTICE",
    "LOGIN_FAILURE": "SECURITY",
    "AUTHZ_DENIED": "SECURITY",
    "OPERATOR_COMMAND": "NOTICE",
    "OPERATOR_MODE_CHANGE": "NOTICE",
    # central ingest (server-generated only)
    "EVENT_SCHEMA_REJECTED": "SECURITY",
    "RATE_LIMITED": "WARNING",
    "UNTRUSTED_CLAIM_DROPPED": "SECURITY",
    "BROKER_AUTH_FAILURE": "SECURITY",
    "BROKER_ACL_DENIED": "SECURITY",
    "TOPIC_IDENTITY_MISMATCH": "SECURITY",
}

# Types a site (via its bridge) may emit. Server-generated types are excluded so a
# device can never forge e.g. RATE_LIMITED or BROKER_ACL_DENIED records.
SERVER_ONLY = {
    "EVENT_SCHEMA_REJECTED", "RATE_LIMITED", "UNTRUSTED_CLAIM_DROPPED", "BROKER_AUTH_FAILURE",
    "BROKER_ACL_DENIED", "TOPIC_IDENTITY_MISMATCH", "LOGIN_SUCCESS", "LOGIN_FAILURE",
    "AUTHZ_DENIED", "OPERATOR_COMMAND", "OPERATOR_MODE_CHANGE",
}
ALLOWED_EVENT_TYPES = frozenset(k for k in SEVERITY_POLICY if k not in SERVER_ONLY)
CENTRAL_API_EVENT_TYPES = frozenset({"LOGIN_SUCCESS", "LOGIN_FAILURE", "AUTHZ_DENIED",
                                     "OPERATOR_COMMAND", "OPERATOR_MODE_CHANGE"})
CRITICAL_SEVERITIES = frozenset({"CRITICAL", "SECURITY"})


def severity_for(event_type: str) -> str:
    return SEVERITY_POLICY.get(event_type, "SECURITY")


def is_critical(severity: str) -> bool:
    return severity in CRITICAL_SEVERITIES
