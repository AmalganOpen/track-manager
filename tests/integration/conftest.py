"""Pytest fixtures for integration tests."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml

from track_manager.config import Config


@pytest.fixture
def temp_output_dir(tmp_path):
    """Create a temporary output directory for tests."""
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def temp_config_file(tmp_path, temp_output_dir):
    """Create a temporary config file."""
    config_path = tmp_path / "config.yaml"
    config_data = {
        "output_dir": str(temp_output_dir),
        "failed_log": str(tmp_path / "failed.txt"),
        "metadata_csv": str(tmp_path / "metadata-review.csv"),
        "duplicate_handling": "interactive",
        "default_format": "auto",
        "playlist_threshold": 50,
    }
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)
    return config_path


@pytest.fixture
def test_config(temp_config_file, temp_output_dir):
    """Create a Config instance for testing."""
    with patch("track_manager.config.Path.home") as mock_home:
        mock_home.return_value = temp_config_file.parent
        config = Config(config_path=temp_config_file)
        return config


@pytest.fixture
def create_test_audio_file():
    """Factory fixture to create test audio files with metadata."""

    def _create(path: Path, artist: str = None, title: str = None, format: str = "mp3"):
        """Create a minimal valid audio file with metadata.

        Args:
            path: Path to create file at
            artist: Artist metadata
            title: Title metadata
            format: File format (mp3 or m4a)
        """
        import shutil

        from mutagen.id3 import ID3, TIT2, TPE1
        from mutagen.mp3 import MP3
        from mutagen.mp4 import MP4

        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Get the template file path
        fixtures_dir = Path(__file__).parent.parent / "fixtures"
        template_file = fixtures_dir / f"template.{format}"

        # Copy template file
        shutil.copy(template_file, path)

        # Add metadata
        if format == "mp3":
            audio = MP3(str(path), ID3=ID3)
            if not audio.tags:
                audio.add_tags()
            if artist:
                audio.tags.add(TPE1(encoding=3, text=artist))
            if title:
                audio.tags.add(TIT2(encoding=3, text=title))
            audio.save()

        elif format == "m4a":
            audio = MP4(str(path))
            if artist:
                audio.tags["\xa9ART"] = [artist]
            if title:
                audio.tags["\xa9nam"] = [title]
            audio.save()

        return path

    return _create


def _make_spotify_mock_instance() -> MagicMock:
    """Build a SpotifyDownloader mock with a writable spotdl.downloader.settings dict.

    The real Downloader mutates ``spotdl.downloader.settings["output"]`` when
    reusing a cached Spotify handler, so the mock must support item assignment
    on that nested attribute.
    """
    instance = MagicMock()
    instance.download = Mock(return_value=None)
    instance.spotdl.downloader.settings = {}
    return instance


@pytest.fixture(autouse=True)
def auto_mock_spotify():
    """Automatically mock SpotifyDownloader for all tests to avoid authentication."""
    with patch("track_manager.sources.spotify.SpotifyDownloader") as mock_spotify_class:
        mock_spotify_class.return_value = _make_spotify_mock_instance()
        yield mock_spotify_class


@pytest.fixture
def mock_spotify_download():
    """Mock spotdl downloads (explicit fixture for tests that need it)."""
    with patch("track_manager.sources.spotify.SpotifyDownloader") as mock_spotify_class:
        mock_spotify_class.return_value = _make_spotify_mock_instance()
        yield mock_spotify_class


@pytest.fixture
def mock_ytdlp_download():
    """Mock yt-dlp downloads."""
    with patch("track_manager.sources.youtube.yt_dlp") as mock_ytdlp:
        yield mock_ytdlp


@pytest.fixture
def mock_requests_download():
    """Mock requests downloads."""
    with patch("track_manager.sources.direct.requests") as mock_requests:
        # Create a mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "audio/mpeg"}
        mock_response.iter_content = Mock(return_value=[b"test data"])
        mock_requests.get.return_value = mock_response
        yield mock_requests
