"""Direct URL downloader using requests."""

import sys
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("Error: requests not installed", file=sys.stderr)
    print("Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

from .base import BaseDownloader


class DirectDownloader(BaseDownloader):
    """Direct URL downloader."""

    def download(self, url: str, format: str = "auto"):
        """Download audio file from direct URL.

        Args:
            url: Direct audio file URL
            format: Output format (auto, m4a, mp3)
        """
        print("⬇️ Downloading direct audio file...")
        print(f"URL: {url}")
        print()

        try:
            # Extract filename from URL
            parsed = urlparse(url)
            original_name = Path(parsed.path).name
            original_ext = (
                Path(original_name).suffix[1:].lower() if "." in original_name else ""
            )

            if not original_ext:
                print("⚠️ Could not determine file extension from URL")
                original_ext = "m4a"  # Default

            # Pre-download dedup by source URL. Direct files carry no metadata
            # until fetched, so URL identity (stored as TRACK_URL on prior
            # downloads) is all we have up front — but it's enough to avoid
            # re-fetching bytes for a file we already have.
            from ..duplicates import find_duplicates_by_track_url

            existing = find_duplicates_by_track_url(url, self.output_dir)
            if existing and self.handle_found_duplicates(
                existing, new_file_name=original_name or url
            ):
                print("⏭️ Skipped (duplicate)")
                return

            print(f"Downloading...")

            # Download file
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            # Create temp file with cleanup on error
            with self.temp_file_cleanup() as register_temp:
                temp_file = (
                    self.output_dir / f".tmp_{Path(original_name).stem}.{original_ext}"
                )

                # Download with progress
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(temp_file, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size:
                                progress = (downloaded / total_size) * 100
                                print(
                                    f"\rProgress: {progress:.1f}%", end="", flush=True
                                )

                # Register temp file for cleanup on error
                register_temp(temp_file)

                print()
                print("✅ Downloaded")
                print()

                # Process the downloaded file
                if self._process_download(temp_file, url, format):
                    print("✅ Download complete")
                else:
                    print("⚠️ Download completed but processing failed", file=sys.stderr)
                    self.log_failure(url, "Processing failed")

        except requests.exceptions.RequestException as e:
            print(f"❌ Download failed: {e}", file=sys.stderr)
            self.log_failure(url, str(e))
            raise
        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            self.log_failure(url, str(e))
            raise

    def _process_download(self, file_path: Path, url: str, desired_format: str) -> bool:
        """Process downloaded file.

        Args:
            file_path: Path to downloaded file
            url: Source URL
            desired_format: Desired output format

        Returns:
            True if successful
        """
        try:
            # Extract metadata
            artist, title = self.extract_metadata(file_path)

            # Determine if format conversion needed
            current_ext = file_path.suffix[1:].lower()

            # Check if metadata is missing
            missing_metadata = not artist or not title

            # Use fallbacks if needed
            if missing_metadata:
                artist = "Unknown"
                title = file_path.stem

            # Create final filename
            if desired_format != "auto" and desired_format != current_ext:
                # User wants different format, but we won't convert
                # (would require ffmpeg, which we're avoiding as hard dependency)
                print(f"ℹ️ Keeping original format ({current_ext})")
                print(
                    f"   To convert, install ffmpeg and use: ffmpeg -i input.{current_ext} output.{desired_format}"
                )
                final_ext = current_ext
            else:
                final_ext = current_ext

            final_name = self.create_filename(
                artist, title, final_ext, fallback=file_path.stem
            )
            final_path = self.output_dir / final_name

            # Flag for review with final path (after rename)
            if missing_metadata:
                self.flag_metadata_review(
                    final_path,
                    "Missing or incomplete metadata from direct download",
                    url,
                )

            # Check for duplicates
            if self.check_duplicate(file_path):
                # User chose to skip
                file_path.unlink()
                print("⏭️ Skipped (duplicate)")
                return True

            # Move to final location
            if file_path != final_path:
                file_path.rename(final_path)

            # Add provenance metadata
            self._add_provenance_metadata(
                final_path,
                url,
                final_ext,
                None,  # Bitrate unknown for direct downloads
            )

            print(f"✅ Saved: {final_name}")
            return True

        except Exception as e:
            print(f"⚠️ Error processing download: {e}", file=sys.stderr)
            return False
