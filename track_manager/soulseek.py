"""Soulseek via the sockseek (formerly sldl) CLI.

Thin wrapper — no Soulseek protocol reimplementation. Requires a free
Soulseek account and ``sockseek`` (or legacy ``sldl``) on PATH.

``download_track`` is the non-interactive fallback used by the download
pipeline (one auto-picked FLAC). ``search`` / ``download_link`` back the
``seek`` command: list ranked results, then fetch the file the user chose.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, List, Optional, Sequence, TextIO

from .config import Config

# Preferred download extensions, in preference order.
_AUDIO_EXTENSIONS = (".flac", ".wav", ".aiff", ".aif", ".alac", ".m4a", ".mp3", ".ogg")

# How long an explicit ``seek`` waits for Soulseek responses. The download
# fallback keeps sockseek's own default; a person watching a list wants a
# fuller set than the first acceptable file.
SEEK_SEARCH_TIMEOUT_MS = 20_000

# How long a chosen file may sit with no bytes before we give up. Sockseek's
# 30s default reports a queue as a failed download. The subprocess cap is
# longer so a slow transfer can finish after the peer starts.
SEEK_STALE_TIMEOUT_MS = 180_000
SEEK_DOWNLOAD_TIMEOUT_S = 600

# Required extensions for a seek. Sockseek's own default list drops AIFF
# and a few other lossless types. This is an allowlist, not a preference:
# an MP3 still shows up when that is all the network has.
_SEEK_FORMATS = "flac,wav,aiff,aif,alac,ape,wv,mp3,m4a,ogg,opus,aac"

# Soft ranking only. Files outside this set stay in the list, further down.
_SEEK_PREF_FORMAT = "flac,wav,aiff,aif,alac,ape,wv"

# Advertised length within this many seconds sorts with the close matches.
# Anything further still stays in the list. The download fallback keeps the
# tighter ``soulseek.length_tol_seconds`` because it picks a file itself.
SEEK_PREF_LENGTH_TOL_SECONDS = 15

_LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif", ".alac", ".ape", ".wv"}

_KEYED_FIELD = re.compile(
    r"(?:^|,)\s*(artist|title|album|length)\s*=\s*",
    re.IGNORECASE,
)
_ARTIST_TITLE_SEP = re.compile(r"\s+[-–—]\s+")
_LENGTH_FIELD = re.compile(
    r"((?:^|,)\s*length\s*=)\s*[^,]*",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class SeekQuery:
    """Sockseek input plus the artist/title to stamp on the file, if known."""

    text: str
    artist: Optional[str]
    title: Optional[str]
    duration_s: Optional[float]


@dataclass(frozen=True)
class SoulseekHit:
    """One file from a sockseek ``--print json-all`` song search."""

    username: str
    filename: str
    size: int
    length: Optional[int] = None
    bitrate: Optional[int] = None
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
    upload_speed: Optional[float] = None
    has_free_slot: bool = False

    @property
    def basename(self) -> str:
        """Share path with directories stripped (Soulseek uses backslashes)."""
        return Path(self.filename.replace("\\", "/")).name

    def link(self) -> str:
        """``slsk://user/path`` sockseek accepts as a direct download.

        Usernames that contain ``/`` cannot use this. Sockseek decodes the
        link and then splits on the first slash, so ``https://host`` becomes
        the user ``https:``.
        """
        path = self.filename.replace("\\", "/")
        return f"slsk://{self.username}/{path}"

    def direct_link(self) -> Optional[str]:
        """A sockseek link, or None when the username would be split wrong."""
        if username_breaks_slsk_link(self.username):
            return None
        return self.link()


def username_breaks_slsk_link(username: str) -> bool:
    """True when ``username`` cannot survive ``slsk://user/path`` parsing."""
    return "/" in username or "\\" in username


def query_for_user_file(hit: SoulseekHit) -> str:
    """Song query that names one shared file, for a user-restricted download.

    Used when the username itself contains a slash and a direct link would
    point at the wrong peer. A comma in the filename is left as free text
    because sockseek splits keyed fields on commas.
    """
    stem = Path(hit.basename).stem.strip() or hit.basename
    parts = _ARTIST_TITLE_SEP.split(stem, maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts) and "," not in stem:
        duration = float(hit.length) if hit.length else None
        return build_song_query(
            parts[0].strip(),
            parts[1].strip(),
            duration_s=duration,
        )
    return stem


def _parse_keyed_fields(query: str) -> dict[str, str]:
    """Split ``artist=A, title=B`` into a dict. Values may contain commas."""
    matches = list(_KEYED_FIELD.finditer(query))
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(query)
        value = query[start:end].strip()
        if value:
            fields[match.group(1).lower()] = value
    return fields


