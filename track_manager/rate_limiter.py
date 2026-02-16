"""Rate limiting utilities for API calls."""

import time
from threading import Lock
from collections import deque
from typing import Optional


class RateLimiter:
    """Token bucket rate limiter with thread safety."""

    def __init__(self, calls_per_second: float, burst_size: Optional[int] = None):
        """
        Initialize rate limiter.

        Args:
            calls_per_second: Maximum sustained calls per second
            burst_size: Maximum burst size (defaults to calls_per_second)
        """
        self.rate = calls_per_second
        self.burst = burst_size or int(calls_per_second)
        self.tokens = self.burst
        self.last_update = time.monotonic()
        self.lock = Lock()
        self.call_times = deque(maxlen=100)  # Track recent calls for stats

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire permission to make an API call.

        Args:
            blocking: If True, wait until a token is available
            timeout: Maximum time to wait in seconds (None = infinite)

        Returns:
            True if acquired, False if timeout or non-blocking and no tokens
        """
        start_time = time.monotonic()

        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_update

                # Refill tokens based on elapsed time
                self.tokens = min(
                    self.burst, self.tokens + elapsed * self.rate
                )
                self.last_update = now

                if self.tokens >= 1:
                    self.tokens -= 1
                    self.call_times.append(now)
                    return True

                if not blocking:
                    return False

                # Calculate wait time for next token
                wait_time = (1 - self.tokens) / self.rate

            # Check timeout
            if timeout is not None:
                remaining = timeout - (time.monotonic() - start_time)
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

            time.sleep(wait_time)

    def get_stats(self) -> dict:
        """Get statistics about recent API calls."""
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            
            # Update tokens before reading stats
            self.tokens = min(
                self.burst, self.tokens + elapsed * self.rate
            )
            self.last_update = now
            
            recent_calls = [t for t in self.call_times if now - t < 60]
            
            return {
                'calls_last_minute': len(recent_calls),
                'tokens_available': int(self.tokens),
                'burst_size': self.burst,
                'rate': self.rate
            }


# Global rate limiters for each service
# Note: Spotify rate limit is very conservative (1/sec) because spotdl
# makes many internal calls during playlist fetching. Better to be slow
# and reliable than hit rate limits.
_spotify_limiter = RateLimiter(calls_per_second=1.0, burst_size=3)
_songlink_limiter = RateLimiter(calls_per_second=0.15, burst_size=2)  # ~9/min, conservative
_dab_limiter = RateLimiter(calls_per_second=2.0, burst_size=5)
_tidal_limiter = RateLimiter(calls_per_second=2.0, burst_size=5)  # Conservative for public APIs


import sys


def spotify_rate_limit(show_progress: bool = False) -> None:
    """Apply Spotify API rate limiting."""
    if show_progress:
        stats = _spotify_limiter.get_stats()
        if stats['tokens_available'] < 1:
            print("⏳ Rate limiting active (Spotify API)...", file=sys.stderr)
    _spotify_limiter.acquire()


def songlink_rate_limit(show_progress: bool = False) -> None:
    """Apply song.link API rate limiting."""
    if show_progress:
        stats = _songlink_limiter.get_stats()
        if stats['tokens_available'] < 1:
            print("⏳ Rate limiting active (song.link API)...", file=sys.stderr)
    _songlink_limiter.acquire()


def dab_rate_limit(show_progress: bool = False) -> None:
    """Apply DAB Music API rate limiting."""
    if show_progress:
        stats = _dab_limiter.get_stats()
        if stats['tokens_available'] < 1:
            print("⏳ Rate limiting active (DAB Music API)...", file=sys.stderr)
    _dab_limiter.acquire()


def tidal_rate_limit(show_progress: bool = False) -> None:
    """Apply TIDAL API rate limiting."""
    if show_progress:
        stats = _tidal_limiter.get_stats()
        if stats['tokens_available'] < 1:
            print("⏳ Rate limiting active (TIDAL API)...", file=sys.stderr)
    _tidal_limiter.acquire()


def get_rate_limit_stats() -> dict:
    """Get statistics for all rate limiters."""
    return {
        'spotify': _spotify_limiter.get_stats(),
        'songlink': _songlink_limiter.get_stats(),
        'dab_music': _dab_limiter.get_stats(),
        'tidal': _tidal_limiter.get_stats()
    }
