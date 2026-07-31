"""Metadata handling for audio files."""

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from mutagen import File as MutagenFile

# utf-8-sig: write a BOM so Excel on Windows recognises UTF-8 (Chinese /
# other non-ANSI paths otherwise become mojibake); on read the BOM is
# stripped so DictReader still sees the normal header row. Without an
# explicit encoding, Windows defaults to the ANSI code page (e.g. cp1252)
# and crashes or corrupts any non-ASCII file_path.
_CSV_ENCODING = "utf-8-sig"

CSV_HEADERS = [
    "file_path",
    "current_artist",
    "current_title",
    "suggested_artist",
    "suggested_title",
    "source_url",
    "notes",
]


def get_metadata_csv_path() -> Path:
    """Get the metadata review CSV path from config."""
    from .config import Config

    config = Config()
    return config.metadata_csv


def has_junk_patterns(text: str) -> bool:
    """Check if text contains common junk patterns.

    Args:
        text: Text to check

    Returns:
        True if junk patterns found
    """
    if not text:
        return False

    junk_patterns = [
        r"\[official.*?\]",
        r"\(official.*?\)",
        r"\[.*?video.*?\]",
        r"\(.*?video.*?\)",
        r"\[.*?audio.*?\]",
        r"\(.*?audio.*?\)",
        r"\[hd\]",
        r"\(hd\)",
        r"official video",
        r"official audio",
        r"music video",
    ]

    for pattern in junk_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


def extract_metadata(file_path: Path) -> tuple[Optional[str], Optional[str]]:
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
    except Exception:
        return None, None


def flag_for_review(file_path: Path, reason: str, url: str):
    """Flag file for metadata review.

    Args:
        file_path: Path to audio file
        reason: Reason for flagging
        url: Source URL
    """
    csv_path = get_metadata_csv_path()
    artist, title = extract_metadata(file_path)

    # Create CSV if it doesn't exist
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding=_CSV_ENCODING) as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()

    # Append entry
    with open(csv_path, "a", newline="", encoding=_CSV_ENCODING) as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writerow(
            {
                "file_path": str(file_path),
                "current_artist": artist or "",
                "current_title": title or "",
                "suggested_artist": "",
                "suggested_title": "",
                "source_url": url,
                "notes": reason,
            }
        )

    print(f"⚠️ Flagged for review: {file_path.name}")
    print(f"   Reason: {reason}")
    print(f"   Review/edit CSV: file://{csv_path.resolve()}")


def show_pending_reviews():
    """Show pending metadata reviews."""
    csv_path = get_metadata_csv_path()
    if not csv_path.exists():
        print(f"No review file found at: {csv_path}")
        return

    with open(csv_path, "r", newline="", encoding=_CSV_ENCODING) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("No pending reviews")
        return

    print(f"📝 Pending reviews: {len(rows)}\n")

    for i, row in enumerate(rows, 1):
        file_path = Path(row["file_path"])
        print(f"{i}. {file_path.name}")
        print(f"   Current: {row['current_artist']} - {row['current_title']}")
        print(
            f"   Suggested: {row['suggested_artist'] or '(empty)'} - {row['suggested_title'] or '(empty)'}"
        )
        if row["notes"]:
            print(f"   Notes: {row['notes']}")
        if row["source_url"]:
            print(f"   URL: {row['source_url']}")
        print()

    print(f"Edit the CSV file to fill in suggested metadata:")
    print(f"  {csv_path}")
    print(f"Then run: track-manager apply-metadata")


def sanitize_filename(text: str) -> str:
    """Sanitize text for use in filename.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized text
    """
    # Windows + cross-platform forbidden filename characters. Keep non-ASCII
    # (Chinese, Japanese, etc.) intact — NTFS/APFS handle them fine; the
    # failure mode on Windows is usually locale encoding of *text files*
    # that store paths, not the filesystem itself.
    unsafe_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
    for char in unsafe_chars:
        text = text.replace(char, "-")
    # Strip C0 control characters (illegal on Windows; can break terminals).
    text = "".join(c for c in text if ord(c) >= 32)
    text = text.strip(". ")
    return text