def _upsert_length(query: str, duration_s: float) -> str:
    """Set or replace the ``length=`` field on a keyed sockseek query."""
    length = str(int(round(duration_s)))
    if _LENGTH_FIELD.search(query):
        return _LENGTH_FIELD.sub(rf"\g<1>{length}", query, count=1)
    return f"{query}, length={length}"


def parse_seek_query(query: str, duration_s: Optional[float] = None) -> SeekQuery:
    """Turn a ``seek`` argument into a sockseek song search.

    Keyed input (``artist=…, title=…``) is passed through. ``Artist - Title``
    becomes a keyed song query. Anything else is a free-text Soulseek search;
    artist and title are left empty and filled from the chosen filename.
    """
    raw = query.strip()
    if not raw:
        raise ValueError("Empty search query")

    if _KEYED_FIELD.search(raw):
        fields = _parse_keyed_fields(raw)
        artist = fields.get("artist") or None
        title = fields.get("title") or None
        length = duration_s
        if length is None and fields.get("length"):
            try:
                length = float(fields["length"])
            except ValueError:
                length = None
        text = _upsert_length(raw, length) if length is not None else raw
        return SeekQuery(text=text, artist=artist, title=title, duration_s=length)

    parts = _ARTIST_TITLE_SEP.split(raw, maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts):
        artist, title = parts[0].strip(), parts[1].strip()
        return SeekQuery(
            text=build_song_query(artist, title, duration_s=duration_s),
            artist=artist,
            title=title,
            duration_s=duration_s,
        )

    return SeekQuery(text=raw, artist=None, title=None, duration_s=duration_s)


def artist_title_from_share_name(name: str) -> tuple[Optional[str], Optional[str]]:
    """Guess artist and title from a share filename like ``Artist - Title.flac``."""
    stem = Path(name).stem.strip()
    if not stem:
        return None, None
    parts = _ARTIST_TITLE_SEP.split(stem, maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts):
        return parts[0].strip(), parts[1].strip()
    return None, stem


