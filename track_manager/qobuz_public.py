"""Public Qobuz API integration (no credentials required).

Uses community-hosted Qobuz proxies that wrap the official Qobuz API.
The flow is dead simple compared to the TIDAL hifi-api ecosystem:

    1. GET /api/get-music?q=<ISRC>&offset=0
       → returns search results; first item's `id` is the Qobuz track id.
    2. GET /api/download-music?track_id=<id>&quality=27
       → returns {"data": {"url": "<https://akamai-cdn-link…>"}}
    3. Fetch that URL → 16/44.1 FLAC bytes (real lossless, full track).

Quality codes: 27=FLAC 16-bit, 7=FLAC 24-bit, 6=MP3 320, 5=MP3 lower.
The community proxies typically expose 27 reliably; higher tiers depend
on the operator's Qobuz subscription level.

This currently uses a single-endpoint setup because only one proxy in the
wild (qobuz2.kennyy.com.br) actually responds today; if more come online
we can add rotation similar to tidal_public._fetch_instances().

Originally discovered by inspecting monochrome.tf's web frontend (which
used Qobuz for audio and TIDAL for metadata). As of 2026-07 the operator
relocated from qobuz.kennyy.com.br → qobuz2.kennyy.com.br; monochrome's
frontend still hardcodes the old host and has stubbed Qobuz streaming.
See docs/tidal-endpoints.md and the conversation log for context.
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import requests

from .rate_limiter import tidal_rate_limit  # reuse the global music-API rate limiter

_CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "track-manager"
)
_CACHE_FILE = _CACHE_DIR / "qobuz_id_cache.json"

# Quality 27 = FLAC 16-bit/44.1kHz. Higher tiers (7=24-bit) often 401 on
# free proxies because the operator's account doesn't have HiFi+. 27 is
# the sweet spot — universally available, lossless, and matches CD quality.
_QUALITY_FLAC = 27

# Browser-like UA: Cloudflare in front of the community proxy rejects
# some non-browser agents. Keep this generic — no OS fingerprint.
_USER_AGENT = "Mozilla/5.0 (compatible; track-manager/1.0; +https://github.com/gptme)"


class QobuzPublicClient:
    """Client for community-hosted Qobuz proxies.

    Single-endpoint for now; the canonical working proxy is
    `qobuz2.kennyy.com.br` (operated by `kennyy`, same Brazilian
    Qobuz infrastructure previously at qobuz.kennyy.com.br).
    """

    ENDPOINTS = [
        "https://qobuz2.kennyy.com.br",
        # Known-broken (kept for re-evaluation):
        # "https://qobuz.kennyy.com.br",  # CF 522 — origin down since ~2026-07
        # "https://qobuz.squid.wtf",      # NXDOMAIN (entire squid qobuz fleet retired)
        # "https://qobuz.kennyy.com",     # NXDOMAIN
    ]

    def __init__(self, bypass_cache: bool = False):
        """Initialize Qobuz public client.

        Args:
            bypass_cache: When True, ignore the persistent ISRC→Qobuz-id
                          cache. Same semantics as tidal_public's flag.
        """
        self.endpoint = self.ENDPOINTS[0]
        self.bypass_cache = bypass_cache
        self.session = requests.Session()
        # Most community proxies front-end through Cloudflare and reject
        # non-browser User-Agents. A generic Mozilla string is enough.
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self._isrc_cache: Optional[dict] = None

    # ------------------------------------------------------------------
    # ISRC → Qobuz ID cache (persistent)
    # ------------------------------------------------------------------

    def _load_cache(self) -> dict:
        if self._isrc_cache is not None:
            return self._isrc_cache
        try:
            if _CACHE_FILE.exists():
                import json

                self._isrc_cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            else:
                self._isrc_cache = {}
        except (OSError, ValueError):
            self._isrc_cache = {}
        return self._isrc_cache

    def _save_cache(self) -> None:
        if self._isrc_cache is None:
            return
        try:
            import json

            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(
                json.dumps(self._isrc_cache, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Search + download
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal: retry-on-timeout HTTP helper
    # ------------------------------------------------------------------

    def _get_with_retry(
        self,
        path: str,
        params: dict,
        timeout: int,
        *,
        label: str,
    ) -> Optional[requests.Response]:
        """GET with one retry on Read/Connect timeout.

        qobuz2.kennyy.com.br is a single-endpoint community proxy that
        occasionally takes >10s to respond — usually for ISRCs whose
        Qobuz search returns lots of hits. A single retry with a tiny
        backoff salvages those bad-luck failures cheaply (worst-case
        adds ~timeout seconds, never more). Other RequestExceptions
        (HTTP errors, ConnectionError, etc.) are *not* retried here —
        they're either authoritative or pointless to retry.

        Returns a successful (raised-for-status) response, or None on
        give-up. Errors print to stderr matching the pre-retry format.
        """
        last_exc: Optional[BaseException] = None
        for attempt in (1, 2):
            try:
                tidal_rate_limit()
                r = self.session.get(
                    f"{self.endpoint}{path}", params=params, timeout=timeout
                )
                r.raise_for_status()
                return r
            except requests.Timeout as e:
                last_exc = e
                if attempt == 1:
                    # quick backoff, then second pass
                    time.sleep(0.5)
                    continue
                print(
                    f"⚠️ Qobuz {label} failed (timeout, after retry): {e}",
                    file=sys.stderr,
                )
                return None
            except requests.RequestException as e:
                # Non-timeout failure: report and bail (no retry).
                print(f"⚠️ Qobuz {label} failed: {e}", file=sys.stderr)
                return None
        # Unreachable, but keep type-checkers happy.
        if last_exc is not None:
            print(f"⚠️ Qobuz {label} failed: {last_exc}", file=sys.stderr)
        return None

    # ------------------------------------------------------------------
    # Search + download
    # ------------------------------------------------------------------

    def search_by_isrc(self, isrc: str) -> Optional[Dict]:
        """Look up the first Qobuz track matching `isrc`.

        Returns the track dict from Qobuz (with `id`, `title`, `performer`,
        `album`, `duration`, `maximum_bit_depth`, …) or None if not found.
        Caches the *full* track metadata (not just the id) by ISRC so we
        can build doc/cover from it without re-querying.
        """
        if not self.bypass_cache:
            cache = self._load_cache()
            if isrc in cache:
                return cache[isrc]

        r = self._get_with_retry(
            "/api/get-music",
            {"q": isrc, "offset": 0},
            timeout=10,
            label="search",
        )
        if r is None:
            return None
        try:
            items = r.json().get("data", {}).get("tracks", {}).get("items") or []
        except (ValueError, KeyError) as e:
            print(f"⚠️ Qobuz search parse error: {e}", file=sys.stderr)
            return None
        if not items:
            return None
        track = items[0]
        if not self.bypass_cache:
            cache = self._load_cache()
            cache[isrc] = track
            self._save_cache()
        return track

    def _get_download_url(
        self, track_id: int, quality: int = _QUALITY_FLAC
    ) -> Optional[str]:
        r = self._get_with_retry(
            "/api/download-music",
            {"track_id": track_id, "quality": quality},
            timeout=15,
            label="download URL request",
        )
        if r is None:
            return None
        try:
            body = r.json()
        except ValueError as e:
            print(f"⚠️ Qobuz download URL parse error: {e}", file=sys.stderr)
            return None
        # Shape varies between proxies — handle both flat and {data: …}.
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        url = (data or {}).get("url")
        if not url:
            print(
                f"⚠️ Qobuz download URL missing in response: {body!r}"[:200],
                file=sys.stderr,
            )
        return url

    def download_by_isrc(self, isrc: str, output_path: Path) -> Optional[Dict]:
        """Search Qobuz by ISRC and download the first match to `output_path`.

        Returns the Qobuz track metadata dict on success (so the caller
        can build a metadata doc), None on any failure.
        """
        track = self.search_by_isrc(isrc)
        if not track:
            return None
        track_id = track.get("id")
        if not track_id:
            return None

        url = self._get_download_url(track_id, quality=_QUALITY_FLAC)
        if not url:
            return None

        try:
            t0 = time.time()
            print("⬇️ Downloading from Qobuz (lossless FLAC)...")
            r = self.session.get(url, timeout=120, stream=True)
            r.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            print(f"   {output_path.stat().st_size:,} bytes in {time.time() - t0:.1f}s")
            return track
        except requests.RequestException as e:
            print(f"❌ Qobuz audio fetch failed: {e}", file=sys.stderr)
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
