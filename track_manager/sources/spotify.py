"""Spotify downloader using spotdl Python API."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from spotdl import Spotdl
    from spotdl.types.song import Song
except ImportError:
    print("Error: spotdl not installed", file=sys.stderr)
    print("Install with: pip install spotdl", file=sys.stderr)
    sys.exit(1)

from .. import __version__
from .. import audio as tm_audio
from .. import blob as tm_blob
from .. import pipeline as tm_pipeline
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
            print("   1. Edit config.yaml in the project root\n", file=sys.stderr)
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

    def _stop_spotdl_progress(self) -> None:
        """Stop spotdl's Rich Live display and set progress_handler to None.

        spotdl's ProgressHandler enters Rich's Live display in __init__ and
        never exits it between individual song downloads.  We stop it here so
        the Live display does not keep refreshing the terminal (wiping prompts,
        etc.) between tracks.  We set progress_handler to None so that
        _start_spotdl_progress() knows it must create a fresh one next time.
        Note: we call rich_progress_bar.stop() directly instead of ph.close()
        because ph.close() also calls logging.shutdown() which disables logging
        for the rest of the process.
        """
        ph = getattr(self.spotdl.downloader, "progress_handler", None)
        if ph is not None and hasattr(ph, "rich_progress_bar"):
            try:
                ph.rich_progress_bar.stop()
            except Exception:
                pass
        self.spotdl.downloader.progress_handler = None

    def _start_spotdl_progress(self) -> None:
        """Create a fresh ProgressHandler (starts Rich Live display).

        Must be called immediately before self.spotdl.download() so the Live
        display is only active while a download is actually in progress.
        """
        from spotdl.download.progress_handler import ProgressHandler

        if self.spotdl.downloader.progress_handler is None:
            simple_tui = self.spotdl.downloader.settings.get("simple_tui", False)
            self.spotdl.downloader.progress_handler = ProgressHandler(simple_tui)

    def download(self, url: str, format: str = "auto"):
        """Download track(s) from Spotify.

        Args:
            url: Spotify URL (track, playlist, or album)
            format: Output format ('auto', 'aiff', 'm4a', 'mp3')
        """
        # `audio_format` is the format spotdl writes to disk in the fallback
        # path. spotdl cannot produce AIFF, so until that path is rewritten to
        # use the unified pipeline we keep its output at M4A. The smart-
        # download path passes the user's `format` through unchanged so AIFF
        # works when TIDAL has the track.
        audio_format = "m4a" if format in ("auto", "aiff") else format

        print("🔍 Finding tracks on Spotify...")
        print(f"URL: {url}")
        print()

        try:
            # Get songs from URL
            # Apply rate limiting before fetching playlist
            from ..rate_limiter import spotify_rate_limit
            spotify_rate_limit()

            try:
                songs = self.spotdl.search([url])
            except KeyError as e:
                if "genres" in str(e):
                    """ print(
                        "⚠️  spotdl failed: artist has no 'genres' in Spotify API response.",
                        file=sys.stderr,
                    )
                    print(
                        "   This is a known spotdl bug (spotdl/types/artist.py#104).",
                        file=sys.stderr,
                    )
                    print() """
                    if self.parent_downloader:
                        print("🔄 Falling back to TIDAL download...")
                        success = self.parent_downloader.try_smart_download(
                            url, format
                        )
                        if success:
                            return
                        print("❌ TIDAL fallback also failed.", file=sys.stderr)
                    return
                raise

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
                            format,
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
                    self._start_spotdl_progress()
                    result = self.spotdl.download(song)
                    self._stop_spotdl_progress()

                    if result:
                        # Find downloaded file (spotdl always emits the
                        # intermediate format set in DownloaderOptions, i.e.
                        # m4a). Pass `result` so we use spotdl's returned
                        # path directly without a redundant re-download.
                        file_path = self._find_downloaded_file(song, audio_format, spotdl_result=result)

                        # spotdl re-encodes its YouTube download to AAC@192
                        # before handing it back, so probing the on-disk file
                        # would record 192 kbps as the "source" — wrong.  Ask
                        # yt-dlp directly about the upstream stream so the
                        # blob carries the real source codec/bitrate (~128
                        # for fmt 140, ~160 for fmt 251).
                        upstream_codec, upstream_kbps = self._probe_upstream_youtube_source(
                            song
                        )

                        if file_path and self._process_download(
                            file_path,
                            song,
                            format,
                            playlist_url,
                            source_codec_override=upstream_codec,
                            source_bitrate_kbps_override=upstream_kbps,
                        ):
                            success += 1
                        else:
                            # Log the failure when file is not found or processing fails
                            if not file_path:
                                print("⚠️ Download failed: file not found")
                                self.log_failure(song.url, "spotdl completed but file not found")
                            else:
                                print("⚠️ Download failed: processing error")
                                self.log_failure(song.url, "File processing failed")
                            failed += 1
                    else:
                        print("⚠️ Download failed")
                        self.log_failure(song.url, "Download returned None")
                        failed += 1

                except Exception as e:
                    err_str = str(e)
                    if "401" in err_str or "authentication required" in err_str.lower():
                        print("❌ Spotify authentication failed (401)", file=sys.stderr)
                        print(
                            "   Your Spotify API credentials appear to be invalid.",
                            file=sys.stderr,
                        )
                        print(
                            "   Please check your client_id and client_secret in config.yaml:",
                            file=sys.stderr,
                        )
                        print(
                            "   1. Go to https://developer.spotify.com/dashboard",
                            file=sys.stderr,
                        )
                        print(
                            "   2. Open your app → Settings → regenerate Client Secret if needed",
                            file=sys.stderr,
                        )
                        print(
                            "   3. Make sure at least one Redirect URI is set (e.g. http://localhost:8888/callback)",
                            file=sys.stderr,
                        )
                        raise
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
            err_str = str(e)
            if "401" in err_str or "authentication required" in err_str.lower():
                print("❌ Spotify authentication failed (401)", file=sys.stderr)
                print(
                    "   Your Spotify API credentials appear to be invalid.",
                    file=sys.stderr,
                )
                print(
                    "   Please check your client_id and client_secret in config.yaml:",
                    file=sys.stderr,
                )
                print(
                    "   1. Go to https://developer.spotify.com/dashboard",
                    file=sys.stderr,
                )
                print(
                    "   2. Open your app → Settings → regenerate Client Secret if needed",
                    file=sys.stderr,
                )
                print(
                    "   3. Make sure at least one Redirect URI is set (e.g. http://localhost:8888/callback)",
                    file=sys.stderr,
                )
                raise
            print(f"❌ Error: {e}", file=sys.stderr)
            self.log_failure(url, str(e))
            raise

    def _find_downloaded_file(
        self,
        song: Song,
        format: str,
        spotdl_result: Optional[tuple] = None,
    ) -> Optional[Path]:
        """Find the downloaded file for a song.

        Args:
            song: Song object
            format: Expected format
            spotdl_result: Return value of a prior self.spotdl.download(song) call.
                           When provided, its path is checked first so we avoid
                           re-triggering a second (unnecessary) download.

        Returns:
            Path to downloaded file or None
        """
        from datetime import datetime

        # Prefer the path returned directly by spotdl (no second download needed).
        if spotdl_result and len(spotdl_result) >= 2:
            file_path = spotdl_result[1]
            if isinstance(file_path, Path) and file_path.exists():
                return file_path

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

    def _probe_upstream_youtube_source(
        self, song: Song
    ) -> tuple[Optional[str], Optional[int]]:
        """Ask yt-dlp what the upstream YouTube source for `song` actually is.

        spotdl re-encodes its YouTube download to AAC@192 before handing the
        file back, so probing the on-disk file would record 192 kbps as the
        "source" — wrong.  We re-resolve the YouTube URL with the same format
        selector spotdl uses (251 → 140 → bestaudio) and read ``acodec`` /
        ``abr`` from the resolved format. ``download=False`` means this is a
        single metadata HTTP request — no audio is fetched.

        Returns ``(codec, bitrate_kbps)``; either field may be ``None`` when
        yt-dlp doesn't expose it (we then leave the corresponding provenance
        field unset rather than recording garbage).
        """
        download_url = getattr(song, "download_url", None)
        if not download_url:
            return (None, None)

        try:
            import yt_dlp
        except ImportError:
            return (None, None)

        opts = {
            # Match the selector configured on the spotdl DownloaderOptions
            # (see __init__) so the inspected format is the same one spotdl
            # actually downloaded.
            "format": "251/140/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(download_url, download=False)
        except Exception as e:
            print(f"  ⚠️  Upstream source probe failed: {e}", file=sys.stderr)
            return (None, None)

        if not isinstance(info, dict):
            return (None, None)

        # When a format selector resolves to a single stream, yt-dlp lifts
        # that format's fields (acodec, abr, ext, …) onto the top-level info
        # dict. Be defensive in case it doesn't.
        codec = info.get("acodec") or None
        if codec in (None, "none"):
            codec = info.get("ext") or None
        abr = info.get("abr")
        if abr in (None, 0):
            # Some yt-dlp versions only fill abr on entries inside `formats`.
            for fmt in (info.get("formats") or []):
                if fmt.get("format_id") == info.get("format_id") and fmt.get("abr"):
                    abr = fmt["abr"]
                    if not codec or codec == "none":
                        codec = fmt.get("acodec") or fmt.get("ext")
                    break

        try:
            abr_kbps = int(round(float(abr))) if abr else None
        except (TypeError, ValueError):
            abr_kbps = None

        return (codec, abr_kbps)

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
                
                # ORIGINAL_BITRATE = source stream bitrate (what YouTube
                # delivered), not our re-encoded output.  yt-dlp reports this
                # as "abr" (average bitrate of the selected format).
                # Format 251 (Opus) ≈ 160 kbps; format 140 (M4A) ≈ 128 kbps.
                # Fall back to None if yt-dlp doesn't expose it.
                source_bitrate_kbps = info.get("abr") or None
                if source_bitrate_kbps is not None:
                    source_bitrate_kbps = int(source_bitrate_kbps)

                self._add_provenance_metadata(
                    final_path,
                    song.url,
                    format,
                    source_bitrate_kbps,
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
        self,
        file_path: Path,
        song: Song,
        target_format: str,
        playlist_url: Optional[str] = None,
        source_codec_override: Optional[str] = None,
        source_bitrate_kbps_override: Optional[int] = None,
    ) -> bool:
        """Finalize a spotdl-produced file: encode/passthrough → tag → blob.

        spotdl writes a 192 kbps M4A (AAC); we treat that as the temp file
        and route through the shared pipeline. With target=m4a this is a
        rename (no generation loss); with target=aiff/mp3 it's a re-encode
        from the AAC source.

        ``source_codec_override`` / ``source_bitrate_kbps_override`` let the
        caller supply the upstream YouTube source's actual codec/abr — those
        are recorded as ``provenance.original_format`` / ``original_bitrate``
        instead of probing spotdl's already-transcoded output (which would
        misleadingly report spotdl's ~192 kbps target as the source).
        """
        try:
            target_format = tm_audio.resolve_format(target_format)

            # spotdl always emits a complete file with embedded tags. Use its
            # written file as our temp; we will replace it with the
            # canonical-named output in target_format.
            if not file_path.exists():
                print(f"⚠️ spotdl output file not found: {file_path}")
                return False

            # Trust the upstream override when given (real source quality).
            # Fall back to probing spotdl's output only when no override is
            # available — that's the legacy behaviour and produces a known-
            # incorrect value (spotdl's transcode bitrate, not the source).
            if source_codec_override or source_bitrate_kbps_override:
                source_codec = source_codec_override
                source_bitrate_kbps = source_bitrate_kbps_override
            else:
                probed_source = tm_audio.probe_audio(file_path)
                source_codec = probed_source.get("codec")
                source_bitrate_kbps = probed_source.get("bitrate_kbps")

            doc = self._build_spotify_doc(
                song,
                playlist_url=playlist_url,
                source_codec=source_codec,
                source_bitrate_kbps=source_bitrate_kbps,
            )

            # Pre-finalize duplicate check using known artist/title.
            if self.check_duplicate_for(
                doc["track"]["artist_string"],
                doc["track"]["title"],
                exclude_path=file_path,
            ):
                file_path.unlink()
                print("⏭️ Skipped (duplicate)")
                return True

            final_name = self.create_filename(
                doc["track"]["artist_string"],
                doc["track"]["title"],
                target_format,
                fallback=file_path.stem,
            )
            final_path = self.output_dir / final_name

            # spotdl already embedded basic tags + cover art into the m4a.
            # Pull the cover bytes out before encoding so we can re-embed
            # them in the final file (a re-encode strips embedded artwork).
            cover_data = self._extract_embedded_cover(file_path)

            result = tm_pipeline.finalize(
                file_path, final_path, doc, target_format, cover_data
            )
            if result is None:
                return False

            print(f"✅ Saved: {final_name}")
            return True

        except Exception as e:
            print(f"⚠️ Error processing: {e}", file=sys.stderr)
            return False

    def _build_spotify_doc(
        self,
        song: Song,
        *,
        playlist_url: Optional[str],
        source_codec: Optional[str],
        source_bitrate_kbps: Optional[int],
    ) -> dict:
        """Assemble the canonical metadata document for a spotdl download."""
        doc = tm_blob.empty_document()

        artists = list(getattr(song, "artists", []) or [])
        if not artists and getattr(song, "artist", None):
            artists = [song.artist]
        doc["track"]["title"] = song.name
        doc["track"]["artists"] = artists
        doc["track"]["artist_string"] = ", ".join(artists) if artists else (song.artist or "")
        if getattr(song, "album_name", None):
            doc["track"]["album"] = song.album_name
        if getattr(song, "album_artist", None):
            doc["track"]["album_artist"] = song.album_artist
        if getattr(song, "year", None):
            doc["track"]["date"] = str(song.year)
        if getattr(song, "genres", None):
            genres = song.genres
            if isinstance(genres, (list, tuple)) and genres:
                doc["track"]["genre"] = genres[0]
        if getattr(song, "track_number", None) is not None:
            doc["track"]["track_number"] = song.track_number
        if getattr(song, "disc_number", None) is not None:
            doc["track"]["disc_number"] = song.disc_number
        if getattr(song, "duration", None) is not None:
            try:
                doc["track"]["duration_seconds"] = float(song.duration)
            except (TypeError, ValueError):
                pass
        if getattr(song, "isrc", None):
            doc["track"]["isrc"] = song.isrc
        if getattr(song, "publisher", None):
            doc["track"]["label"] = song.publisher

        if getattr(song, "song_id", None):
            doc["identifiers"]["spotify_id"] = song.song_id

        if getattr(song, "cover_url", None):
            doc["cover_art"]["url"] = song.cover_url

        doc["provenance"]["track_url"] = song.url
        doc["provenance"]["playlist_url"] = playlist_url
        doc["provenance"]["source"] = "spotify"
        doc["provenance"]["original_format"] = source_codec
        doc["provenance"]["original_bitrate"] = source_bitrate_kbps
        doc["provenance"]["downloaded_at"] = datetime.now(timezone.utc).isoformat()
        doc["provenance"]["tool_version"] = __version__

        return doc

    @staticmethod
    def _extract_embedded_cover(file_path: Path) -> Optional[bytes]:
        """Pull JPEG cover bytes out of a tagged audio file (M4A or MP3)."""
        try:
            suffix = file_path.suffix.lower()
            if suffix in (".m4a", ".mp4"):
                from mutagen.mp4 import MP4

                tags = MP4(str(file_path)).tags
                if tags and "covr" in tags and tags["covr"]:
                    return bytes(tags["covr"][0])
            elif suffix == ".mp3":
                from mutagen.id3 import ID3

                tags = ID3(str(file_path))
                for frame in tags.getall("APIC"):
                    if frame.data:
                        return bytes(frame.data)
        except Exception:
            return None
        return None
