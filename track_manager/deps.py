"""Dependency helpers for track-manager.

Centralise FFmpeg/ffprobe checks and unified error messages so the rest of
the codebase can behave consistently and be easier to test.

Supports a debug simulation via the environment variable
`TM_SIMULATE_FFMPEG` with values:
  - "missing_ffmpeg"     -> behave as if ffmpeg is absent
  - "missing_ffprobe"    -> behave as if ffprobe is absent
  - "both_missing"       -> both absent
  - "ffmpeg_fail"        -> used by audio layer to simulate a failing encode

This file intentionally keeps a small, clear API:
  - ensure_ffmpeg_available() -> (ffmpeg_path, ffprobe_path) or raises
    MissingDependencyError with a concise user-facing message.
  - ffmpeg_paths() -> (ffmpeg_path|None, ffprobe_path|None)
  - MissingDependencyError: exception class for callers to catch/translate.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional, Tuple


class MissingDependencyError(RuntimeError):
    """Raised when required native binaries are missing."""


def _simulate_mode() -> Optional[str]:
    """Return the debug simulation mode from env, if any."""
    return os.environ.get("TM_SIMULATE_FFMPEG")


def ffmpeg_paths() -> Tuple[Optional[str], Optional[str]]:
    """Return (ffmpeg_path, ffprobe_path) or (None, None) when missing.

    Respects the TM_SIMULATE_FFMPEG env var to let callers exercise failure
    modes for debugging.
    """
    mode = _simulate_mode()

    # Special-case: "ffmpeg_fail" simulation exercises a runtime failure path
    # but should not be blocked by the preflight check. Return placeholder
    # non-empty paths so ensure_ffmpeg_available() doesn't raise.
    if mode == "ffmpeg_fail":
        return ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")

    if mode in ("missing_ffmpeg", "both_missing"):
        ffmpeg_path = None
    else:
        ffmpeg_path = shutil.which("ffmpeg")

    if mode in ("missing_ffprobe", "both_missing"):
        ffprobe_path = None
    else:
        ffprobe_path = shutil.which("ffprobe")

    return ffmpeg_path, ffprobe_path


def _format_missing_message(missing: List[str]) -> str:
    """Return a short, actionable message listing missing binaries."""
    if not missing:
        return ""
    if len(missing) == 1:
        return (
            f"Required dependency missing: {missing[0]}. "
            "Track Manager requires FFmpeg (ffmpeg & ffprobe) on your PATH."
        )
    return (
        f"Required dependencies missing: {', '.join(missing)}. "
        "Track Manager requires FFmpeg (ffmpeg & ffprobe) on your PATH."
    )


def ensure_ffmpeg_available() -> Tuple[str, str]:
    """Ensure ffmpeg/ffprobe are available.

    Returns the resolved (ffmpeg_path, ffprobe_path) on success. Raises
    MissingDependencyError with a concise message on failure.
    """
    ffmpeg_path, ffprobe_path = ffmpeg_paths()
    missing: List[str] = []
    if not ffmpeg_path:
        missing.append("ffmpeg")
    if not ffprobe_path:
        missing.append("ffprobe")

    if missing:
        msg = _format_missing_message(missing) + (
            " See README.md for install instructions (e.g. 'brew install ffmpeg' or\n"
            "'sudo apt-get install -y ffmpeg')."
        )
        raise MissingDependencyError(msg)

    return ffmpeg_path, ffprobe_path
