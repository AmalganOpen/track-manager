"""Instagram downloader using yt-dlp.

Instagram is not on song.link, so this path skips the smart-download chain
and fetches the reel/post directly. yt-dlp writes the native MP4 (video +
AAC audio); encoding/tagging is the same YouTube finalize pipeline with
``-vn`` so only audio lands in the library.

Public posts sometimes work logged-out. Login cookies are required for
most reels, private accounts, and after Instagram's anonymous rate limit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yt_dlp

from .. import audio as tm_audio
from ..config import Config
from .youtube import YouTubeDownloader

InstagramURLKind = Literal["media", "profile", "unsupported"]

_LOGIN_MARKERS = (
    "login required",
    "cookies are no longer valid",
    "rate-limit for accessing posts anonymously",
    "only available for registered users",
    "redirected to the login page",
)

# Generic yt-dlp titles — prefer the caption when present.
_GENERIC_TITLE = re.compile(r"^(video|post) by\s+", re.IGNORECASE)

# Keep titles usable as filenames/tags; captions can be thousands of chars.
_TITLE_MAX_CHARS = 80


def parse_instagram_url(url: str) -> InstagramURLKind:
    """Classify an Instagram URL.

    ``media`` is a single post/reel/IGTV/story/share link we can download.
    Profiles and hashtag/explore pages are rejected — they are not tracks.
    """
    path = urlparse(url).path or ""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "unsupported"

    head = parts[0].lower()
    if head in {"explore", "accounts"}:
        return "unsupported"
    if head == "reels" and len(parts) >= 2 and parts[1].lower() == "audio":
        return "unsupported"
    if head in {"p", "tv", "reel", "reels"}:
        return "media" if len(parts) >= 2 else "unsupported"
    if head == "share":
        return "media" if len(parts) >= 2 else "unsupported"
    if head == "stories":
        # stories/<user>/<id> is one item; stories/<user> is the whole tray.
        return "media" if len(parts) >= 3 else "unsupported"

    # /<user>/reel/<code> or /<user>/p/<code>
    if len(parts) >= 3 and parts[1].lower() in {"p", "tv", "reel", "reels"}:
        return "media"
    if len(parts) == 1:
        return "profile"
    return "unsupported"


def instagram_artist_title(info: dict[str, Any]) -> tuple[str, str]:
    """Best-effort artist/title from yt-dlp Instagram metadata.

    Artist is the display name (uploader) falling back to the username
    (channel). Title prefers a real ``track`` tag, then a non-generic
    ``title``, then the first line of the caption.
    """
    artist = (
        info.get("artist") or info.get("uploader") or info.get("channel") or "Unknown"
    )
    title = info.get("track") or info.get("title") or ""
    caption = (info.get("description") or "").strip()
    if caption and (not title or _GENERIC_TITLE.match(title)):
        first_line = caption.splitlines()[0].strip()
        first_line = re.sub(r"\s+", " ", first_line)
        if first_line:
            title = first_line
    if not title:
        title = "Unknown"
    if len(title) > _TITLE_MAX_CHARS:
        title = title[:_TITLE_MAX_CHARS].rstrip() + "…"
    return str(artist), title


def _auth_opts() -> dict[str, Any]:
    """yt-dlp cookies for Instagram.

    ``instagram.cookies_file`` wins. Otherwise ``instagram.cookies_from_browser``,
    then ``youtube.cookies_from_browser`` — browser cookies include every
    site, so a Firefox/Chrome profile already logged into Instagram works.
    """
    cfg = Config()
    opts: dict[str, Any] = {}
    cookies_file = cfg.instagram_cookies_file
    if cookies_file:
        opts["cookiefile"] = cookies_file
        return opts
    browser = cfg.instagram_cookies_from_browser
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _ydl_opts(output_dir: Path) -> dict[str, Any]:
    opts: dict[str, Any] = {
        # Instagram is a video host: prefer a separate audio stream when the
        # DASH manifest has one, else the best muxed MP4.
        "format": "bestaudio/best",
        "writethumbnail": True,
        "outtmpl": str(output_dir / ".tmp_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": False,
        "extract_flat": False,
        "remote_components": ["ejs:github"],
    }
    opts.update(_auth_opts())
    return opts


def _is_login_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _LOGIN_MARKERS)


def _print_login_hint() -> None:
    print(
        "💡 Instagram usually needs a logged-in session.\n"
        "   Set instagram.cookies_from_browser (chrome, firefox, safari, …)\n"
        "   or instagram.cookies_file in config.yaml, then log in to Instagram\n"
        "   in that browser. If unset, youtube.cookies_from_browser is reused.",
        file=sys.stderr,
    )


class InstagramDownloader(YouTubeDownloader):
    """Instagram reel/post downloader using yt-dlp.

    Reuses the YouTube finalize pipeline (encode → tag → blob). Does **not**
    try song.link/TIDAL: Instagram URLs are not a music-platform identity.
    """

    def download(self, url: str, format: str = "auto") -> bool:
        target_format = tm_audio.resolve_format(format)
        kind = parse_instagram_url(url)
        if kind == "profile":
            print(
                "❌ Instagram profile URLs are not supported — pass a reel or post URL",
                file=sys.stderr,
            )
            self.log_failure(url, "Instagram profile URL not supported")
            return False
        if kind != "media":
            print(
                "❌ Not a downloadable Instagram URL (need /reel/, /p/, /tv/, or /share/)",
                file=sys.stderr,
            )
            self.log_failure(url, "Unsupported Instagram URL")
            return False

        from ..duplicates import find_duplicates_by_track_url

        existing = find_duplicates_by_track_url(url, self.output_dir)
        if existing and self.handle_found_duplicates(existing, new_file_name=url):
            return True

        print("⬇️ Downloading from Instagram")
        print()
        return self._download_url(url, target_format)

    def _download_url(self, url: str, target_format: str) -> bool:
        with yt_dlp.YoutubeDL(_ydl_opts(self.output_dir)) as ydl:
            try:
                if self._check_predownload_duplicate(ydl, url):
                    return True
                info = ydl.extract_info(url, download=True)
            except Exception as e:
                print(f"❌ Download failed: {e}", file=sys.stderr)
                if _is_login_error(e):
                    _print_login_hint()
                self.log_failure(url, str(e))
                return False

        if not info:
            print("❌ Download failed: no media returned", file=sys.stderr)
            self.log_failure(url, "No media returned")
            return False

        if info.get("_type") == "playlist":
            return self._process_playlist(info, target_format, url)

        self._apply_instagram_tags(info)
        if self._process_download(info, target_format, None):
            print("✅ Download complete")
            return True
        print("❌ Download failed", file=sys.stderr)
        return False

    def _process_playlist(
        self, info: dict[str, Any], target_format: str, playlist_url: str
    ) -> bool:
        entries = [e for e in (info.get("entries") or []) if e]
        total = len(entries)
        if total == 0:
            print("❌ Instagram post has no video", file=sys.stderr)
            self.log_failure(playlist_url, "Instagram post has no video")
            return False

        print(f"📝 Carousel: {total} video{'s' if total != 1 else ''}")
        print()
        success = 0
        failed = 0
        for idx, entry in enumerate(entries, 1):
            self._apply_instagram_tags(entry)
            title = entry.get("track") or entry.get("title") or "Unknown"
            print(f"[{idx}/{total}] {title}")
            if self._process_download(entry, target_format, playlist_url):
                success += 1
            else:
                failed += 1
            print()

        print("━" * 60)
        print("✅ Download complete")
        print(f"   Success: {success}")
        if failed > 0:
            print(f"   Failed: {failed} (see {self.config.failed_log})")
        return failed == 0

    @staticmethod
    def _apply_instagram_tags(info: dict[str, Any]) -> None:
        """Rewrite title from caption; leave artist/track unset so finalize flags review."""
        artist, title = instagram_artist_title(info)
        info["title"] = title
        # Prefer display name for the artist tag path in `_process_download`.
        if not info.get("uploader"):
            info["uploader"] = artist
