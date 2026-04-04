"""Public TIDAL API integration (no credentials required)."""

import os
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
import base64
import json

import requests
from .rate_limiter import songlink_rate_limit, tidal_rate_limit

_CACHE_FILE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "track-manager" / "tidal_id_cache.json"


class TidalPublicClient:
    """Client for public TIDAL API endpoints (community-hosted)."""

    # List of public API endpoints (fallback if one fails).
    # Ordered by reliability; broken/suspended endpoints removed.
    ENDPOINTS = [
        "https://wolf.qqdl.site",
        "https://api.monochrome.tf",
        "https://tidal-api.binimum.org",
        # "https://triton.squid.wtf",  # SSL cert expired
    ]

    def __init__(self, endpoint_index: int = 0):
        """Initialize TIDAL public client.

        Args:
            endpoint_index: Which endpoint to use (0-based)
        """
        if endpoint_index >= len(self.ENDPOINTS):
            endpoint_index = 0
        
        self.endpoint = self.ENDPOINTS[endpoint_index]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "track-manager/0.2.0",
            }
        )
        print(f"ℹ️ Using TIDAL endpoint: {self.endpoint}")
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
        _TRACKING_PARAMS = {"si", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
        parsed = urlparse(url)
        qs = {k: v for k, v in parse_qs(parsed.query).items() if k not in _TRACKING_PARAMS}
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

    def _songlink_request(self, params: dict) -> Optional[dict]:
        """Make a rate-limited request to the song.link API.

        Args:
            params: Query parameters dict

        Returns:
            Parsed JSON response or None on failure
        """
        try:
            songlink_rate_limit()
            response = self.session.get(
                "https://api.song.link/v1-alpha.1/links",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"⚠️ song.link lookup failed: {e}", file=sys.stderr)
            return None
        except (ValueError, KeyError) as e:
            print(f"⚠️ song.link parsing failed: {e}", file=sys.stderr)
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

        Args:
            isrc: ISRC code (e.g. "SE5AJ1900779")

        Returns:
            TIDAL track ID string if found, else None
        """
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
        if tidal_id:
            cache[isrc] = tidal_id
            self._save_isrc_cache()
        return tidal_id

    def get_tidal_id_from_url(self, url: str, isrc: Optional[str] = None) -> Optional[str]:
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

    def get_track_info(self, track_id: str) -> Optional[Dict]:
        """Get track information by TIDAL ID.

        Args:
            track_id: TIDAL track ID

        Returns:
            Track data if found
        """
        try:
            tidal_rate_limit()
            response = self.session.get(
                f"{self.endpoint}/info/",
                params={"id": track_id},
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            return data.get("data")

        except requests.RequestException as e:
            print(f"⚠️ TIDAL track info failed: {e}", file=sys.stderr)
            return None
        except (ValueError, KeyError) as e:
            print(f"⚠️ TIDAL track info parsing failed: {e}", file=sys.stderr)
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
            timeout=30,
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
            print("⚠️ Hi-Res MPD manifest not yet supported, falling back", file=sys.stderr)
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
        self, track_id: str, output_path: Path, quality: str = "LOSSLESS"
    ) -> bool:
        """Download track from TIDAL public API, rotating endpoints on 403.

        Args:
            track_id: TIDAL track ID
            output_path: Output file path
            quality: Quality tier (LOSSLESS or HIGH)

        Returns:
            True if successful
        """
        # Build ordered endpoint list: current first, then the rest
        endpoints = [self.endpoint] + [e for e in self.ENDPOINTS if e != self.endpoint]

        for endpoint in endpoints:
            if endpoint != self.endpoint:
                print(f"ℹ️ Trying alternate TIDAL endpoint: {endpoint}", file=sys.stderr)
            try:
                success = self._download_track_from_endpoint(endpoint, track_id, output_path, quality)
                if success:
                    # Promote working endpoint so future calls skip failed ones
                    self.endpoint = endpoint
                    return True
                return False  # non-rotatable failure (bad manifest etc.)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                print(f"❌ TIDAL download failed: {e}", file=sys.stderr)
                if status == 403:
                    continue  # track restricted on this endpoint, try next
                if status is not None and status < 500:
                    return False  # other 4xx — won't change on a different endpoint
                continue  # 5xx or unknown → try next
            except requests.RequestException as e:
                # Connection-level failure (SSL error, timeout, DNS) — endpoint is broken
                print(f"❌ TIDAL download failed: {e}", file=sys.stderr)
                continue
            except (ValueError, KeyError) as e:
                print(f"❌ TIDAL download parsing failed: {e}", file=sys.stderr)
                return False

        return False
