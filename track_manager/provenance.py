"""Download provenance tracking for metadata."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DownloadProvenance:
    """Track download provenance information.

    This captures where a track came from and its original quality,
    which is stored in the M4A file metadata for reference.
    """

    track_url: str
    """Original URL of the track"""

    playlist_url: Optional[str]
    """URL of playlist if downloaded from a playlist"""

    source: str
    """Download source: 'dab', 'youtube', 'soundcloud', 'spotify'"""

    original_format: str
    """Original audio format: 'flac', 'opus', 'm4a', etc."""

    original_bitrate: Optional[int]
    """Original bitrate in kbps, None for lossless formats"""

    isrc: Optional[str] = None
    """ISRC code if available"""
