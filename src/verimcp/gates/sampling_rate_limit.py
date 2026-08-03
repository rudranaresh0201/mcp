"""Rate-limits sampling/createMessage: devmcp asking the Client to run an LLM
completion has no correctness question attached to it, but it's a real cost
and abuse surface -- a misbehaving or compromised backend could otherwise
spam the Host's model unbounded. See docs/adr/0001, "Policy gates"."""
import time
from collections import deque
from typing import Any

from verimcp.gates.base import RequestGate


class SamplingRateLimitGate(RequestGate):
    def __init__(self, limit: int = 10, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    def applies_to(self, method: str) -> bool:
        return method == "sampling/createMessage"

    def check(self, request: dict[str, Any]) -> dict[str, Any] | None:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] >= self._window_seconds:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._limit:
            return self._deny(
                request,
                f"sampling rate limit exceeded ({self._limit} per {self._window_seconds:.0f}s)",
            )

        self._timestamps.append(now)
        return None
