"""Per-key cooldown, so one person does not trigger an alert on every frame."""

from __future__ import annotations

import time
from typing import Callable, Hashable


class Cooldown:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds = seconds
        self.clock = clock
        self._last: dict[Hashable, float] = {}

    def ready(self, key: Hashable) -> bool:
        """True (and restart the timer) if `key` has not fired in the last `seconds`."""
        now = self.clock()
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            return False
        self._last[key] = now
        return True
