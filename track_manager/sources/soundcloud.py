"""SoundCloud downloader using yt-dlp Python API.

SoundCloud support via yt-dlp - similar to YouTube handler.
"""

import sys
from typing import Optional

import yt_dlp

from .youtube import YouTubeDownloader


def _sc_ydl_opts(output_dir, audio_format: str) -> dict:
    """Return yt-dlp options for a single SoundCloud track download."""
    return {
        "format": "bestaudio/best",
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": "128",
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
        "outtmpl": str(output_dir / ".tmp_%(id)s.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
        "extract_flat": False,
        "remote_components": ["ejs:github"],
    }


class SoundCloudDownloader(YouTubeDownloader):
    """SoundCloud downloader using yt-dlp.

    Inherits from YouTubeDownloader since yt-dlp handles both similarly.
    SoundCloud can offer higher quality than YouTube (up to 256kbps on Go+).

    Per-track flow:
      1. Try smart download (song.link → TIDAL) for the individual track URL.
      2. Fall back to yt-dlp if TIDAL lookup fails.

    For sets/playlists the playlist URL is extracted flat first so that step 1
    is attempted per-track rather than against the playlist URL (which
    song.link cannot resolve).
    """

    def download(self, url: str, format: str = "auto"):
        audio_format = "m4a" if format == "auto" else format

        if "/sets/" in url:
            self._download_playlist(url, audio_format)
        else:
            self._download_single(url, audio_format)

    # ------------------------------------------------------------------
    # Single-track helpers
    # ------------------------------------------------------------------

    def _download_single(
        self,
        url: str,
        audio_format: str,
        playlist_url: Optional[str] = None,
        label: Optional[str] = None,
    ) -> bool:
        """Try TIDAL then yt-dlp for one SoundCloud track.

        Returns True on success, False on failure.
        """
        if label:
            print(label)

        if self.parent_downloader:
            print("🔗 Trying smart download...")
            if self.parent_downloader.try_smart_download(
                url, audio_format, playlist_url=playlist_url
            ):
                return True

            print("⬇️ Downloading from SoundCloud")
            print()

        with yt_dlp.YoutubeDL(_sc_ydl_opts(self.output_dir, audio_format)) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                return self._process_download(info, audio_format, playlist_url)
            except Exception as e:
                print(f"❌ Download failed: {e}", file=sys.stderr)
                self.log_failure(url, str(e))
                return False

    # ------------------------------------------------------------------
    # Playlist handling
    # ------------------------------------------------------------------

    def _download_playlist(self, url: str, audio_format: str):
        """Extract playlist entries flat, then process each track individually."""
        # Extract entries without downloading
        with yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
        ) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                print(f"❌ Could not extract playlist info: {e}", file=sys.stderr)
                self.log_failure(url, str(e))
                return

        entries = info.get("entries", [])
        playlist_title = info.get("title", "Unknown playlist")
        total = len(entries)

        print(f"📝 Playlist: {playlist_title} ({total} tracks)")
        print()

        success = 0
        failed = 0

        for idx, entry in enumerate(entries, 1):
            if not entry:
                continue

            track_url = entry.get("url") or entry.get("webpage_url")
            title = entry.get("title", "Unknown")

            if not track_url:
                print(f"[{idx}/{total}] ⚠️ No URL for: {title}", file=sys.stderr)
                failed += 1
                print()
                continue

            label = f"[{idx}/{total}] {title}"
            if self._download_single(track_url, audio_format, playlist_url=url, label=label):
                success += 1
            else:
                failed += 1

            print()

        print("━" * 60)
        print(f"✅ Download complete")
        print(f"   Success: {success}")
        if failed > 0:
            print(f"   Failed: {failed} (see {self.config.failed_log})")
