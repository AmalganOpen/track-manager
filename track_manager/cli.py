"""Command-line interface for track-manager."""

import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import click
except ImportError:
    print("Error: click not installed", file=sys.stderr)
    print("Install with: pip install click", file=sys.stderr)
    sys.exit(1)

from . import __version__
from .config import Config
from .downloader import Downloader


class DefaultGroup(click.Group):
    """Click group that defaults to a specified command when no command is given."""

    def __init__(self, *args, **kwargs):
        self.default_command = kwargs.pop("default_command", None)
        super(DefaultGroup, self).__init__(*args, **kwargs)

    def get_command(self, ctx, cmd_name):
        # If no command is specified and we have a default command,
        # treat the first argument as the URL for the default command
        if not cmd_name and self.default_command is not None:
            # No command provided, use default
            return self.commands[self.default_command]

        # If the command name is not found and we have a default command,
        # treat the command name as the URL for the default command
        # BUT: don't redirect if it's a flag (starts with -)
        if (
            cmd_name not in self.commands
            and self.default_command is not None
            and not cmd_name.startswith("-")
        ):
            # Prepend the command name (URL) to the args for the default command
            ctx.args = [cmd_name] + ctx.args
            return self.commands[self.default_command]

        return super(DefaultGroup, self).get_command(ctx, cmd_name)

    def parse_args(self, ctx, args):
        # If the first argument is not a known command and we have a default command,
        # treat it as the URL for the default command
        # BUT: don't redirect if it's a flag (starts with -) or if there are no args
        if (
            args
            and args[0] not in self.commands
            and self.default_command is not None
            and not args[0].startswith("-")
        ):
            args.insert(0, self.default_command)

        return super(DefaultGroup, self).parse_args(ctx, args)


@click.group(cls=DefaultGroup, default_command="download", invoke_without_command=True)
@click.version_option()
@click.pass_context
def cli(ctx):
    """Track Manager - Universal music downloader with smart duplicate detection."""
    # If no arguments provided and no command, show help
    if ctx.invoked_subcommand is None and not ctx.protected_args and not ctx.args:
        click.echo(ctx.get_help())
        ctx.exit()


@cli.command()
@click.argument("url")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["auto", "aiff", "m4a", "mp3"]),
    default="auto",
    help="Output format. 'auto' resolves to AIFF (gear-compatible default). "
    "Pass aiff/m4a/mp3 to override.",
)
@click.option(
    "--output", "-o", type=click.Path(), help="Output directory (overrides config)"
)
@click.option(
    "--dumb",
    is_flag=True,
    help="Disable smart downloads (download directly from source)",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Bypass the persistent TIDAL ISRC→ID cache (forces fresh song.link "
    "lookups). Useful when a cached id has gone stale.",
)
def download(url: str, format: str, output: Optional[str], dumb: bool, no_cache: bool):
    """Download track(s) from URL.

    Supports: Spotify, YouTube, SoundCloud, and direct URLs.

    Automatically downloads FLAC when available via ISRC lookup
    (unless --dumb is specified).
    """
    config = Config()

    # Override output directory if specified
    if output:
        output_dir = Path(output)
    else:
        output_dir = config.output_dir

    downloader = Downloader(config, output_dir, dumb=dumb, bypass_cache=no_cache)

    try:
        downloader.download(url, format)
    except KeyboardInterrupt:
        click.echo("\n⚠️ Download cancelled by user")
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@cli.command("retry-failed")
@click.option(
    "--log",
    "log_path",
    type=click.Path(),
    help="Failed downloads log (overrides config)",
)
@click.option(
    "--list",
    "-l",
    "list_only",
    is_flag=True,
    help="List failed URLs without retrying",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show what would be retried without downloading",
)
@click.option(
    "--clear",
    is_flag=True,
    help="Clear the failed-downloads log without retrying",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["auto", "aiff", "m4a", "mp3"]),
    default="auto",
    help="Output format for retried downloads",
)
@click.option(
    "--output", "-o", type=click.Path(), help="Output directory (overrides config)"
)
@click.option(
    "--dumb",
    is_flag=True,
    help="Disable smart downloads (download directly from source)",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Bypass the persistent TIDAL ISRC→ID cache",
)
def retry_failed(
    log_path: Optional[str],
    list_only: bool,
    dry_run: bool,
    clear: bool,
    yes: bool,
    format: str,
    output: Optional[str],
    dumb: bool,
    no_cache: bool,
):
    """Retry URLs from the failed-downloads log.

    Reads ``failed_log`` from config (default: ``failed-downloads.txt``),
    deduplicates by URL (newest failure first), and re-runs each download.
    Successfully retried URLs are removed from the log; new failures are
    logged automatically by the downloader.
    """
    from .failed_downloads import (
        clear_log,
        parse_failed_log,
        remove_urls,
        summarize_failed,
    )

    config = Config()
    failed_log = Path(log_path) if log_path else config.failed_log
    entries = parse_failed_log(failed_log)
    candidates = summarize_failed(entries)

    if not candidates:
        click.echo(f"✅ No failed downloads in {failed_log}")
        return

    if list_only:
        click.echo(f"Failed downloads ({len(candidates)} unique URL(s)):\n")
        for index, (url, timestamp, error) in enumerate(candidates, 1):
            click.echo(f"{index}. [{timestamp}] {url}")
            click.echo(f"   {error}")
        return

    if clear:
        if not yes and not click.confirm(
            f"Clear {len(entries)} log line(s) ({len(candidates)} unique URL(s))?",
            default=False,
        ):
            click.echo("Aborted.")
            return
        clear_log(failed_log)
        click.echo(f"✅ Cleared {failed_log}")
        return

    if dry_run:
        click.echo(f"Would retry {len(candidates)} URL(s) from {failed_log}:\n")
        for index, (url, timestamp, error) in enumerate(candidates, 1):
            click.echo(f"{index}. [{timestamp}] {url}")
            click.echo(f"   {error}")
        return

    if not yes and not click.confirm(
        f"Retry {len(candidates)} failed URL(s) from {failed_log}?",
        default=True,
    ):
        click.echo("Aborted.")
        return

    output_dir = Path(output) if output else config.output_dir
    downloader = Downloader(config, output_dir, dumb=dumb, bypass_cache=no_cache)
    urls = [url for url, _timestamp, _error in candidates]

    remove_urls(failed_log, set(urls))

    succeeded = 0
    failed = 0
    try:
        for index, url in enumerate(urls):
            if index:
                click.echo()
            click.echo(f"▶ [{index + 1}/{len(urls)}] {url}")
            try:
                result = downloader.download(url, format, show_header=False)
            except KeyboardInterrupt:
                click.echo("\n⚠️ Retry cancelled by user")
                sys.exit(1)
            except Exception as exc:
                click.echo(f"❌ Error: {exc}", err=True)
                failed += 1
                continue

            if result is False:
                failed += 1
            else:
                succeeded += 1
    finally:
        remaining = len(summarize_failed(parse_failed_log(failed_log)))

    click.echo()
    click.echo("━" * 60)
    click.echo(f"✅ Succeeded: {succeeded}")
    if failed:
        click.echo(f"❌ Failed:    {failed} (see {failed_log})")
    if remaining:
        click.echo(f"   {remaining} unique URL(s) still in log")

    if failed:
        sys.exit(1)


