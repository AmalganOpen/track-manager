"""song.link integration for finding tracks across platforms."""

import sys
from typing import Dict, Optional
from urllib.parse import quote

import requests


class SongLinkClient:
    """Client for song.link API."""

    API_BASE = "https://api.song.link/v1-alpha.1/links"

    def __init__(self):
        """Initialize song.link client."""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "track-manager/0.2.0",
            }
        )

    def find_platforms(self, url: str) -> Dict[str, str]:
        """Find track on other platforms.

        Args:
            url: Input URL (any platform)

        Returns:
            Dictionary of platform -> URL mappings

        Raises:
            requests.RequestException: If API request fails
        """
        try:
            response = self.session.get(f"{self.API_BASE}?url={quote(url)}", timeout=10)
            response.raise_for_status()

            data = response.json()
            platforms = data.get("linksByPlatform", {})

            return {
                platform: info["url"]
                for platform, info in platforms.items()
                if "url" in info
            }

        except requests.RequestException as e:
            print(f"⚠️ song.link API error: {e}", file=sys.stderr)
            return {}

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
        try:
            response = self.session.get(f"{self.API_BASE}?url={quote(url)}", timeout=10)
            response.raise_for_status()

            data = response.json()

            # Extract first entity (usually the track)
            entities = data.get("entitiesByUniqueId", {})
            if entities:
                entity = next(iter(entities.values()))
                return {
                    "title": entity.get("title"),
                    "artist": entity.get("artistName"),
                }

            return None

        except requests.RequestException as e:
            print(f"⚠️ song.link API error: {e}", file=sys.stderr)
            return None
