"""YouTube downloader using yt-dlp Python API."""

import sys
import tempfile
from pathlib import Path
from typing import Optional, Literal
from urllib.parse import urlparse, parse_qs

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp not installed", file=sys.stderr)
    print("Install with: pip install yt-dlp", file=sys.stderr)
    sys.exit(1)

from .base import BaseDownloader


URLType = Literal["video", "playlist", "video_in_playlist"]


def parse_youtube_url(url: str) -> tuple[URLType, Optional[str], Optional[str]]:
    """Parse a YouTube URL to determine its type.
    
    Args:
        url: YouTube URL to parse
        
    Returns:
        Tuple of (url_type, video_id, playlist_id)
        - url_type: "video", "playlist", or "video_in_playlist"
        - video_id: Video ID if present
        - playlist_id: Playlist ID if present
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    
    # Extract IDs
    video_id = query.get("v", [None])[0]
    playlist_id = query.get("list", [None])[0]
    
    # Check path for playlist URLs
    if "/playlist" in parsed.path and playlist_id:
        return "playlist", None, playlist_id
    
    # Video with playlist context
    if video_id and playlist_id:
        return "video_in_playlist", video_id, playlist_id
    
    # Plain video
    if video_id:
        return "video", video_id, None
    
    # Fallback - treat as video
    return "video", None, None


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

        # Parse URL to determine type
        url_type, video_id, playlist_id = parse_youtube_url(url)
        
        # Handle video in playlist context - ask user what they want
        if url_type == "video_in_playlist":
            print("🎵 URL contains both video and playlist information", flush=True)
            print()
            print("What would you like to download?")
            print("  1. Just this video")
            print("  2. Entire playlist")
            print()
            
            while True:
                response = input("Choice [1/2]: ").strip()
                if response == "1":
                    # Download just the video - construct plain video URL
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    url_type = "video"
                    break
                elif response == "2":
                    # Download entire playlist - construct playlist URL
                    url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    url_type = "playlist"
                    break
                else:
                    print("Invalid choice. Please enter 1 or 2.")
            print()

        # Check if it's a playlist and extract entries
        is_playlist = False
        playlist_entries = []
        
        # For plain playlists, extract and confirm
        if url_type == "playlist":
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",
            }) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                    is_playlist = info.get("_type") == "playlist"

                    if is_playlist:
                        playlist_entries = info.get("entries", [])
                        track_count = len(playlist_entries)
                        playlist_title = info.get("title", "Unknown playlist")
                        
                        print(f"📝 Playlist: {playlist_title}", flush=True)
                        print(f"   Contains {track_count} video{'s' if track_count != 1 else ''}", flush=True)
                        print()
                        
                        response = input(f"Download all {track_count} tracks? [y/N]: ")
                        if response.lower() != "y":
                            print("Cancelled")
                            return
                except Exception as e:
                    error_msg = str(e).lower()
                    
                    # Check for private/restricted content indicators
                    private_indicators = [
                        'private',
                        'unavailable',
                        'does not exist',  # YouTube's message for private playlists
                        'sign in',
                        'members-only',
                        'join this channel',
                    ]
                    
                    is_private = any(indicator in error_msg for indicator in private_indicators)
                    
                    if is_private:
                        print("❌ Cannot access playlist", file=sys.stderr)
                        print()
                        print("💡 This may be a private or members-only playlist.", file=sys.stderr)
                        print("   To download it, you need to:", file=sys.stderr)
                        print("   1. Go to YouTube and open the playlist", file=sys.stderr)
                        print("   2. Click 'Edit' → 'Playlist privacy'", file=sys.stderr)
                        print("   3. Change from 'Private' to 'Unlisted'", file=sys.stderr)
                        print(file=sys.stderr)
                        print("   Note: 'Unlisted' means only people with the link can view it.", file=sys.stderr)
                        return
                    else:
                        print(f"⚠️ Could not extract playlist info: {e}", file=sys.stderr)
                        print(file=sys.stderr)
                        print("💡 If this is a private playlist, make sure it's set to 'Unlisted' instead.", file=sys.stderr)
                        return  # Don't continue - can't process this URL

        # Download tracks
        success = 0
        failed = 0
        
        # Store playlist URL if it's a playlist
        playlist_url = url if is_playlist else None

        # For single videos, skip straight to download
        if url_type == "video":
            if self.parent_downloader:
                smart_success = self.parent_downloader.try_smart_download(
                    url, audio_format
                )
                
                if smart_success:
                    print("✅ Downloaded via smart download")
                    return
                
                print("⬇️ Downloading from YouTube")
                print()
            
            # Download single video with yt-dlp
            ydl_opts = {
                "format": "251/140/bestaudio/best",
                "writethumbnail": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_format,
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

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                    if self._process_download(info, audio_format, None):
                        print("✅ Download complete")
                    else:
                        print("❌ Download failed", file=sys.stderr)
                except Exception as e:
                    print(f"❌ Download failed: {e}", file=sys.stderr)
                    self.log_failure(url, str(e))
                    raise
            return

        # Handle playlists
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
                    print("🔗 Trying smart download...")
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
            # Fallback for playlists without parent downloader (shouldn't happen in normal usage)
            ydl_opts = {
                "format": "251/140/bestaudio/best",
                "writethumbnail": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_format,
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

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
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
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
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
        with self.temp_file_cleanup() as register_temp:
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

            # Register temp file for cleanup on error
            register_temp(temp_file)

            try:
                # Extract metadata
                artist, title = self.extract_metadata(temp_file)

                # Check if metadata is missing
                missing_metadata = not artist or not title
                
                # Use fallbacks if needed
                if missing_metadata:
                    video_title = info.get("title", "unknown")
                    artist = info.get("uploader", "Unknown")
                    title = video_title

                # Create final filename
                final_name = self.create_filename(
                    artist, title, audio_format, fallback=f"youtube-{video_id}"
                )
                final_path = self.output_dir / final_name

                # Flag for review with final path (after rename)
                if missing_metadata:
                    self.flag_metadata_review(
                        final_path,
                        "Missing or incomplete metadata from YouTube",
                        info.get("webpage_url", ""),
                    )

                # Check for duplicates using already-computed metadata so the
                # check works even if the temp file has no embedded tags yet.
                if self.check_duplicate_for(artist, title, exclude_path=temp_file):
                    temp_file.unlink()
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