@cli.command("check-duplicates")
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True),
    help="Check specific file for duplicates",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Library directory to scan (overrides config)",
)
def check_duplicates(file: Optional[str], output: Optional[str]):
    """Check for duplicate tracks in library."""
    from .duplicates import check_file, scan_library

    config = Config()
    library_dir = Path(output) if output else config.output_dir

    if file:
        check_file(Path(file), library_dir)
    else:
        scan_library(library_dir)


@cli.command("verify-metadata")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Library directory to scan (overrides config)",
)
def verify_metadata(output: Optional[str]):
    """Verify metadata quality in library."""
    from .metadata import verify_library

    config = Config()
    library_dir = Path(output) if output else config.output_dir
    verify_library(library_dir)


@cli.command("check-quality")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed info for each file")
@click.option(
    "--verbose", "-v", is_flag=True, help="Show outlier tracks (worst/best quality)"
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Library directory to scan (overrides config)",
)
def check_quality(detailed: bool, verbose: bool, output: Optional[str]):
    """Check audio quality of tracks in library."""
    from .quality import analyze_library

    config = Config()
    library_dir = Path(output) if output else config.output_dir
    analyze_library(library_dir, detailed, verbose)


@cli.command("check-compat")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Library directory to scan (overrides config)",
)
@click.option(
    "--all",
    "scan_all",
    is_flag=True,
    help="Audit the whole Rekordbox collection (master.db) instead of just the "
    "library directory. Requires Rekordbox to be CLOSED.",
)
def check_compat(output: Optional[str], scan_all: bool):
    """Audit tracks for CDJ-2000NXS playability.

    The original CDJ-2000NXS (2012) has a strict decoder: AIFF/WAV must be
    uncompressed PCM at 16/24-bit and 44.1/48 kHz; AAC and MP3 are capped at
    48 kHz; FLAC, Apple Lossless, 32-bit float, compressed AIFF-C, and
    WAVE_FORMAT_EXTENSIBLE WAVs are all rejected.

    By default this scans the configured library directory on disk (fast, no
    database lock). Pass --all to instead audit every track in Rekordbox's
    master.db, which mirrors what the "export to device" popup checks.
    """
    from . import compat as tm_compat

    config = Config()
    library_dir = Path(output) if output else config.output_dir

    if scan_all:
        results = _collect_compat_from_rekordbox(library_dir)
    else:
        if not library_dir.exists():
            click.echo(f"❌ Library directory not found: {library_dir}", err=True)
            sys.exit(1)
        click.echo(f"🔍 Scanning {library_dir} for CDJ-2000NXS compatibility...")
        click.echo()
        results = tm_compat.scan_dir(library_dir)

    if not results:
        click.echo("No audio files found to check.")
        return

    compatible = [(p, r) for p, r in results if r.compatible]
    unknown = [(p, r) for p, r in results if not r.compatible and r.unknown]
    incompatible = [(p, r) for p, r in results if not r.compatible and not r.unknown]

    click.echo(f"Checked {len(results)} track(s):")
    click.echo(f"  ✅ Compatible:   {len(compatible)}")
    click.echo(f"  ❌ Incompatible: {len(incompatible)}")
    click.echo(f"  ⚠️  Unknown:      {len(unknown)}")

    if incompatible:
        click.echo()
        click.echo("Incompatible with CDJ-2000NXS:")
        for p, r in incompatible:
            click.echo(f"  ❌ {p.name}: {r.reason}")

    if unknown:
        click.echo()
        click.echo("Could not verify (manual check recommended):")
        for p, r in unknown:
            click.echo(f"  ⚠️  {p.name}: {r.reason}")

    if incompatible:
        sys.exit(1)
    click.echo()
    click.echo("🎉 All checked tracks are CDJ-2000NXS compatible.")


