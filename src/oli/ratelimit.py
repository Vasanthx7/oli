"""A tiny async token-bucket rate limiter.

Groq's free tier caps requests per minute; browser-use makes one LLM call per
step, so a single browse can burst past the ceiling and hit 429s with long
back-offs. Pacing those calls to just under the limit turns hard 429 errors into
smooth, small waits.

`acquire()` blocks (awaits) until a token is available, so callers never exceed
the configured rate. A limit of 0 disables limiting entirely.
"""

import asyncio
import time


class RateLimiter:
    def __init__(self, per_minute: int):
        self.enabled = per_minute > 0
        self.capacity = float(max(per_minute, 1))
        self.refill_per_sec = self.capacity / 60.0
        self.tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.refill_per_sec)
            self._last = now
            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.refill_per_sec
                await asyncio.sleep(wait)
                self.tokens = 0.0
                self._last = time.monotonic()
            else:
                self.tokens -= 1.0
