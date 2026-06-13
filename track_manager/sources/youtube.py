"""YouTube downloader using yt-dlp Python API.

The yt-dlp pipeline only *fetches* audio; encoding, tagging, and metadata
embedding are owned by `track_manager.audio` and `track_manager.blob`. yt-dlp
postprocessors are not used because they cannot produce AIFF, and we want a
single code path that handles all three target formats consistently.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp not installed", file=sys.stderr)
    print("Install with: pip install yt-dlp", file=sys.stderr)
    sys.exit(1)

from .. import __version__
from .. import audio as tm_audio
from .. import blob as tm_blob
from .. import pipeline as tm_pipeline
from ..config import Config
from .base import BaseDownloader

URLType = Literal["video", "playlist", "video_in_playlist"]


def parse_youtube_url(url: str) -> tuple[URLType, Optional[str], Optional[str]]:
    """Parse a YouTube URL to determine its type.

    Returns:
        Tuple of (url_type, video_id, playlist_id)
        - url_type: "video", "playlist", or "video_in_playlist"
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    video_id = query.get("v", [None])[0]
    playlist_id = query.get("list", [None])[0]

    if "/playlist" in parsed.path and playlist_id:
        return "playlist", None, playlist_id
    if video_id and playlist_id:
        return "video_in_playlist", video_id, playlist_id
    if video_id:
        return "video", video_id, None
    return "video", None, None


def _auth_opts() -> Dict[str, Any]:
    """yt-dlp auth options sourced from config for age-restricted videos.

    Returns either {"cookiefile": ...} or {"cookiesfrombrowser": (...,)},
    or {} if neither is configured. `cookiefile` takes precedence.
    """
    cfg = Config()
    opts: Dict[str, Any] = {}
    cookies_file = cfg.youtube_cookies_file
    if cookies_file:
        opts["cookiefile"] = cookies_file
    else:
        browser = cfg.youtube_cookies_from_browser
        if browser:
            # yt-dlp expects a tuple: (browser_name, profile, keyring, container)
            opts["cookiesfrombrowser"] = (browser,)

    # Per-extractor escape hatches for when YouTube's default clients are
    # blocked by JS challenges or PO token requirements.
    extractor_args: Dict[str, list[str]] = {}
    clients = cfg.youtube_player_clients
    if clients:
        extractor_args.setdefault("youtube", []).append(
            "player_client=" + ",".join(clients)
        )
    po_token = cfg.youtube_po_token
    if po_token:
        extractor_args.setdefault("youtube", []).append(f"po_token={po_token}")
    if extractor_args:
        opts["extractor_args"] = extractor_args
    return opts