def _optional_int(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _optional_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _field(payload: dict, *names: str) -> object:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def parse_search_results(stdout: str) -> Optional[List[SoulseekHit]]:
    """Parse sockseek ``--print json-all`` stdout.

    Returns None when stdout has no JSON array (login chatter, a crash).
    Returns an empty list when sockseek printed ``[]``.
    """
    data = _extract_json_array(stdout)
    if data is None:
        return None

    hits: List[SoulseekHit] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        user = _field(item, "User", "user")
        file_info = _field(item, "File", "file")
        if not isinstance(user, dict) or not isinstance(file_info, dict):
            continue
        username = _field(user, "Username", "username")
        filename = _field(file_info, "Filename", "filename")
        if not isinstance(username, str) or not username.strip():
            continue
        if not isinstance(filename, str) or not filename.strip():
            continue
        size_raw = _field(file_info, "Size", "size")
        try:
            size = int(size_raw) if size_raw is not None else 0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            size = 0
        speed = _optional_float(_field(user, "UploadSpeed", "uploadSpeed"))
        free = _field(user, "HasFreeUploadSlot", "hasFreeUploadSlot")
        hits.append(
            SoulseekHit(
                username=username.strip(),
                filename=filename,
                size=max(0, size),
                length=_optional_int(_field(file_info, "Length", "length")),
                bitrate=_optional_int(_field(file_info, "Bitrate", "bitrate")),
                sample_rate=_optional_int(
                    _field(file_info, "SampleRate", "sampleRate")
                ),
                bit_depth=_optional_int(_field(file_info, "BitDepth", "bitDepth")),
                upload_speed=speed,
                has_free_slot=bool(free),
            )
        )
    return hits


def _extract_json_array(stdout: str) -> Optional[list]:
    """Pull the first JSON array out of sockseek output."""
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("["):
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data

    start = stdout.find("[")
    end = stdout.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(stdout[start : end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return data
    return None


def rank_hits(
    hits: Sequence[SoulseekHit],
    duration_s: Optional[float],
    tolerance_s: int,
) -> List[SoulseekHit]:
    """Sort hits without dropping any.

    Close lengths (and unknown lengths) come first, then lossless files.
    Order from sockseek is kept inside each group.
    """

    def sort_key(item: tuple[int, SoulseekHit]) -> tuple[int, int, int]:
        index, hit = item
        if duration_s is None or hit.length is None:
            length_rank = 0
        elif abs(hit.length - duration_s) <= tolerance_s:
            length_rank = 0
        else:
            length_rank = 1
        ext = Path(hit.basename).suffix.lower()
        lossless_rank = 0 if ext in _LOSSLESS_EXTENSIONS else 1
        return (length_rank, lossless_rank, index)

    return [hit for _, hit in sorted(enumerate(hits), key=sort_key)]


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.0f}KB"
    if size > 0:
        return f"{size}B"
    return "?"


def _format_length(seconds: Optional[int]) -> str:
    if seconds is None or seconds <= 0:
        return "?"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def format_hit(hit: SoulseekHit) -> str:
    """One terminal line describing a search hit."""
    ext = Path(hit.basename).suffix.lower().lstrip(".") or "audio"
    details: List[str] = [ext, _format_length(hit.length), _format_size(hit.size)]
    if hit.bitrate:
        details.append(f"{hit.bitrate}kbps")
    if hit.sample_rate:
        details.append(f"{hit.sample_rate}Hz")
    if hit.bit_depth:
        details.append(f"{hit.bit_depth}-bit")
    details.append(hit.username)
    if hit.upload_speed:
        details.append(f"{hit.upload_speed:.1f}MB/s")
    if not hit.has_free_slot:
        details.append("busy")
    return f"{hit.basename}  ({', '.join(details)})"


def choose_hit(
    hits: Sequence[SoulseekHit],
    *,
    pick: Optional[int] = None,
    auto: bool = False,
    prompt: bool = False,
) -> Optional[SoulseekHit]:
    """Pick one hit. ``None`` means the user cancelled.

    A single hit, or ``auto=True``, takes the first result without a prompt.
    ``pick`` is 1-based into ``hits``. ``prompt`` always asks, including when
    only one hit is left after a failed handshake.
    """
    if not hits:
        return None
    if not prompt and pick is not None:
        if pick < 1 or pick > len(hits):
            raise ValueError(f"Result {pick} is out of range (1-{len(hits)})")
        return hits[pick - 1]
    if not prompt and (auto or len(hits) == 1):
        label = "One Soulseek result" if len(hits) == 1 else "Using top Soulseek result"
        print(f"🎵 {label}: {format_hit(hits[0])}")
        return hits[0]

    print(f"🔍 Found {len(hits)} Soulseek results:")
    for index, hit in enumerate(hits, 1):
        print(f"  [{index}] {format_hit(hit)}")
    print("  [q] Cancel", flush=True)

    while True:
        try:
            choice = input(f"Choice [1-{len(hits)}/q]: ").strip().lower()
        except EOFError:
            print(
                "ℹ️ No selection. Re-run with --yes or --pick N",
                file=sys.stderr,
            )
            return None
        if choice in ("q", "quit", "cancel"):
            return None
        try:
            index = int(choice)
        except ValueError:
            print(f"Invalid choice. Enter 1-{len(hits)} or q.")
            continue
        if 1 <= index <= len(hits):
            return hits[index - 1]
        print(f"Invalid choice. Enter 1-{len(hits)} or q.")


def is_handshake_failure(text: str) -> bool:
    """True when the peer never accepted the transfer.

    Soulseek.NET gives the peer 15 seconds to answer. That wait is a
    ``TimeoutException``, or the peer is reported offline. A transfer that
    started and then stalled is a stale download, not a handshake failure.
    """
    lowered = text.lower()
    if "staledownloadexception" in lowered:
        return False
    markers = (
        "timeoutexception",
        "the wait timed out",
        "appears to be offline",
        "failed to connect",
        "could not connect",
        "connection refused",
    )
    return any(marker in lowered for marker in markers)


def _failure_detail(text: str) -> str:
    """Pick the useful sockseek error line, not the trailing summary."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    for marker in ("Error:", "Exception:", "failed ["):
        for line in reversed(lines):
            if marker in line and "Completed:" not in line:
                return line[:300]
    for line in reversed(lines):
        if "Completed:" not in line:
            return line[:300]
    return lines[-1][:300]


def _stream_process(
    cmd: List[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a command and copy its log to the terminal as lines arrive."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []

    def pump(pipe: Optional[IO[str]], sink: TextIO, lines: List[str]) -> None:
        if pipe is None:
            return
        try:
            for line in pipe:
                lines.append(line)
                print(line, end="", file=sink, flush=True)
        finally:
            pipe.close()

    threads = [
        threading.Thread(
            target=pump,
            args=(proc.stdout, sys.stdout, stdout_lines),
            daemon=True,
        ),
        threading.Thread(
            target=pump,
            args=(proc.stderr, sys.stderr, stderr_lines),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        for thread in threads:
            thread.join()
    code = proc.returncode if proc.returncode is not None else 1
    return subprocess.CompletedProcess(
        cmd,
        code,
        "".join(stdout_lines),
        "".join(stderr_lines),
    )


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
    """Search and download via sockseek/sldl."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self.last_failure = ""

    def _prepare(self) -> Optional[tuple[Path, str, str]]:
        """Binary plus credentials, or None after printing why Soulseek can't run."""
        binary = find_sockseek_binary(self.config)
        if binary is None:
            print("ℹ️ sockseek not found on PATH", file=sys.stderr)
            return None

        username = self.config.soulseek_username
        password = self.config.soulseek_password
        if not username or not password:
            print(
                "ℹ️ Soulseek is not configured "
                "(set soulseek.username and soulseek.password)",
                file=sys.stderr,
            )
            return None
        return binary, username, password

    def _run(
        self,
        cmd: List[str],
        *,
        action: str,
        timeout: Optional[int] = None,
        stream: bool = False,
    ) -> Optional[subprocess.CompletedProcess[str]]:
        """Run sockseek. Prints a short failure and returns None on error.

        ``stream`` leaves sockseek's own log on the terminal. Search stays
        captured so the JSON result list is not mixed with progress bars.
        """
        if timeout is None:
            timeout = self.config.soulseek_timeout_seconds
        try:
            if stream:
                result = _stream_process(cmd, timeout)
            else:
                result = subprocess.run(
                    cmd,
                    timeout=timeout,
                    check=False,
                    capture_output=True,
                    text=True,
                )
        except subprocess.TimeoutExpired:
            self.last_failure = f"Soulseek {action} timed out after {timeout}s"
            print(
                f"⚠️ Soulseek {action} timed out after {timeout}s",
                file=sys.stderr,
            )
            return None
        except FileNotFoundError:
            print(f"ℹ️ sockseek binary not runnable: {cmd[0]}", file=sys.stderr)
            return None
        except OSError as exc:
            print(f"⚠️ Soulseek launch failed: {exc}", file=sys.stderr)
            return None

        if result.returncode != 0:
            note = _failure_detail(f"{result.stderr or ''}\n{result.stdout or ''}")
            self.last_failure = note
            if note:
                print(f"⚠️ Soulseek {action} failed: {note}", file=sys.stderr)
            else:
                print(
                    f"⚠️ Soulseek {action} failed (exit {result.returncode})",
                    file=sys.stderr,
                )
            return None
        return result

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
        prepared = self._prepare()
        if prepared is None:
            return None
        binary, username, password = prepared

        own_temp = output_dir is None
        work_dir = (
            Path(output_dir)
            if output_dir
            else Path(tempfile.mkdtemp(prefix="tm_soulseek_"))
        )
        work_dir.mkdir(parents=True, exist_ok=True)

        query = build_song_query(artist, title, duration_s=duration_s)
        length_tol = self.config.soulseek_length_tol_seconds

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

        result = self._run(cmd, action="download")
        if result is None:
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

    def search(self, parsed: SeekQuery) -> Optional[List[SoulseekHit]]:
        """List ranked song results. None means the search itself failed.

        An empty list means sockseek ran and found nothing. Length is not
        required: a passed duration only sorts close matches first. Lossless
        formats rank above other audio, which is still returned.
        """
        prepared = self._prepare()
        if prepared is None:
            return None
        binary, username, password = prepared

        # Length and format here are allowlists / sort hints. A required
        # length (sockseek's default is 3s) hides the near-misses a person
        # opened ``seek`` to look at.
        cmd: List[str] = [
            str(binary),
            parsed.text,
            "--song",
            "--print",
            "json-all",
            "--format",
            _SEEK_FORMATS,
            "--pref-format",
            _SEEK_PREF_FORMAT,
            # A bare "-1" is parsed as another flag. Equals binds the value.
            "--length-tol=-1",
            "--search-timeout",
            str(SEEK_SEARCH_TIMEOUT_MS),
            "--user",
            username,
            "--pass",
            password,
            "--no-config",
            "--no-progress",
            "--no-skip-existing",
            "--no-write-index",
        ]
        if parsed.duration_s is not None:
            cmd.extend(["--pref-length-tol", str(SEEK_PREF_LENGTH_TOL_SECONDS)])

        timeout = self.config.soulseek_timeout_seconds
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
                f"⚠️ Soulseek search timed out after {timeout}s",
                file=sys.stderr,
            )
            return None
        except FileNotFoundError:
            print(f"ℹ️ sockseek binary not runnable: {binary}", file=sys.stderr)
            return None
        except OSError as exc:
            print(f"⚠️ Soulseek launch failed: {exc}", file=sys.stderr)
            return None

        hits = parse_search_results(result.stdout or "")
        if hits is not None:
            # A printed result list is the search, even if sockseek exits
            # non-zero because nothing matched.
            return hits

        if result.returncode != 0:
            note = _failure_detail(f"{result.stderr or ''}\n{result.stdout or ''}")
            if note:
                print(f"⚠️ Soulseek search failed: {note}", file=sys.stderr)
            else:
                print(
                    f"⚠️ Soulseek search failed (exit {result.returncode})",
                    file=sys.stderr,
                )
            return None

        print("⚠️ Soulseek search returned no results list", file=sys.stderr)
        return None

    def download_link(
        self,
        link: str,
        *,
        output_dir: Optional[Path] = None,
    ) -> Optional[Path]:
        """Download one ``slsk://`` file chosen from a search."""
        prepared = self._prepare()
        if prepared is None:
            return None
        binary, username, password = prepared

        work_dir = (
            Path(output_dir)
            if output_dir
            else Path(tempfile.mkdtemp(prefix="tm_soulseek_"))
        )
        work_dir.mkdir(parents=True, exist_ok=True)

        cmd = self._transfer_cmd(
            binary,
            username,
            password,
            link,
            work_dir,
        )
        return self._finish_transfer(cmd, work_dir)

    def download_hit(
        self,
        hit: SoulseekHit,
        *,
        query: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> Optional[Path]:
        """Download one search hit.

        A normal peer is fetched by ``slsk://`` link. A username containing
        ``/`` is fetched by searching that user for the filename, because a
        direct link would be parsed as a different peer.
        """
        if hit.direct_link() is not None:
            return self.download_link(hit.link(), output_dir=output_dir)
        if "," in hit.username:
            print(
                f"❌ Cannot target {hit.username}: the name contains a comma",
                file=sys.stderr,
            )
            return None
        print(
            f"ℹ️ {hit.username} has a slash in the name, "
            "so this repeats the search limited to that user"
        )
        return self._download_from_user(hit, query=query, output_dir=output_dir)

    def _download_from_user(
        self,
        hit: SoulseekHit,
        *,
        query: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> Optional[Path]:
        prepared = self._prepare()
        if prepared is None:
            return None
        binary, username, password = prepared

        work_dir = (
            Path(output_dir)
            if output_dir
            else Path(tempfile.mkdtemp(prefix="tm_soulseek_"))
        )
        work_dir.mkdir(parents=True, exist_ok=True)

        # Repeat the query that already returned this file. Rebuilding a
        # query from the full filename (brackets, mix name, length) is a
        # different Soulseek search and can come back empty.
        text = (query or "").strip() or query_for_user_file(hit)
        extra = ["--song", "--allowed-users", hit.username]
        extension = Path(hit.basename).suffix.lower().lstrip(".")
        if extension:
            extra.extend(["--format", extension])
        cmd = self._transfer_cmd(
            binary,
            username,
            password,
            text,
            work_dir,
            extra,
        )
        return self._finish_transfer(cmd, work_dir)

    def _transfer_cmd(
        self,
        binary: Path,
        username: str,
        password: str,
        source: str,
        work_dir: Path,
        extra: Optional[Sequence[str]] = None,
    ) -> List[str]:
        cmd = [
            str(binary),
            source,
            "--user",
            username,
            "--pass",
            password,
            "--output-dir",
            str(work_dir),
            "--no-config",
            "--no-skip-existing",
            "--no-write-index",
            f"--max-stale-time={SEEK_STALE_TIMEOUT_MS}",
        ]
        if extra:
            cmd.extend(extra)
        return cmd

    def _finish_transfer(self, cmd: List[str], work_dir: Path) -> Optional[Path]:
        self.last_failure = ""
        result = self._run(
            cmd,
            action="download",
            timeout=max(
                self.config.soulseek_timeout_seconds,
                SEEK_DOWNLOAD_TIMEOUT_S,
            ),
            stream=True,
        )
        if result is None:
            return None

        found = _pick_downloaded_file(work_dir)
        if found is None:
            snippet = (
                _failure_detail(f"{result.stderr or ''}\n{result.stdout or ''}")
                or "no audio file written"
            )
            self.last_failure = snippet
            print(f"⚠️ Soulseek produced no audio: {snippet}", file=sys.stderr)
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
