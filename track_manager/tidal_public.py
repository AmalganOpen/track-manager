"""Public TIDAL API integration (no credentials required).

Endpoint discovery: the binimum/hifi-api ecosystem (community-hosted TIDAL
proxies) is volatile — operators rotate, OAuth refresh tokens expire,
hosts get retired weekly. Rather than maintain a hand-curated list, we
fetch the canonical instance list from monochrome.tf, the most active
frontend in the ecosystem, which publishes its current pool at
`https://monochrome.tf/instances.json`. That list is split into:
  - "api" hosts:        for /info/, /search/, /album/, /artist/, /lyrics/.
                        Most run on free TIDAL preview tier, no OAuth needed.
  - "streaming" hosts:  for /track/. Need the operator's paid TIDAL OAuth
                        refresh token to return full audio. Working set
                        flips frequently.
The list is fetched once per process, disk-cached for `_INSTANCES_TTL`,
and falls back to a small hardcoded set when monochrome.tf is unreachable.
See docs/tidal-endpoints.md for the full testing/maintenance protocol.
"""

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from .rate_limiter import songlink_note_throttle, songlink_rate_limit, tidal_rate_limit

_CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "track-manager"
)
_CACHE_FILE = _CACHE_DIR / "tidal_id_cache.json"
_INSTANCES_CACHE_FILE = _CACHE_DIR / "monochrome_instances.json"
_INSTANCES_URL = "https://monochrome.tf/instances.json"
_INSTANCES_TTL = 6 * 3600  # 6 hours; monochrome.tf updates a few times a day

# Hardcoded last-resort fallback if monochrome.tf is unreachable AND no
# disk cache exists. Don't bother grooming this list — the live source
# is the one that gets updated. This just keeps us afloat for the first
# run if the user has no network or monochrome.tf itself is down.
_HARDCODED_FALLBACK = {
    "api": [
        "https://eu-central.monochrome.tf",
        "https://us-west.monochrome.tf",
        "https://api.monochrome.tf",
    ],
    "streaming": [
        "https://hifi.p1nkhamster.xyz",
        "https://eu-central.monochrome.tf",
    ],
}


def _normalize(url: str) -> str:
    """Strip trailing slash so endpoint comparisons / set ops are stable."""
    return url.rstrip("/")


def _fetch_instances() -> Tuple[Dict[str, List[str]], str]:
    """Return (`{api: [...], streaming: [...]}`, source_label).

    source_label is "fresh", "cache", or "fallback" — purely for logging.
    Resolution order:
      1. Live fetch from monochrome.tf (cache fresh result on disk).
      2. Disk cache, even if stale (better than nothing).
      3. Hardcoded fallback baked into this module.
    """
    # Step 1: try live fetch if cache is missing or older than TTL
    use_live = True
    if _INSTANCES_CACHE_FILE.exists():
        try:
            age = time.time() - _INSTANCES_CACHE_FILE.stat().st_mtime
            if age < _INSTANCES_TTL:
                # Cache is fresh enough — skip the network call entirely
                data = json.loads(_INSTANCES_CACHE_FILE.read_text())
                if isinstance(data.get("api"), list) and isinstance(
                    data.get("streaming"), list
                ):
                    return _normalize_instances(data), "cache"
        except (OSError, json.JSONDecodeError):
            pass

    if use_live:
        try:
            r = requests.get(_INSTANCES_URL, timeout=8)
            r.raise_for_status()
            data = r.json()
            if isinstance(data.get("api"), list) and isinstance(
                data.get("streaming"), list
            ):
                try:
                    _INSTANCES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    _INSTANCES_CACHE_FILE.write_text(json.dumps(data, indent=2))
                except OSError:
                    pass  # disk-cache write is best-effort
                return _normalize_instances(data), "fresh"
        except (requests.RequestException, ValueError, KeyError):
            pass

    # Step 2: stale cache as fallback
    if _INSTANCES_CACHE_FILE.exists():
        try:
            data = json.loads(_INSTANCES_CACHE_FILE.read_text())
            if isinstance(data.get("api"), list) and isinstance(
                data.get("streaming"), list
            ):
                return _normalize_instances(data), "cache"
        except (OSError, json.JSONDecodeError):
            pass

    # Step 3: hardcoded fallback
    return _normalize_instances(_HARDCODED_FALLBACK), "fallback"


