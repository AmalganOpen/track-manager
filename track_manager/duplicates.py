"""Duplicate detection for audio files."""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from mutagen import File as MutagenFile


def normalize_text(text: str) -> str:
    """Normalize text for comparison.

    Args:
        text: Text to normalize

    Returns:
        Normalized text
    """
    if not text:
        return ""

    # Convert to lowercase
    text = text.lower()

    # Remove junk patterns (organized by category)
    # Note: Preserves meaningful distinctions like Live, Acoustic, Remix, Edit versions

    # Official/Video/Audio patterns
    patterns = [
        r"\[official.*?\]",
        r"\(official.*?\)",
        r"\[.*?video.*?\]",
        r"\(.*?video.*?\)",
        r"\[.*?audio.*?\]",
        r"\(.*?audio.*?\)",
    ]

    # Lyrics/Visualizer patterns
    patterns.extend(
        [
            r"[\[\(].*?lyric.*?s?.*?[\]\)]",  # [Lyrics], [Lyric Video]
            r"[\[\(]visuali[sz]er.*?[\]\)]",  # [Visualizer]
        ]
    )

    # Quality/Resolution patterns
    patterns.extend(
        [
            r"[\[\(]hd[\]\)]",
            r"[\[\(]4k[\]\)]",
            r"[\[\(]8k[\]\)]",
            r"[\[\(]uhd[\]\)]",
            r"[\[\(]hq[\]\)]",
            r"[\[\(]lq[\]\)]",
            r"[\[\(].*?quality.*?[\]\)]",  # [High Quality], etc.
        ]
    )

    # Platform/Source patterns
    patterns.extend(
        [
            r"\s*-\s*topic\s*$",  # "Artist - Topic" (YouTube)
            r"[\[\(]premium[\]\)]",
        ]
    )

    # Misc promotional patterns
    patterns.extend(
        [
            r"[\[\(]free\s*download[\]\)]",
            r"[\[\(]download.*?[\]\)]",
            r"[\[\(]out\s*now[\]\)]",
            r"[\[\(]new[\]\)]",
        ]
    )

    # Apply all patterns
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # Normalize featuring variations (handle both with and without parens properly)
    # Match full (feat. Artist) patterns to avoid orphaned parens
    text = re.sub(
        r"\s*\(\s*(ft\.?|feat\.?|featuring)\s+([^)]+)\)",
        r" feat. \2",
        text,
        flags=re.IGNORECASE,
    )
    # Handle feat./ft./featuring without parens
    text = re.sub(
        r"\s+(ft\.?|feat\.?|featuring)\s+", r" feat. ", text, flags=re.IGNORECASE
    )

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def extract_metadata(file_path: Path) -> Tuple[Optional[str], Optional[str]]:
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


def normalize_metadata(artist: Optional[str], title: Optional[str]) -> Tuple[str, str]:
    """Normalize artist and title for comparison.

    Args:
        artist: Artist name
        title: Track title

    Returns:
        Tuple of (normalized_artist, normalized_title)
    """
    return normalize_text(artist or ""), normalize_text(title or "")


def find_duplicates(artist: str, title: str, library_dir: Path) -> List[Path]:
    """Find duplicate tracks in library.

    Args:
        artist: Artist name
        title: Track title
        library_dir: Library directory

    Returns:
        List of duplicate file paths
    """
    if not artist or not title:
        return []

    norm_artist, norm_title = normalize_metadata(artist, title)
    if not norm_artist or not norm_title:
        return []

    duplicates = []

    # Scan for audio files
    for pattern in ["*.m4a", "*.M4A", "*.mp3", "*.MP3"]:
        for file_path in library_dir.glob(pattern):
            existing_artist, existing_title = extract_metadata(file_path)
            norm_existing = normalize_metadata(existing_artist, existing_title)

            if norm_existing == (norm_artist, norm_title):
                duplicates.append(file_path)

    return duplicates


def check_file_duplicate(file_path: Path, library_dir: Path, handling: str) -> bool:
    """Check if file is a duplicate.

    Args:
        file_path: Path to file to check
        library_dir: Library directory
        handling: Handling mode (interactive, skip, keep)

    Returns:
        True if should skip file, False if should keep
    """
    artist, title = extract_metadata(file_path)

    if not artist or not title:
        return False

    duplicates = find_duplicates(artist, title, library_dir)

    # Remove the file itself from duplicates list
    duplicates = [d for d in duplicates if d.resolve() != file_path.resolve()]

    if not duplicates:
        return False

    # Handle based on mode
    if handling == "skip":
        print(f"⚠️ Duplicate found: {file_path.name}")
        return True

    elif handling == "keep":
        return False

    else:  # interactive
        print(f"\n⚠️ Duplicate track detected!")
        print(f"New file: {file_path.name}")
        print(f"  Artist: {artist}")
        print(f"  Title: {title}")
        print(f"\nExisting files:")
        for i, dup in enumerate(duplicates, 1):
            dup_artist, dup_title = extract_metadata(dup)
            print(f"  {i}. {dup.name}")
            print(f"     Artist: {dup_artist}")
            print(f"     Title: {dup_title}")

        print(f"\nWhat would you like to do?")
        print(f"  [s] Skip new file (keep existing)")
        print(f"  [k] Keep both")
        print(f"  [r] Replace existing with new file", flush=True)

        while True:
            choice = input("Choice [s/k/r]: ").lower().strip()

            if choice == "s":
                return True  # Skip new file
            elif choice == "k":
                return False  # Keep both
            elif choice == "r":
                # Remove existing files
                for dup in duplicates:
                    print(f"Removing: {dup.name}")
                    dup.unlink()
                return False  # Keep new file
            else:
                print("Invalid choice. Please enter s, k, or r.")


def scan_library(library_dir: Path) -> dict:
    """Scan library for duplicates.

    Args:
        library_dir: Library directory

    Returns:
        Dict of normalized metadata -> list of duplicate file paths
    """
    print(f"Scanning {library_dir}...\n")

    tracks = {}  # normalized -> [paths]

    # Scan for audio files
    for pattern in ["*.m4a", "*.M4A", "*.mp3", "*.MP3"]:
        for file_path in library_dir.glob(pattern):
            artist, title = extract_metadata(file_path)
            if artist and title:
                norm = normalize_metadata(artist, title)
                key = f"{norm[0]}|||{norm[1]}"
                if key not in tracks:
                    tracks[key] = []
                tracks[key].append(file_path)

    # Find duplicates
    duplicates = {k: v for k, v in tracks.items() if len(v) > 1}

    if not duplicates:
        print("✅ No duplicates found")
        return {}

    print(f"⚠️ Found {len(duplicates)} duplicate groups:\n")

    for key, files in duplicates.items():
        artist, title = key.split("|||")
        print(f"Artist: {artist}")
        print(f"Title: {title}")
        for f in files:
            print(f"  - {f.name}")
        print()

    return duplicates


def check_file(file_path: Path, library_dir: Path):
    """Check specific file for duplicates.

    Args:
        file_path: File to check
        library_dir: Library directory
    """
    print(f"Checking {file_path.name} for duplicates...\n")

    artist, title = extract_metadata(file_path)

    if not artist or not title:
        print("⚠️ Could not extract metadata")
        return

    duplicates = find_duplicates(artist, title, library_dir)
    duplicates = [d for d in duplicates if d.resolve() != file_path.resolve()]

    if duplicates:
        print(f"⚠️ Found {len(duplicates)} duplicate(s):")
        for dup in duplicates:
            print(f"  - {dup.name}")
    else:
        print("✅ No duplicates found")
