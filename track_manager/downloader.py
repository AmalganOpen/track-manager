"""Main downloader orchestrator."""

import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import Config
from .songlink import SongLinkClient
from .sources import direct, soundcloud, spotify, youtube
from .rate_limiter import spotify_rate_limit, dab_rate_limit


class Downloader:
    """Main downloader class that routes to appropriate source handler."""

    def __init__(self, config: Config, output_dir: Optional[Path] = None, dumb: bool = False):
        """Initialize downloader.

        Args:
            config: Configuration object
            output_dir: Override output directory
            dumb: If True, disable smart downloads
        """
        self.config = config
        self.output_dir = output_dir or config.output_dir
        self.dumb = dumb

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def _has_spotify_credentials(self) -> bool:
        """Check if Spotify API credentials are available.
        
        Returns:
            True if both client_id and client_secret are configured
        """
        import os
        
        client_id = os.getenv("SPOTIPY_CLIENT_ID", "")
        client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "")
        
        if not client_id:
            client_id = self.config.get("spotdl.client_id", "")
        if not client_secret:
            client_secret = self.config.get("spotdl.client_secret", "")
        
        return bool(client_id and client_secret)
        
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

            # Get track data with rate limiting
            spotify_rate_limit()
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
            spotify_id = self._extract_spotify_id(spotify_url)
            if spotify_id:
                return self._get_isrc_from_spotify(spotify_id, return_metadata=True)
        else:
            print("ℹ️ No match found on song.link")
            print("   Proceeding to download from original source")

        return None, None

    def _try_dab_music(
        self,
        isrc: str,
        format: str,
        spotify_metadata: Optional[dict] = None,
        track_url: Optional[str] = None,
        playlist_url: Optional[str] = None,
    ) -> bool:
        """Try to download from DAB Music using ISRC.

        Args:
            isrc: ISRC code
            format: Output format
            spotify_metadata: Optional Spotify metadata (for multi-artist support)
            track_url: Original track URL
            playlist_url: Playlist URL if from a playlist

        Returns:
            True if successful, False otherwise
        """
        from .provenance import DownloadProvenance
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
                # Create provenance information
                provenance = DownloadProvenance(
                    track_url=track_url or f"isrc:{isrc}",
                    playlist_url=playlist_url,
                    source="dab",
                    original_format="flac",
                    original_bitrate=None,  # FLAC is lossless
                )
                
                # Collect all metadata (don't apply to FLAC yet)
                metadata = self._collect_dab_metadata(track, isrc, spotify_metadata)
                
                # Convert FLAC to M4A and apply all metadata at once
                m4a_path = self._convert_to_m4a(output_path, metadata, provenance)
                if m4a_path:
                    print(f"✅ Downloaded and converted to M4A: {m4a_path}")
                    print()
                    return True
                else:
                    # Conversion failed, but FLAC is still there
                    print(f"✅ Downloaded FLAC (conversion failed): {output_path}")
                    print()
                    return True
            else:
                print("❌ DAB Music download failed", file=sys.stderr)
                print()
                return False

        except Exception as e:
            print(f"⚠️ DAB Music error: {e}", file=sys.stderr)
            return False

    def _try_tidal_public(
        self,
        url: str,
        format: str,
        spotify_metadata: Optional[dict] = None,
        playlist_url: Optional[str] = None,
    ) -> bool:
        """Try to download from public TIDAL API (no credentials required).

        Args:
            url: Track URL (any platform)
            format: Output format
            spotify_metadata: Optional Spotify metadata
            playlist_url: Playlist URL if from a playlist

        Returns:
            True if successful, False otherwise
        """
        from .provenance import DownloadProvenance
        from .tidal_public import TidalPublicClient

        try:
            # Create client
            if not hasattr(self, '_tidal_client'):
                self._tidal_client = TidalPublicClient()
            
            client = self._tidal_client

            print("🎵 Looking up track on TIDAL...")
            
            # Get TIDAL ID from URL via song.link
            tidal_id = client.get_tidal_id_from_url(url)
            
            if not tidal_id:
                print("ℹ️ Track not found on TIDAL")
                return False

            # Get track info
            track = client.get_track_info(tidal_id)
            
            if not track:
                print("ℹ️ Could not get track info from TIDAL")
                return False

            print(f"✅ Found on TIDAL: {track['title']} by {track['artist']['name']}")
            
            # Generate output path using existing naming convention
            from .metadata import sanitize_filename

            # Use Spotify metadata for filename (same as file metadata)
            if spotify_metadata:
                artist = ", ".join(spotify_metadata["artists"]) if spotify_metadata.get("artists") else track["artist"]["name"]
                title = spotify_metadata.get("title", track["title"])
            else:
                artist = track["artist"]["name"]
                title = track["title"]

            artist = sanitize_filename(artist)
            title = sanitize_filename(title)
            output_path = self.output_dir / f"{artist} - {title}.flac"

            # Download FLAC
            success = client.download_track(tidal_id, output_path, quality="LOSSLESS")

            if success:
                # Create provenance information
                provenance = DownloadProvenance(
                    track_url=url,
                    playlist_url=playlist_url,
                    source="tidal-public",
                    original_format="flac",
                    original_bitrate=None,  # FLAC is lossless
                )
                
                # Collect all metadata (use TIDAL data as fallback)
                metadata = self._collect_tidal_metadata(track, spotify_metadata)
                
                # Convert FLAC to M4A and apply all metadata at once
                m4a_path = self._convert_to_m4a(output_path, metadata, provenance)
                if m4a_path:
                    print(f"✅ Downloaded and converted to M4A: {m4a_path}")
                    print()
                    return True
                else:
                    # Conversion failed, but FLAC is still there
                    print(f"✅ Downloaded FLAC (conversion failed): {output_path}")
                    print()
                    return True
            else:
                print("❌ TIDAL download failed", file=sys.stderr)
                print()
                return False

        except Exception as e:
            print(f"⚠️ TIDAL error: {e}", file=sys.stderr)
            return False

    def _collect_tidal_metadata(
        self,
        track: dict,
        spotify_metadata: Optional[dict] = None,
    ) -> dict:
        """Collect metadata from TIDAL download.

        Args:
            track: Track data from TIDAL
            spotify_metadata: Spotify metadata (preferred source for all metadata)

        Returns:
            Dictionary of metadata to apply
        """
        # Extract cover URL from TIDAL (always use TIDAL's cover art)
        cover_url = None
        if track.get("album", {}).get("cover"):
            cover_id = track["album"]["cover"]
            # TIDAL cover art URL pattern: replace dashes with slashes
            cover_path = cover_id.replace("-", "/")
            cover_url = f"https://resources.tidal.com/images/{cover_path}/1280x1280.jpg"
        
        # Prefer Spotify metadata over TIDAL when available
        if spotify_metadata:
            return {
                "title": spotify_metadata.get("title"),
                "artist": ", ".join(spotify_metadata["artists"]) if spotify_metadata.get("artists") else None,
                "album": spotify_metadata.get("album"),
                "date": spotify_metadata.get("date"),
                "isrc": track.get("isrc"),  # Always use TIDAL's ISRC
                "cover_url": cover_url,  # Always use TIDAL's cover art
            }
        
        # Use TIDAL metadata as fallback
        # Handle both single artist and multiple artists
        artists = track.get("artists", [])
        if artists:
            artist = ", ".join([a["name"] for a in artists])
        else:
            artist = track.get("artist", {}).get("name")
        
        return {
            "title": track.get("title"),
            "artist": artist,
            "album": track.get("album", {}).get("title"),
            "date": track.get("streamStartDate", "").split("T")[0] if track.get("streamStartDate") else None,
            "isrc": track.get("isrc"),
            "cover_url": cover_url,
        }

    def _collect_dab_metadata(
        self,
        track: dict,
        isrc: str,
        spotify_metadata: Optional[dict] = None,
    ) -> dict:
        """Collect metadata from DAB Music download.

        Args:
            track: Track data from DAB Music
            isrc: ISRC code
            spotify_metadata: Spotify metadata (preferred source for all metadata)

        Returns:
            Dictionary of metadata to apply
        """
        # Use Spotify metadata when available (it's always provided for DAB downloads)
        if spotify_metadata:
            artist_str = ", ".join(spotify_metadata["artists"]) if spotify_metadata.get("artists") else track.get("artist", "")
            title = spotify_metadata.get("title", track.get("title", ""))
            album = spotify_metadata.get("album", track.get("albumTitle", ""))
        else:
            # Fallback to DAB metadata (shouldn't happen in practice)
            artist_str = track.get("artist", "")
            title = track.get("title", "")
            album = track.get("albumTitle", "")

        # Collect all metadata
        metadata = {
            'title': title,
            'artist': artist_str,
            'album': album,
            'date': track.get("releaseDate", ""),
            'isrc': isrc,
        }
        
        # Add optional fields
        if track.get("upc"):
            metadata['barcode'] = track["upc"]
        if track.get("label"):
            metadata['label'] = track["label"]
        if track.get("albumCover"):
            metadata['cover_url'] = track["albumCover"]
        
        return metadata
    
    def _apply_dab_metadata(
        self,
        file_path: Path,
        track: dict,
        isrc: str,
        spotify_metadata: Optional[dict] = None,
    ):
        """Apply metadata to DAB Music download.
        
        DEPRECATED: Use _collect_dab_metadata + _convert_to_m4a instead.
        This method is kept for backward compatibility but will be removed.

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

            # Collect metadata
            metadata = self._collect_dab_metadata(track, isrc, spotify_metadata)
            
            # Apply to FLAC
            audio["TITLE"] = metadata['title']
            audio["ARTIST"] = metadata['artist']
            audio["ALBUM"] = metadata['album']
            audio["DATE"] = metadata.get('date', '')
            audio["ISRC"] = metadata['isrc']

            if metadata.get('barcode'):
                audio["BARCODE"] = metadata['barcode']
            if metadata.get('label'):
                audio["LABEL"] = metadata['label']

            audio.save()

            # Download and embed cover art
            cover_url = metadata.get('cover_url')
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

    def _convert_to_m4a(
        self,
        flac_path: Path,
        metadata: dict,
        provenance: Optional["DownloadProvenance"] = None,
    ) -> Optional[Path]:
        """Convert FLAC to M4A at 256kbps AAC and apply all metadata.

        Args:
            flac_path: Path to FLAC file
            metadata: Metadata dictionary to apply
            provenance: Download provenance information

        Returns:
            Path to M4A file if successful, None otherwise
        """
        import subprocess

        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4, MP4Cover

        m4a_path = flac_path.with_suffix(".m4a")

        try:
            print(f"🔄 Converting to M4A (256kbps AAC)...")

            # Extract cover art from FLAC before conversion (if embedded)
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
                "-ar",
                "48000",  # Standard sample rate for DJ software compatibility
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

            # Apply all metadata to M4A
            m4a_audio = MP4(str(m4a_path))
            
            # Basic metadata (iTunes standard atoms)
            if metadata.get('title'):
                m4a_audio['\xa9nam'] = metadata['title']
            if metadata.get('artist'):
                m4a_audio['\xa9ART'] = metadata['artist']
            if metadata.get('album'):
                m4a_audio['\xa9alb'] = metadata['album']
            if metadata.get('date'):
                m4a_audio['\xa9day'] = metadata['date']
            
            # Music metadata (freeform atoms)
            if metadata.get('isrc'):
                m4a_audio['----:com.apple.iTunes:ISRC'] = metadata['isrc'].encode('utf-8')
            if metadata.get('barcode'):
                m4a_audio['----:com.apple.iTunes:BARCODE'] = metadata['barcode'].encode('utf-8')
            if metadata.get('label'):
                m4a_audio['----:com.apple.iTunes:LABEL'] = metadata['label'].encode('utf-8')
            
            # Provenance metadata (freeform atoms)
            if provenance:
                m4a_audio['----:com.apple.iTunes:TRACK_URL'] = provenance.track_url.encode('utf-8')
                if provenance.playlist_url:
                    m4a_audio['----:com.apple.iTunes:PLAYLIST_URL'] = provenance.playlist_url.encode('utf-8')
                m4a_audio['----:com.apple.iTunes:SOURCE'] = provenance.source.encode('utf-8')
                m4a_audio['----:com.apple.iTunes:ORIGINAL_FORMAT'] = provenance.original_format.encode('utf-8')
                if provenance.original_bitrate:
                    m4a_audio['----:com.apple.iTunes:ORIGINAL_BITRATE'] = str(provenance.original_bitrate).encode('utf-8')
            
            # Download and embed cover art
            if not cover_data and metadata.get('cover_url'):
                try:
                    import requests
                    response = requests.get(metadata['cover_url'], timeout=10)
                    response.raise_for_status()
                    cover_data = response.content
                except Exception as e:
                    print(f"⚠️ Failed to download cover art: {e}")
            
            # Embed cover art
            if cover_data:
                m4a_audio["covr"] = [
                    MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)
                ]
            
            m4a_audio.save()
            
            # Print confirmation
            print(f"🔄 Applied metadata (ISRC: {metadata.get('isrc', 'N/A')})")
            if provenance:
                print(f"🔄 Added provenance (source: {provenance.source}, format: {provenance.original_format})")
            if cover_data:
                print(f"🔄 Embedded cover art")

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
        
        Raises:
            ValueError: If URL is invalid or not supported
        """
        parsed = urlparse(url)
        
        # Validate URL has proper scheme
        if not parsed.scheme or parsed.scheme not in ['http', 'https']:
            raise ValueError(
                f"Invalid URL: '{url}'\n"
                "URLs must start with http:// or https://\n"
                "Run 'track-manager --help' for usage examples"
            )
        
        # Validate URL has domain
        if not parsed.netloc:
            raise ValueError(
                f"Invalid URL: '{url}'\n"
                "URL must include a domain name\n"
                "Run 'track-manager --help' for usage examples"
            )
        
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
        playlist_url: Optional[str] = None,
    ) -> bool:
        """Try to download using smart download (ISRC → DAB Music).

        Args:
            url: Track URL (for ISRC lookup if needed)
            format: Output format
            isrc: Pre-fetched ISRC (optional, will lookup if not provided)
            spotify_metadata: Pre-fetched Spotify metadata (optional)
            playlist_url: Playlist URL if downloading from a playlist

        Returns:
            True if downloaded successfully, False if should fallback to source
        """
        # Skip smart download if dumb mode is enabled
        if self.dumb:
            return False
        
        # Note: ISRC parameter is only used when provided by Spotify playlists
        # For individual URLs, we use song.link → TIDAL which provides ISRC
        if isrc:
            print(f"🔍 Using ISRC from Spotify: {isrc}")
        
        # Skip smart download for direct audio URLs
        source_type = self.detect_source(url)
        if source_type == "direct":
            return False
        
        # Try public TIDAL API (no credentials required)
        # TIDAL will get metadata from song.link, no Spotify API needed
        return self._try_tidal_public(
            url,
            format,
            spotify_metadata,
            playlist_url=playlist_url,
        )

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
            # Check if Spotify credentials are available
            if not self._has_spotify_credentials():
                # Determine if it's a playlist/album or single track
                is_playlist = "/playlist/" in url or "/album/" in url
                
                if is_playlist:
                    print("❌ Spotify playlists/albums require API credentials", file=sys.stderr)
                    print("\n📝 Spotify API credentials are optional but needed for playlists:", file=sys.stderr)
                    print("   • Individual Spotify tracks work without credentials (via TIDAL)", file=sys.stderr)
                    print("   • Playlists/albums require Spotify API setup\n", file=sys.stderr)
                    print("🔧 To enable Spotify playlist support:", file=sys.stderr)
                    print("   1. Get credentials from: https://developer.spotify.com/dashboard", file=sys.stderr)
                    print("   2. Set environment variables:", file=sys.stderr)
                    print("      export SPOTIPY_CLIENT_ID='your_id'", file=sys.stderr)
                    print("      export SPOTIPY_CLIENT_SECRET='your_secret'", file=sys.stderr)
                    print("   3. Or add to config.yaml:", file=sys.stderr)
                    print("      spotdl:", file=sys.stderr)
                    print("        client_id: 'your_id'", file=sys.stderr)
                    print("        client_secret: 'your_secret'\n", file=sys.stderr)
                    self._log_failure(url, "Spotify playlist requires API credentials")
                    return
                else:
                    # Single track - download via TIDAL (song.link)
                    print("ℹ️ Spotify API not configured, downloading via TIDAL")
                    print("   (For playlist support, add Spotify API credentials)")
                    print()
                    
                    # Try smart download directly (bypasses Spotify handler)
                    success = self.try_smart_download(url, format)
                    if success:
                        return
                    else:
                        print("❌ Failed to download via TIDAL", file=sys.stderr)
                        print("   Spotify API credentials needed for this track", file=sys.stderr)
                        self._log_failure(url, "TIDAL download failed, Spotify API needed")
                        return
            
            # If we get here, we have Spotify credentials
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
