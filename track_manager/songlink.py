"""song.link / Odesli integration for finding tracks across platforms."""

import os
import sys
import time
from typing import Dict, Optional

import requests

from .rate_limiter import songlink_note_throttle, songlink_rate_limit

API_BASE = "https://api.song.link/v1-alpha.1/links"

# Cap total time spent on a single lookup (cooldown + in-call retries).
_MAX_WAIT_PER_CALL = 45.0

# Process-wide: once we know the API is unusable, skip remaining calls so a
# batch download doesn't 401 + rate-limit wait on every track.
_disabled_reason: Optional[str] = None


def reset_state() -> None:
    """Reset the process-wide disable flag (for tests)."""
    global _disabled_reason
    _disabled_reason = None


def is_disabled() -> bool:
    """Return True if song.link lookups should be skipped for this process."""
    return _disabled_reason is not None


def api_key_from_env() -> Optional[str]:
    """Read an Odesli key from SONGLINK_API_KEY or ODESLI_API_KEY."""
    for name in ("SONGLINK_API_KEY", "ODESLI_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def resolve_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """Return a configured API key, or None for the unauthenticated tier.

    Official docs: no key is required; if provided it is sent as the ``key``
    query param for higher rate limits.
    """
    key = (api_key or "").strip() or api_key_from_env()
    return key or None


def _disable(reason: str) -> None:
    global _disabled_reason
    if _disabled_reason is not None:
        return
    _disabled_reason = reason
    print(f"⚠️ song.link disabled: {reason}", file=sys.stderr)


_PUBLIC_ACCESS_DEPRECATED_REASON = (
    "401 PUBLIC_API_ACCESS_DEPRECATED (unauthenticated). "
    "Odesli docs still describe a no-key tier; the live API is rejecting it. "
    "If you have a key, set songlink.api_key or SONGLINK_API_KEY "
    "(sent as the key query param). Request one from developers@song.link"
)


def _response_code(response: requests.Response) -> Optional[str]:
    try:
        data = response.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        code = data.get("code")
        if isinstance(code, str) and code:
            return code
    return None


def fetch_links(
    params: dict,
    *,
    api_key: Optional[str] = None,
    session: Optional[requests.Session] = None,
    timeout: float = 10,
    max_retries: int = 1,
) -> Optional[dict]:
    """GET /links with rate limiting, optional API key, and 429 retry.

    Returns parsed JSON, or None on failure / when song.link is disabled.
    A key is optional; when set it is passed as the ``key`` query param.
    """
    if _disabled_reason is not None:
        return None

    query = dict(params)
    key = resolve_api_key(api_key)
    if key:
        query["key"] = key

    client = session or requests.Session()

    for attempt in range(max_retries + 1):
        try:
            songlink_rate_limit()
            response = client.get(API_BASE, params=query, timeout=timeout)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 30.0
                except ValueError:
                    wait = 30.0
                songlink_note_throttle(wait)
                if attempt >= max_retries or wait > _MAX_WAIT_PER_CALL:
                    print(
                        f"⚠️ song.link 429 (Retry-After {wait:.0f}s); skipping this track",
                        file=sys.stderr,
                    )
                    return None
                print(
                    f"⏳ song.link 429; sleeping {wait:.1f}s before single retry...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if response.status_code == 401:
                code = _response_code(response) or "401"
                if code == "INVALID_ACCESS_KEY":
                    _disable(
                        "API key rejected (INVALID_ACCESS_KEY). "
                        "Check songlink.api_key / SONGLINK_API_KEY"
                    )
                elif code == "PUBLIC_API_ACCESS_DEPRECATED":
                    _disable(_PUBLIC_ACCESS_DEPRECATED_REASON)
                else:
                    _disable(f"unauthorized ({code})")
                return None
            if response.status_code == 400:
                try:
                    detail = response.json().get("message") or response.text[:120]
                except Exception:
                    detail = response.text[:120]
                msg = "Track not indexed by song.link"
                if detail:
                    msg += f" ({detail})"
                print(f"ℹ️ {msg}", file=sys.stderr)
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            print(
                f"⚠️ song.link timeout after {timeout}s",
                file=sys.stderr,
            )
            return None
        except requests.RequestException as e:
            print(f"⚠️ song.link lookup failed: {e}", file=sys.stderr)
            return None
        except (ValueError, KeyError) as e:
            print(f"⚠️ song.link parsing failed: {e}", file=sys.stderr)
            return None
    return None


class SongLinkClient:
    """Client for song.link API."""

    API_BASE = API_BASE

    def __init__(
        self,
        timeout: int = 20,
        max_retries: int = 3,
        api_key: Optional[str] = None,
    ):
        """Initialize song.link client.

        Args:
            timeout: Request timeout in seconds (default: 20)
            max_retries: Maximum number of 429 retry attempts (default: 3)
            api_key: Optional Odesli API key, sent as the ``key`` query param
                for higher rate limits. Unauthenticated access is still attempted.
        """
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "track-manager/0.2.0",
            }
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_key = api_key

    def find_platforms(self, url: str) -> Dict[str, str]:
        """Find track on other platforms.

        Args:
            url: Input URL (any platform)

        Returns:
            Dictionary of platform -> URL mappings
        """
        data = fetch_links(
            {"url": url},
            api_key=self.api_key,
            session=self.session,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        if not data:
            return {}

        platforms = data.get("linksByPlatform", {})
        return {
            platform: info["url"]
            for platform, info in platforms.items()
            if "url" in info
        }

    def find_spotify_url(self, url: str) -> Optional[str]:
        """Find Spotify URL for given track.

        Args:
            url: Input URL (any platform)

        Returns:
            Spotify URL if found, None otherwise
        """
        platforms = self.find_platforms(url)
        return platforms.get("spotify")

    def get_track_info(self, url: str) -> Optional[Dict]:
        """Get track metadata from song.link.

        Args:
            url: Input URL (any platform)

        Returns:
            Dictionary with title, artist, etc. if found
        """
        data = fetch_links(
            {"url": url},
            api_key=self.api_key,
            session=self.session,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        if not data:
            return None

        entities = data.get("entitiesByUniqueId", {})
        if entities:
            entity = next(iter(entities.values()))
            return {
                "title": entity.get("title"),
                "artist": entity.get("artistName"),
            }

        return None
