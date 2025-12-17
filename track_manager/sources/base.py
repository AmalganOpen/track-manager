"""Base downloader class with common functionality."""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

from mutagen import File as MutagenFile

from ..config import Config

if TYPE_CHECKING:
    from ..provenance import DownloadProvenance


class BaseDownloader(ABC):
    """Base class for source-specific downloaders."""

    def __init__(self, config: Config, output_dir: Path, parent_downloader=None):
        """Initialize base downloader.

        Args:
            config: Configuration object
            output_dir: Output directory for downloads
            parent_downloader: Parent Downloader instance (for smart downloads)
        """
        self.config = config
        self.output_dir = output_dir
        self.parent_downloader = parent_downloader

    @abstractmethod
    def download(self, url: str, format: str):
        """Download track(s) from URL.

        Args:
            url: URL to download from
            format: Output format (auto, m4a, mp3)
        """
        pass

    def _add_provenance_metadata(
        self,
        file_path: Path,
        track_url: str,
        original_format: str,
        original_bitrate: Optional[int],
        playlist_url: Optional[str] = None,
    ):
        """Add provenance metadata to downloaded file.

        Args:
            file_path: Path to audio file
            track_url: Original track URL
            original_format: Original audio format
            original_bitrate: Original bitrate in kbps
            playlist_url: Optional playlist URL
        """
        from ..provenance import DownloadProvenance

        try:
            # Determine source from class name
            source = self.__class__.__name__.replace("Downloader", "").lower()

            # Create provenance object
            provenance = DownloadProvenance(
                track_url=track_url,
                playlist_url=playlist_url,
                source=source,
                original_format=original_format,
                original_bitrate=original_bitrate,
            )

            # Apply to file based on format
            if file_path.suffix == ".m4a":
                self._apply_provenance_m4a(file_path, provenance)
            elif file_path.suffix == ".mp3":
                self._apply_provenance_mp3(file_path, provenance)

        except Exception as e:
            print(f"⚠️ Failed to add provenance metadata: {e}", file=sys.stderr)

    def _apply_provenance_m4a(self, file_path: Path, provenance: DownloadProvenance):
        """Apply provenance to M4A file.

        Args:
            file_path: Path to M4A file
            provenance: Provenance information
        """
        from mutagen.mp4 import MP4

        audio = MP4(str(file_path))

        # Add provenance as freeform atoms
        audio["----:com.apple.iTunes:TRACK_URL"] = provenance.track_url.encode("utf-8")
        if provenance.playlist_url:
            audio["----:com.apple.iTunes:PLAYLIST_URL"] = provenance.playlist_url.encode(
                "utf-8"
            )
        audio["----:com.apple.iTunes:SOURCE"] = provenance.source.encode("utf-8")
        audio["----:com.apple.iTunes:ORIGINAL_FORMAT"] = provenance.original_format.encode(
            "utf-8"
        )
        if provenance.original_bitrate:
            audio["----:com.apple.iTunes:ORIGINAL_BITRATE"] = str(
                provenance.original_bitrate
            ).encode("utf-8")

        audio.save()

    def _apply_provenance_mp3(self, file_path: Path, provenance: DownloadProvenance):
        """Apply provenance to MP3 file.

        Args:
            file_path: Path to MP3 file
            provenance: Provenance information
        """
        from mutagen.id3 import ID3, TXXX

        try:
            audio = ID3(str(file_path))
        except:
            from mutagen.id3 import ID3NoHeaderError

            audio = ID3()

        # Add provenance as TXXX frames (user-defined text)
        audio.add(TXXX(encoding=3, desc="TRACK_URL", text=provenance.track_url))
        if provenance.playlist_url:
            audio.add(
                TXXX(encoding=3, desc="PLAYLIST_URL", text=provenance.playlist_url)
            )
        audio.add(TXXX(encoding=3, desc="SOURCE", text=provenance.source))
        audio.add(
            TXXX(encoding=3, desc="ORIGINAL_FORMAT", text=provenance.original_format)
        )
        if provenance.original_bitrate:
            audio.add(
                TXXX(
                    encoding=3,
                    desc="ORIGINAL_BITRATE",
                    text=str(provenance.original_bitrate),
                )
            )

        audio.save(str(file_path))

    def extract_metadata(self, file_path: Path) -> Tuple[Optional[str], Optional[str]]:
        """Extract artist and title from audio file.

        Args:
            file_path: Path to audio file

        Returns:
            Tuple of (artist, title)
        """
        try:
            audio = MutagenFile(str(file_path), easy=True)
            if not audio:
                return None, None

            artist = audio.get("artist", [None])[0] if "artist" in audio else None
            title = audio.get("title", [None])[0] if "title" in audio else None

            return artist, title
        except Exception as e:
            print(f"⚠️ Error reading metadata: {e}", file=sys.stderr)
            return None, None

    def sanitize_filename(self, text: str) -> str:
        """Sanitize text for use in filename.

        Args:
            text: Text to sanitize

        Returns:
            Sanitized text
        """
        # Replace unsafe characters
        unsafe_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
        for char in unsafe_chars:
            text = text.replace(char, "-")

        # Remove leading/trailing whitespace and dots
        text = text.strip(". ")

        return text

    def create_filename(
        self,
        artist: Optional[str],
        title: Optional[str],
        extension: str,
        fallback: str = "unknown",
    ) -> str:
        """Create filename from metadata.

        Args:
            artist: Artist name
            title: Track title
            extension: File extension
            fallback: Fallback name if metadata missing

        Returns:
            Filename
        """
        if artist and title:
            artist = self.sanitize_filename(artist)
            title = self.sanitize_filename(title)
            return f"{artist} - {title}.{extension}"
        else:
            return f"{fallback}.{extension}"

    def check_duplicate(self, file_path: Path) -> bool:
        """Check if file is a duplicate.

        Args:
            file_path: Path to file to check

        Returns:
            True if user wants to skip (duplicate), False to keep
        """
        from ..duplicates import check_file_duplicate

        return check_file_duplicate(
            file_path, self.output_dir, self.config.duplicate_handling
        )

    def flag_metadata_review(self, file_path: Path, reason: str, url: str = ""):
        """Flag file for metadata review.

        Args:
            file_path: Path to file
            reason: Reason for flagging
            url: Source URL
        """
        from ..metadata import flag_for_review

        flag_for_review(file_path, reason, url, self.config.metadata_csv)

    def log_failure(self, url: str, error: str):
        """Log failed download.

        Args:
            url: URL that failed
            error: Error message
        """
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_entry = f"{timestamp} | {url} | {error}\n"

        with open(self.config.failed_log, "a") as f:
            f.write(log_entry)