def apply_metadata_csv(dry_run: bool = False) -> dict:
    """Apply metadata corrections from CSV.

    Args:
        dry_run: If True, don't modify files or CSV (just show what would be done)

    Returns:
        Dict with 'processed', 'remaining', and 'errors' counts
    """
    csv_path = get_metadata_csv_path()
    result = {"processed": 0, "remaining": 0, "errors": 0}

    if not csv_path.exists():
        print(f"No review file found at: {csv_path}")
        return result

    # Read all rows
    with open(csv_path, "r", newline="", encoding=_CSV_ENCODING) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("No reviews to process")
        return result

    remaining_rows = []

    for row in rows:
        file_path = Path(row["file_path"])
        suggested_artist = row["suggested_artist"].strip()
        suggested_title = row["suggested_title"].strip()

        # Check if row is ready to process
        if not suggested_artist or not suggested_title:
            remaining_rows.append(row)
            result["remaining"] += 1
            continue

        # Check if file exists
        if not file_path.exists():
            print(f"⚠️ File not found: {file_path}")
            result["errors"] += 1
            continue

        # Apply update
        if dry_run:
            print(f"\n[DRY RUN] Would process: {file_path.name}")
        else:
            print(f"\nProcessing: {file_path.name}")
        print(f"  Artist: {row['current_artist']} → {suggested_artist}")
        print(f"  Title: {row['current_title']} → {suggested_title}")

        if dry_run:
            result["processed"] += 1
        elif update_metadata(file_path, suggested_artist, suggested_title):
            result["processed"] += 1
        else:
            remaining_rows.append(row)
            result["errors"] += 1

    # Write remaining rows back to CSV (skip in dry run)
    if not dry_run:
        if remaining_rows:
            with open(csv_path, "w", newline="", encoding=_CSV_ENCODING) as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                writer.writeheader()
                writer.writerows(remaining_rows)

            print()
            print(f"✅ Processed {result['processed']} tracks")
            print(f"⚠️ {result['remaining']} rows remain for review")
            print(f"   Review at: {csv_path}")
        else:
            # Remove empty CSV
            csv_path.unlink()
            print()
            print(f"✅ Processed {result['processed']} tracks")
    else:
        print(f"\n[DRY RUN] Would process {result['processed']} tracks")
        if remaining_rows:
            print(f"[DRY RUN] {len(remaining_rows)} rows would remain")

    return result


def update_metadata(file_path: Path, artist: str, title: str) -> bool:
    """Update file metadata and rename file.

    Args:
        file_path: Path to audio file
        artist: New artist name
        title: New title

    Returns:
        True if successful
    """
    try:
        # Update metadata based on file format
        if file_path.suffix.lower() == ".mp3":
            # Use ID3 tags for MP3 files
            from mutagen.id3 import ID3, TIT2, TPE1
            from mutagen.mp3 import MP3

            audio = MP3(str(file_path), ID3=ID3)
            if not audio.tags:
                audio.add_tags()

            # Update ID3 tags
            audio.tags.add(TPE1(encoding=3, text=artist))
            audio.tags.add(TIT2(encoding=3, text=title))
            audio.save()
        else:
            # Use easy interface for other formats
            audio = MutagenFile(str(file_path), easy=True)
            if audio is None:
                print(f"⚠️ Could not read file: {file_path}")
                return False

            audio["artist"] = [artist]
            audio["title"] = [title]
            audio.save()

        # Rename file
        new_name = f"{sanitize_filename(artist)} - {sanitize_filename(title)}{file_path.suffix}"
        new_path = file_path.parent / new_name

        if new_path.exists() and new_path != file_path:
            print(f"⚠️ Target file already exists: {new_name}")
            print(f"   Keeping original name: {file_path.name}")
            return True

        if new_path != file_path:
            file_path.rename(new_path)
            print(f"✅ Renamed: {file_path.name} → {new_name}")
        else:
            print(f"✅ Updated metadata: {file_path.name}")

        return True

    except Exception as e:
        print(f"⚠️ Error updating {file_path}: {e}")
        return False


