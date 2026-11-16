"""Token-bucket rate limiter (injectable clock for tests)."""
from __future__ import annotations

import time
from typing import Callable


class TokenBucket:
    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        if rate <= 0 or burst <= 0:
            raise ValueError("rate and burst must be > 0")
        self.rate = float(rate)
        self.burst = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()

    def allow(self) -> bool:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
