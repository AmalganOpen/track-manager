"""Command-line interface for track-manager."""

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
    type=click.Choice(["auto", "m4a", "mp3"]),
    default="auto",
    help="Output format (default: auto)",
)
@click.option(
    "--output", "-o", type=click.Path(), help="Output directory (overrides config)"
)
@click.option(
    "--dumb",
    is_flag=True,
    help="Disable smart downloads (download directly from source)",
)
def download(url: str, format: str, output: Optional[str], dumb: bool):
    """Download track(s) from URL.

    Supports: Spotify, YouTube, SoundCloud, and direct URLs.

    Automatically downloads FLAC from DAB Music when available via ISRC lookup
    (unless --dumb is specified).
    """
    config = Config()

    # Override output directory if specified
    if output:
        output_dir = Path(output)
    else:
        output_dir = config.output_dir

    downloader = Downloader(config, output_dir, dumb=dumb)

    try:
        downloader.download(url, format)
    except KeyboardInterrupt:
        click.echo("\n⚠️ Download cancelled by user")
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@cli.command("check-duplicates")
@click.option(
    "--file",
    "-f",
    type=click.Path(exists=True),
    help="Check specific file for duplicates",
)
@click.option(
    "--output", "-o", type=click.Path(), help="Library directory to scan (overrides config)"
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
    "--output", "-o", type=click.Path(), help="Library directory to scan (overrides config)"
)
def verify_metadata(output: Optional[str]):
    """Verify metadata quality in library."""
    from .metadata import verify_library

    config = Config()
    library_dir = Path(output) if output else config.output_dir
    verify_library(library_dir)


@cli.command("check-quality")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed info for each file")
@click.option("--verbose", "-v", is_flag=True, help="Show outlier tracks (worst/best quality)")
@click.option(
    "--output", "-o", type=click.Path(), help="Library directory to scan (overrides config)"
)
def check_quality(detailed: bool, verbose: bool, output: Optional[str]):
    """Check audio quality of tracks in library."""
    from .quality import analyze_library

    config = Config()
    library_dir = Path(output) if output else config.output_dir
    analyze_library(library_dir, detailed, verbose)


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


def _collect_diff(user_map: Any, example_map: Any, removed: list, added: list, prefix: str = "") -> None:
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
        import click as _

        click.echo(f"✅ click: {click.__version__}")
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
                    import yaml
                    import copy
                    synced_cfg = copy.deepcopy(example_cfg)
                    _overlay_user_values(synced_cfg, user_cfg)
                    with open(config_path, "w") as f:
                        yaml.dump(synced_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                    click.echo("   Note: Comments were not preserved (ruamel.yaml not installed).")
                click.echo("✅ config.yaml updated")
                click.echo("   Re-run your install command to pick up any new dependencies:")
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
        click.echo(f"     pip install -e {config_path.parent}  (or however you installed it originally)")
        click.echo("  3. Run: tm <url>")

    else:
        click.echo(
            "⚠️ Some dependencies are missing. Please install them first.", err=True
        )
        sys.exit(1)


@cli.command("rate-stats")
def rate_stats():
    """Show API rate limit statistics."""
    from .rate_limiter import get_rate_limit_stats
    
    click.echo("📊 API Rate Limit Statistics")
    click.echo()
    
    stats = get_rate_limit_stats()
    
    for service, data in stats.items():
        service_name = service.replace('_', ' ').title()
        click.echo(f"🔹 {service_name}:")
        click.echo(f"   Calls (last minute): {data['calls_last_minute']}")
        click.echo(f"   Tokens available: {data['tokens_available']}/{data['burst_size']}")
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


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
