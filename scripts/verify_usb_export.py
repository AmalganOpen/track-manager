#!/usr/bin/env python3
"""Audit a rekordbox-exported USB against the files actually on it.

Reads ``PIONEER/rekordbox/export.pdb`` — the table of contents the player
trusts — and checks every playlist entry resolves to a real file whose
byte count matches what the TOC claims. A mismatch is what the deck
reports as E-8302 (CANNOT PLAY TRACK).

Usage:  python scripts/verify_usb_export.py /Volumes/NIKAN
"""

from __future__ import annotations

import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

PAGE_HEADER_LEN = 40
ROW_GROUP_STRIDE = 0x24

TABLE_TRACKS = 0
TABLE_PLAYLIST_TREE = 7
TABLE_PLAYLIST_ENTRIES = 8

# Offsets within a track row, from the 0x0024 magic.
TRACK_FILE_SIZE = 16
TRACK_ID = 72
TRACK_STRING_OFFSETS = 94
STR_TITLE = 17
STR_PATH = 20


class Track(NamedTuple):
    id: int
    path: str
    title: str
    toc_size: int


def device_string(buf: bytes, off: int) -> Optional[str]:
    """Decode a DeviceSQL string at absolute offset ``off``."""
    if off < 0 or off >= len(buf):
        return None
    kind = buf[off]
    if kind in (0x40, 0x90):  # long ASCII / long UTF-16
        length = struct.unpack_from("<H", buf, off + 1)[0]
        if length < 4:
            return None
        body = buf[off + 4 : off + length]
        if kind == 0x90:
            # Rekordbox writes these little-endian despite the format
            # notes describing UTF-16BE; decoding BE yields CJK mojibake.
            return body[: len(body) - len(body) % 2].decode("utf-16-le", "replace")
        try:
            return body.decode("ascii")
        except UnicodeDecodeError:
            return None
    if kind & 1:  # short ASCII, length packed into the header byte
        length = (kind >> 1) - 1
        if length < 0:
            return None
        try:
            return buf[off + 1 : off + 1 + length].decode("ascii")
        except UnicodeDecodeError:
            return None
    return None