def _collect_compat_from_rekordbox(library_dir: Path) -> list:
    """Classify every on-disk track Rekordbox knows about (requires it closed)."""
    from . import compat as tm_compat
    from . import rekordbox_db as tm_rb

    procs = tm_rb.running_rekordbox_processes()
    if procs:
        click.echo(
            "❌ Rekordbox is running. Quit it before running with --all:", err=True
        )
        for p in procs:
            click.echo(f"   • pid {p.pid}: {p.command}", err=True)
        sys.exit(1)

    try:
        tracks = tm_rb.list_tracks(library_dir.resolve())
    except ImportError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Could not open Rekordbox database: {e}", err=True)
        sys.exit(1)

    click.echo(
        f"🔍 Auditing {len(tracks)} Rekordbox track(s) for CDJ-2000NXS compatibility..."
    )
    click.echo()

    results: list = []
    missing = 0
    for t in tracks:
        path = t.folder_path
        if not path.exists():
            missing += 1
            continue
        results.append((path, tm_compat.classify(path)))

    if missing:
        click.echo(f"(Skipped {missing} track(s) whose file is missing on disk.)")
        click.echo()
    return results


@cli.command("apply-metadata")
@click.option("--show", is_flag=True, help="Show pending reviews without applying")
def apply_metadata(show: bool):
    """Apply metadata corrections from CSV."""
    from .metadata import apply_metadata_csv, show_pending_reviews

    config = Config()

    if show:
        show_pending_reviews()
    else:
        apply_metadata_csv()


def _collect_diff(
    user_map: Any, example_map: Any, removed: list, added: list, prefix: str = ""
) -> None:
    """Compute which keys will be removed from / added to user_map relative to example_map."""
    for k in user_map:
        full = f"{prefix}.{k}" if prefix else k
        if k not in example_map:
            removed.append(full)
        elif hasattr(user_map[k], "keys") and hasattr(example_map[k], "keys"):
            _collect_diff(user_map[k], example_map[k], removed, added, full)

    for k in example_map:
        full = f"{prefix}.{k}" if prefix else k
        if k not in user_map:
            added.append(full)
        elif hasattr(user_map[k], "keys") and hasattr(example_map[k], "keys"):
            pass  # already handled above


def _overlay_user_values(base_map: Any, user_map: Any) -> None:
    """Copy user values onto base_map (example structure), preserving example comments."""
    for k, v in user_map.items():
        if k not in base_map:
            continue
        if hasattr(base_map[k], "keys") and hasattr(v, "keys"):
            _overlay_user_values(base_map[k], v)
        else:
            base_map[k] = v


@cli.command("check-setup")
def check_setup():
    """Verify all dependencies are installed and sync config.yaml with config.example.yaml."""
    click.echo("🔍 Checking track-manager dependencies...")
    click.echo()

    all_ok = True

    # Check yt-dlp
    try:
        import yt_dlp

        click.echo(f"✅ yt-dlp: {yt_dlp.version.__version__}")
    except ImportError:
        click.echo("❌ yt-dlp: Not installed", err=True)
        click.echo("   Install: pip install yt-dlp", err=True)
        all_ok = False

    # Check spotdl
    try:
        import spotdl

        click.echo(f"✅ spotdl: {spotdl.__version__}")
    except ImportError:
        click.echo("⚠️ spotdl: Not installed (optional, needed for Spotify)")
        click.echo("   Install: pip install spotdl")

    # Check requests
    try:
        import requests

        click.echo(f"✅ requests: {requests.__version__}")
    except ImportError:
        click.echo("❌ requests: Not installed", err=True)
        click.echo("   Install: pip install requests", err=True)
        all_ok = False

    # Check mutagen
    try:
        import mutagen

        click.echo(f"✅ mutagen: {mutagen.version_string}")
    except ImportError:
        click.echo("❌ mutagen: Not installed", err=True)
        click.echo("   Install: pip install mutagen", err=True)
        all_ok = False

    # Check PyYAML
    try:
        import yaml

        click.echo("✅ PyYAML: Installed")
    except ImportError:
        click.echo("❌ PyYAML: Not installed", err=True)
        click.echo("   Install: pip install pyyaml", err=True)
        all_ok = False

    # Check click
    try:
        from importlib.metadata import version as _pkg_version

        click_version = _pkg_version("click")
        click.echo(f"✅ click: {click_version}")
    except ImportError:
        click.echo("❌ click: Not installed", err=True)
        click.echo("   Install: pip install click", err=True)
        all_ok = False

    # Check and sync config
    click.echo()
    click.echo("🔧 Checking configuration...")
    config_path = Path(__file__).parent.parent / "config.yaml"
    example_path = Path(__file__).parent.parent / "config.example.yaml"

    if not config_path.exists():
        click.echo("⚠️  config.yaml not found")
        click.echo("   Copy config.example.yaml to config.yaml")
    elif not example_path.exists():
        click.echo("✅ config.yaml found (config.example.yaml missing, skipping sync)")
    else:
        try:
            from ruamel.yaml import YAML as RuamelYAML

            ryaml = RuamelYAML()
            ryaml.preserve_quotes = True
            with open(config_path) as f:
                user_cfg = ryaml.load(f) or {}
            with open(example_path) as f:
                example_cfg = ryaml.load(f) or {}
            use_ruamel = True
        except ImportError:
            import yaml

            with open(config_path) as f:
                user_cfg = yaml.safe_load(f) or {}
            with open(example_path) as f:
                example_cfg = yaml.safe_load(f) or {}
            use_ruamel = False

        removed: list = []
        added: list = []
        _collect_diff(user_cfg, example_cfg, removed, added)

        if not removed and not added:
            click.echo("✅ config.yaml is up to date")
        else:
            if removed:
                click.echo(f"  Keys to remove ({len(removed)}):")
                for key in removed:
                    click.echo(f"    - {key}")
            if added:
                click.echo(f"  Keys to add ({len(added)}):")
                for key in added:
                    click.echo(f"    + {key}")

            click.echo()
            if click.confirm("Apply these changes to config.yaml?", default=True):
                if use_ruamel:
                    # Re-load example fresh so its comments are intact, then overlay user values
                    with open(example_path) as f:
                        synced_cfg = ryaml.load(f)
                    _overlay_user_values(synced_cfg, user_cfg)
                    with open(config_path, "w") as f:
                        ryaml.dump(synced_cfg, f)
                else:
                    import copy

                    import yaml

                    synced_cfg = copy.deepcopy(example_cfg)
                    _overlay_user_values(synced_cfg, user_cfg)
                    with open(config_path, "w") as f:
                        yaml.dump(
                            synced_cfg,
                            f,
                            default_flow_style=False,
                            allow_unicode=True,
                            sort_keys=False,
                        )
                    click.echo(
                        "   Note: Comments were not preserved (ruamel.yaml not installed)."
                    )
                click.echo("✅ config.yaml updated")
                click.echo(
                    "   Re-run your install command to pick up any new dependencies:"
                )
                click.echo(f"     pip install -e {config_path.parent}")
            else:
                click.echo("Skipped config sync.")

    click.echo()

    if all_ok:
        click.echo("🎉 All required dependencies are installed")
        click.echo()
        click.echo("Next steps:")
        click.echo("  1. Ensure config.yaml is set up")
        click.echo("  2. Re-run your install command to pick up any new dependencies:")
        click.echo(
            f"     pip install -e {config_path.parent}  (or however you installed it originally)"
        )
        click.echo("  3. Run: tm <url>")

    else:
        click.echo(
            "⚠️ Some dependencies are missing. Please install them first.", err=True
        )
        sys.exit(1)


