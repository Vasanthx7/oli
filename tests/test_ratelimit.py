"""Tests for the async token-bucket rate limiter."""

import time

from oli.ratelimit import RateLimiter


async def test_disabled_limiter_never_waits():
    lim = RateLimiter(0)  # 0 => unlimited
    start = time.monotonic()
    for _ in range(100):
        await lim.acquire()
    assert time.monotonic() - start < 0.05


async def test_allows_burst_up_to_capacity():
    lim = RateLimiter(6000)  # 100/sec; a small burst should be instant
    start = time.monotonic()
    for _ in range(10):
        await lim.acquire()
    assert time.monotonic() - start < 0.1


async def test_paces_when_exhausted():
    lim = RateLimiter(6000)  # refill 100 tokens/sec => ~0.01s per token
    lim.tokens = 0.0  # drain the bucket
    lim._last = time.monotonic()
    start = time.monotonic()
    await lim.acquire()  # must wait for a token to refill
    assert time.monotonic() - start >= 0.005
