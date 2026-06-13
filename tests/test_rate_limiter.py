"""Tests for rate limiting functionality."""

import time

import pytest

from track_manager.rate_limiter import RateLimiter


def test_rate_limiter_basic():
    """Test basic rate limiting."""
    limiter = RateLimiter(calls_per_second=2.0, burst_size=2)

    # Should allow burst
    assert limiter.acquire(blocking=False) is True
    assert limiter.acquire(blocking=False) is True

    # Should block after burst
    assert limiter.acquire(blocking=False) is False

    # Should allow after waiting
    time.sleep(0.6)  # Wait for 1 token to refill
    assert limiter.acquire(blocking=False) is True


def test_rate_limiter_blocking():
    """Test blocking rate limiting."""
    limiter = RateLimiter(calls_per_second=5.0, burst_size=1)

    # Use up token
    assert limiter.acquire(blocking=False) is True

    # Should wait and succeed
    start = time.monotonic()
    assert limiter.acquire(blocking=True) is True
    elapsed = time.monotonic() - start

    # Should have waited ~0.2s (1/5)
    assert 0.15 < elapsed < 0.35


def test_rate_limiter_timeout():
    """Test timeout behavior."""
    limiter = RateLimiter(calls_per_second=1.0, burst_size=1)

    # Use up token
    assert limiter.acquire(blocking=False) is True

    # Should timeout
    start = time.monotonic()
    assert limiter.acquire(blocking=True, timeout=0.2) is False
    elapsed = time.monotonic() - start

    # Should have waited ~0.2s before timeout
    assert 0.15 < elapsed < 0.35


def test_rate_limiter_stats():
    """Test statistics tracking."""
    limiter = RateLimiter(calls_per_second=2.0, burst_size=5)

    # Make some calls
    for _ in range(3):
        limiter.acquire()

    stats = limiter.get_stats()

    assert stats["calls_last_minute"] == 3
    assert stats["tokens_available"] == 2  # 5 - 3 = 2
    assert stats["burst_size"] == 5
    assert stats["rate"] == 2.0


def test_rate_limiter_refill():
    """Test token refill over time."""
    limiter = RateLimiter(calls_per_second=10.0, burst_size=2)

    # Use up tokens
    limiter.acquire()
    limiter.acquire()

    # Wait for refill
    time.sleep(0.3)  # Should refill 3 tokens (10/sec * 0.3s = 3)

    stats = limiter.get_stats()
    # Should have 2 (burst cap)
    assert stats["tokens_available"] == 2