@cli.command("update")
@click.option(
    "--no-install",
    is_flag=True,
    help="Only git pull; never run pip install -e .",
)
@click.option(
    "--reinstall",
    is_flag=True,
    help="Always run pip install -e . (default: only when pyproject.toml changed)",
)
def update(no_install: bool, reinstall: bool):
    """Pull the latest code and reinstall this editable checkout."""
    from .self_update import project_root, update_checkout

    root = project_root()
    if root is None:
        click.echo(
            "❌ Could not find a track-manager source checkout.",
            err=True,
        )
        click.echo(
            "   This command only works for editable installs from git.",
            err=True,
        )
        click.echo("   Try: pip install --upgrade track-manager", err=True)
        sys.exit(1)

    click.echo(f"📦 Updating track-manager in {root}")
    click.echo()

    try:
        result = update_checkout(
            root,
            reinstall=not no_install,
            force_reinstall=reinstall,
        )
    except subprocess.CalledProcessError as exc:
        click.echo(
            f"\n❌ Update failed: {exc.cmd[0]} exited with {exc.returncode}", err=True
        )
        sys.exit(exc.returncode or 1)
    except RuntimeError as exc:
        click.echo(f"\n❌ {exc}", err=True)
        sys.exit(1)

    click.echo()
    click.echo("✅ Update complete")
    if not result.reinstall_ran and not no_install:
        click.echo(
            "   Dependencies unchanged; skipped pip install "
            "(code is already live in editable mode)."
        )
    elif result.reinstall_deferred:
        click.echo(
            "   pip install is finishing in the background "
            "(Windows cannot replace tm.exe while it is running)."
        )
    click.echo("   Run `tm check-setup` to sync config and verify dependencies.")


