"""Tests for duplicate detection functionality."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from track_manager.duplicates import (
    check_file_duplicate,
    extract_metadata,
    find_duplicates,
    handle_duplicates,
    normalize_metadata,
    normalize_text,
    scan_library,
)


class TestNormalizeText:
    """Test text normalization for duplicate detection."""

    def test_normalize_text_basic(self):
        """Test basic text normalization."""
        assert normalize_text("Hello World") == "hello world"
        assert normalize_text("  Test  ") == "test"

    def test_normalize_text_remove_junk_patterns(self):
        """Test removal of junk patterns."""
        assert normalize_text("Song [Official Video]") == "song"
        assert normalize_text("Track (Audio)") == "track"
        assert normalize_text("Title [HD]") == "title"

    def test_normalize_text_featuring_variations(self):
        """Test normalization of featuring variations."""
        assert normalize_text("Artist feat. Guest") == "artist feat. guest"
        assert normalize_text("Artist ft. Guest") == "artist feat. guest"
        assert normalize_text("Artist featuring Guest") == "artist feat. guest"

    def test_normalize_text_artist_separators_preserved(self):
        """Test that artist separators are preserved (not normalized)."""
        # We preserve & and x to avoid breaking artist names like "Excision"
        assert normalize_text("Artist1 & Artist2") == "artist1 & artist2"
        assert normalize_text("Artist1 x Artist2") == "artist1 x artist2"
        assert normalize_text("Excision") == "excision"
        assert normalize_text("Flux Pavilion") == "flux pavilion"

    def test_normalize_text_lyrics_visualizer_patterns(self):
        """Test removal of lyrics and visualizer patterns."""
        assert normalize_text("Song [Lyrics]") == "song"
        assert normalize_text("Song [Lyric Video]") == "song"
        assert normalize_text("Song (Lyric Video)") == "song"
        assert normalize_text("Song [Visualizer]") == "song"
        assert normalize_text("Song [Visualiser]") == "song"

    def test_normalize_text_quality_resolution_patterns(self):
        """Test removal of quality and resolution patterns."""
        assert normalize_text("Song [4K]") == "song"
        assert normalize_text("Song [8K]") == "song"
        assert normalize_text("Song [UHD]") == "song"
        assert normalize_text("Song [HQ]") == "song"
        assert normalize_text("Song [LQ]") == "song"
        assert normalize_text("Song (High Quality)") == "song"
        assert normalize_text("Song [Best Quality]") == "song"

    def test_normalize_text_platform_patterns(self):
        """Test removal of platform-specific patterns."""
        assert normalize_text("Artist - Topic") == "artist"
        assert normalize_text("Song [Premium]") == "song"

    def test_normalize_text_promo_patterns(self):
        """Test removal of promotional patterns."""
        assert normalize_text("Song [Free Download]") == "song"
        assert normalize_text("Song [Download]") == "song"
        assert normalize_text("Song [Out Now]") == "song"
        assert normalize_text("Song [NEW]") == "song"

    def test_normalize_text_multiple_patterns(self):
        """Test removal of multiple junk patterns together."""
        assert normalize_text("Song [Official Video] [4K] [Lyrics]") == "song"
        assert normalize_text("Song (Official Audio) [HQ] [NEW]") == "song"

    def test_normalize_text_meaningful_distinctions_preserved(self):
        """Test that meaningful distinctions are preserved for DJ use."""
        assert normalize_text("Song (Live)") == "song (live)"
        assert normalize_text("Song (Acoustic)") == "song (acoustic)"
        assert normalize_text("Song (Remix)") == "song (remix)"
        assert normalize_text("Song (Extended Edit)") == "song (extended edit)"
        assert normalize_text("Song (Radio Edit)") == "song (radio edit)"
        assert normalize_text("Song (Explicit)") == "song (explicit)"
        assert normalize_text("Song (Clean)") == "song (clean)"
        assert normalize_text("Song (Instrumental)") == "song (instrumental)"

    def test_normalize_text_feat_with_parens(self):
        """Test featuring normalization with parentheses doesn't break other parens."""
        assert normalize_text("Song (feat. Artist)") == "song feat. artist"
        assert normalize_text("Song (ft. Artist)") == "song feat. artist"
        assert normalize_text("Song (featuring Artist)") == "song feat. artist"
        assert (
            normalize_text("Song (feat. Artist) (Live)") == "song feat. artist (live)"
        )


