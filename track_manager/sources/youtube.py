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

        # Check if it's a playlist and extract entries
        is_playlist = False
        playlist_entries = []
        
        # Detect if URL is a playlist (don't use extract_flat for detection)
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "noplaylist": False,  # Extract playlists even when video ID is present
            "extract_flat": "in_playlist",  # Only flat-extract playlist entries
        }) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                is_playlist = info.get("_type") == "playlist"

                if is_playlist:
                    playlist_entries = info.get("entries", [])
                    track_count = len(playlist_entries)
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

        # Download tracks
        success = 0
        failed = 0
        
        # Store playlist URL if it's a playlist
        playlist_url = url if is_playlist else None

        if is_playlist and self.parent_downloader:
            # Try smart download for each track in playlist
            total = len(playlist_entries)
            
            for idx, entry in enumerate(playlist_entries, 1):
                if not entry:
                    continue
                    
                video_url = entry.get("url")
                title = entry.get("title", "Unknown")
                
                print(f"[{idx}/{total}] {title}")
                
                try:
                    # Try smart download
                    print("  🔗 Trying smart download...")
                    smart_success = self.parent_downloader.try_smart_download(
                        video_url, audio_format, playlist_url=playlist_url
                    )
                    
                    if smart_success:
                        success += 1
                        continue
                    
                    # Fallback to yt-dlp
                    print("  ⬇️ Downloading from YouTube")
                    if self._download_single_video(video_url, audio_format, playlist_url):
                        success += 1
                    else:
                        failed += 1
                        
                except Exception as e:
                    print(f"  ⚠️ Error: {e}", file=sys.stderr)
                    self.log_failure(video_url, str(e))
                    failed += 1
                
                print()
            
            # Summary
            print()
            print("━" * 60)
            print("✅ Download complete")
            print(f"   Success: {success}")
            if failed > 0:
                print(f"  Failed: {failed} (see {self.config.failed_log})")
        else:
            # Single video with smart download support
            if not is_playlist and self.parent_downloader:
                print("🔗 Trying smart download...")
                smart_success = self.parent_downloader.try_smart_download(
                    url, audio_format
                )
                
                if smart_success:
                    print("✅ Downloaded via smart download")
                    return
                
                print("⬇️ Downloading from YouTube")
                print()
            
            # Original flow for single videos or when no parent downloader
            ydl_opts = {
                "format": "251/140/bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_format,
                        "preferredquality": "192",
                    }
                ],
                "outtmpl": str(self.output_dir / ".tmp_%(id)s.%(ext)s"),
                "quiet": True,
                "no_warnings": False,
                "extract_flat": False,
                "remote_components": ["ejs:github"],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)

                    if is_playlist:
                        entries = info.get("entries", [])
                        total = len(entries)

                        for idx, entry in enumerate(entries, 1):
                            if entry:
                                print(
                                    f"[{idx}/{total}] Processing: {entry.get('title', 'Unknown')}"
                                )

                                if self._process_download(entry, audio_format, playlist_url):
                                    success += 1
                                else:
                                    failed += 1
                                print()
                    else:
                        if self._process_download(info, audio_format, playlist_url):
                            success += 1
                        else:
                            failed += 1

                    if is_playlist:
                        print()
                        print("━" * 60)
                        print("✅ Download complete")
                        print(f"   Success: {success}")
                        if failed > 0:
                            print(f"   Failed: {failed} (see {self.config.failed_log})")

                except Exception as e:
                    print(f"❌ Download failed: {e}", file=sys.stderr)
                    self.log_failure(url, str(e))
                    raise

    def _download_single_video(
        self, video_url: str, audio_format: str, playlist_url: Optional[str] = None
    ) -> bool:
        """Download a single video and process it.

        Args:
            video_url: URL of the video
            audio_format: Audio format (m4a or mp3)
            playlist_url: Optional playlist URL if from a playlist

        Returns:
            True if successful, False if failed
        """
        ydl_opts = {
            "format": "251/140/bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "192",
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
                info = ydl.extract_info(video_url, download=True)
                return self._process_download(info, audio_format, playlist_url)
        except Exception as e:
            print(f"  ⚠️ Download failed: {e}", file=sys.stderr)
            return False

    def _process_download(
        self, info: dict, audio_format: str, playlist_url: Optional[str] = None
    ) -> bool:
        """Process a downloaded file.

        Args:
            info: Video info dict from yt-dlp
            audio_format: Audio format (m4a or mp3)
            playlist_url: Optional playlist URL if from a playlist

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
            
            # Add provenance metadata
            self._add_provenance_metadata(
                final_path,
                info.get("webpage_url", ""),
                info.get("ext", audio_format),
                info.get("abr"),  # Average bitrate
                playlist_url,
            )
            
            print(f"✅ Saved: {final_name}")

            return True

        except Exception as e:
            print(f"⚠️ Error processing download: {e}", file=sys.stderr)
            return False