@cli.command("upgrade")
@click.option(
    "--threshold",
    "-t",
    type=int,
    default=256,
    help="Quality threshold in kbps — tracks below this are candidates (default: 256)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Library directory to scan (overrides config)",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show candidates without downloading anything",
)
@click.option("--yes", "-y", is_flag=True, help="Skip per-track confirmation")
@click.option("--verbose", "-v", is_flag=True, help="Show extra detail during download")
@click.option(
    "--retry-attempted",
    "-r",
    is_flag=True,
    help="Also include tracks that have been attempted before "
    "(default: only never-attempted tracks)",
)
@click.option(
    "--max-attempts",
    "-m",
    type=int,
    default=None,
    help="Include tracks attempted up to N times (overrides --retry-attempted; "
    "0 = never-attempted only, omit for no limit)",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Upgrade at most N tracks this run (after filtering)",
)
def upgrade(
    threshold: int,
    output: Optional[str],
    dry_run: bool,
    yes: bool,
    verbose: bool,
    retry_attempted: bool,
    max_attempts: Optional[int],
    limit: Optional[int],
):
    """Upgrade low/mid quality tracks to higher quality versions.

    Scans the library for tracks whose bitrate is below THRESHOLD kbps and
    that have a TRACK_URL provenance tag (set automatically by track-manager).
    Each candidate is re-downloaded from its original source URL and the file
    is replaced in-place so Rekordbox cue points are preserved.

    If the upgraded file uses a different extension (e.g. mp3 → m4a) you will
    need to relocate the track in Rekordbox once — all cue points survive.
    """
    from .quality import format_bitrate
    from .upgrade import find_upgradeable_tracks, upgrade_track

    config = Config()
    library_dir = Path(output) if output else config.output_dir

    # --max-attempts wins if both are given; otherwise --retry-attempted means
    # "no upper bound" and the default is "only never-attempted tracks".
    if max_attempts is not None:
        attempts_filter: Optional[int] = max_attempts
    elif retry_attempted:
        attempts_filter = None
    else:
        attempts_filter = 0

    filter_desc = (
        "no attempt limit"
        if attempts_filter is None
        else (
            f"up to {attempts_filter} prior attempt(s)"
            if attempts_filter > 0
            else "never-attempted only"
        )
    )
    click.echo(
        f"🔍 Scanning {library_dir} for tracks below {threshold} kbps "
        f"({filter_desc})..."
    )
    click.echo()

    candidates = find_upgradeable_tracks(
        library_dir, threshold_kbps=threshold, max_attempts=attempts_filter
    )

    total_matches = len(candidates)
    truncated = False
    if limit is not None and limit >= 0 and len(candidates) > limit:
        candidates = candidates[:limit]
        truncated = True

    if not candidates:
        click.echo(
            "✅ No upgradeable tracks found (all tracks meet quality threshold, "
            "lack TRACK_URL, or have already been attempted — pass "
            "--retry-attempted to include them)"
        )
        return

    if truncated:
        click.echo(
            f"Found {total_matches} matching track(s); showing first {len(candidates)} "
            f"(--limit {limit}):\n"
        )
    else:
        click.echo(f"Found {len(candidates)} track(s) to upgrade:\n")
    for i, c in enumerate(candidates, 1):
        attempts_str = f"  ({c['attempts']}× tried)" if c.get("attempts") else ""
        click.echo(
            f"  {i:>3}. [{format_bitrate(c['bitrate']):>10}]{attempts_str} "
            f"{c['path'].name}"
        )
        if verbose:
            click.echo(f"       URL: {c['track_url']}")
    click.echo()

    if dry_run:
        click.echo("ℹ️  Dry-run mode — nothing downloaded.")
        return

    if not yes and not click.confirm(
        f"Upgrade all {len(candidates)} track(s)?", default=True
    ):
        click.echo("Aborted.")
        return

    click.echo()

    # Create a single Downloader for the session so that spotdl's global
    # Spotify client is only initialised once across all tracks.
    from .downloader import Downloader

    shared_downloader = Downloader(config)

    ok = 0
    failed = 0
    for c in candidates:
        click.echo(f"⬆️  {c['path'].name}  ({format_bitrate(c['bitrate'])})")

        if not yes:
            if not click.confirm("   Upgrade this track?", default=True):
                click.echo("   Skipped.")
                click.echo()
                continue

        success, msg = upgrade_track(
            c["path"],
            c["track_url"],
            config,
            verbose=verbose,
            downloader=shared_downloader,
        )

        if success:
            click.echo(f"   ✅ {msg}")
            ok += 1
        else:
            click.echo(f"   ❌ {msg}", err=True)
            failed += 1

        click.echo()

    click.echo(f"Done — {ok} upgraded, {failed} failed.")


