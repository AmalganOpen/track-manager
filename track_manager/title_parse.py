"""Turn messy uploader-written track titles into artist/title guesses.

SoundCloud titles are free text ("Artist - Title [FREE DL]", fancy Unicode,
remixer as the "artist") rather than catalogue metadata. These helpers
normalise that text into plain ``{artists, title, duration_seconds}`` dicts
that Soulseek and catalogue searches can match against.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Optional

# Combining overlays used for decorative strikethrough/underline (e.g. "7̶").
_OVERLAY_MARKS = {chr(c) for c in range(0x0332, 0x0339)}
# Symbols (emoji, ★, ♫…), private-use and unassigned code points.
_DROP_CATEGORIES = {"So", "Co", "Cn", "Cs"}

_PROMO_WORDS = (
    r"free\s*(?:dl|download)|download|premiere|out\s*now|exclusive|buy|"
    r"link\s+in|supported\s+by|support|preview|snippet|clip"
)
_PROMO_BRACKET = re.compile(
    rf"[\[\(\{{][^\]\)\}}]*\b(?:{_PROMO_WORDS})\b[^\]\)\}}]*[\]\)\}}]",
    re.IGNORECASE,
)
_PROMO_PREFIX = re.compile(
    r"^\s*(?:premiere|exclusive|free\s*(?:dl|download))\s*[:|\-–—]\s*",
    re.IGNORECASE,
)
_PROMO_BARE = re.compile(r"\b(?:free\s*(?:dl|download)|out\s*now)\b", re.IGNORECASE)

_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|\+|(?-i:\sx\s)|\bvs\.?(?!\w)|\bfeat\.?(?!\w)|\bft\.?(?!\w)|\bfeaturing\b)\s*",
    re.IGNORECASE,
)
_TITLE_SEPARATOR = re.compile(r"\s+[-–—]\s+")

_VERSION_MARKERS = {
    "remix": r"remix|rmx",
    "edit": r"edit",
    "vip": r"vip",
    "bootleg": r"bootleg",
    "flip": r"flip",
    "rework": r"re-?work",
    "refix": r"refix",
    "dub": r"dub",
    "mashup": r"mash-?up",
    "cover": r"cover",
    "live": r"live",
    "acoustic": r"acoustic",
    "instrumental": r"instrumental",
    "extended": r"extended",
}
_VERSION_RE = {
    name: re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)
    for name, pattern in _VERSION_MARKERS.items()
}


def normalize_text(text: str) -> str:
    """Fold decorative Unicode to plain text and collapse whitespace.

    NFKC maps math-bold/fullwidth letters and punctuation to ASCII
    (``𝘿𝙅`` → ``DJ``, ``，`` → ``,``); overlay marks and emoji are dropped.
    Regular accents (``Björk``) are kept.
    """
    folded = unicodedata.normalize("NFKC", text)
    kept = "".join(
        ch
        for ch in folded
        if ch not in _OVERLAY_MARKS
        and unicodedata.category(ch) not in _DROP_CATEGORIES
        and ch != "\ufe0f"
    )
    return re.sub(r"\s+", " ", kept).strip()


def strip_promo(title: str) -> str:
    """Remove promo noise like ``[FREE DL]``, ``Premiere:`` or ``OUT NOW``."""
    cleaned = _PROMO_PREFIX.sub("", title)
    cleaned = _PROMO_BRACKET.sub("", cleaned)
    cleaned = _PROMO_BARE.sub("", cleaned)
    cleaned = re.sub(r"[\[\(\{]\s*[\]\)\}]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -–—|*")


def split_artists(text: str) -> List[str]:
    """Split ``"A, B & C feat. D"`` into ``["A", "B", "C", "D"]``."""
    parts = [p.strip() for p in _ARTIST_SPLIT.split(text)]
    return [p for p in parts if p]


def version_markers(text: str) -> frozenset[str]:
    """Version keywords (remix, edit, VIP, live…) present in ``text``."""
    return frozenset(name for name, rx in _VERSION_RE.items() if rx.search(text))


def simplify(text: str) -> str:
    """Lowercase alphanumerics only, for fuzzy comparison."""
    return re.sub(r"[^0-9a-z]+", "", normalize_text(text).casefold())


def base_title(title: str) -> str:
    """Title without bracketed parts or a trailing `` - … Remix`` suffix."""
    without_brackets = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", title)
    head = _TITLE_SEPARATOR.split(without_brackets, maxsplit=1)[0]
    return simplify(head)


def similar(a: str, b: str, threshold: float = 0.85) -> bool:
    """Fuzzy equality on simplified strings."""
    sa, sb = simplify(a), simplify(b)
    if not sa or not sb:
        return False
    return sa == sb or SequenceMatcher(None, sa, sb).ratio() >= threshold


def _matches_any(text: str, names: Iterable[str]) -> bool:
    return any(similar(text, name) for name in names)


def _guess(artists: List[str], title: str, duration: Optional[float]) -> dict:
    return {"artists": artists, "title": title, "duration_seconds": duration}


_BRACKETED = re.compile(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]")


def _split_guess(artist_part: str, title_part: str, duration: Optional[float]) -> dict:
    """Guess from one side of ``"X - Y"``; brackets on the artist side
    (``"Addison Rae (DJ-7 remix)"``) belong to the title."""
    brackets = "".join(m.group(0) for m in _BRACKETED.finditer(artist_part))
    artist = _BRACKETED.sub("", artist_part).strip()
    title = f"{title_part}{brackets}".strip()
    return _guess(split_artists(artist), title, duration)


def soundcloud_guesses(info: dict) -> List[dict]:
    """Ranked ``{artists, title, duration_seconds}`` guesses from yt-dlp info.

    The first entry is the best guess; later entries are alternative
    readings (e.g. ``"Title - Artist"`` order) for catalogue matching.
    Returns ``[]`` when there is no usable title.
    """
    raw_title = normalize_text(info.get("track") or info.get("title") or "")
    title = strip_promo(raw_title)
    if not title:
        return []

    listed = info.get("artists")
    if not isinstance(listed, list) or not listed:
        listed = [info.get("artist") or ""]
    known = [name for a in listed for name in split_artists(normalize_text(str(a)))]
    uploader = normalize_text(info.get("uploader") or info.get("channel") or "")
    if not known and uploader:
        known = [uploader]
    hints = known + ([uploader] if uploader else [])

    try:
        duration: Optional[float] = float(info["duration"])
    except (KeyError, TypeError, ValueError):
        duration = None

    guesses: List[dict] = []
    parts = _TITLE_SEPARATOR.split(title, maxsplit=1)
    if len(parts) == 2 and all(parts):
        left, right = parts
        left_first = _split_guess(left, right, duration)
        right_first = _split_guess(right, left, duration)
        if _matches_any(right, hints) and not _matches_any(left, hints):
            guesses += [right_first, left_first]
        else:
            guesses += [left_first, right_first]
    if known:
        guesses.append(_guess(known, title, duration))
    return [g for g in guesses if g["artists"] and g["title"]]
