"""song.link integration for finding tracks across platforms."""

import sys
import time
from typing import Dict, Optional
from urllib.parse import quote

import requests
from .rate_limiter import songlink_rate_limit


class SongLinkClient:
    """Client for song.link API."""

    API_BASE = "https://api.song.link/v1-alpha.1/links"

    def __init__(self, timeout: int = 20, max_retries: int = 3):
        """Initialize song.link client.
        
        Args:
            timeout: Request timeout in seconds (default: 20)
            max_retries: Maximum number of retry attempts (default: 3)
        """
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "track-manager/0.2.0",
            }
        )
        self.timeout = timeout
        self.max_retries = max_retries

    def _make_request_with_retry(self, url: str) -> Optional[dict]:
        """Make API request with exponential backoff retry logic.
        
        Args:
            url: Full API URL to request
            
        Returns:
            JSON response data or None if all retries failed
        """
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                # Apply rate limiting before each attempt
                songlink_rate_limit()
                
                if attempt > 0:
                    # Exponential backoff: 2, 4, 8 seconds
                    backoff_time = 2 ** attempt
                    print(f"⏳ Retrying in {backoff_time}s (attempt {attempt + 1}/{self.max_retries})...", file=sys.stderr)
                    time.sleep(backoff_time)
                
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.Timeout as e:
                last_error = f"Request timed out after {self.timeout}s"
                if attempt < self.max_retries - 1:
                    print(f"⚠️ song.link timeout: {last_error}", file=sys.stderr)
                    
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                if attempt < self.max_retries - 1:
                    print(f"⚠️ song.link connection error: {last_error}", file=sys.stderr)
                    
            except requests.exceptions.HTTPError as e:
                # Don't retry on 4xx errors (client errors)
                if 400 <= e.response.status_code < 500:
                    print(f"⚠️ song.link client error ({e.response.status_code}): {e}", file=sys.stderr)
                    return None
                last_error = f"HTTP error ({e.response.status_code}): {e}"
                if attempt < self.max_retries - 1:
                    print(f"⚠️ song.link HTTP error: {last_error}", file=sys.stderr)
                    
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                if attempt < self.max_retries - 1:
                    print(f"⚠️ song.link API error: {last_error}", file=sys.stderr)
        
        # All retries failed
        print(f"❌ song.link lookup failed after {self.max_retries} attempts: {last_error}", file=sys.stderr)
        return None

    def find_platforms(self, url: str) -> Dict[str, str]:
        """Find track on other platforms.

        Args:
            url: Input URL (any platform)

        Returns:
            Dictionary of platform -> URL mappings
        """
        api_url = f"{self.API_BASE}?url={quote(url)}"
        data = self._make_request_with_retry(api_url)
        
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
        api_url = f"{self.API_BASE}?url={quote(url)}"
        data = self._make_request_with_retry(api_url)
        
        if not data:
            return None

        # Extract first entity (usually the track)
        entities = data.get("entitiesByUniqueId", {})
        if entities:
            entity = next(iter(entities.values()))
            return {
                "title": entity.get("title"),
                "artist": entity.get("artistName"),
            }

        return None