@cli.command("migrate-to-aiff")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    help="Library directory to migrate (overrides config)",
)
@click.option(
    "--dry-run", "-n", is_flag=True, help="Show what would be migrated without doing it"
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Migrate at most N files (useful for a small test run before going all-in)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt")
def migrate_to_aiff(
    output: Optional[str], dry_run: bool, limit: Optional[int], yes: bool
):
    """Re-encode every non-AIFF file in the library to AIFF in place.

    Each original is moved to a hidden ``.tm-migration-backup/`` folder
    next to the library so Rekordbox stops indexing it (but the file is
    still on disk, recoverable). The new AIFF lives in the library with
    the same filename stem.

    \b
    Rekordbox doesn't bulk-relink across extension changes, so after
    migration you'll need to either:
      1. Right-click each missing track → "Locate Missing Track" → pick
         the new .aiff (per-track manual; tedious for large libraries).
      2. Export your collection XML, edit the file paths to .aiff, and
         re-import (preserves cue points; works for bulk).

    \b
    Strongly recommended: run with --limit 5 first, verify the new files
    play correctly and that Rekordbox can locate them with cue points
    intact, then re-run on the full library.
    """
    from . import migrate as tm_migrate

    config = Config()
    library_dir = Path(output) if output else config.output_dir

    if not library_dir.exists():
        click.echo(f"❌ Library directory not found: {library_dir}", err=True)
        sys.exit(1)

    candidates = tm_migrate.find_migratable_files(library_dir)
    if limit is not None:
        candidates = candidates[:limit]

    if not candidates:
        click.echo(
            "✅ Nothing to migrate (all files already AIFF or no audio files found)."
        )
        return

    current_total = sum(p.stat().st_size for p in candidates)
    projected_total = sum(tm_migrate.projected_aiff_size(p) for p in candidates)
    delta = projected_total - current_total
    peak = current_total + projected_total
    delta_sign = "+" if delta >= 0 else "-"

    click.echo(f"Library: {library_dir}")
    click.echo(
        f"Found {len(candidates)} non-AIFF file(s){' (limited)' if limit else ''}"
    )
    click.echo(f"  Current size:   {tm_migrate.fmt_size(current_total)}")
    click.echo(
        f"  Projected AIFF: {tm_migrate.fmt_size(projected_total)} ({delta_sign}{tm_migrate.fmt_size(abs(delta))})"
    )
    click.echo(
        f"  Peak transient: {tm_migrate.fmt_size(peak)} (originals kept in .tm-migration-backup/ until you delete them)"
    )
    click.echo()

    if dry_run:
        click.echo("Dry run — files that would be migrated:")
        for p in candidates:
            click.echo(f"  {p.name}")
        return

    if not yes:
        if not click.confirm(
            f"Re-encode {len(candidates)} file(s) to AIFF? Originals will be moved to "
            f".tm-migration-backup/ inside the library.",
            default=False,
        ):
            click.echo("Aborted.")
            return

    succeeded = 0
    failed = 0
    skipped = 0
    failures: list[tuple[Path, str]] = []

    for i, p in enumerate(candidates, 1):
        click.echo(f"[{i:>{len(str(len(candidates)))}}/{len(candidates)}] {p.name}")
        try:
            ok, msg = tm_migrate.migrate_one(p)
        except KeyboardInterrupt:
            click.echo(
                "\n⚠️ Interrupted by user — stopping. Already-migrated files are kept."
            )
            break
        except Exception as e:
            ok, msg = False, f"unexpected error: {e}"

        if ok:
            click.echo(f"   ✅ {msg}")
            succeeded += 1
        elif msg.startswith(("already", "target already")):
            click.echo(f"   ⏭️  {msg}")
            skipped += 1
        else:
            click.echo(f"   ❌ {msg}", err=True)
            failed += 1
            failures.append((p, msg))

    click.echo()
    click.echo("━" * 60)
    click.echo(f"Done — {succeeded} migrated, {skipped} skipped, {failed} failed.")
    if failures:
        click.echo()
        click.echo("Failed files (left untouched):")
        for p, msg in failures:
            click.echo(f"  {p.name}: {msg}")
    click.echo()
    click.echo("Next steps for Rekordbox (CLOSE Rekordbox first):")
    click.echo("  1. Quit Rekordbox AND the rekordboxAgent helper process.")
    click.echo("  2. tm rekordbox-update-paths --dry-run    # preview the DB changes")
    click.echo(
        "  3. tm rekordbox-update-paths             # apply (creates a master.db backup)"
    )
    click.echo("  4. Open Rekordbox; verify cue points + beat grids on a few tracks.")
    click.echo(
        f"  5. Once verified, delete {library_dir / tm_migrate.BACKUP_DIRNAME} to reclaim disk."
    )


@cli.command("rekordbox-list")
@click.option(
    "--library-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Library directory (default: from config). Tracks inside this dir are flagged.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show extension breakdown for every track, not just library tracks",
)
@click.option(
    "--show-outside",
    is_flag=True,
    help="Also list every track outside the library (sample of 20)",
)
def rekordbox_list(library_dir: Optional[str], show_all: bool, show_outside: bool):
    """Read-only audit of Rekordbox's master.db.

    Lists what Rekordbox knows about, broken down by file extension and
    whether each track lives inside the configured library directory.
    Run this before ``rekordbox-update-paths`` to sanity-check what would
    be touched.

    Requires Rekordbox to be CLOSED (the database is locked while it runs).
    """
    from . import rekordbox_db as tm_rb

    config = Config()
    library = (
        Path(library_dir).resolve() if library_dir else config.output_dir.resolve()
    )

    procs = tm_rb.running_rekordbox_processes()
    if procs:
        click.echo("❌ Rekordbox is running. Quit it before running this:", err=True)
        for p in procs:
            click.echo(f"   • pid {p.pid}: {p.command}", err=True)
        sys.exit(1)

    try:
        tracks = tm_rb.list_tracks(library)
    except ImportError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Could not open Rekordbox database: {e}", err=True)
        click.echo("   Confirm Rekordbox is fully closed and try again.", err=True)
        sys.exit(1)

    inside = [t for t in tracks if t.inside_library]
    outside = [t for t in tracks if not t.inside_library]

    click.echo(f"Library: {library}")
    click.echo(f"Tracks in master.db total:   {len(tracks)}")
    click.echo(f"  Inside library:            {len(inside)}")
    click.echo(f"  Outside library:           {len(outside)}")
    click.echo()

    by_ext: dict[str, int] = {}
    target = tracks if show_all else inside
    label = "all tracks" if show_all else "library tracks only"
    for t in target:
        ext = t.folder_path.suffix.lower() or "(no ext)"
        by_ext[ext] = by_ext.get(ext, 0) + 1

    click.echo(f"By file extension ({label}):")
    for ext, count in sorted(by_ext.items()):
        click.echo(f"  {ext:10s}  {count}")

    if show_outside and outside:
        click.echo()
        click.echo(f"Sample of tracks outside library (first 20 of {len(outside)}):")
        for t in outside[:20]:
            click.echo(f"  {t.folder_path}")


@cli.command("rekordbox-update-paths")
@click.option(
    "--library-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Library directory (default: from config)",
)
@click.option(
    "--dry-run", "-n", is_flag=True, help="Show what would change without writing"
)
@click.option(
    "--no-backup", is_flag=True, help="Skip the master.db backup (NOT recommended)"
)
@click.option(
    "--kill-agent",
    is_flag=True,
    help="Auto-kill rekordboxAgent if it's running (the GUI app must still be quit manually)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt")
def rekordbox_update_paths(
    library_dir: Optional[str],
    dry_run: bool,
    no_backup: bool,
    kill_agent: bool,
    yes: bool,
):
    """Update Rekordbox's master.db so each library track points at its .aiff.

    Run this AFTER ``tm migrate-to-aiff``. Rekordbox MUST be quit before
    this runs (the database is locked while it runs). The
    ``rekordboxAgent`` background helper persists after you quit the GUI;
    pass ``--kill-agent`` to terminate it automatically.

    Cue points, beat grids, playlists, ratings, color tags, and play
    counts are all keyed by ContentID; the path update doesn't touch any
    of them.

    A timestamped backup of master.db is created before any write unless
    ``--no-backup`` is passed.
    """
    from . import rekordbox_db as tm_rb

    config = Config()
    library = (
        Path(library_dir).resolve() if library_dir else config.output_dir.resolve()
    )

    # ------------------------------------------------------------------
    # Pre-flight: confirm Rekordbox + agent are both not running.
    # ------------------------------------------------------------------
    click.echo("🔍 Checking for running Rekordbox processes...")
    procs = tm_rb.running_rekordbox_processes()
    if procs:
        for p in procs:
            tag = (
                " (agent)"
                if p.is_agent
                else (
                    " (main app)"
                    if "rekordbox 7" in p.command.lower()
                    or "rekordbox.app" in p.command.lower()
                    else ""
                )
            )
            click.echo(f"   • pid {p.pid}: {p.command}{tag}")

        agents = [p for p in procs if p.is_agent]
        non_agents = [p for p in procs if not p.is_agent]

        if non_agents:
            click.echo(
                "❌ The Rekordbox GUI app is running. Quit it (⌘Q) before continuing — "
                "the GUI app holds unsaved collection state and we won't auto-kill it.",
                err=True,
            )
            sys.exit(1)

        if agents:
            if not kill_agent:
                click.echo(
                    "❌ rekordboxAgent is running. It will hold the database lock until "
                    "it exits.\n"
                    "   Re-run with --kill-agent to terminate it automatically, or "
                    "quit it manually:\n"
                    f"     kill {' '.join(str(p.pid) for p in agents)}",
                    err=True,
                )
                sys.exit(1)

            click.echo("🛑 Terminating rekordboxAgent…")
            ok, remaining = tm_rb.kill_rekordbox_agent(timeout=10.0)
            if not ok:
                click.echo(
                    "❌ rekordboxAgent did not exit within 10s. Try killing it manually:\n"
                    f"   kill -9 {' '.join(str(p.pid) for p in remaining if p.is_agent)}",
                    err=True,
                )
                sys.exit(1)
            if remaining:
                # Non-agent rekordbox process still up — refuse.
                click.echo(
                    "❌ A Rekordbox process is still running after killing the agent:",
                    err=True,
                )
                for p in remaining:
                    click.echo(f"   • pid {p.pid}: {p.command}", err=True)
                sys.exit(1)
            click.echo("   ✅ rekordboxAgent stopped.")
    else:
        click.echo("   ✅ No Rekordbox processes running.")
    click.echo()

    try:
        result = tm_rb.update_paths_to_aiff(library, dry_run=True, backup=False)
    except ImportError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Failed to plan updates: {e}", err=True)
        sys.exit(1)

    click.echo(f"Library: {library}")
    click.echo(f"Tracks to update:           {len(result.planned)}")
    click.echo(f"Skipped (outside library):  {len(result.skipped_outside)}")
    click.echo(f"Skipped (already AIFF):     {len(result.skipped_already_aiff)}")
    click.echo(f"Skipped (no .aiff on disk): {len(result.skipped_no_aiff)}")
    click.echo()

    if not result.planned:
        click.echo("Nothing to do. Did you run `tm migrate-to-aiff` first?")
        return

    if result.skipped_no_aiff:
        click.echo("Tracks Rekordbox knows about but with no migrated .aiff:")
        for t in result.skipped_no_aiff[:10]:
            click.echo(f"  ContentID={t.content_id}: {t.folder_path.name}")
        if len(result.skipped_no_aiff) > 10:
            click.echo(f"  … and {len(result.skipped_no_aiff) - 10} more")
        click.echo()

    if dry_run:
        click.echo("First 10 planned updates:")
        for plan in result.planned[:10]:
            click.echo(f"  {plan.old_path.name}  →  {plan.new_path.name}")
        if len(result.planned) > 10:
            click.echo(f"  … and {len(result.planned) - 10} more")
        click.echo()
        click.echo("Dry run — no changes written. Re-run without --dry-run to apply.")
        return

    if not yes:
        msg = f"Update {len(result.planned)} tracks in master.db? "
        msg += (
            "A timestamped backup of master.db will be created."
            if not no_backup
            else "⚠️  NO BACKUP will be made (--no-backup)."
        )
        if not click.confirm(msg, default=False):
            click.echo("Aborted.")
            return

    try:
        result = tm_rb.update_paths_to_aiff(
            library, dry_run=False, backup=not no_backup
        )
    except Exception as e:
        click.echo(f"❌ Update failed: {e}", err=True)
        sys.exit(1)

    click.echo()
    click.echo(f"✅ Updated {len(result.planned)} tracks in master.db.")
    if result.backup_path:
        click.echo(f"   Backup: {result.backup_path}")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. Open Rekordbox.")
    click.echo(
        "  2. Spot-check a few tracks: cue points + beat grid intact, audio plays from .aiff."
    )
    if result.backup_path:
        click.echo("  3. If anything looks wrong, restore the backup:")
        click.echo(f'       cp "{result.backup_path}" "{tm_rb.MASTER_DB_PATH}"')
        click.echo("     (Rekordbox must be closed when restoring.)")


@cli.command("rekordbox-rewrite")
@click.argument("input_xml", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False),
    default=None,
    help="Output XML path (default: <input>.aiff.xml next to the input)",
)
@click.option(
    "--library-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Library directory (default: from config)",
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Show what would change without writing the output",
)
def rekordbox_rewrite(
    input_xml: str, output: Optional[str], library_dir: Optional[str], dry_run: bool
):
    """Rewrite a Rekordbox XML export so each <TRACK> points at its migrated AIFF.

    Run this AFTER ``tm migrate-to-aiff``. Cue points, beat grids, loops,
    and per-track metadata live inside the XML and are keyed by TrackID,
    so re-importing the rewritten XML preserves all of them while only
    updating the file paths and audio properties.

    \b
    Workflow:
      1. tm migrate-to-aiff
      2. In Rekordbox: Preferences → Advanced → enable "rekordbox xml"
      3. File → Export Collection in xml format → save as collection.xml
      4. tm rekordbox-rewrite collection.xml
      5. In Rekordbox: File → Library → Import Library → pick collection.aiff.xml
      6. Verify a few tracks: cue points + beat grid intact, audio plays.

    Tracks whose Location resolves outside the library directory are
    left alone, so this is safe to run on a collection that mixes
    track-manager content with manually-imported folders.
    """
    from . import rekordbox_xml as tm_rb

    config = Config()
    library = (
        Path(library_dir).resolve() if library_dir else config.output_dir.resolve()
    )

    input_path = Path(input_xml).resolve()
    if output is None:
        output_path = tm_rb.default_output_path(input_path)
    else:
        output_path = Path(output).resolve()

    click.echo(f"Library: {library}")
    click.echo(f"Reading: {input_path}")
    click.echo(f"{'Would write' if dry_run else 'Writing'}: {output_path}")
    click.echo()

    if dry_run:
        import tempfile

        # Run the rewriter against a throwaway path so we get an accurate
        # picture of what would happen, including AIFF probe results.
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tmp:
            result = tm_rb.rewrite_xml(input_path, Path(tmp.name), library)
    else:
        result = tm_rb.rewrite_xml(input_path, output_path, library)

    click.echo(f"Updated:                   {len(result.updated)}")
    click.echo(f"Skipped (already AIFF):    {len(result.skipped_already_aiff)}")
    click.echo(f"Skipped (no AIFF on disk): {len(result.skipped_no_aiff)}")
    click.echo(f"Skipped (outside library): {len(result.skipped_outside_library)}")
    click.echo(f"Parse errors:              {len(result.parse_errors)}")

    if result.skipped_no_aiff:
        click.echo()
        click.echo("Tracks that needed migration but no .aiff was found:")
        for tid, p in result.skipped_no_aiff[:10]:
            click.echo(f"  TrackID={tid}: {p.name}")
        if len(result.skipped_no_aiff) > 10:
            click.echo(f"  … and {len(result.skipped_no_aiff) - 10} more")
        click.echo("These tracks were left pointing at their original Location.")

    if result.parse_errors:
        click.echo()
        click.echo("Parse errors (these tracks were left untouched):")
        for err in result.parse_errors[:10]:
            click.echo(f"  {err}")
        if len(result.parse_errors) > 10:
            click.echo(f"  … and {len(result.parse_errors) - 10} more")

    if not dry_run and result.updated:
        click.echo()
        click.echo("Next steps in Rekordbox:")
        click.echo("  1. File → Library → Import Library…")
        click.echo(f"  2. Select: {output_path}")
        click.echo("  3. Confirm the import dialog.")
        click.echo(
            "  4. Verify a few tracks: cue points + beat grid intact, audio plays."
        )


