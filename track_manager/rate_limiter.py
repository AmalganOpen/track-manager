"""Rate limiting utilities for API calls."""

import json
import os
import sys
import time
from pathlib import Path
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


class PersistentRateLimiter:
    """Sliding-window rate limiter backed by a file so limits persist across processes.

    Stores a list of recent call timestamps in a JSON file and enforces a
    maximum number of calls within a rolling time window.
    """

    def __init__(self, max_calls: int, window_seconds: float, state_file: Path):
        self.max_calls = max_calls
        self.window = window_seconds
        self.state_file = state_file
        self.lock = Lock()

    def _read_timestamps(self) -> list:
        try:
            if self.state_file.exists():
                return json.loads(self.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        return []

    def _write_timestamps(self, timestamps: list) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(timestamps))
        except OSError:
            pass

    def acquire(self) -> None:
        """Block until a call slot is available within the rate limit window."""
        with self.lock:
            while True:
                now = time.time()
                timestamps = self._read_timestamps()
                # Drop timestamps outside the window
                timestamps = [t for t in timestamps if now - t < self.window]

                # Honour any pinned cooldown timestamp written by note_throttle()
                cooldown_until = self._read_cooldown()
                if cooldown_until and now < cooldown_until:
                    wait = cooldown_until - now + 0.05
                    print(
                        f"⏳ song.link cooldown active: waiting {wait:.1f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    continue

                if len(timestamps) < self.max_calls:
                    timestamps.append(now)
                    self._write_timestamps(timestamps)
                    return

                # Wait until the oldest timestamp falls outside the window
                oldest = min(timestamps)
                wait = self.window - (now - oldest) + 0.05  # small buffer
                print(f"⏳ song.link rate limit ({self.max_calls}/{self.window:.0f}s): waiting {wait:.1f}s...", file=sys.stderr)
                time.sleep(wait)

    def _cooldown_file(self) -> Path:
        return self.state_file.with_suffix(self.state_file.suffix + ".cooldown")

    def _read_cooldown(self) -> float:
        try:
            f = self._cooldown_file()
            if f.exists():
                return float(f.read_text().strip())
        except (OSError, ValueError):
            pass
        return 0.0

    def note_throttle(self, retry_after_seconds: float) -> None:
        """Record an upstream-imposed cooldown so future acquires honour it.

        Called when a 429 is observed so the next call (this process or any
        other) waits out the server-side window before banging on the API.
        """
        try:
            until = time.time() + max(retry_after_seconds, 0.0)
            self._cooldown_file().parent.mkdir(parents=True, exist_ok=True)
            self._cooldown_file().write_text(str(until))
        except OSError:
            pass


_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "track-manager"

# Global rate limiters for each service
# Note: Spotify rate limit is very conservative (1/sec) because spotdl
# makes many internal calls during playlist fetching. Better to be slow
# and reliable than hit rate limits.
_spotify_limiter = RateLimiter(calls_per_second=1.0, burst_size=3)
# song.link: 10 req/min hard limit. Persistent across invocations so rapid
# successive `tm` runs don't collectively exceed the limit.
_songlink_limiter = PersistentRateLimiter(
    max_calls=6,           # documented limit is 10/min but observed 429s at 8/min
    window_seconds=60.0,
    state_file=_CACHE_DIR / "songlink_calls.json",
)
_dab_limiter = RateLimiter(calls_per_second=2.0, burst_size=5)
_tidal_limiter = RateLimiter(calls_per_second=2.0, burst_size=5)  # Conservative for public APIs


def spotify_rate_limit(show_progress: bool = False) -> None:
    """Apply Spotify API rate limiting."""
    if show_progress:
        stats = _spotify_limiter.get_stats()
        if stats['tokens_available'] < 1:
            print("⏳ Rate limiting active (Spotify API)...", file=sys.stderr)
    _spotify_limiter.acquire()


def songlink_rate_limit(show_progress: bool = False) -> None:
    """Apply song.link API rate limiting (persistent across invocations)."""
    _songlink_limiter.acquire()


def songlink_note_throttle(retry_after_seconds: float) -> None:
    """Record a server-imposed cooldown so the next call waits it out."""
    _songlink_limiter.note_throttle(retry_after_seconds)


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
    now = time.time()
    sl_timestamps = _songlink_limiter._read_timestamps()
    sl_recent = [t for t in sl_timestamps if now - t < _songlink_limiter.window]
    return {
        'spotify': _spotify_limiter.get_stats(),
        'songlink': {
            'calls_last_minute': len(sl_recent),
            'max_calls': _songlink_limiter.max_calls,
            'window_seconds': _songlink_limiter.window,
        },
        'dab_music': _dab_limiter.get_stats(),
        'tidal': _tidal_limiter.get_stats(),
    }