class TestExtractMetadata:
    """Test metadata extraction from audio files."""

    def test_extract_metadata_nonexistent_file(self):
        """Test metadata extraction from nonexistent file."""
        artist, title = extract_metadata(Path("/nonexistent/file.mp3"))
        assert artist is None
        assert title is None

    @patch("track_manager.duplicates.MutagenFile")
    def test_extract_metadata_success(self, mock_mutagen):
        """Test successful metadata extraction."""
        # Create a mock that properly simulates mutagen behavior
        mock_audio = MagicMock()

        # Mock the bool check - mutagen file should be truthy
        mock_audio.__bool__.return_value = True

        # Mock the get method to return our test values
        mock_audio.get.side_effect = lambda key, default=None: {
            "artist": ["Test Artist"],
            "title": ["Test Title"],
        }.get(key, default)

        # Mock the 'in' operator for metadata field checks
        mock_audio.__contains__.side_effect = lambda key: key in ["artist", "title"]

        # Set the mock mutagen to return our mock audio object
        mock_mutagen.return_value = mock_audio

        artist, title = extract_metadata(Path("test.mp3"))

        # Verify mutagen was called correctly
        mock_mutagen.assert_called_once_with("test.mp3", easy=True)

        assert artist == "Test Artist"
        assert title == "Test Title"

    @patch("track_manager.duplicates.MutagenFile")
    def test_extract_metadata_missing_fields(self, mock_mutagen):
        """Test metadata extraction with missing fields."""
        # Mock mutagen file with missing metadata
        mock_audio = MagicMock()
        mock_audio.get.return_value = [None]
        mock_mutagen.return_value = mock_audio

        artist, title = extract_metadata(Path("test.mp3"))
        assert artist is None
        assert title is None


class TestNormalizeMetadata:
    """Test metadata normalization."""

    def test_normalize_metadata_basic(self):
        """Test basic metadata normalization."""
        artist, title = normalize_metadata("Test Artist", "Test Title")
        assert artist == "test artist"
        assert title == "test title"

    def test_normalize_metadata_with_junk(self):
        """Test metadata normalization with junk patterns."""
        artist, title = normalize_metadata("Artist [Official]", "Title (Audio) [HD]")
        assert artist == "artist"
        assert title == "title"

    def test_normalize_metadata_none_values(self):
        """Test metadata normalization with None values."""
        artist, title = normalize_metadata(None, None)
        assert artist == ""
        assert title == ""


class TestFindDuplicates:
    """Test duplicate finding functionality."""

    def test_find_duplicates_no_metadata(self):
        """Test duplicate finding with no metadata."""
        with tempfile.TemporaryDirectory() as temp_dir:
            duplicates = find_duplicates("", "", Path(temp_dir))
            assert duplicates == []

    def test_find_duplicates_no_matches(self):
        """Test duplicate finding with no matches."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Create test files
            (temp_path / "file1.mp3").touch()
            (temp_path / "file2.mp3").touch()

            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.side_effect = [
                    ("Artist1", "Title1"),
                    ("Artist2", "Title2"),
                ]
                duplicates = find_duplicates("Artist3", "Title3", temp_path)
                assert duplicates == []

    def test_find_duplicates_with_matches(self):
        """Test duplicate finding with matches."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Create test files
            file1 = temp_path / "file1.mp3"
            file2 = temp_path / "file2.mp3"
            file1.touch()
            file2.touch()

            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.side_effect = [
                    ("Artist", "Title"),
                    ("Artist", "Title"),
                ]
                duplicates = find_duplicates("Artist", "Title", temp_path)
                assert len(duplicates) == 2