def _normalize_instances(data: Dict) -> Dict[str, List[str]]:
    """Strip trailing slashes and dedupe while preserving order."""
    out: Dict[str, List[str]] = {}
    for key in ("api", "streaming"):
        seen = set()
        clean: List[str] = []
        for url in data.get(key, []) or []:
            n = _normalize(url)
            if n and n not in seen:
                seen.add(n)
                clean.append(n)
        out[key] = clean
    return out


class TidalPublicClient:
    """Client for public TIDAL API endpoints (community-hosted).

    Uses two separate endpoint pools, populated from monochrome.tf's
    `instances.json` at construction time:
      - `self.api_endpoints`       → for /info/, /search/, etc.
      - `self.streaming_endpoints` → for /track/ (full audio)

    On first success in each pool, the working endpoint is pinned for
    the rest of the process so subsequent calls skip rotation.
    """

    def __init__(self, bypass_cache: bool = False):
        """Initialize TIDAL public client.

        Args:
            bypass_cache: When True, ignore the persistent ISRC→TIDAL-id
                          cache entirely (no reads, no writes). Useful
                          for forcing a fresh song.link lookup when a
                          cached id has gone stale (track re-uploaded,
                          region change, etc.). Delete
                          `~/.cache/track-manager/tidal_id_cache.json`
                          to throw the cache away permanently.

        Note: the endpoint pools come from monochrome.tf/instances.json
        (with disk-cached + hardcoded fallbacks); there's no
        `endpoint_index` parameter anymore because the list is dynamic.
        """
        instances, source = _fetch_instances()
        self.api_endpoints: List[str] = instances["api"]
        self.streaming_endpoints: List[str] = instances["streaming"]
        # `self.endpoint` is the pinned api endpoint (kept for backward
        # compatibility); `self.streaming_endpoint` is the pinned /track/
        # endpoint. Both update on first success in their respective pool.
        self.endpoint: Optional[str] = (
            self.api_endpoints[0] if self.api_endpoints else None
        )
        self.streaming_endpoint: Optional[str] = (
            self.streaming_endpoints[0] if self.streaming_endpoints else None
        )

        self.bypass_cache = bypass_cache
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "track-manager/0.2.0"})

        # Only announce endpoint loading when something noteworthy happened:
        # a fresh fetch from monochrome.tf or a fallback to hardcoded list.
        # The cache-hit case is the steady state and would just be noise on
        # every run.
        if source != "cache":
            print(
                f"ℹ️ TIDAL endpoints loaded from {source}: "
                f"{len(self.api_endpoints)} api, {len(self.streaming_endpoints)} streaming"
            )
        if bypass_cache:
            print("ℹ️ TIDAL ID cache disabled (--no-cache)")
        self._isrc_cache: Optional[dict] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_tracking_params(url: str) -> str:
        """Remove known tracking/session parameters from a URL.

        Spotify appends ?si=<token> to shared URLs; passing these to song.link
        prevents it from serving a cached response and burns extra quota.
        """
        _TRACKING_PARAMS = {
            "si",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
        }
        parsed = urlparse(url)
        qs = {
            k: v for k, v in parse_qs(parsed.query).items() if k not in _TRACKING_PARAMS
        }
        clean = parsed._replace(query=urlencode(qs, doseq=True))
        return urlunparse(clean)

    # ------------------------------------------------------------------
    # ISRC → TIDAL ID cache (persistent, keyed by ISRC)
    # ------------------------------------------------------------------

    def _load_isrc_cache(self) -> dict:
        if self._isrc_cache is not None:
            return self._isrc_cache
        try:
            if _CACHE_FILE.exists():
                self._isrc_cache = json.loads(_CACHE_FILE.read_text())
            else:
                self._isrc_cache = {}
        except (OSError, json.JSONDecodeError):
            self._isrc_cache = {}
        return self._isrc_cache

    def _save_isrc_cache(self) -> None:
        if self._isrc_cache is None:
            return
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps(self._isrc_cache, indent=2))
        except OSError:
            pass  # non-fatal

    # Cap total time we'll spend on a single song.link lookup (cooldown +
    # in-call retries combined). Beyond this, fall back to YouTube — better
    # to take a YouTube rip than hang the whole batch on one track.
    _SONGLINK_MAX_WAIT_PER_CALL = 45.0

    def _songlink_request(self, params: dict, max_retries: int = 1) -> Optional[dict]:
        """Make a rate-limited request to the song.link API.

        On 429 we honour the `Retry-After` header (capped) and retry once.
        The local rate limiter (`songlink_rate_limit`) already enforces a
        persistent cooldown across calls/processes, so additional in-call
        backoff stacking just wastes wall time before the inevitable
        YouTube fallback.

        Args:
            params: Query parameters dict
            max_retries: How many times to retry on 429 before giving up

        Returns:
            Parsed JSON response or None on failure
        """
        for attempt in range(max_retries + 1):
            try:
                songlink_rate_limit()
                response = self.session.get(
                    "https://api.song.link/v1-alpha.1/links",
                    params=params,
                    timeout=10,
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else 30.0
                    except ValueError:
                        wait = 30.0
                    # Always persist the cooldown so other tracks in this run
                    # (and follow-up `tm` invocations) wait it out instead of
                    # immediately tripping another 429.
                    songlink_note_throttle(wait)
                    if (
                        attempt >= max_retries
                        or wait > self._SONGLINK_MAX_WAIT_PER_CALL
                    ):
                        print(
                            f"⚠️ song.link 429 (Retry-After {wait:.0f}s); skipping TIDAL for this track",
                            file=sys.stderr,
                        )
                        return None
                    print(
                        f"⏳ song.link 429; sleeping {wait:.1f}s before single retry...",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    continue
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
            except requests.RequestException as e:
                print(f"⚠️ song.link lookup failed: {e}", file=sys.stderr)
                return None
            except (ValueError, KeyError) as e:
                print(f"⚠️ song.link parsing failed: {e}", file=sys.stderr)
                return None
        return None

    @staticmethod
    def _extract_tidal_id_from_response(data: dict) -> Optional[str]:
        tidal_url = data.get("linksByPlatform", {}).get("tidal", {}).get("url")
        if not tidal_url:
            return None
        return tidal_url.split("/")[-1]

    def get_tidal_id_from_isrc(self, isrc: str) -> Optional[str]:
        """Get TIDAL track ID for an ISRC, using the local cache or song.link.

        When the ISRC is cached no network call is made. Otherwise, song.link
        is queried using the ISRC platform directly (no Spotify URL needed).
        When `self.bypass_cache` is True, the cache is ignored for both
        reads and writes.

        Args:
            isrc: ISRC code (e.g. "SE5AJ1900779")

        Returns:
            TIDAL track ID string if found, else None
        """
        if not self.bypass_cache:
            cache = self._load_isrc_cache()
            if isrc in cache:
                print(f"ℹ️ TIDAL ID found in cache (ISRC: {isrc})")
                return cache[isrc]

        # song.link supports ISRC as a first-class platform — no URL needed
        data = self._songlink_request(
            {"platform": "isrc", "type": "song", "id": isrc},
        )
        if not data:
            return None

        tidal_id = self._extract_tidal_id_from_response(data)
        if tidal_id and not self.bypass_cache:
            cache = self._load_isrc_cache()
            cache[isrc] = tidal_id
            self._save_isrc_cache()
        return tidal_id

    def get_tidal_id_from_url(
        self, url: str, isrc: Optional[str] = None
    ) -> Optional[str]:
        """Get TIDAL track ID from any music platform URL using song.link.

        When *isrc* is provided the ISRC-based lookup path is used (which
        checks the local cache first, then queries song.link by ISRC rather
        than by URL — a cleaner lookup that song.link can serve from cache).

        Args:
            url: URL from any music platform (Spotify, Apple Music, etc.)
            isrc: ISRC code if already known (preferred lookup path)

        Returns:
            TIDAL track ID if found
        """
        # Prefer ISRC-based lookup: cache check + cleaner song.link query
        if isrc:
            return self.get_tidal_id_from_isrc(isrc)

        # Fallback: URL-based lookup — strip tracking params first so song.link
        # can serve a cached response rather than doing a fresh fetch every time.
        clean_url = self._strip_tracking_params(url)
        data = self._songlink_request({"url": clean_url})
        if not data:
            return None
        return self._extract_tidal_id_from_response(data)

    def _rotation_order(self, pool: List[str], pinned: Optional[str]) -> List[str]:
        """Return `pool` reordered so `pinned` comes first (if set & still in pool)."""
        if pinned and pinned in pool:
            return [pinned] + [e for e in pool if e != pinned]
        return list(pool)

    def get_track_info(self, track_id: str) -> Optional[Dict]:
        """Get track information by TIDAL ID, rotating through the api pool.

        Tries every api endpoint in turn; promotes the working one to be
        the default for subsequent calls. Only HTTP 400 stops rotation
        early — everything else (timeout, 401/403/404/429/5xx, parse errors)
        rotates because the same upstream call may succeed on a different host.

        Args:
            track_id: TIDAL track ID

        Returns:
            Track data if found
        """
        endpoints = self._rotation_order(self.api_endpoints, self.endpoint)

        for endpoint in endpoints:
            if endpoint != self.endpoint:
                print(f"ℹ️ Trying alternate TIDAL endpoint: {endpoint}", file=sys.stderr)
            try:
                tidal_rate_limit()
                response = self.session.get(
                    f"{endpoint}/info/",
                    params={"id": track_id},
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json().get("data")
                if data is None:
                    print(f"⚠️ TIDAL track info empty on {endpoint}", file=sys.stderr)
                    continue
                self.endpoint = endpoint
                return data
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                print(f"⚠️ TIDAL track info failed on {endpoint}: {e}", file=sys.stderr)
                if status == 400:
                    return None  # malformed request — same on every endpoint
                continue  # 401/403/404/429/5xx → try next
            except requests.RequestException as e:
                print(f"⚠️ TIDAL track info failed on {endpoint}: {e}", file=sys.stderr)
                continue
            except (ValueError, KeyError) as e:
                print(
                    f"⚠️ TIDAL track info parsing failed on {endpoint}: {e}",
                    file=sys.stderr,
                )
                continue

        print(
            f"❌ TIDAL track info: all {len(endpoints)} endpoints exhausted",
            file=sys.stderr,
        )
        return None

    def _download_track_from_endpoint(
        self, endpoint: str, track_id: str, output_path: Path, quality: str
    ) -> bool:
        """Attempt to download a track from a specific endpoint.

        Returns True on success, raises requests.HTTPError on 4xx/5xx so the
        caller can decide whether to retry on a different endpoint.
        """
        tidal_rate_limit()
        response = self.session.get(
            f"{endpoint}/track/",
            params={"id": track_id, "quality": quality},
            timeout=8,
        )
        response.raise_for_status()

        manifest_data = response.json().get("data", {})
        manifest_mime = manifest_data.get("manifestMimeType")
        manifest_b64 = manifest_data.get("manifest")

        if not manifest_b64:
            print("❌ No download manifest returned", file=sys.stderr)
            return False

        if manifest_mime == "application/vnd.tidal.bts":
            manifest_json = json.loads(base64.b64decode(manifest_b64))
            download_url = manifest_json.get("urls", [None])[0]
        elif manifest_mime == "application/dash+xml":
            print(
                "⚠️ Hi-Res MPD manifest not yet supported, falling back", file=sys.stderr
            )
            return False
        else:
            print(f"❌ Unknown manifest type: {manifest_mime}", file=sys.stderr)
            return False

        if not download_url:
            print("❌ No download URL in manifest", file=sys.stderr)
            return False

        tidal_rate_limit()
        print(f"⬇️ Downloading from TIDAL...")
        response = self.session.get(download_url, timeout=120)
        response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return True

    def download_track(
        self,
        track_id: str,
        output_path: Path,
        quality: str = "LOSSLESS",
        fallback_qualities: tuple = (),
    ) -> bool:
        """Download track via the streaming pool, rotating through every host.

        For each endpoint, try `quality` first, then each item in
        `fallback_qualities` in order — but only on hosts where the
        previous attempt failed in a way that *another quality might fix*.

        Per-endpoint quality fallback policy:
        - Success → pin endpoint, return True.
        - HTTP 400 → stop completely; request itself is malformed.
        - HTTP 401/403/5xx → endpoint is broken (auth revoked, upstream
          gone, or Cloudflare wrapping an origin auth failure as 520/525/
          530). Every quality will fail identically; skip remaining
          qualities, move to next host.
        - Connection error / timeout → endpoint unreachable; same as above.
        - HTTP 404/429 / no manifest / MPD-only / parse error → this
          *quality* didn't work, but another might on the same host.
          Try next quality on the same endpoint.

        Streaming hosts differ per-track because the upstream TIDAL
        session, region, and tier-availability vary by host, so we keep
        rotating until one works or the list is exhausted.

        Args:
            track_id: TIDAL track ID
            output_path: Output file path
            quality: Primary quality tier (LOSSLESS or HIGH).
            fallback_qualities: Other tiers to try on the same endpoint
                                if `quality` fails with a quality-specific
                                error (not auth/network). e.g. `("HIGH",)`
                                falls back to AAC when LOSSLESS isn't
                                available on a particular host.

        Returns:
            True if successful at any quality on any endpoint.
        """
        qualities = (quality, *fallback_qualities)
        endpoints = self._rotation_order(
            self.streaming_endpoints, self.streaming_endpoint
        )

        for endpoint in endpoints:
            if endpoint != self.streaming_endpoint:
                print(
                    f"ℹ️ Trying alternate TIDAL streaming endpoint: {endpoint}",
                    file=sys.stderr,
                )
            for q in qualities:
                try:
                    if self._download_track_from_endpoint(
                        endpoint, track_id, output_path, q
                    ):
                        self.streaming_endpoint = endpoint
                        return True
                    # Inner returned False (no manifest, MPD-only, missing URL).
                    # This quality has no usable stream on this host, but
                    # another quality might — keep trying on this endpoint.
                    continue
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    print(
                        f"❌ TIDAL download failed on {endpoint} ({q}): {e}",
                        file=sys.stderr,
                    )
                    if status == 400:
                        return False  # malformed request — won't change anywhere
                    if status in (401, 403) or (status is not None and status >= 500):
                        # Endpoint is broken for everything (auth revoked,
                        # upstream gone, or Cloudflare-wrapped origin auth
                        # failure as 520/525/530). Every quality fails the
                        # same; skip remaining qualities.
                        break
                    continue  # 404/429 — track/rate-limit specific, try next quality
                except requests.RequestException as e:
                    # Connection-level (SSL/timeout/DNS) — endpoint dead.
                    print(
                        f"❌ TIDAL download failed on {endpoint} ({q}): {e}",
                        file=sys.stderr,
                    )
                    break
                except (ValueError, KeyError) as e:
                    print(
                        f"❌ TIDAL download parsing failed on {endpoint} ({q}): {e}",
                        file=sys.stderr,
                    )
                    continue

        print(
            f"❌ TIDAL download: all {len(endpoints)} streaming endpoints exhausted",
            file=sys.stderr,
        )
        return False
