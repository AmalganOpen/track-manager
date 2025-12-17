"""Main downloader orchestrator."""

import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import Config
from .songlink import SongLinkClient
from .sources import direct, soundcloud, spotify, youtube


class Downloader:
    """Main downloader class that routes to appropriate source handler."""

    def __init__(self, config: Config, output_dir: Optional[Path] = None):
        """Initialize downloader.

        Args:
            config: Configuration object
            output_dir: Override output directory
        """
        self.config = config
        self.output_dir = output_dir or config.output_dir

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache DAB Music client (created lazily on first use)
        self._dab_client = None

    def _extract_spotify_id(self, url: str) -> Optional[str]:
        """Extract Spotify track ID from URL."""
        import re

        patterns = [
            r"spotify\.com/track/([a-zA-Z0-9]+)",
            r"spotify:track:([a-zA-Z0-9]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _get_isrc_from_spotify(
        self, spotify_id: str, return_metadata: bool = False
    ) -> tuple[Optional[str], Optional[dict]]:
        """Get ISRC from Spotify API.

        Args:
            spotify_id: Spotify track ID
            return_metadata: If True, return (isrc, metadata), else (isrc, None)

        Returns:
            Tuple of (isrc, metadata_dict) if return_metadata=True, else (isrc, None)
        """
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            # Get credentials from environment or config
            client_id = os.environ.get("SPOTIPY_CLIENT_ID") or self.config.get(
                "spotdl.client_id"
            )
            client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET") or self.config.get(
                "spotdl.client_secret"
            )

            if not client_id or not client_secret:
                return None, None

            # Initialize Spotify client
            auth_manager = SpotifyClientCredentials(
                client_id=client_id, client_secret=client_secret
            )
            sp = spotipy.Spotify(auth_manager=auth_manager)

            # Get track data
            track = sp.track(spotify_id)
            isrc = track.get("external_ids", {}).get("isrc")

            if return_metadata and isrc:
                # Extract metadata for multi-artist support
                metadata = {
                    "artists": [a["name"] for a in track.get("artists", [])],
                    "title": track.get("name"),
                    "album": track.get("album", {}).get("name"),
                }
                return isrc, metadata

            return isrc, None

        except Exception as e:
            print(f"⚠️ Spotify API error: {e}", file=sys.stderr)
            return None, None

    def _lookup_isrc(
        self, url: str, source_type: str
    ) -> tuple[Optional[str], Optional[dict]]:
        """Lookup ISRC for track URL.

        Returns:
            Tuple of (isrc, spotify_metadata) where spotify_metadata includes artist info
        """
        # Tier 1: Direct from Spotify
        if source_type == "spotify":
            spotify_id = self._extract_spotify_id(url)
            if spotify_id:
                return self._get_isrc_from_spotify(spotify_id, return_metadata=True)

        # Tier 2: Use song.link to find Spotify URL, then get ISRC
        from .songlink import SongLinkClient

        print("🔗 Looking up track on song.link...")
        songlink = SongLinkClient()
        spotify_url = songlink.find_spotify_url(url)

        if spotify_url:
            print("✅ Found on Spotify")
            spotify_id = self._extract_spotify_id(spotify_url)
            if spotify_id:
                return self._get_isrc_from_spotify(spotify_id, return_metadata=True)
        else:
            print("ℹ️ No match found on song.link")
            print("   Proceeding to download from original source")

        return None, None

    def _try_dab_music(
        self, isrc: str, format: str, spotify_metadata: Optional[dict] = None
    ) -> bool:
        """Try to download from DAB Music using ISRC.

        Args:
            isrc: ISRC code
            format: Output format
            spotify_metadata: Optional Spotify metadata (for multi-artist support)

        Returns:
            True if successful, False otherwise
        """
        # Check if DAB Music credentials are configured
        email = self.config.dabmusic_email
        password = self.config.dabmusic_password

        if not email or not password:
            print("ℹ️ DAB Music credentials not configured, skipping")
            return False

        try:
            from .dabmusic import DABMusicClient

            # Create client once and reuse it
            if self._dab_client is None:
                print("🔐 Logging in to DAB Music...")
                self._dab_client = DABMusicClient(email, password, self.config.dabmusic_endpoint)
            
            print("🎵 Searching DAB Music...")
            client = self._dab_client

            # Search by ISRC
            track = client.search_by_isrc(isrc)

            if not track:
                print("ℹ️ Track not found on DAB Music")
                return False

            # Verify ISRC matches
            if track.get("isrc") != isrc:
                print(f"⚠️ ISRC mismatch: expected {isrc}, got {track.get('isrc')}")
                return False

            print(f"✅ Found on DAB Music: {track['title']} by {track['artist']}")
            print(f"⬇️ Downloading FLAC from DAB Music...")

            # Generate output path using existing naming convention
            from .metadata import sanitize_filename

            # Use Spotify metadata for filename (same as file metadata)
            if spotify_metadata:
                artist = ", ".join(spotify_metadata["artists"]) if spotify_metadata.get("artists") else track["artist"]
                title = spotify_metadata.get("title", track["title"])
            else:
                artist = track["artist"]
                title = track["title"]

            artist = sanitize_filename(artist)
            title = sanitize_filename(title)
            output_path = self.output_dir / f"{artist} - {title}.flac"

            # Download (quality 27 = FLAC)
            success = client.download_track(track["id"], output_path, quality=27)

            if success:
                # Apply metadata using existing system (pass Spotify metadata for multi-artist)
                self._apply_dab_metadata(output_path, track, isrc, spotify_metadata)

                # Convert FLAC to M4A at 256kbps
                m4a_path = self._convert_to_m4a(output_path)
                if m4a_path:
                    print(f"✅ Downloaded and converted to M4A: {m4a_path}")
                    return True
                else:
                    # Conversion failed, but FLAC is still there
                    print(f"✅ Downloaded FLAC (conversion failed): {output_path}")
                    return True
            else:
                print("❌ DAB Music download failed", file=sys.stderr)
                return False

        except Exception as e:
            print(f"⚠️ DAB Music error: {e}", file=sys.stderr)
            return False

    def _apply_dab_metadata(
        self,
        file_path: Path,
        track: dict,
        isrc: str,
        spotify_metadata: Optional[dict] = None,
    ):
        """Apply metadata to DAB Music download.

        Args:
            file_path: Path to downloaded file
            track: Track data from DAB Music (used only for cover art fallback)
            isrc: ISRC code
            spotify_metadata: Spotify metadata (preferred source for all metadata)
        """
        try:
            import requests
            from mutagen.flac import FLAC

            audio = FLAC(str(file_path))

            # Use Spotify metadata when available (it's always provided for DAB downloads)
            if spotify_metadata:
                # Use Spotify's data for everything
                artist_str = ", ".join(spotify_metadata["artists"]) if spotify_metadata.get("artists") else track.get("artist", "")
                title = spotify_metadata.get("title", track.get("title", ""))
                album = spotify_metadata.get("album", track.get("albumTitle", ""))
                
                print(f"   Using Spotify metadata: {artist_str} - {title}")
            else:
                # Fallback to DAB metadata (shouldn't happen in practice)
                artist_str = track.get("artist", "")
                title = track.get("title", "")
                album = track.get("albumTitle", "")

            # Set basic metadata
            audio["TITLE"] = title
            audio["ARTIST"] = artist_str
            audio["ALBUM"] = album
            audio["DATE"] = track.get("releaseDate", "")  # Keep DAB's release date
            audio["ISRC"] = isrc

            # Keep DAB-specific fields
            if track.get("upc"):
                audio["BARCODE"] = track["upc"]

            if track.get("label"):
                audio["LABEL"] = track["label"]

            # Save metadata
            audio.save()

            # Download and embed cover art
            cover_url = track.get("albumCover")
            if cover_url:
                try:
                    response = requests.get(cover_url, timeout=10)
                    response.raise_for_status()

                    import base64

                    from mutagen.flac import Picture

                    picture = Picture()
                    picture.type = 3  # Cover (front)
                    picture.data = response.content
                    picture.mime = "image/jpeg"

                    audio.add_picture(picture)
                    audio.save()

                except Exception as e:
                    print(f"⚠️ Failed to embed cover art: {e}", file=sys.stderr)

            print(f"✅ Metadata applied (including ISRC: {isrc})")

        except Exception as e:
            print(f"⚠️ Failed to apply metadata: {e}", file=sys.stderr)

    def _convert_to_m4a(self, flac_path: Path) -> Optional[Path]:
        """Convert FLAC to M4A at 256kbps AAC.

        Args:
            flac_path: Path to FLAC file

        Returns:
            Path to M4A file if successful, None otherwise
        """
        import subprocess

        from mutagen.flac import FLAC, Picture
        from mutagen.mp4 import MP4, MP4Cover

        m4a_path = flac_path.with_suffix(".m4a")

        try:
            print(f"🔄 Converting to M4A (256kbps AAC)...")

            # Extract cover art from FLAC before conversion
            flac_audio = FLAC(str(flac_path))
            cover_data = None
            if flac_audio.pictures:
                cover_data = flac_audio.pictures[0].data

            # Use FFmpeg to convert FLAC to M4A (audio only)
            # -vn: No video (skip cover art during conversion)
            # -c:a aac: Use AAC codec
            # -b:a 256k: Set bitrate to 256kbps
            # -movflags +faststart: Optimize for streaming
            # -map_metadata 0: Copy all metadata
            cmd = [
                "ffmpeg",
                "-i",
                str(flac_path),
                "-vn",  # Skip video/cover art
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-movflags",
                "+faststart",
                "-map_metadata",
                "0",
                "-y",  # Overwrite output file
                str(m4a_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=True)

            # Verify M4A file was created
            if not m4a_path.exists():
                raise Exception("M4A file not created")

            # Re-embed cover art into M4A
            if cover_data:
                m4a_audio = MP4(str(m4a_path))
                m4a_audio["covr"] = [
                    MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)
                ]
                m4a_audio.save()
                print(f"✅ Re-embedded cover art")

            # Delete FLAC file
            flac_path.unlink()
            print(f"✅ Converted to M4A and removed FLAC")

            return m4a_path

        except subprocess.CalledProcessError as e:
            print(f"⚠️ FFmpeg conversion failed: {e.stderr}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"⚠️ Conversion error: {e}", file=sys.stderr)
            return None

        except Exception as e:
            print(f"⚠️ DAB Music error: {e}", file=sys.stderr)
            return False

    def detect_source(self, url: str) -> str:
        """Detect source type from URL.

        Args:
            url: URL to analyze

        Returns:
            Source type: 'spotify', 'youtube', 'soundcloud', or 'direct'
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        if "spotify.com" in domain:
            return "spotify"
        elif "youtube.com" in domain or "youtu.be" in domain:
            return "youtube"
        elif "soundcloud.com" in domain:
            return "soundcloud"
        else:
            # Assume direct audio file URL
            return "direct"

    def try_smart_download(
        self,
        url: str,
        format: str,
        isrc: Optional[str] = None,
        spotify_metadata: Optional[dict] = None,
    ) -> bool:
        """Try to download using smart download (ISRC → DAB Music).

        Args:
            url: Track URL (for ISRC lookup if needed)
            format: Output format
            isrc: Pre-fetched ISRC (optional, will lookup if not provided)
            spotify_metadata: Pre-fetched Spotify metadata (optional)

        Returns:
            True if downloaded successfully, False if should fallback to source
        """
        # Use provided ISRC or look it up
        if not isrc:
            source_type = self.detect_source(url)
            if source_type == "direct":
                return False  # Skip smart download for direct URLs

            isrc, spotify_metadata = self._lookup_isrc(url, source_type)

        if isrc:
            print(f"🔍 Found ISRC: {isrc}")
            return self._try_dab_music(isrc, format, spotify_metadata)

        return False

    def download(self, url: str, format: str = "auto"):
        """Download track(s) from URL.

        Args:
            url: URL to download from
            format: Output format (auto, m4a, mp3)
        """
        source_type = self.detect_source(url)

        print(f"🎵 Detected source: {source_type.title()}")
        print(f"📁 Output directory: {self.output_dir}")
        print()

        # Route to appropriate handler (handlers now manage smart downloads internally)
        if source_type == "spotify":
            handler = spotify.SpotifyDownloader(self.config, self.output_dir, self)
        elif source_type == "youtube":
            handler = youtube.YouTubeDownloader(self.config, self.output_dir, self)
        elif source_type == "soundcloud":
            handler = soundcloud.SoundCloudDownloader(self.config, self.output_dir)
        else:
            handler = direct.DirectDownloader(self.config, self.output_dir)

        # Download
        try:
            handler.download(url, format)
        except Exception as e:
            print(f"❌ Download failed: {e}", file=sys.stderr)
            # Log to failed downloads
            self._log_failure(url, str(e))
            raise

    def _log_failure(self, url: str, error: str):
        """Log failed download.

        Args:
            url: URL that failed
            error: Error message
        """
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_entry = f"{timestamp} | {url} | {error}\n"

        with open(self.config.failed_log, "a") as f:
            f.write(log_entry)
