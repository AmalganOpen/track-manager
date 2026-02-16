"""Public TIDAL API integration (no credentials required)."""

import sys
from pathlib import Path
from typing import Dict, Optional
import base64
import json

import requests
from .rate_limiter import tidal_rate_limit


class TidalPublicClient:
    """Client for public TIDAL API endpoints (community-hosted)."""

    # List of public API endpoints (fallback if one fails)
    ENDPOINTS = [
        "https://api.monochrome.tf",
        "https://triton.squid.wtf",
        "https://wolf.qqdl.site",
        "https://tidal-api.binimum.org",
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

    def get_tidal_id_from_url(self, url: str) -> Optional[str]:
        """Get TIDAL track ID from any music platform URL using song.link.

        Args:
            url: URL from any music platform (Spotify, Apple Music, etc.)

        Returns:
            TIDAL track ID if found
        """
        try:
            response = self.session.get(
                f"https://api.song.link/v1-alpha.1/links?url={url}",
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            tidal_url = data.get("linksByPlatform", {}).get("tidal", {}).get("url")

            if not tidal_url:
                return None

            # Extract ID from URL like "https://listen.tidal.com/track/450756447"
            tidal_id = tidal_url.split("/")[-1]
            return tidal_id

        except requests.RequestException as e:
            print(f"⚠️ song.link lookup failed: {e}", file=sys.stderr)
            return None
        except (ValueError, KeyError) as e:
            print(f"⚠️ song.link parsing failed: {e}", file=sys.stderr)
            return None

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

    def download_track(
        self, track_id: str, output_path: Path, quality: str = "LOSSLESS"
    ) -> bool:
        """Download track from TIDAL public API.

        Args:
            track_id: TIDAL track ID
            output_path: Output file path
            quality: Quality (HI_RES_LOSSLESS or LOSSLESS)

        Returns:
            True if successful
        """
        try:
            # Get download manifest
            tidal_rate_limit()
            response = self.session.get(
                f"{self.endpoint}/track/",
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

            # Decode manifest based on type
            if manifest_mime == "application/vnd.tidal.bts":
                # Simple JSON manifest
                manifest_json = json.loads(base64.b64decode(manifest_b64))
                download_url = manifest_json.get("urls", [None])[0]
            elif manifest_mime == "application/dash+xml":
                # MPD manifest (Hi-Res) - more complex, skip for now
                print("⚠️ Hi-Res MPD manifest not yet supported, falling back", file=sys.stderr)
                return False
            else:
                print(f"❌ Unknown manifest type: {manifest_mime}", file=sys.stderr)
                return False

            if not download_url:
                print("❌ No download URL in manifest", file=sys.stderr)
                return False

            # Download audio file
            tidal_rate_limit()
            print(f"⬇️ Downloading from TIDAL...")
            response = self.session.get(download_url, timeout=120)
            response.raise_for_status()

            # Save to file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)

            return True

        except requests.RequestException as e:
            print(f"❌ TIDAL download failed: {e}", file=sys.stderr)
            return False
        except (ValueError, KeyError) as e:
            print(f"❌ TIDAL download parsing failed: {e}", file=sys.stderr)
            return False
