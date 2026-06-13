"""Integration tests for download workflows."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from track_manager.downloader import Downloader


class TestDownloadWorkflow:
    """Test complete download workflows."""

    def test_spotify_download_workflow(
        self,
        test_config,
        temp_output_dir,
        mock_spotify_download,
        create_test_audio_file,
    ):
        """Test complete Spotify download workflow."""
        downloader = Downloader(test_config, temp_output_dir)

        # Configure the mock to create a test file
        def mock_download(url, fmt):
            # Simulate spotdl creating a file
            test_file = temp_output_dir / "Artist - Song.mp3"
            create_test_audio_file(test_file, artist="Artist", title="Song")
            return [str(test_file)]

        # Use the existing mock fixture
        mock_spotify_download.return_value.download.side_effect = mock_download

        # Pretend Spotify credentials are configured so the downloader uses the
        # Spotify handler path rather than the song.link/TIDAL fallback.
        with patch.object(Downloader, "_has_spotify_credentials", return_value=True):
            downloader.download("https://open.spotify.com/track/test123", "auto")

        # Verify download was attempted
        mock_spotify_download.return_value.download.assert_called_once()

    def test_youtube_download_workflow(
        self, test_config, temp_output_dir, mock_ytdlp_download, create_test_audio_file
    ):
        """Test complete YouTube download workflow."""
        downloader = Downloader(test_config, temp_output_dir)

        # Mock yt-dlp to create a test file
        def mock_download(url, output_dir):
            test_file = output_dir / "Video Title.m4a"
            create_test_audio_file(
                test_file, artist="Artist", title="Video Title", format="m4a"
            )
            return str(test_file)

        with patch(
            "track_manager.sources.youtube.YouTubeDownloader.download"
        ) as mock_dl:
            mock_dl.side_effect = lambda url, fmt: mock_download(url, temp_output_dir)

            # Execute download
            downloader.download("https://www.youtube.com/watch?v=test123", "auto")

            # Verify download was attempted
            mock_dl.assert_called_once()

    def test_soundcloud_download_workflow(
        self, test_config, temp_output_dir, mock_ytdlp_download, create_test_audio_file
    ):
        """Test complete SoundCloud download workflow."""
        downloader = Downloader(test_config, temp_output_dir)

        with patch(
            "track_manager.sources.soundcloud.SoundCloudDownloader.download"
        ) as mock_dl:
            # Execute download
            downloader.download("https://soundcloud.com/artist/track", "auto")

            # Verify download was attempted
            mock_dl.assert_called_once()

    def test_direct_url_download_workflow(
        self,
        test_config,
        temp_output_dir,
        mock_requests_download,
        create_test_audio_file,
    ):
        """Test complete direct URL download workflow."""
        downloader = Downloader(test_config, temp_output_dir)

        with patch("track_manager.sources.direct.DirectDownloader.download") as mock_dl:
            # Execute download
            downloader.download("https://example.com/track.mp3", "auto")

            # Verify download was attempted
            mock_dl.assert_called_once()

    def test_source_detection(self, test_config, temp_output_dir):
        """Test that URLs are routed to correct handlers."""
        downloader = Downloader(test_config, temp_output_dir)

        test_cases = [
            ("https://open.spotify.com/track/123", "spotify"),
            ("https://www.youtube.com/watch?v=123", "youtube"),
            ("https://youtu.be/123", "youtube"),
            ("https://soundcloud.com/artist/track", "soundcloud"),
            ("https://example.com/audio.mp3", "direct"),
        ]

        for url, expected_source in test_cases:
            detected = downloader.detect_source(url)
            assert detected == expected_source, f"Failed for {url}"

    def test_format_selection(
        self,
        test_config,
        temp_output_dir,
        create_test_audio_file,
        mock_spotify_download,
    ):
        """Test format selection works correctly."""
        downloader = Downloader(test_config, temp_output_dir)

        # Pretend Spotify credentials are configured so the downloader uses the
        # Spotify handler path rather than the song.link/TIDAL fallback.
        with patch.object(Downloader, "_has_spotify_credentials", return_value=True):
            # Test explicit format
            downloader.download("https://open.spotify.com/track/123", "mp3")
            mock_spotify_download.return_value.download.assert_called_with(
                "https://open.spotify.com/track/123", "mp3"
            )

            # Test auto format
            downloader.download("https://open.spotify.com/track/456", "auto")
            mock_spotify_download.return_value.download.assert_called_with(
                "https://open.spotify.com/track/456", "auto"
            )

    def test_custom_output_directory(self, test_config, tmp_path):
        """Test custom output directory is used."""
        custom_dir = tmp_path / "custom_output"
        custom_dir.mkdir()

        downloader = Downloader(test_config, custom_dir)
        assert downloader.output_dir == custom_dir

    def test_output_directory_creation(self, test_config, tmp_path):
        """Test output directory is created if it doesn't exist."""
        new_dir = tmp_path / "new_output"
        assert not new_dir.exists()

        downloader = Downloader(test_config, new_dir)
        assert new_dir.exists()
        assert downloader.output_dir == new_dir
