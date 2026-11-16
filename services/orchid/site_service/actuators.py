"""Local actuator outputs (relay DO + feedback DI), simulated.

Fans and dehumidifiers are modeled as hard-wired relay outputs of the site
controller with an observed-state feedback input, like a PLC. Faults can be
injected only through the controller's loopback-only fault interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FAULTS = ("STUCK_OFF", "STUCK_ON", "NO_FEEDBACK")


@dataclass(frozen=True)
class GroupResult:
    group: str
    requested_state: str
    result: str  # APPLIED | PARTIAL_FAILURE | FAILED
    members: dict[str, str] = field(default_factory=dict)


class RelayBank:
    def __init__(self, groups: dict[str, list[str]]) -> None:
        self.groups = {g: list(m) for g, m in groups.items()}
        self._commanded: dict[str, str] = {m: "OFF" for ms in groups.values() for m in ms}
        self._fault: dict[str, str | None] = {m: None for m in self._commanded}

    def members(self) -> list[str]:
        return list(self._commanded)

    def set_fault(self, member: str, fault: str | None) -> None:
        if member not in self._commanded:
            raise ValueError(f"unknown actuator {member}")
        if fault is not None and fault not in FAULTS:
            raise ValueError(f"unknown fault {fault}")
        self._fault[member] = fault

    def faults(self) -> dict[str, str | None]:
        return dict(self._fault)

    def observed(self, member: str) -> str:
        fault = self._fault[member]
        if fault == "NO_FEEDBACK":
            return "UNKNOWN"
        if fault == "STUCK_OFF":
            return "OFF"
        if fault == "STUCK_ON":
            return "ON"
        return self._commanded[member]

    def commanded(self, member: str) -> str:
        return self._commanded[member]

    def apply_group(self, group: str, desired: str) -> GroupResult:
        if group not in self.groups:
            raise ValueError(f"unknown group {group}")
        if desired not in ("ON", "OFF"):
            raise ValueError("desired must be ON or OFF")
        for member in self.groups[group]:  # drive both outputs in the same step
            self._commanded[member] = desired
        return self.read_group(group, desired)

    def read_group(self, group: str, desired: str) -> GroupResult:
        observed = {m: self.observed(m) for m in self.groups[group]}
        ok = sum(1 for v in observed.values() if v == desired)
        if ok == len(observed):
            result = "APPLIED"
        elif ok == 0:
            result = "FAILED"
        else:
            result = "PARTIAL_FAILURE"
        return GroupResult(group, desired, result, observed)
