"""AUTO rule: hysteresis + N-of-M debounce over VALIDATED samples only."""
from __future__ import annotations

from collections import deque
from datetime import datetime


class HysteresisNofM:
    """ON when >= n of the last m fresh samples are >= high; OFF when >= n are <= low.

    Values between low and high hold the current state (no threshold chatter).
    Samples older than max_age_s are ignored so stale history cannot drive control.
    """

    def __init__(self, high: float, low: float, n: int, m: int, max_age_s: float) -> None:
        if not low < high:
            raise ValueError("low must be < high")
        if not 0 < n <= m:
            raise ValueError("require 0 < n <= m")
        self.high = high
        self.low = low
        self.n = n
        self.max_age_s = max_age_s
        self._window: deque[tuple[datetime, float]] = deque(maxlen=m)
        self.state = False

    def add(self, value: float, at: datetime) -> None:
        self._window.append((at, value))

    def _fresh(self, now: datetime) -> list[float]:
        return [v for ts, v in self._window if (now - ts).total_seconds() <= self.max_age_s]

    def fresh_count(self, now: datetime) -> int:
        return len(self._fresh(now))

    def decide(self, now: datetime) -> bool:
        values = self._fresh(now)
        if not self.state and sum(v >= self.high for v in values) >= self.n:
            self.state = True
        elif self.state and sum(v <= self.low for v in values) >= self.n:
            self.state = False
        return self.state

    def reset(self) -> None:
        self._window.clear()
