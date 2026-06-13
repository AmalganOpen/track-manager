"""Integration tests for metadata management workflow."""

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from track_manager.metadata import apply_metadata_csv, flag_for_review, verify_library


class TestMetadataWorkflow:
    """Test complete metadata management workflows."""

    def test_flag_for_review_creates_csv(
        self, temp_output_dir, create_test_audio_file, test_config
    ):
        """Test that flagging creates CSV file."""
        # Use unique CSV path for this test
        csv_path = temp_output_dir.parent / "metadata-review-test1.csv"
        assert not csv_path.exists()

        # Create test file
        test_file = temp_output_dir / "test.mp3"
        create_test_audio_file(
            test_file, artist="Artist", title="Song [Official Video]", format="mp3"
        )

        # Mock get_metadata_csv_path to use test path
        with patch(
            "track_manager.metadata.get_metadata_csv_path", return_value=csv_path
        ):
            # Flag for review
            flag_for_review(test_file, "Junk in title", "https://example.com")

        # Verify CSV was created
        assert csv_path.exists()

        # Verify content
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["file_path"] == str(test_file)
            assert rows[0]["current_title"] == "Song [Official Video]"
            assert rows[0]["notes"] == "Junk in title"

    def test_flag_for_review_appends_to_csv(
        self, temp_output_dir, create_test_audio_file, test_config
    ):
        """Test that flagging appends to existing CSV."""
        csv_path = temp_output_dir.parent / "metadata-review-test2.csv"

        # Create and flag first file
        file1 = temp_output_dir / "test1.mp3"
        create_test_audio_file(file1, artist="Artist1", title="Song1", format="mp3")

        # Create and flag second file
        file2 = temp_output_dir / "test2.mp3"
        create_test_audio_file(file2, artist="Artist2", title="Song2", format="mp3")

        # Mock get_metadata_csv_path to use test path
        with patch(
            "track_manager.metadata.get_metadata_csv_path", return_value=csv_path
        ):
            flag_for_review(file1, "Reason 1", "url1")
            flag_for_review(file2, "Reason 2", "url2")

        # Verify both entries exist
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 2

    def test_apply_metadata_csv(
        self, temp_output_dir, create_test_audio_file, test_config
    ):
        """Test applying metadata corrections from CSV."""
        # Use unique CSV path for this test
        csv_path = temp_output_dir.parent / "metadata-review-test3.csv"

        # Create test file with incorrect metadata
        test_file = temp_output_dir / "test.mp3"
        create_test_audio_file(
            test_file, artist="Wrong Artist", title="Wrong Title", format="mp3"
        )

        # Create CSV with corrections
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "file_path",
                    "current_artist",
                    "current_title",
                    "suggested_artist",
                    "suggested_title",
                    "source_url",
                    "notes",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "file_path": str(test_file),
                    "current_artist": "Wrong Artist",
                    "current_title": "Wrong Title",
                    "suggested_artist": "Correct Artist",
                    "suggested_title": "Correct Title",
                    "source_url": "https://example.com",
                    "notes": "Test correction",
                }
            )

        # Apply corrections
        with patch(
            "track_manager.metadata.get_metadata_csv_path", return_value=csv_path
        ):
            apply_metadata_csv(dry_run=False)

        # Verify metadata was updated
        from mutagen.mp3 import MP3

        # The file might have been renamed, so check for the new name
        new_file = temp_output_dir / "Correct Artist - Correct Title.mp3"
        if new_file.exists():
            audio = MP3(str(new_file))
        else:
            audio = MP3(str(test_file))

        assert audio.get("TPE1", [None])[0] == "Correct Artist"
        assert audio.get("TIT2", [None])[0] == "Correct Title"

        # Verify CSV was removed when empty
        assert not csv_path.exists()

    def test_apply_partial_corrections(
        self, temp_output_dir, create_test_audio_file, test_config
    ):
        """Test that only rows with suggestions are processed."""
        # Use unique CSV path for this test
        csv_path = temp_output_dir.parent / "metadata-review-test4.csv"

        # Create two test files
        file1 = temp_output_dir / "test1.mp3"
        create_test_audio_file(file1, artist="Artist1", title="Title1", format="mp3")

        file2 = temp_output_dir / "test2.mp3"
        create_test_audio_file(file2, artist="Artist2", title="Title2", format="mp3")

        # Create CSV with one correction and one pending
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "file_path",
                    "current_artist",
                    "current_title",
                    "suggested_artist",
                    "suggested_title",
                    "source_url",
                    "notes",
                ],
            )
            writer.writeheader()
            # Row with suggestions (will be processed)
            writer.writerow(
                {
                    "file_path": str(file1),
                    "current_artist": "Artist1",
                    "current_title": "Title1",
                    "suggested_artist": "New Artist1",
                    "suggested_title": "New Title1",
                    "source_url": "url1",
                    "notes": "note1",
                }
            )
            # Row without suggestions (will remain)
            writer.writerow(
                {
                    "file_path": str(file2),
                    "current_artist": "Artist2",
                    "current_title": "Title2",
                    "suggested_artist": "",
                    "suggested_title": "",
                    "source_url": "url2",
                    "notes": "note2",
                }
            )

        # Apply corrections
        with patch(
            "track_manager.metadata.get_metadata_csv_path", return_value=csv_path
        ):
            apply_metadata_csv(dry_run=False)

        # Verify only first file was updated
        from mutagen.mp3 import MP3

        # Check for renamed file first
        new_file1 = temp_output_dir / "New Artist1 - New Title1.mp3"
        if new_file1.exists():
            audio1 = MP3(str(new_file1))
        else:
            audio1 = MP3(str(file1))

        assert audio1.get("TPE1", [None])[0] == "New Artist1"

        audio2 = MP3(str(file2))
        assert audio2.get("TPE1", [None])[0] == "Artist2"  # Unchanged

        # Verify only pending row remains in CSV
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["file_path"] == str(file2)

    def test_dry_run_mode(self, temp_output_dir, create_test_audio_file, test_config):
        """Test that dry run doesn't modify files or CSV."""
        csv_path = temp_output_dir.parent / "metadata-review-dryrun.csv"

        # Create test file
        test_file = temp_output_dir / "test.mp3"
        create_test_audio_file(
            test_file, artist="Original", title="Original", format="mp3"
        )

        # Create CSV with correction
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "file_path",
                    "current_artist",
                    "current_title",
                    "suggested_artist",
                    "suggested_title",
                    "source_url",
                    "notes",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "file_path": str(test_file),
                    "current_artist": "Original",
                    "current_title": "Original",
                    "suggested_artist": "Updated",
                    "suggested_title": "Updated",
                    "source_url": "url",
                    "notes": "note",
                }
            )

        # Apply in dry run mode
        with patch(
            "track_manager.metadata.get_metadata_csv_path", return_value=csv_path
        ):
            apply_metadata_csv(dry_run=True)

        # Verify file was NOT updated
        from mutagen.mp3 import MP3

        audio = MP3(str(test_file))
        assert audio.get("TPE1", [None])[0] == "Original"

        # Verify CSV was NOT modified
        assert csv_path.exists()
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]["suggested_artist"] == "Updated"

    def test_verify_library(self, temp_output_dir, create_test_audio_file):
        """Test metadata quality verification."""
        # Create files with various metadata quality
        good_file = temp_output_dir / "good.mp3"
        create_test_audio_file(good_file, artist="Artist", title="Song", format="mp3")

        missing_file = temp_output_dir / "missing.mp3"
        create_test_audio_file(missing_file, artist=None, title=None, format="mp3")

        junk_file = temp_output_dir / "junk.mp3"
        create_test_audio_file(
            junk_file, artist="Artist", title="Song [Official Video]", format="mp3"
        )

        issues = verify_library(temp_output_dir)

        # Should find issues with missing and junk files
        assert len(issues) >= 2

    def test_csv_removed_when_empty(
        self, temp_output_dir, create_test_audio_file, test_config
    ):
        """Test that CSV is removed when all corrections are applied."""
        csv_path = temp_output_dir.parent / "metadata-review-test5.csv"

        # Create test file
        test_file = temp_output_dir / "test.mp3"
        create_test_audio_file(test_file, artist="Old", title="Old", format="mp3")

        # Create CSV with one correction
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "file_path",
                    "current_artist",
                    "current_title",
                    "suggested_artist",
                    "suggested_title",
                    "source_url",
                    "notes",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "file_path": str(test_file),
                    "current_artist": "Old",
                    "current_title": "Old",
                    "suggested_artist": "New",
                    "suggested_title": "New",
                    "source_url": "url",
                    "notes": "note",
                }
            )

        # Apply corrections
        with patch(
            "track_manager.metadata.get_metadata_csv_path", return_value=csv_path
        ):
            apply_metadata_csv(dry_run=False)

        # Verify CSV was removed
        assert not csv_path.exists()