def _ydl_opts(output_dir: Path) -> Dict[str, Any]:
    """yt-dlp options used for every single-track download.

    No FFmpegExtractAudio or EmbedThumbnail postprocessors: yt-dlp gives us
    bestaudio in its native container (Opus-in-WebM via fmt 251, AAC-in-M4A
    via fmt 140) and a thumbnail file alongside; encoding and embedding are
    handled downstream by `tm_audio` / `tm_blob`.
    """
    opts: Dict[str, Any] = {
        # 251 = Opus ~160 kbps @ 48 kHz; 140 = AAC ~128 kbps @ 44.1 kHz.
        "format": "251/140/bestaudio/best",
        "writethumbnail": True,
        "outtmpl": str(output_dir / ".tmp_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": False,
        "extract_flat": False,
        "remote_components": ["ejs:github"],
    }
    opts.update(_auth_opts())
    return opts


class YouTubeDownloader(BaseDownloader):
    """YouTube downloader."""

    def download(self, url: str, format: str = "auto"):
        """Download video(s) from YouTube.

        Args:
            url: YouTube URL (video or playlist)
            format: Output format (auto, aiff, m4a, mp3); 'auto' resolves to AIFF.
        """
        target_format = tm_audio.resolve_format(format)

        url_type, video_id, playlist_id = parse_youtube_url(url)

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
                    url = f"https://www.youtube.com/watch?v={video_id}"
                    url_type = "video"
                    break
                elif response == "2":
                    url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    url_type = "playlist"
                    break
                else:
                    print("Invalid choice. Please enter 1 or 2.")
            print()

        is_playlist = False
        playlist_entries = []

        if url_type == "playlist":
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": "in_playlist",
                    **_auth_opts(),
                }
            ) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                    is_playlist = info.get("_type") == "playlist"

                    if is_playlist:
                        playlist_entries = info.get("entries", [])
                        track_count = len(playlist_entries)
                        playlist_title = info.get("title", "Unknown playlist")

                        print(f"📝 Playlist: {playlist_title}", flush=True)
                        print(
                            f"   Contains {track_count} video{'s' if track_count != 1 else ''}",
                            flush=True,
                        )
                        print()

                        response = input(f"Download all {track_count} tracks? [y/N]: ")
                        if response.lower() != "y":
                            print("Cancelled")
                            return
                except Exception as e:
                    error_msg = str(e).lower()

                    private_indicators = [
                        "private",
                        "unavailable",
                        "does not exist",
                        "sign in",
                        "members-only",
                        "join this channel",
                    ]

                    is_private = any(ind in error_msg for ind in private_indicators)

                    if is_private:
                        print("❌ Cannot access playlist", file=sys.stderr)
                        print()
                        print(
                            "💡 This may be a private or members-only playlist.",
                            file=sys.stderr,
                        )
                        print("   To download it, you need to:", file=sys.stderr)
                        print(
                            "   1. Go to YouTube and open the playlist", file=sys.stderr
                        )
                        print(
                            "   2. Click 'Edit' → 'Playlist privacy'", file=sys.stderr
                        )
                        print(
                            "   3. Change from 'Private' to 'Unlisted'", file=sys.stderr
                        )
                        print(file=sys.stderr)
                        print(
                            "   Note: 'Unlisted' means only people with the link can view it.",
                            file=sys.stderr,
                        )
                        return
                    else:
                        print(
                            f"⚠️ Could not extract playlist info: {e}", file=sys.stderr
                        )
                        print(file=sys.stderr)
                        print(
                            "💡 If this is a private playlist, make sure it's set to 'Unlisted' instead.",
                            file=sys.stderr,
                        )
                        return

        success = 0
        failed = 0

        playlist_url = url if is_playlist else None

        # ------------------------------------------------------------------
        # Single video
        # ------------------------------------------------------------------
        if url_type == "video":
            if self.parent_downloader:
                smart_success = self.parent_downloader.try_smart_download(
                    url, target_format, check_duplicates=True
                )
                if smart_success:
                    print("✅ Downloaded via smart download")
                    return
                print("⬇️ Downloading from YouTube")
                print()

            with yt_dlp.YoutubeDL(_ydl_opts(self.output_dir)) as ydl:
                try:
                    if self._check_predownload_duplicate(ydl, url):
                        return
                    info = ydl.extract_info(url, download=True)
                    if self._process_download(info, target_format, None):
                        print("✅ Download complete")
                    else:
                        print("❌ Download failed", file=sys.stderr)
                except Exception as e:
                    print(f"❌ Download failed: {e}", file=sys.stderr)
                    self.log_failure(url, str(e))
                    raise
            return

        # ------------------------------------------------------------------
        # Playlist
        # ------------------------------------------------------------------
        if is_playlist and self.parent_downloader:
            total = len(playlist_entries)

            for idx, entry in enumerate(playlist_entries, 1):
                if not entry:
                    continue

                video_url = entry.get("url")
                title = entry.get("title", "Unknown")

                print(f"[{idx}/{total}] {title}")

                try:
                    print("🔗 Trying smart download...")
                    smart_success = self.parent_downloader.try_smart_download(
                        video_url,
                        target_format,
                        playlist_url=playlist_url,
                        check_duplicates=True,
                    )

                    if smart_success:
                        success += 1
                        continue

                    print("  ⬇️ Downloading from YouTube")
                    if self._download_single_video(
                        video_url, target_format, playlist_url
                    ):
                        success += 1
                    else:
                        failed += 1

                except Exception as e:
                    print(f"  ⚠️ Error: {e}", file=sys.stderr)
                    self.log_failure(video_url, str(e))
                    failed += 1

                print()

            print()
            print("━" * 60)
            print("✅ Download complete")
            print(f"   Success: {success}")
            if failed > 0:
                print(f"  Failed: {failed} (see {self.config.failed_log})")
        else:
            # Playlist without parent downloader (rare; mostly tests).
            with yt_dlp.YoutubeDL(_ydl_opts(self.output_dir)) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                    entries = info.get("entries", [])
                    total = len(entries)

                    for idx, entry in enumerate(entries, 1):
                        if entry:
                            print(
                                f"[{idx}/{total}] Processing: {entry.get('title', 'Unknown')}"
                            )
                            if self._process_download(
                                entry, target_format, playlist_url
                            ):
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

    # ------------------------------------------------------------------
    # Per-track helpers
    # ------------------------------------------------------------------

    def _check_predownload_duplicate(self, ydl: "yt_dlp.YoutubeDL", url: str) -> bool:
        """Metadata-only pre-check: True if `url` is already in the library.

        Does a cheap ``extract_info(download=False)`` so we can skip fetching
        audio bytes for a track we already own. This runs on the yt-dlp path —
        including ``--dumb``, where the smart-download dedup is bypassed — so a
        re-download of an owned track is caught before any audio is fetched.
        The post-download check in ``_process_download`` still remains as a
        backstop for the cases this can't resolve (e.g. metadata only available
        after download).
        """
        try:
            meta = ydl.extract_info(url, download=False)
        except Exception:
            return False
        pre_artist = meta.get("artist") or meta.get("uploader")
        pre_title = meta.get("track") or meta.get("title")
        return self.check_duplicate_for(pre_artist, pre_title)

    def _download_single_video(
        self, video_url: str, target_format: str, playlist_url: Optional[str] = None
    ) -> bool:
        """Download one video and route it through the finalize pipeline."""
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(self.output_dir)) as ydl:
                if self._check_predownload_duplicate(ydl, video_url):
                    return True
                info = ydl.extract_info(video_url, download=True)
                return self._process_download(info, target_format, playlist_url)
        except Exception as e:
            print(f"  ⚠️ Download failed: {e}", file=sys.stderr)
            return False

    def _process_download(
        self, info: dict, target_format: str, playlist_url: Optional[str] = None
    ) -> bool:
        """Encode a yt-dlp temp file into the target format and embed metadata."""
        with self.temp_file_cleanup() as register_temp:
            video_id = info.get("id")
            temp_audio = self._find_temp_audio(video_id)
            if temp_audio is None:
                print(f"⚠️ Downloaded file not found for {video_id}")
                return False

            register_temp(temp_audio)

            try:
                title = info.get("track") or info.get("title") or "Unknown"
                artist = info.get("artist") or info.get("uploader") or "Unknown"
                missing_metadata = (
                    info.get("track") is None and info.get("artist") is None
                )

                final_name = self.create_filename(
                    artist, title, target_format, fallback=f"youtube-{video_id}"
                )
                final_path = self.output_dir / final_name

                if missing_metadata:
                    self.flag_metadata_review(
                        final_path,
                        "Missing or incomplete metadata from YouTube",
                        info.get("webpage_url", ""),
                    )

                if self.check_duplicate_for(artist, title, exclude_path=temp_audio):
                    temp_audio.unlink()
                    self._cleanup_temp_thumbnail(video_id)
                    return True

                cover_data = self._read_temp_thumbnail(video_id)
                doc = self._build_blob_doc(
                    info=info,
                    artist=artist,
                    title=title,
                    playlist_url=playlist_url,
                )

                result = tm_pipeline.finalize(
                    temp_audio, final_path, doc, target_format, cover_data
                )
                self._cleanup_temp_thumbnail(video_id)
                if result is None:
                    return False

                print(f"✅ Saved: {final_name}")
                return True

            except Exception as e:
                print(f"⚠️ Error processing download: {e}", file=sys.stderr)
                return False

    # Image extensions so we can exclude thumbnails from the audio search.
    _THUMBNAIL_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

    def _find_temp_audio(self, video_id: Optional[str]) -> Optional[Path]:
        """Locate the audio file yt-dlp wrote for `video_id`.

        Globs `.tmp_<id>.*` rather than hard-coding extensions: SoundCloud's
        authenticated `download` format can yield any container the uploader
        used (wav, aiff, flac, m4a, mp3, …) and we don't want to maintain
        that list by hand. Thumbnail files (yt-dlp writes one per track)
        are filtered out by extension.
        """
        if not video_id:
            return None
        candidates = [
            p
            for p in self.output_dir.glob(f".tmp_{video_id}.*")
            if p.suffix.lower() not in self._THUMBNAIL_EXTS and p.is_file()
        ]
        if not candidates:
            return None
        # Prefer the largest file when there's somehow more than one, e.g.
        # if a previous run left a partial alongside a successful download.
        candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
        return candidates[0]

    def _read_temp_thumbnail(self, video_id: Optional[str]) -> Optional[bytes]:
        """Locate and read the thumbnail file written by yt-dlp."""
        if not video_id:
            return None
        for ext in ("jpg", "jpeg", "png", "webp"):
            p = self.output_dir / f".tmp_{video_id}.{ext}"
            if p.exists():
                return tm_audio.thumbnail_to_jpeg(p)
        return None

    def _cleanup_temp_thumbnail(self, video_id: Optional[str]) -> None:
        if not video_id:
            return
        for ext in ("jpg", "jpeg", "png", "webp"):
            p = self.output_dir / f".tmp_{video_id}.{ext}"
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    def _build_blob_doc(
        self,
        *,
        info: dict,
        artist: str,
        title: str,
        playlist_url: Optional[str],
    ) -> dict[str, Any]:
        """Assemble the canonical metadata document for a yt-dlp download.

        The `audio.*` block and `cover_art.{sha256, embedded}` are filled in
        downstream by `tm_pipeline.finalize`, which probes the encoded
        result.
        """
        doc = tm_blob.empty_document()

        doc["track"]["title"] = title
        doc["track"]["artists"] = [artist]
        doc["track"]["artist_string"] = artist
        if info.get("album"):
            doc["track"]["album"] = info["album"]
        if info.get("release_date"):
            doc["track"]["date"] = info["release_date"]
        elif info.get("upload_date"):
            doc["track"]["date"] = info["upload_date"]
        if info.get("duration") is not None:
            try:
                doc["track"]["duration_seconds"] = float(info["duration"])
            except (TypeError, ValueError):
                pass

        # Prefer the audio codec (e.g. "opus", "aac") over the container
        # extension (e.g. "webm", "m4a") so `provenance.original_format`
        # describes the actual lossy/lossless transformer rather than the
        # wrapper.
        original_format = info.get("acodec") or info.get("ext")
        original_bitrate = info.get("abr")
        if original_bitrate is not None:
            try:
                original_bitrate = int(round(float(original_bitrate)))
            except (TypeError, ValueError):
                original_bitrate = None

        doc["provenance"]["track_url"] = info.get("webpage_url", "")
        doc["provenance"]["playlist_url"] = playlist_url
        doc["provenance"]["source"] = self.__class__.__name__.replace(
            "Downloader", ""
        ).lower()
        doc["provenance"]["original_format"] = original_format
        doc["provenance"]["original_bitrate"] = original_bitrate
        doc["provenance"]["downloaded_at"] = datetime.now(timezone.utc).isoformat()
        doc["provenance"]["tool_version"] = __version__

        if info.get("thumbnail"):
            doc["cover_art"]["url"] = info["thumbnail"]

        return doc
