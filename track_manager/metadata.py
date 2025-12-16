"""Metadata handling for audio files."""

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from mutagen import File as MutagenFile

CSV_HEADERS = [
    "file_path",
    "current_artist",
    "current_title",
    "suggested_artist",
    "suggested_title",
    "source_url",
    "notes",
]


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


def flag_for_review(file_path: Path, reason: str, url: str, csv_path: Path):
    """Flag file for metadata review.

    Args:
        file_path: Path to audio file
        reason: Reason for flagging
        url: Source URL
        csv_path: Path to review CSV
    """
    artist, title = extract_metadata(file_path)

    # Create CSV if it doesn't exist
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()

    # Append entry
    with open(csv_path, "a", newline="") as f:
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


def show_pending_reviews(csv_path: Path):
    """Show pending metadata reviews.

    Args:
        csv_path: Path to review CSV
    """
    if not csv_path.exists():
        print(f"No review file found at: {csv_path}")
        return

    with open(csv_path, "r", newline="") as f:
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
    unsafe_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
    for char in unsafe_chars:
        text = text.replace(char, "-")
    text = text.strip(". ")
    return text


def apply_metadata_csv(csv_path: Path, dry_run: bool = False) -> dict:
    """Apply metadata corrections from CSV.

    Args:
        csv_path: Path to review CSV
        dry_run: If True, don't modify files or CSV (just show what would be done)

    Returns:
        Dict with 'processed', 'remaining', and 'errors' counts
    """
    result = {"processed": 0, "remaining": 0, "errors": 0}

    if not csv_path.exists():
        print(f"No review file found at: {csv_path}")
        return result

    # Read all rows
    with open(csv_path, "r", newline="") as f:
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
            with open(csv_path, "w", newline="") as f:
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
            if not audio:
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