class TestCheckFileDuplicate:
    """Test file duplicate checking."""

    def test_check_file_duplicate_no_duplicates(self):
        """Test duplicate check with no duplicates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.return_value = ("Artist", "Title")
                with patch("track_manager.duplicates.find_duplicates") as mock_find:
                    mock_find.return_value = []
                    result = check_file_duplicate(
                        Path("test.mp3"), Path(temp_dir), "interactive"
                    )
                    assert result is False

    def test_check_file_duplicate_skip_mode(self):
        """Test duplicate check in skip mode."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.return_value = ("Artist", "Title")
                with patch("track_manager.duplicates.find_duplicates") as mock_find:
                    mock_find.return_value = [Path("existing.mp3")]
                    result = check_file_duplicate(
                        Path("test.mp3"), Path(temp_dir), "skip"
                    )
                    assert result is True

    def test_check_file_duplicate_keep_mode(self):
        """Test duplicate check in keep mode."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.return_value = ("Artist", "Title")
                with patch("track_manager.duplicates.find_duplicates") as mock_find:
                    mock_find.return_value = [Path("existing.mp3")]
                    result = check_file_duplicate(
                        Path("test.mp3"), Path(temp_dir), "keep"
                    )
                    assert result is False

    @patch("builtins.input")
    def test_check_file_duplicate_interactive_skip(self, mock_input):
        """Test interactive duplicate check with skip."""
        mock_input.return_value = "s"
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.return_value = ("Artist", "Title")
                with patch("track_manager.duplicates.find_duplicates") as mock_find:
                    mock_find.return_value = [Path("existing.mp3")]
                    result = check_file_duplicate(
                        Path("test.mp3"), Path(temp_dir), "interactive"
                    )
                    assert result is True

    @patch("builtins.input")
    def test_check_file_duplicate_interactive_keep(self, mock_input):
        """Test interactive duplicate check with keep."""
        mock_input.return_value = "k"
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.return_value = ("Artist", "Title")
                with patch("track_manager.duplicates.find_duplicates") as mock_find:
                    mock_find.return_value = [Path("existing.mp3")]
                    result = check_file_duplicate(
                        Path("test.mp3"), Path(temp_dir), "interactive"
                    )
                    assert result is False

    @patch("builtins.input")
    def test_check_file_duplicate_interactive_replace(self, mock_input):
        """Test interactive duplicate check with replace."""
        mock_input.return_value = "r"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Create the existing file that will be deleted
            existing_file = temp_path / "existing.mp3"
            existing_file.touch()

            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.return_value = ("Artist", "Title")
                with patch("track_manager.duplicates.find_duplicates") as mock_find:
                    mock_find.return_value = [existing_file]
                    result = check_file_duplicate(
                        Path("test.mp3"), temp_path, "interactive"
                    )
                    assert result is False
                    # Verify the existing file was deleted
                    assert not existing_file.exists()


class TestHandleDuplicates:
    """Test the centralized duplicate-handling decision logic."""

    def test_handle_duplicates_no_duplicates(self):
        """Empty duplicate list never skips."""
        assert handle_duplicates([], "skip") is False
        assert handle_duplicates([], "interactive", artist="A", title="T") is False

    def test_handle_duplicates_skip_mode(self):
        """Skip mode returns True when duplicates exist."""
        result = handle_duplicates(
            [Path("existing.mp3")], "skip", artist="A", title="T"
        )
        assert result is True

    def test_handle_duplicates_keep_mode(self):
        """Keep mode returns False (keep both)."""
        result = handle_duplicates(
            [Path("existing.mp3")], "keep", artist="A", title="T"
        )
        assert result is False

    @patch("builtins.input")
    def test_handle_duplicates_interactive_skip(self, mock_input):
        """Interactive 's' skips the new file."""
        mock_input.return_value = "s"
        result = handle_duplicates(
            [Path("existing.mp3")], "interactive", artist="A", title="T"
        )
        assert result is True

    @patch("builtins.input")
    def test_handle_duplicates_interactive_keep(self, mock_input):
        """Interactive 'k' keeps both."""
        mock_input.return_value = "k"
        result = handle_duplicates(
            [Path("existing.mp3")], "interactive", artist="A", title="T"
        )
        assert result is False

    @patch("builtins.input")
    def test_handle_duplicates_interactive_replace(self, mock_input):
        """Interactive 'r' deletes existing files and keeps new."""
        mock_input.return_value = "r"
        with tempfile.TemporaryDirectory() as temp_dir:
            existing_file = Path(temp_dir) / "existing.mp3"
            existing_file.touch()

            result = handle_duplicates(
                [existing_file], "interactive", artist="A", title="T"
            )
            assert result is False
            assert not existing_file.exists()


class TestScanLibrary:
    """Test library scanning for duplicates."""

    def test_scan_library_no_duplicates(self):
        """Test library scan with no duplicates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "file1.mp3").touch()
            (temp_path / "file2.m4a").touch()

            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.side_effect = [
                    ("Artist1", "Title1"),
                    ("Artist2", "Title2"),
                ]
                duplicates = scan_library(temp_path)
                assert duplicates == {}

    def test_scan_library_with_duplicates(self):
        """Test library scan with duplicates."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            file1 = temp_path / "file1.mp3"
            file2 = temp_path / "file2.m4a"
            file1.touch()
            file2.touch()

            with patch("track_manager.duplicates.extract_metadata") as mock_extract:
                mock_extract.side_effect = [
                    ("Test Artist", "Test Title"),
                    ("Test Artist", "Test Title"),
                ]
                duplicates = scan_library(temp_path)
                assert len(duplicates) == 1
                # Check that the duplicate group has 2 files
                files = list(duplicates.values())[0]
                assert len(files) == 2
                # Verify the key format (normalized artist|||title)
                key = list(duplicates.keys())[0]
                assert "test artist|||test title" == key


class TestFindDuplicatesByISRC:
    """Test ISRC-based duplicate detection."""

    def test_find_duplicates_by_isrc_no_isrc(self, tmp_path):
        """Test with empty ISRC."""
        from track_manager.duplicates import find_duplicates_by_isrc

        result = find_duplicates_by_isrc("", tmp_path)
        assert result == []

    def test_find_duplicates_by_isrc_no_matches(self, tmp_path):
        """Test when no files match the ISRC."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_isrc

        # Create a test M4A file with FFmpeg
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        # Add ISRC metadata
        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:ISRC"] = [b"USRC12345678"]
        audio.save()

        result = find_duplicates_by_isrc("USRC87654321", tmp_path)
        assert result == []

    def test_find_duplicates_by_isrc_with_match(self, tmp_path):
        """Test when a file matches the ISRC."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_isrc

        # Create a test M4A file with FFmpeg
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        # Add ISRC metadata
        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:ISRC"] = [b"USRC12345678"]
        audio.save()

        result = find_duplicates_by_isrc("USRC12345678", tmp_path)
        assert len(result) == 1
        assert result[0] == file_path

    def test_find_duplicates_by_isrc_case_insensitive(self, tmp_path):
        """Test that ISRC matching is case-insensitive."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_isrc

        # Create a test M4A file with FFmpeg
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        # Add ISRC metadata in lowercase
        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:ISRC"] = [b"usrc12345678"]
        audio.save()

        result = find_duplicates_by_isrc("USRC12345678", tmp_path)
        assert len(result) == 1
        assert result[0] == file_path

    def test_find_duplicates_by_isrc_multiple_matches(self, tmp_path):
        """Test when multiple files have the same ISRC."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_isrc

        # Create two test M4A files with FFmpeg
        file1 = tmp_path / "test1.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file1),
            ],
            capture_output=True,
            check=True,
        )
        audio1 = MP4(str(file1))
        audio1["----:com.apple.iTunes:ISRC"] = [b"USRC12345678"]
        audio1.save()

        file2 = tmp_path / "test2.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file2),
            ],
            capture_output=True,
            check=True,
        )
        audio2 = MP4(str(file2))
        audio2["----:com.apple.iTunes:ISRC"] = [b"USRC12345678"]
        audio2.save()

        result = find_duplicates_by_isrc("USRC12345678", tmp_path)
        assert len(result) == 2
        assert file1 in result
        assert file2 in result


class TestFindDuplicatesByTrackURL:
    """Test track URL-based duplicate detection."""

    def test_find_duplicates_by_track_url_no_url(self, tmp_path):
        """Test with empty track URL."""
        from track_manager.duplicates import find_duplicates_by_track_url

        result = find_duplicates_by_track_url("", tmp_path)
        assert result == []

    def test_find_duplicates_by_track_url_no_matches(self, tmp_path):
        """Test when no files match the track URL."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_track_url

        # Create a test M4A file with different track URL
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        # Add track URL metadata
        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:TRACK_URL"] = [
            b"https://open.spotify.com/track/123"
        ]
        audio.save()

        result = find_duplicates_by_track_url(
            "https://open.spotify.com/track/456", tmp_path
        )
        assert result == []

    def test_find_duplicates_by_track_url_with_match(self, tmp_path):
        """Test when a file matches the track URL."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_track_url

        # Create a test M4A file with matching track URL
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        # Add track URL metadata
        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:TRACK_URL"] = [
            b"https://open.spotify.com/track/123"
        ]
        audio.save()

        result = find_duplicates_by_track_url(
            "https://open.spotify.com/track/123", tmp_path
        )
        assert len(result) == 1
        assert result[0] == file_path

    def test_find_duplicates_by_track_url_ignores_trailing_slash(self, tmp_path):
        """Test that trailing slashes in URLs are ignored."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_track_url

        # Create file with URL without trailing slash
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:TRACK_URL"] = [
            b"https://open.spotify.com/track/123"
        ]
        audio.save()

        # Search with trailing slash
        result = find_duplicates_by_track_url(
            "https://open.spotify.com/track/123/", tmp_path
        )
        assert len(result) == 1
        assert result[0] == file_path

    def test_find_duplicates_by_track_url_ignores_tracking_params(self, tmp_path):
        """Tracking params (e.g. Spotify ``si=``) are ignored; identity is the path."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_track_url

        # Create file with clean URL
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:TRACK_URL"] = [
            b"https://open.spotify.com/track/123"
        ]
        audio.save()

        # Search with query parameters
        result = find_duplicates_by_track_url(
            "https://open.spotify.com/track/123?si=abc123", tmp_path
        )
        assert len(result) == 1
        assert result[0] == file_path

    def test_find_duplicates_by_track_url_youtube_video_id_is_identity(self, tmp_path):
        """Different YouTube ``v=`` values are different recordings.

        Stripping all query params would collapse every watch URL to
        ``youtube.com/watch`` and let a new download overwrite an unrelated
        existing file (the smart-upgrade path).
        """
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_track_url

        file_path = tmp_path / "owned.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:TRACK_URL"] = [
            b"https://www.youtube.com/watch?v=TVSTATICID1"
        ]
        audio.save()

        assert (
            find_duplicates_by_track_url(
                "https://www.youtube.com/watch?v=AMYDIAMOND1", tmp_path
            )
            == []
        )
        matches = find_duplicates_by_track_url(
            "https://www.youtube.com/watch?v=TVSTATICID1&list=PLother", tmp_path
        )
        assert matches == [file_path]

    def test_find_duplicates_by_track_url_case_insensitive(self, tmp_path):
        """Test that URL matching is case-insensitive."""
        import subprocess

        from mutagen.mp4 import MP4

        from track_manager.duplicates import find_duplicates_by_track_url

        # Create file with lowercase URL
        file_path = tmp_path / "test.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:a",
                "aac",
                "-y",
                str(file_path),
            ],
            capture_output=True,
            check=True,
        )

        audio = MP4(str(file_path))
        audio["----:com.apple.iTunes:TRACK_URL"] = [
            b"https://open.spotify.com/track/abc"
        ]
        audio.save()

        # Search with uppercase
        result = find_duplicates_by_track_url(
            "https://open.spotify.com/track/ABC", tmp_path
        )
        assert len(result) == 1
        assert result[0] == file_path