def show_full_metadata(file_path: Path):
    """Display all metadata for an audio file.

    Args:
        file_path: Path to audio file
    """
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4

    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return

    print(f"📋 Metadata for: {file_path.name}")
    print(f"   Path: {file_path.resolve()}")
    print()

    try:
        # Get file type and size
        file_size = file_path.stat().st_size
        print(f"📁 File Info:")
        print(f"   Format: {file_path.suffix.upper()[1:]}")
        print(f"   Size: {file_size / 1024 / 1024:.2f} MB")
        print()

        # Read all metadata
        audio = MutagenFile(str(file_path))

        if audio is None:
            print("⚠️ Could not read metadata")
            return

        # Display audio properties
        if audio.info:
            print(f"🎵 Audio Properties:")
            print(f"   Duration: {audio.info.length:.2f}s")
            if hasattr(audio.info, "bitrate"):
                print(f"   Bitrate: {audio.info.bitrate // 1000} kbps")
            if hasattr(audio.info, "sample_rate"):
                print(f"   Sample Rate: {audio.info.sample_rate} Hz")
            if hasattr(audio.info, "channels"):
                print(f"   Channels: {audio.info.channels}")
            print()

        # Display metadata tags
        print(f"🏷️  Metadata Tags:")

        if isinstance(audio, MP4):
            # M4A/MP4 files
            for key, value in sorted(audio.tags.items()):
                # Format the value nicely
                if isinstance(value, list):
                    if len(value) == 1:
                        value = value[0]
                    else:
                        value = ", ".join(str(v) for v in value)

                # Convert MP4 tag keys to readable names
                tag_names = {
                    "\xa9nam": "Title",
                    "\xa9ART": "Artist",
                    "\xa9alb": "Album",
                    "\xa9day": "Year",
                    "\xa9gen": "Genre",
                    "trkn": "Track Number",
                    "disk": "Disk Number",
                    "\xa9cmt": "Comment",
                    "covr": "Cover Art",
                    "----:com.apple.iTunes:ORIGINAL_BITRATE": "Original Bitrate",
                    "----:com.apple.iTunes:SOURCE": "Source",
                    "----:com.apple.iTunes:ISRC": "ISRC",
                }

                readable_key = tag_names.get(key, key)

                # Handle binary data
                if isinstance(value, bytes):
                    if "covr" in key.lower() or "cover" in readable_key.lower():
                        print(f"   {readable_key}: [Image data, {len(value)} bytes]")
                    else:
                        try:
                            decoded = value.decode("utf-8")
                            print(f"   {readable_key}: {decoded}")
                        except:
                            print(
                                f"   {readable_key}: [Binary data, {len(value)} bytes]"
                            )
                else:
                    print(f"   {readable_key}: {value}")

        else:
            # MP3 and other formats using ID3 or easy tags
            if hasattr(audio, "tags") and audio.tags:
                for key in sorted(audio.tags.keys()):
                    value = audio.tags[key]

                    # Handle different tag types
                    if hasattr(value, "text"):
                        # ID3 tags have .text attribute
                        text = value.text
                        if isinstance(text, list):
                            text = ", ".join(str(t) for t in text)
                        print(f"   {key}: {text}")
                    elif isinstance(value, bytes):
                        print(f"   {key}: [Binary data, {len(value)} bytes]")
                    else:
                        print(f"   {key}: {value}")
            else:
                print("   No tags found")

    except Exception as e:
        print(f"❌ Error reading metadata: {e}")


def verify_library(output_dir: Path) -> dict:
    """Verify metadata quality in library.

    Args:
        output_dir: Library directory

    Returns:
        Dict with 'missing' and 'junk' lists of (file_path, artist, title) tuples
    """
    print(f"Verifying metadata in {output_dir}...\n")

    missing_metadata = []
    junk_metadata = []

    # Scan audio files
    for pattern in ["*.m4a", "*.M4A", "*.mp3", "*.MP3"]:
        for file_path in output_dir.glob(pattern):
            artist, title = extract_metadata(file_path)

            if not artist or not title:
                missing_metadata.append((file_path, artist, title))
            elif has_junk_patterns(artist or "") or has_junk_patterns(title or ""):
                junk_metadata.append((file_path, artist, title))

    if missing_metadata:
        print(f"⚠️ {len(missing_metadata)} files with missing metadata:")
        for f, a, t in missing_metadata[:10]:
            print(f"  {f.name}")
            print(f"    Artist: {a or '(missing)'}")
            print(f"    Title: {t or '(missing)'}")
        if len(missing_metadata) > 10:
            print(f"  ... and {len(missing_metadata) - 10} more")
        print()

    if junk_metadata:
        print(f"⚠️ {len(junk_metadata)} files with junk in metadata:")
        for f, a, t in junk_metadata[:10]:
            print(f"  {f.name}")
            print(f"    Artist: {a}")
            print(f"    Title: {t}")
        if len(junk_metadata) > 10:
            print(f"  ... and {len(junk_metadata) - 10} more")
        print()

    return {"missing": missing_metadata, "junk": junk_metadata}

    if not missing_metadata and not junk_metadata:
        print("✅ All tracks have clean metadata")