@cli.command("rate-stats")
def rate_stats():
    """Show API rate limit statistics."""
    from .rate_limiter import get_rate_limit_stats

    click.echo("📊 API Rate Limit Statistics")
    click.echo()

    stats = get_rate_limit_stats()

    for service, data in stats.items():
        service_name = service.replace("_", " ").title()
        click.echo(f"🔹 {service_name}:")
        click.echo(f"   Calls (last minute): {data['calls_last_minute']}")
        click.echo(
            f"   Tokens available: {data['tokens_available']}/{data['burst_size']}"
        )
        click.echo(f"   Rate limit: {data['rate']:.2f} calls/sec")
        click.echo()


@cli.command("show-metadata")
@click.argument("file", type=click.Path(exists=True))
def show_metadata(file: str):
    """Show all metadata for a track.

    Displays comprehensive metadata including:
    - File information (format, size)
    - Audio properties (duration, bitrate, sample rate)
    - All metadata tags (title, artist, album, etc.)
    - Provenance information (original source, bitrate)
    """
    from .metadata import show_full_metadata

    show_full_metadata(Path(file))


@cli.command("completions")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.option(
    "--print",
    "print_only",
    is_flag=True,
    help="Print the completion script to stdout instead of installing",
)
@click.pass_context
def completions(ctx: click.Context, shell: str, print_only: bool) -> None:
    """Install shell tab-completion for this checkout.

    By default writes an untracked script under ``completions/`` in the repo
    (added to ``.gitignore``) and adds a marked block to your shell rc file
    that sources it.

    \b
    Examples:
      tm completions zsh
      tm completions zsh --print   # stdout only, no install
    """
    from . import shell_completions as tm_completions

    prog_name = ctx.find_root().info_name or "tm"

    if print_only:
        script = tm_completions.generate_script(
            shell=shell, prog_name=prog_name, cli_group=cli
        )
        header = f"\n# tm shell completions (generated by: {prog_name} completions {shell})\n"
        click.echo(f"{header}{script}", nl=False)
        return

    try:
        result = tm_completions.install(shell=shell, prog_name=prog_name, cli_group=cli)
    except click.ClickException:
        raise
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"✅ Wrote {result.completion_file}")
    click.echo(f"✅ Updated {result.shell_rc}")
    click.echo(f"   ({result.completion_file.name} is gitignored via completions/)")
    click.echo()
    click.echo("Restart your shell or run:")
    click.echo(f"  source {result.shell_rc}")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
