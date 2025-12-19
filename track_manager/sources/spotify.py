"""Spotify downloader using spotdl Python API."""

import sys
from pathlib import Path
from typing import Optional

try:
    from spotdl import Spotdl
    from spotdl.types.song import Song
except ImportError:
    print("Error: spotdl not installed", file=sys.stderr)
    print("Install with: pip install spotdl", file=sys.stderr)
    sys.exit(1)

from .base import BaseDownloader


class SpotifyDownloader(BaseDownloader):
    """Spotify downloader using spotdl."""

    def __init__(self, config, output_dir: Path, parent_downloader=None):
        """Initialize Spotify downloader.

        Args:
            config: Configuration object
            output_dir: Output directory
            parent_downloader: Parent Downloader instance (for smart downloads)
        """
        super().__init__(config, output_dir, parent_downloader)

        # Initialize spotdl
        import os

        from spotdl.types.options import DownloaderOptions

        # Get Spotify credentials from environment variables or config
        client_id = os.getenv("SPOTIPY_CLIENT_ID", "")
        client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "")

        # Fall back to config if not in environment
        if not client_id:
            client_id = config.get("spotdl.client_id", "")
        if not client_secret:
            client_secret = config.get("spotdl.client_secret", "")

        # Validate credentials
        if not client_id or not client_secret:
            print("\n❌ Spotify API credentials not found", file=sys.stderr)
            print(
                "\n📝 Note: Spotify downloads require API credentials", file=sys.stderr
            )
            print(
                "   Other sources (YouTube, SoundCloud, direct URLs) work without setup!\n",
                file=sys.stderr,
            )
            print("🔧 Setup options:\n", file=sys.stderr)
            print("   1. Run: track-manager init", file=sys.stderr)
            print(
                "      Then edit: ~/.config/track-manager/config.yaml\n",
                file=sys.stderr,
            )
            print("   2. Or set environment variables:", file=sys.stderr)
            print("      export SPOTIPY_CLIENT_ID='your_id'", file=sys.stderr)
            print("      export SPOTIPY_CLIENT_SECRET='your_secret'\n", file=sys.stderr)
            print("🔑 Get credentials:", file=sys.stderr)
            print("   https://developer.spotify.com/dashboard", file=sys.stderr)
            print("   (Create app → Copy Client ID & Secret)\n", file=sys.stderr)
            sys.exit(1)

        downloader_settings = DownloaderOptions()
        downloader_settings["output"] = str(output_dir)
        # Don't set format to m4a - let yt-dlp args control the download
        # This forces spotdl to use yt_dlp_args instead of looking for native m4a
        downloader_settings["format"] = "m4a"
        downloader_settings["bitrate"] = "192"
        # Prefer format 251 (Opus ~160kbps, 20kHz) over 140 (AAC ~128kbps, 16kHz)
        downloader_settings["yt_dlp_args"] = "--format 251/140/bestaudio/best"

        self.spotdl = Spotdl(
            client_id=client_id,
            client_secret=client_secret,
            downloader_settings=downloader_settings,
        )

    def download(self, url: str, format: str = "auto"):
        """Download track(s) from Spotify.

        Args:
            url: Spotify URL (track, playlist, or album)
            format: Output format (auto, m4a, mp3)
        """
        # Determine output format
        if format == "auto":
            audio_format = "m4a"
        else:
            audio_format = format

        print("🔍 Finding tracks on Spotify...")
        print(f"URL: {url}")
        print()

        try:
            # Get songs from URL
            # Apply rate limiting before fetching playlist
            from ..rate_limiter import spotify_rate_limit
            spotify_rate_limit()
            
            songs = self.spotdl.search([url])

            if not songs:
                print("❌ No tracks found", file=sys.stderr)
                self.log_failure(url, "No tracks found")
                return

            track_count = len(songs)
            print(f"✅ Found {track_count} tracks")
            print()
            
            # Determine if this is a playlist/album (multiple tracks)
            playlist_url = url if track_count > 1 else None

            # Ask for confirmation if > threshold
            if track_count > self.config.playlist_threshold:
                response = input(
                    f"⚠️ Large playlist ({track_count} tracks). Continue? [y/N]: "
                )
                if response.lower() != "y":
                    print("Cancelled")
                    return

            print("⬇️ Downloading...")
            print()

            success = 0
            failed = 0

            for idx, song in enumerate(songs, 1):
                print(f"[{idx}/{track_count}] {song.artist} - {song.name}")

                try:
                    # Check for duplicates BEFORE downloading
                    existing_duplicates = self._check_existing_duplicates(
                        song, audio_format
                    )
                    if existing_duplicates:
                        print(
                            f"⏭️ Skipped: Already exists at {existing_duplicates[0].name}"
                        )
                        continue

                    # Try smart download if parent downloader available
                    if self.parent_downloader and song.isrc:
                        spotify_metadata = {
                            "artists": song.artists,
                            "title": song.name,
                            "album": song.album_name,
                        }
                        
                        smart_success = self.parent_downloader.try_smart_download(
                            song.url,
                            audio_format,
                            isrc=song.isrc,
                            spotify_metadata=spotify_metadata,
                            playlist_url=playlist_url,
                        )
                        
                        if smart_success:
                            success += 1
                            continue

                    # Fallback: Download using spotdl
                    # With format="opus", spotdl will respect yt_dlp_args and download format 251
                    print("  ⬇️ Downloading from YouTube (via spotdl)")
                    result = self.spotdl.download(song)

                    if result:
                        # Find downloaded file (will be .m4a after conversion)
                        file_path = self._find_downloaded_file(song, audio_format)

                        if file_path and self._process_download(
                            file_path, song, audio_format, playlist_url
                        ):
                            success += 1
                        else:
                            failed += 1
                    else:
                        print("⚠️ Download failed")
                        self.log_failure(song.url, "Download returned None")
                        failed += 1

                except Exception as e:
                    print(f"⚠️ Error: {e}", file=sys.stderr)
                    self.log_failure(song.url, str(e))
                    failed += 1

                print()

            # Summary
            print()
            print("━" * 60)
            print("✅ Download complete")
            print(f"   Success: {success}")
            if failed > 0:
                print(f"   Failed: {failed} (see {self.config.failed_log})")

        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            self.log_failure(url, str(e))
            raise

    def _find_downloaded_file(self, song: Song, format: str) -> Optional[Path]:
        """Find the downloaded file for a song.

        Args:
            song: Song object
            format: Expected format

        Returns:
            Path to downloaded file or None
        """
        from datetime import datetime, timedelta

        # First, try to download the song and get the exact file path from spotdl
        try:
            result = self.spotdl.download(song)
            if result and len(result) >= 2:
                file_path = result[1]
                if isinstance(file_path, Path) and file_path.exists():
                    return file_path
        except Exception as e:
            # If spotdl download fails, falling back to file search
            logger.debug(f"spotdl download failed: {e}")  # nosec B110

        # Fallback: search for files containing the song title
        # Use a more reasonable time window (10 minutes) to account for existing files
        cutoff_time = datetime.now().timestamp() - 600  # 10 minutes
        title_part = self.sanitize_filename(song.name).lower()

        # Search in the expected format first
        for file_path in self.output_dir.glob(f"*.{format}"):
            # Check if file was created recently enough
            if file_path.stat().st_mtime > cutoff_time:
                # Check if title appears in filename
                if title_part in file_path.stem.lower():
                    return file_path

        # Also try MP3 if looking for other formats
        if format != "mp3":
            for file_path in self.output_dir.glob("*.mp3"):
                if file_path.stat().st_mtime > cutoff_time:
                    if title_part in file_path.stem.lower():
                        return file_path

        # Final fallback: check for any file with the title, regardless of timestamp
        for file_path in self.output_dir.glob(f"*.{format}"):
            if title_part in file_path.stem.lower():
                return file_path

        if format != "mp3":
            for file_path in self.output_dir.glob("*.mp3"):
                if title_part in file_path.stem.lower():
                    return file_path

        return None

    def _check_existing_duplicates(self, song: Song, format: str) -> list:
        """Check if track already exists in library before downloading.

        Args:
            song: Song object
            format: Expected format

        Returns:
            List of existing duplicate file paths, empty if no duplicates
        """
        from ..duplicates import find_duplicates, find_duplicates_by_isrc, find_duplicates_by_track_url

        # Priority 1: Check by track URL (most comprehensive)
        if song.url:
            duplicates = find_duplicates_by_track_url(song.url, self.output_dir)
            if duplicates:
                return duplicates

        # Priority 2: Check by ISRC (very reliable)
        if song.isrc:
            duplicates = find_duplicates_by_isrc(song.isrc, self.output_dir)
            if duplicates:
                return duplicates

        # Priority 3: Check by metadata (fallback)
        artist = song.artist
        title = song.name

        if not artist or not title:
            return []

        duplicates = find_duplicates(artist, title, self.output_dir)

        return duplicates

    def _download_from_youtube(
        self, song: Song, format: str, playlist_url: Optional[str] = None
    ) -> bool:
        """Download from YouTube using yt-dlp directly.
        
        This ensures we get format 251 (Opus ~160kbps, 20kHz) and convert to M4A
        instead of getting format 140 (native M4A ~128kbps, 16kHz).
        
        Args:
            song: Song object with download_url
            format: Audio format (m4a or mp3)
            playlist_url: Optional playlist URL
            
        Returns:
            True if successful
        """
        import yt_dlp
        
        ydl_opts = {
            "format": "251/140/bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": format,
                    "preferredquality": "192",
                },
                {
                    "key": "EmbedThumbnail",
                }
            ],
            "outtmpl": str(self.output_dir / ".tmp_%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": False,
            "extract_flat": False,
            "remote_components": ["ejs:github"],
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(song.download_url, download=True)
                
                # Find the downloaded file
                video_id = info.get("id")
                temp_file = None
                
                for ext in [format, "m4a", "mp3", "opus", "webm"]:
                    potential_file = self.output_dir / f".tmp_{video_id}.{ext}"
                    if potential_file.exists():
                        temp_file = potential_file
                        break
                
                if not temp_file or not temp_file.exists():
                    print(f"⚠️ Downloaded file not found")
                    return False
                
                # Use Spotify metadata (more reliable than YouTube)
                artist = song.artist
                title = song.name
                
                # Create final filename
                final_name = self.create_filename(artist, title, format)
                final_path = self.output_dir / final_name
                
                # Check for duplicates
                if self.check_duplicate(temp_file):
                    temp_file.unlink()
                    print("⏭️ Skipped (duplicate)")
                    return True
                
                # Move to final location
                temp_file.rename(final_path)
                
                # Add provenance metadata (including ISRC from Spotify)
                # When downloaded from YouTube via yt-dlp with format 251 -> M4A conversion
                self._add_provenance_metadata(
                    final_path,
                    song.url,
                    format,
                    info.get("abr", 192),  # Use reported bitrate or default to 192
                    playlist_url,
                    isrc=song.isrc,
                )
                
                print(f"✅ Saved: {final_name}")
                return True
                
        except Exception as e:
            print(f"⚠️ Download failed: {e}", file=sys.stderr)
            self.log_failure(song.download_url, str(e))
            return False

    def _process_download(
        self, file_path: Path, song: Song, format: str, playlist_url: Optional[str] = None
    ) -> bool:
        """Process a downloaded file.

        Args:
            file_path: Path to downloaded file
            song: Song object
            format: Desired format
            playlist_url: Optional playlist URL if from a playlist

        Returns:
            True if successful
        """
        try:
            # Extract metadata
            artist, title = self.extract_metadata(file_path)

            # Verify metadata is good
            if not artist or not title:
                # Use Spotify metadata as fallback
                artist = song.artist
                title = song.name

                # Flag for review
                self.flag_metadata_review(
                    file_path, "Missing or incomplete metadata from Spotify", song.url
                )

            # Create final filename
            final_name = self.create_filename(artist, title, file_path.suffix[1:])
            final_path = self.output_dir / final_name

            # Check for duplicates
            if self.check_duplicate(file_path):
                # User chose to skip
                file_path.unlink()
                print("⏭️ Skipped (duplicate)")
                return True

            # Rename if needed
            if file_path != final_path:
                file_path.rename(final_path)

            # Add provenance metadata
            # Spotify via spotdl uses YouTube, so bitrate is typically 128kbps
            self._add_provenance_metadata(
                final_path,
                song.url,
                final_path.suffix[1:],  # Get format from file extension
                128,  # Spotify via spotdl downloads at ~128kbps from YouTube
                playlist_url,
                isrc=song.isrc,
            )

            print(f"✅ Saved: {final_name}")
            return True

        except Exception as e:
            print(f"⚠️ Error processing: {e}", file=sys.stderr)
            return False
