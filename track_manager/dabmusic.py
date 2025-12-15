"""DAB Music integration for high-quality FLAC downloads."""

import sys
from pathlib import Path
from typing import Dict, Optional

import requests


class DABMusicClient:
    """Client for DAB Music API."""

    def __init__(self, email: str, password: str, endpoint: str = "https://dabmusic.xyz"):
        """Initialize DAB Music client.

        Args:
            email: DAB Music email
            password: DAB Music password
            endpoint: DAB Music API endpoint
        """
        self.endpoint = endpoint
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "track-manager/0.2.0",
            }
        )
        
        # Login to get session
        self._login(email, password)

    def _login(self, email: str, password: str):
        """Login to DAB Music and store session cookie.

        Args:
            email: DAB Music email
            password: DAB Music password

        Raises:
            requests.RequestException: If login fails
        """
        try:
            response = self.session.post(
                f"{self.endpoint}/api/auth/login",
                json={"email": email, "password": password},
                timeout=10,
            )
            
            if response.status_code == 401:
                raise ValueError("Invalid DAB Music credentials")
            
            response.raise_for_status()
            
            # Session cookie is automatically stored in self.session
            print("✅ Logged in to DAB Music")

        except requests.RequestException as e:
            print(f"❌ DAB Music login failed: {e}", file=sys.stderr)
            raise

    def search_by_isrc(self, isrc: str) -> Optional[Dict]:
        """Search for track by ISRC.

        Args:
            isrc: ISRC code

        Returns:
            Track data if found
        """
        try:
            response = self.session.get(
                f"{self.endpoint}/api/search",
                params={"q": isrc, "type": "track"},
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            tracks = data.get("tracks", [])
            
            if tracks:
                # Return first match
                return tracks[0]
            
            return None

        except requests.RequestException as e:
            print(f"⚠️ DAB Music search failed: {e}", file=sys.stderr)
            return None

    def download_track(
        self, track_id: int, output_path: Path, quality: int = 27
    ) -> bool:
        """Download track from DAB Music.

        Args:
            track_id: DAB Music track ID
            output_path: Output file path
            quality: Quality (27=FLAC, 5=MP3)

        Returns:
            True if successful
        """
        try:
            # Get stream URL
            response = self.session.get(
                f"{self.endpoint}/api/stream",
                params={"trackId": track_id, "quality": quality},
                timeout=10,
            )
            response.raise_for_status()

            stream_data = response.json()
            stream_url = stream_data.get("url")

            if not stream_url:
                print("❌ No stream URL returned", file=sys.stderr)
                return False

            # Download audio file
            response = self.session.get(stream_url, timeout=60)
            response.raise_for_status()

            # Save to file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(response.content)

            return True

        except requests.RequestException as e:
            print(f"❌ DAB Music download failed: {e}", file=sys.stderr)
            return False