class Pdb:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.page_len = struct.unpack_from("<I", data, 4)[0]
        num_tables = struct.unpack_from("<I", data, 8)[0]
        self.tables: dict[int, tuple[int, int]] = {}
        for i in range(num_tables):
            base = 28 + i * 16
            ttype, _empty, first, last = struct.unpack_from("<4I", data, base)
            self.tables.setdefault(ttype, (first, last))

    def _pages(self, table_type: int) -> Iterator[int]:
        entry = self.tables.get(table_type)
        if entry is None:
            return
        page, last = entry
        seen = set()
        while page not in seen:
            seen.add(page)
            yield page
            if page == last:
                return
            start = page * self.page_len
            if start + PAGE_HEADER_LEN > len(self.data):
                return
            page = struct.unpack_from("<I", self.data, start + 12)[0]

    def rows(self, table_type: int) -> Iterator[int]:
        """Yield absolute offsets of every live row in a table."""
        for page in self._pages(table_type):
            start = page * self.page_len
            if start + self.page_len > len(self.data):
                continue
            small, _u3, _u4, flags = struct.unpack_from("<4B", self.data, start + 24)
            large = struct.unpack_from("<H", self.data, start + 34)[0]
            if flags & 0x40:  # not a data page
                continue
            num_rows = large if (large > small and large != 0x1FFF) else small
            heap = start + PAGE_HEADER_LEN
            page_end = start + self.page_len
            for group in range((num_rows + 15) // 16):
                base = page_end - group * ROW_GROUP_STRIDE
                if base - 36 < heap:
                    continue
                present = struct.unpack_from("<H", self.data, base - 4)[0]
                for j in range(16):
                    index = group * 16 + j
                    if index >= num_rows or not (present >> j) & 1:
                        continue
                    row_off = struct.unpack_from("<H", self.data, base - 6 - j * 2)[0]
                    absolute = heap + row_off
                    if heap <= absolute < page_end:
                        yield absolute


def read_tracks(pdb: Pdb) -> dict[int, Track]:
    tracks: dict[int, Track] = {}
    for off in pdb.rows(TABLE_TRACKS):
        if struct.unpack_from("<H", pdb.data, off)[0] != 0x0024:
            continue
        offsets = struct.unpack_from("<21H", pdb.data, off + TRACK_STRING_OFFSETS)
        path = device_string(pdb.data, off + offsets[STR_PATH])
        if not path or not path.startswith("/Contents/"):
            continue
        track_id = struct.unpack_from("<I", pdb.data, off + TRACK_ID)[0]
        tracks[track_id] = Track(
            id=track_id,
            path=path,
            title=device_string(pdb.data, off + offsets[STR_TITLE]) or "?",
            toc_size=struct.unpack_from("<I", pdb.data, off + TRACK_FILE_SIZE)[0],
        )
    return tracks


def read_playlists(pdb: Pdb) -> tuple[dict[int, str], dict[int, list[int]]]:
    names: dict[int, str] = {}
    folders: set[int] = set()
    for off in pdb.rows(TABLE_PLAYLIST_TREE):
        _parent, _u, _sort, pid, is_folder = struct.unpack_from("<5I", pdb.data, off)
        name = device_string(pdb.data, off + 20)
        if name is None:
            continue
        names[pid] = name
        if is_folder:
            folders.add(pid)

    entries: dict[int, list[int]] = defaultdict(list)
    for off in pdb.rows(TABLE_PLAYLIST_ENTRIES):
        index, track_id, playlist_id = struct.unpack_from("<3I", pdb.data, off)
        entries[playlist_id].append((index, track_id))
    ordered = {
        pid: [tid for _i, tid in sorted(rows)]
        for pid, rows in entries.items()
        if pid not in folders
    }
    return names, ordered


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "/Volumes/NIKAN")
    pdb_path = root / "PIONEER/rekordbox/export.pdb"
    if not pdb_path.is_file():
        print(f"no export.pdb under {root}", file=sys.stderr)
        return 1

    pdb = Pdb(pdb_path.read_bytes())
    tracks = read_tracks(pdb)
    names, playlists = read_playlists(pdb)

    print(f"export.pdb: {len(tracks)} tracks, {len(playlists)} playlists\n")

    problems = 0
    print(
        f"{'playlist':<26} {'n':>4} {'ok':>4} {'badsize':>8} {'missing':>8} {'noref':>6}"
    )
    print("-" * 62)
    for pid, track_ids in sorted(
        playlists.items(), key=lambda kv: names.get(kv[0], "")
    ):
        ok = bad = miss = noref = 0
        detail = []
        for tid in track_ids:
            track = tracks.get(tid)
            if track is None:
                noref += 1
                continue
            f = root / track.path.lstrip("/")
            if not f.is_file():
                miss += 1
                detail.append(("MISSING", track, 0))
                continue
            actual = f.stat().st_size
            if actual != track.toc_size:
                bad += 1
                detail.append(("SIZE", track, actual))
            else:
                ok += 1
        problems += bad + miss + noref
        flag = "" if (bad or miss or noref) == 0 else "  <-- PROBLEM"
        print(
            f"{names.get(pid, f'#{pid}')[:26]:<26} {len(track_ids):>4} {ok:>4} "
            f"{bad:>8} {miss:>8} {noref:>6}{flag}"
        )
        for kind, track, actual in detail[:8]:
            print(
                f"      {kind}: toc={track.toc_size} actual={actual}  {track.title[:44]}"
            )

    # Whole-device sweep, including anything not in a playlist.
    print("\n=== every track in the TOC ===")
    ok = bad = miss = 0
    for track in tracks.values():
        f = root / track.path.lstrip("/")
        if not f.is_file():
            miss += 1
            print(f"  MISSING  {track.path}")
        elif f.stat().st_size != track.toc_size:
            bad += 1
            print(
                f"  SIZE     toc={track.toc_size} actual={f.stat().st_size}  {track.title[:44]}"
            )
        else:
            ok += 1
    print(f"  ok={ok}  wrong size={bad}  missing={miss}")

    dots = list((root / "Contents").rglob("._*"))
    print(f"\nmacOS ._ sidecar files: {len(dots)}")

    return 1 if (problems or bad or miss) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
