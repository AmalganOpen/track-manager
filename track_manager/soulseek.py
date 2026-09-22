"""Soulseek lossless fallback via the sockseek (formerly sldl) CLI.

Thin wrapper — no Soulseek protocol reimplementation. Requires a free
Soulseek account and ``sockseek`` (or legacy ``sldl``) on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from .config import Config

# Preferred download extensions, in preference order.
_AUDIO_EXTENSIONS = (".flac", ".wav", ".aiff", ".aif", ".alac", ".m4a", ".mp3", ".ogg")


def find_sockseek_binary(config: Optional[Config] = None) -> Optional[Path]:
    """Locate the sockseek/sldl binary.

    Order: explicit ``config.soulseek_binary`` → ``sockseek`` on PATH →
    legacy ``sldl`` on PATH. Returns None when nothing is found.
    """
    cfg = config or Config()
    configured = cfg.soulseek_binary
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os_access_executable(path):
            return path
        # Still return the configured path so callers can surface a clear
        # "configured binary missing" message; download_track will fail.
        return path if path.exists() else None

    for name in ("sockseek", "sldl"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def os_access_executable(path: Path) -> bool:
    """True if ``path`` exists and looks runnable (file + execute bit / Windows)."""
    import os

    return path.is_file() and os.access(path, os.X_OK)


def build_song_query(
    artist: str,
    title: str,
    *,
    duration_s: Optional[float] = None,
) -> str:
    """Build a sockseek keyed search string: ``artist=…, title=…[, length=N]``."""
    parts = [f"artist={artist.strip()}", f"title={title.strip()}"]
    if duration_s is not None and duration_s > 0:
        parts.append(f"length={int(round(duration_s))}")
    return ", ".join(parts)


def _pick_downloaded_file(directory: Path) -> Optional[Path]:
    """Return the best-looking audio file under ``directory`` (non-recursive first)."""
    candidates: List[Path] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() in _AUDIO_EXTENSIONS:
            candidates.append(path)
    if not candidates:
        return None

    def sort_key(p: Path) -> tuple:
        ext = p.suffix.lower()
        try:
            pref = _AUDIO_EXTENSIONS.index(ext)
        except ValueError:
            pref = len(_AUDIO_EXTENSIONS)
        # Prefer larger files (full tracks over tiny stubs).
        try:
            size = -p.stat().st_size
        except OSError:
            size = 0
        return (pref, size)

    candidates.sort(key=sort_key)
    return candidates[0]


class SoulseekClient:
    """Download a single track via sockseek/sldl."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    def download_track(
        self,
        artist: str,
        title: str,
        *,
        duration_s: Optional[float] = None,
        output_dir: Optional[Path] = None,
    ) -> Optional[Path]:
        """Search Soulseek for ``artist``/``title`` and download FLAC.

        Returns the path to the downloaded file (inside ``output_dir`` or a
        temp dir owned by the caller), or None on failure.
        """
        binary = find_sockseek_binary(self.config)
        if binary is None:
            print("ℹ️ sockseek not found on PATH", file=sys.stderr)
            return None

        username = self.config.soulseek_username
        password = self.config.soulseek_password
        if not username or not password:
            print("ℹ️ Skipping Soulseek (not configured)", file=sys.stderr)
            return None

        own_temp = output_dir is None
        work_dir = (
            Path(output_dir)
            if output_dir
            else Path(tempfile.mkdtemp(prefix="tm_soulseek_"))
        )
        work_dir.mkdir(parents=True, exist_ok=True)

        query = build_song_query(artist, title, duration_s=duration_s)
        length_tol = self.config.soulseek_length_tol_seconds
        timeout = self.config.soulseek_timeout_seconds

        cmd: List[str] = [
            str(binary),
            query,
            "--song",
            "--format",
            "flac",
            "--length-tol",
            str(length_tol),
            "--user",
            username,
            "--pass",
            password,
            "--output-dir",
            str(work_dir),
            # Ignore any user sockseek.conf so credentials/flags we pass win.
            "--no-config",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(
                f"⚠️ Soulseek download timed out after {timeout}s",
                file=sys.stderr,
            )
            return None
        except FileNotFoundError:
            print(f"ℹ️ sockseek binary not runnable: {binary}", file=sys.stderr)
            return None
        except OSError as exc:
            print(f"⚠️ Soulseek launch failed: {exc}", file=sys.stderr)
            return None

        if result.returncode != 0:
            note = (result.stderr or result.stdout or "").strip()
            if note:
                # Keep it short — sockseek can be chatty.
                snippet = note.splitlines()[-1][:200]
                print(f"⚠️ Soulseek download failed: {snippet}", file=sys.stderr)
            else:
                print(
                    f"⚠️ Soulseek download failed (exit {result.returncode})",
                    file=sys.stderr,
                )
            return None

        found = _pick_downloaded_file(work_dir)
        if found is None:
            note = (result.stderr or result.stdout or "").strip()
            snippet = note.splitlines()[-1][:200] if note else "no audio file written"
            print(f"⚠️ Soulseek produced no audio: {snippet}", file=sys.stderr)
            if own_temp:
                # Leave empty temp for caller? Better not — nothing to return.
                pass
            return None

        return found


def extract_artist_title(
    spotify_metadata: Optional[dict],
) -> tuple[Optional[str], Optional[str]]:
    """Pull artist + title from Spotify-style metadata dict.

    Returns ``(artist, title)`` or ``(None, None)`` when either is missing.
    Does not invent guesses from filenames.
    """
    if not spotify_metadata:
        return None, None
    title = spotify_metadata.get("title")
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None

    artists = spotify_metadata.get("artists")
    artist: Optional[str] = None
    if isinstance(artists, Sequence) and not isinstance(artists, (str, bytes)):
        names = [str(a).strip() for a in artists if str(a).strip()]
        if names:
            artist = ", ".join(names)
    if artist is None:
        raw = spotify_metadata.get("artist") or spotify_metadata.get("artist_string")
        if isinstance(raw, str) and raw.strip():
            artist = raw.strip()

    if not artist or not title:
        return None, None
    return artist, title


def extract_duration_seconds(spotify_metadata: Optional[dict]) -> Optional[float]:
    """Best-effort duration (seconds) from Spotify-style metadata."""
    if not spotify_metadata:
        return None
    for key in ("duration_seconds", "duration", "length"):
        raw = spotify_metadata.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 1000:
            # Likely milliseconds (Spotify Web API style).
            value = value / 1000.0
        if value > 0:
            return value
    duration_ms = spotify_metadata.get("duration_ms")
    if duration_ms is not None:
        try:
            value = float(duration_ms) / 1000.0
        except (TypeError, ValueError):
            return None
        if value > 0:
            return value
    return None
