"""YouTube downloader using yt-dlp Python API."""

import sys
import tempfile
from pathlib import Path
from typing import Optional

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp not installed", file=sys.stderr)
    print("Install with: pip install yt-dlp", file=sys.stderr)
    sys.exit(1)

from .base import BaseDownloader


class YouTubeDownloader(BaseDownloader):
    """YouTube downloader."""

    def download(self, url: str, format: str = "auto"):
        """Download video(s) from YouTube.

        Args:
            url: YouTube URL (video or playlist)
            format: Output format (auto, m4a, mp3)
        """
        # Determine output format
        if format == "auto": 
            audio_format = "m4a"
        else:
            audio_format = format

        # Configure yt-dlp options
        ydl_opts = {
            # Prefer high-quality audio formats
            # 251: Opus ~160kbps (best quality, requires conversion to M4A)
            # 140: M4A ~128kbps (native, no conversion needed)
            "format": "251/140/bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "192",
                }
            ],
            "outtmpl": str(self.output_dir / ".tmp_%(id)s.%(ext)s"),
            "quiet": False,
            "no_warnings": False,
            "extract_flat": False,
            # Enable remote components for solving JS challenges to access high-quality formats
            "remote_components": ["ejs:github"],
        }

        # Check if it's a playlist
        with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True}) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                is_playlist = info.get("_type") == "playlist"

                if is_playlist:
                    track_count = len(info.get("entries", []))
                    print(f"📝 Playlist detected: {track_count} videos", flush=True)

                    # Ask for confirmation if > threshold
                    if track_count > self.config.playlist_threshold:
                        response = input(
                            f"⚠️ Large playlist ({track_count} videos). Continue? [y/N]: "
                        )
                        if response.lower() != "y":
                            print("Cancelled")
                            return
            except Exception as e:
                print(f"⚠️ Could not extract info: {e}", file=sys.stderr)
                is_playlist = False

        # Download
        print("⬇️ Downloading...")
        print()

        success = 0
        failed = 0

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Download
                info = ydl.extract_info(url, download=True)

                # Process downloaded files
                if is_playlist:
                    entries = info.get("entries", [])
                    total = len(entries)

                    for idx, entry in enumerate(entries, 1):
                        if entry:
                            print(
                                f"[{idx}/{total}] Processing: {entry.get('title', 'Unknown')}"
                            )

                            if self._process_download(entry, audio_format):
                                success += 1
                            else:
                                failed += 1
                            print()
                else:
                    # Single video
                    if self._process_download(info, audio_format):
                        success += 1
                    else:
                        failed += 1

                # Summary
                if is_playlist:
                    print()
                    print("━" * 60)
                    print("✅ Download complete")
                    print(f"  Success: {success}")
                    if failed > 0:
                        print(f"  Failed: {failed} (see {self.config.failed_log})")

            except Exception as e:
                print(f"❌ Download failed: {e}", file=sys.stderr)
                self.log_failure(url, str(e))
                raise

    def _process_download(self, info: dict, audio_format: str) -> bool:
        """Process a downloaded file.

        Args:
            info: Video info dict from yt-dlp
            audio_format: Audio format (m4a or mp3)

        Returns:
            True if successful, False if failed
        """
        try:
            # Find the downloaded file
            video_id = info.get("id")
            temp_file = None

            # Check for common extensions
            for ext in [audio_format, "m4a", "mp3", "opus", "webm"]:
                potential_file = self.output_dir / f".tmp_{video_id}.{ext}"
                if potential_file.exists():
                    temp_file = potential_file
                    break

            if not temp_file or not temp_file.exists():
                print(f"⚠️ Downloaded file not found for {video_id}")
                return False

            # Extract metadata
            artist, title = self.extract_metadata(temp_file)

            # Create final filename
            if not artist or not title:
                # Use video title as fallback
                video_title = info.get("title", "unknown")
                artist = info.get("uploader", "Unknown")
                title = video_title

                # Flag for review
                self.flag_metadata_review(
                    temp_file,
                    "Missing or incomplete metadata from YouTube",
                    info.get("webpage_url", ""),
                )

            final_name = self.create_filename(
                artist, title, audio_format, fallback=f"youtube-{video_id}"
            )
            final_path = self.output_dir / final_name

            # Check for duplicates
            if self.check_duplicate(temp_file):
                # User chose to skip
                temp_file.unlink()
                print("⏭️ Skipped (duplicate)")
                return True

            # Move to final location
            temp_file.rename(final_path)
            print(f"✅ Saved: {final_name}")

            return True

        except Exception as e:
            print(f"⚠️ Error processing download: {e}", file=sys.stderr)
            return False
