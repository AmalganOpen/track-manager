"""Integration tests for CLI commands."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner

from track_manager.cli import cli
from track_manager.self_update import UpdateResult


class TestCLIIntegration:
    """Test CLI commands end-to-end."""

    def test_download_command(self, test_config, temp_output_dir):
        """Test download command execution."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.cli.Downloader.download") as mock_download:
                result = runner.invoke(
                    cli, ["download", "https://open.spotify.com/track/test"]
                )

                assert result.exit_code == 0
                mock_download.assert_called_once()

    def test_download_with_format(self, test_config, temp_output_dir):
        """Test download with explicit format."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.cli.Downloader.download") as mock_download:
                result = runner.invoke(
                    cli,
                    [
                        "download",
                        "https://open.spotify.com/track/test",
                        "--format",
                        "mp3",
                    ],
                )

                assert result.exit_code == 0
                # Verify format was passed
                args = mock_download.call_args
                assert args[0][1] == "mp3"

    def test_download_with_output_dir(self, test_config, tmp_path):
        """Test download with custom output directory."""
        runner = CliRunner()
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.cli.Downloader") as mock_downloader_class:
                result = runner.invoke(
                    cli,
                    [
                        "download",
                        "https://open.spotify.com/track/test",
                        "--output",
                        str(custom_dir),
                    ],
                )

                assert result.exit_code == 0
                # Verify custom directory was used
                call_args = mock_downloader_class.call_args
                assert call_args[0][1] == custom_dir

    def test_check_duplicates_command(
        self, test_config, temp_output_dir, create_test_audio_file
    ):
        """Test check-duplicates command."""
        runner = CliRunner()

        # Create test files
        create_test_audio_file(
            temp_output_dir / "test.mp3", artist="Artist", title="Song", format="mp3"
        )

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.duplicates.scan_library") as mock_scan:
                mock_scan.return_value = []  # No duplicates

                result = runner.invoke(cli, ["check-duplicates"])

                assert result.exit_code == 0
                mock_scan.assert_called_once()

    def test_check_duplicates_with_file(
        self, test_config, temp_output_dir, create_test_audio_file
    ):
        """Test check-duplicates with specific file."""
        runner = CliRunner()

        test_file = temp_output_dir / "test.mp3"
        create_test_audio_file(test_file, artist="Artist", title="Song", format="mp3")

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.duplicates.check_file") as mock_check:
                mock_check.return_value = None

                result = runner.invoke(
                    cli, ["check-duplicates", "--file", str(test_file)]
                )

                assert result.exit_code == 0
                mock_check.assert_called_once()

    def test_verify_metadata_command(self, test_config, temp_output_dir):
        """Test verify-metadata command."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.metadata.verify_library") as mock_verify:
                mock_verify.return_value = []  # No issues

                result = runner.invoke(cli, ["verify-metadata"])

                assert result.exit_code == 0
                mock_verify.assert_called_once()

    def test_apply_metadata_show(self, test_config, temp_output_dir):
        """Test apply-metadata --show command."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.cli.Path.exists") as mock_exists:
                mock_exists.return_value = False

                result = runner.invoke(cli, ["apply-metadata", "--show"])

                assert result.exit_code == 0
                assert "No review file found" in result.output

    def test_apply_metadata_command(self, test_config, temp_output_dir):
        """Test apply-metadata command."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.metadata.apply_metadata_csv") as mock_apply:
                result = runner.invoke(cli, ["apply-metadata"])

                assert result.exit_code == 0
                mock_apply.assert_called_once()

    def test_check_setup_command(self, test_config):
        """Test check-setup command."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            # Mock all the checks
            with patch("shutil.which") as mock_which:
                mock_which.return_value = "/usr/bin/ffmpeg"

                with patch("importlib.import_module") as mock_import:
                    result = runner.invoke(cli, ["check-setup"])

                    assert result.exit_code == 0
                    assert "Checking track-manager dependencies" in result.output

    def test_update_command(self):
        """Test update command delegates to self_update helpers."""
        runner = CliRunner()

        with patch("track_manager.self_update.update_checkout") as mock_update:
            mock_update.return_value = UpdateResult(
                dependencies_changed=False,
                reinstall_ran=False,
                reinstall_deferred=False,
            )
            result = runner.invoke(cli, ["update"])

            assert result.exit_code == 0
            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs == {
                "reinstall": True,
                "force_reinstall": False,
            }
            assert "Update complete" in result.output
            assert "skipped pip install" in result.output

    def test_update_command_no_install(self):
        """Test update --no-install skips pip reinstall."""
        runner = CliRunner()

        with patch("track_manager.self_update.update_checkout") as mock_update:
            mock_update.return_value = UpdateResult(
                dependencies_changed=True,
                reinstall_ran=False,
                reinstall_deferred=False,
            )
            result = runner.invoke(cli, ["update", "--no-install"])

            assert result.exit_code == 0
            assert mock_update.call_args.kwargs == {
                "reinstall": False,
                "force_reinstall": False,
            }

    def test_version_option(self):
        """Test --version option."""
        runner = CliRunner()

        # The CLI supports --version option
        result = runner.invoke(cli, ["--version"])

        # This should succeed and show version
        assert result.exit_code == 0
        assert "version" in result.output.lower()

    def test_help_option(self):
        """Test --help option."""
        runner = CliRunner()

        # The CLI uses a custom group that defaults to download
        # --help at top level shows download command help
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "download" in result.output
        assert "URL" in result.output

    def test_download_help(self):
        """Test download --help."""
        runner = CliRunner()

        result = runner.invoke(cli, ["download", "--help"])

        assert result.exit_code == 0
        assert "Download track" in result.output

    def test_error_handling_in_cli(self, test_config):
        """Test that CLI handles errors gracefully."""
        runner = CliRunner()

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config

            with patch("track_manager.cli.Downloader.download") as mock_download:
                mock_download.side_effect = Exception("Test error")

                result = runner.invoke(
                    cli, ["download", "https://open.spotify.com/track/test"]
                )

                # Should not crash, just report error
                assert result.exit_code != 0
                assert "error" in result.output.lower()

    def test_retry_failed_list(self, test_config, tmp_path):
        """Test retry-failed --list shows unique URLs."""
        runner = CliRunner()
        failed_log = tmp_path / "failed.txt"
        failed_log.write_text(
            "2026-01-01 10:00 | https://example.com/a | timeout\n"
            "2026-01-01 11:00 | https://example.com/b | not found\n"
        )
        test_config.config["failed_log"] = str(failed_log)

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config
            result = runner.invoke(cli, ["retry-failed", "--list"])

        assert result.exit_code == 0
        assert "https://example.com/a" in result.output
        assert "https://example.com/b" in result.output
        assert "2 unique URL" in result.output

    def test_retry_failed_dry_run(self, test_config, tmp_path):
        """Test retry-failed --dry-run does not download."""
        runner = CliRunner()
        failed_log = tmp_path / "failed.txt"
        failed_log.write_text(
            "2026-01-01 10:00 | https://example.com/a | timeout\n",
        )
        test_config.config["failed_log"] = str(failed_log)

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config
            with patch("track_manager.cli.Downloader.download") as mock_download:
                result = runner.invoke(cli, ["retry-failed", "--dry-run"])

        assert result.exit_code == 0
        assert "Would retry" in result.output
        mock_download.assert_not_called()

    def test_retry_failed_retries_and_clears_success(
        self, test_config, tmp_path, temp_output_dir
    ):
        """Test retry-failed removes succeeded URLs from the log."""
        runner = CliRunner()
        failed_log = tmp_path / "failed.txt"
        url = "https://open.spotify.com/track/test"
        failed_log.write_text(f"2026-01-01 10:00 | {url} | timeout\n")
        test_config.config["failed_log"] = str(failed_log)

        with patch("track_manager.cli.Config") as mock_config_class:
            mock_config_class.return_value = test_config
            with patch("track_manager.cli.Downloader.download") as mock_download:
                mock_download.return_value = True
                result = runner.invoke(cli, ["retry-failed", "--yes"])

        assert result.exit_code == 0
        assert "Succeeded: 1" in result.output
        assert failed_log.read_text() == ""
        mock_download.assert_called_once()

    def test_completions_print_flag(self):
        """Test completions --print writes script to stdout."""
        runner = CliRunner()
        result = runner.invoke(cli, ["completions", "zsh", "--print"])

        assert result.exit_code == 0
        assert "# tm shell completions" in result.output
        assert "#compdef" in result.output
        assert result.output.startswith("\n# tm shell completions")

    def test_completions_install(self, tmp_path):
        """Test completions install writes repo file and shell rc block."""
        runner = CliRunner()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname='track-manager'\n")
        rc = tmp_path / ".zshrc"
        rc.write_text("")

        with (
            patch("track_manager.shell_completions.project_root", return_value=repo),
            patch("track_manager.shell_completions.shell_rc_path", return_value=rc),
        ):
            result = runner.invoke(cli, ["completions", "zsh"])

        assert result.exit_code == 0
        installed = list((repo / "completions").glob("_*"))
        assert len(installed) == 1
        assert "completions >>>" in rc.read_text()
        assert "Wrote" in result.output
