"""Base downloader class with common functionality."""

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Tuple

from mutagen import File as MutagenFile

from ..config import Config


class BaseDownloader(ABC):
    """Base class for source-specific downloaders."""

    def __init__(self, config: Config, output_dir: Path):
        """Initialize base downloader.

        Args:
            config: Configuration object
            output_dir: Output directory for downloads
        """
        self.config = config
        self.output_dir = output_dir

    @abstractmethod
    def download(self, url: str, format: str):
        """Download track(s) from URL.

        Args:
            url: URL to download from
            format: Output format (auto, m4a, mp3)
        """
        pass

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
