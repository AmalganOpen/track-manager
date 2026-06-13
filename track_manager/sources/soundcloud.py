"""SoundCloud downloader using yt-dlp Python API.

Same shape as the YouTube handler: yt-dlp fetches bestaudio in its native
container and writes the thumbnail alongside; encoding, tagging, and the
metadata blob are handled by `tm_audio` / `tm_blob` downstream.
"""

import sys
from typing import Optional

import yt_dlp

from .. import audio as tm_audio
from ..config import Config
from .youtube import YouTubeDownloader


def _sc_ydl_opts(output_dir) -> dict:
    """yt-dlp options for a single SoundCloud track download.

    When `soundcloud.oauth_token` is configured, yt-dlp authenticates
    against SoundCloud and gains access to the original uploader-provided
    download (often WAV/AIFF/FLAC) on tracks where the uploader enabled
    "Download file". `bestaudio/best` then naturally picks that
    highest-quality format. Without auth, only the public HLS streams
    (~128 kbps MP3) are available.
    """
    opts: dict = {
        "format": "bestaudio/best",
        "writethumbnail": True,
        "outtmpl": str(output_dir / ".tmp_%(id)s.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
        "extract_flat": False,
        "remote_components": ["ejs:github"],
    }

    oauth_token = Config().get("soundcloud.oauth_token")
    if oauth_token:
        # yt-dlp's SoundCloud extractor accepts an OAuth token via the
        # special username "oauth" (equivalent to `--username oauth
        # --password <token>` on the CLI).
        opts["username"] = "oauth"
        opts["password"] = oauth_token

    return opts


class SoundCloudDownloader(YouTubeDownloader):
    """SoundCloud downloader using yt-dlp.

    Reuses the YouTube finalize pipeline (encode → tag → blob) via inheritance.
    SoundCloud can offer higher quality than YouTube (up to 256kbps on Go+).

    Per-track flow:
      1. Try smart download (song.link → TIDAL) for the individual track URL.
      2. Fall back to yt-dlp if TIDAL lookup fails.

    For sets/playlists the playlist URL is extracted flat first so that step 1
    is attempted per-track rather than against the playlist URL (which
    song.link cannot resolve).
    """

    def download(self, url: str, format: str = "auto"):
        target_format = tm_audio.resolve_format(format)

        if "/sets/" in url:
            self._download_playlist(url, target_format)
        else:
            self._download_single(url, target_format)

    # ------------------------------------------------------------------
    # Single-track helpers
    # ------------------------------------------------------------------

    def _download_single(
        self,
        url: str,
        target_format: str,
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
                url, target_format, playlist_url=playlist_url, check_duplicates=True
            ):
                return True

            print("⬇️ Downloading from SoundCloud")
            print()

        with yt_dlp.YoutubeDL(_sc_ydl_opts(self.output_dir)) as ydl:
            try:
                # Cheap metadata-only fetch first so we can pre-empt
                # downloading audio for tracks already in the library.
                # Flat playlist extraction often gives title='Unknown',
                # so we re-extract per-track here to get real tags.
                if self._check_predownload_duplicate(ydl, url):
                    return True

                info = ydl.extract_info(url, download=True)
                return self._process_download(info, target_format, playlist_url)
            except Exception as e:
                print(f"❌ Download failed: {e}", file=sys.stderr)
                self.log_failure(url, str(e))
                return False

    # ------------------------------------------------------------------
    # Playlist handling
    # ------------------------------------------------------------------

    def _download_playlist(self, url: str, target_format: str):
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
            uploader = entry.get("uploader") or entry.get("channel")

            if not track_url:
                print(f"[{idx}/{total}] ⚠️ No URL for: {title}", file=sys.stderr)
                failed += 1
                print()
                continue

            label = f"[{idx}/{total}] {title}"

            # Pre-download name check: avoids a full download for obvious dupes.
            if self.check_duplicate_for(uploader, title):
                success += 1
                print()
                continue

            if self._download_single(track_url, target_format, playlist_url=url, label=label):
                success += 1
            else:
                failed += 1

            print()

        print("━" * 60)
        print(f"✅ Download complete")
        print(f"   Success: {success}")
        if failed > 0:
            print(f"   Failed: {failed} (see {self.config.failed_log})")
