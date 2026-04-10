"""SoundCloud downloader using yt-dlp Python API.

SoundCloud support via yt-dlp - similar to YouTube handler.
"""

import sys
from .youtube import YouTubeDownloader


class SoundCloudDownloader(YouTubeDownloader):
    """SoundCloud downloader using yt-dlp.

    Inherits from YouTubeDownloader since yt-dlp handles both similarly.
    SoundCloud can offer higher quality than YouTube (up to 256kbps on Go+).
    """

    def download(self, url: str, format: str = "auto"):
        """Download from SoundCloud.

        Uses 128kbps target to match free tier quality.
        Without Go+ credentials, only free tier (~128kbps) is accessible.
        """
        # Get parent's ydl_opts
        audio_format = "m4a" if format == "auto" else format
        
        # Try smart download first (song.link → TIDAL)
        if self.parent_downloader:
            print("🔗 Trying smart download...")
            smart_success = self.parent_downloader.try_smart_download(
                url, audio_format
            )
            
            if smart_success:
                print("✅ Downloaded via smart download")
                return
            
            print("⬇️ Downloading from SoundCloud")
            print()
        
        # Temporarily override preferredquality for SoundCloud
        import yt_dlp

        # Match free tier quality (no Go+ credentials)
        ydl_opts = {
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "128",  # Match SoundCloud free tier (~128kbps)
                },
                {
                    "key": "EmbedThumbnail",
                }
            ],
            "outtmpl": str(self.output_dir / ".tmp_%(id)s.%(ext)s"),
            "quiet": False,
            "no_warnings": False,
            "extract_flat": False,
            "remote_components": ["ejs:github"],
        }

        # Use similar logic as parent but with SoundCloud-specific settings

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)

                # Handle playlists vs single tracks
                if info.get("_type") == "playlist":
                    entries = info.get("entries", [])
                    total = len(entries)
                    success = 0
                    failed = 0

                    for idx, entry in enumerate(entries, 1):
                        if not entry:
                            continue
                        print(f"[{idx}/{total}] Processing: {entry.get('title', 'Unknown')}")
                        if self._process_download(entry, audio_format):
                            success += 1
                        else:
                            failed += 1
                        print()

                    print("━" * 60)
                    print("✅ Download complete")
                    print(f"   Success: {success}")
                    if failed > 0:
                        print(f"   Failed: {failed} (see {self.config.failed_log})")
                else:
                    if self._process_download(info, audio_format):
                        print("✅ Download complete")
                    else:
                        print("❌ Download failed", file=sys.stderr)

            except Exception as e:
                print(f"❌ Download failed: {e}", file=sys.stderr)
                self.log_failure(url, str(e))
                raise
