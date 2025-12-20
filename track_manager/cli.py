"""Command-line interface for track-manager."""

import shutil
import sys
from pathlib import Path
from typing import Optional

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
def check_duplicates(file: Optional[str]):
    """Check for duplicate tracks in library."""
    from .duplicates import check_file, scan_library

    config = Config()

    if file:
        check_file(Path(file), config.output_dir)
    else:
        scan_library(config.output_dir)


@cli.command("verify-metadata")
def verify_metadata():
    """Verify metadata quality in library."""
    from .metadata import verify_library

    config = Config()
    verify_library(config.output_dir)


@cli.command("check-quality")
@click.option("--detailed", "-d", is_flag=True, help="Show detailed info for each file")
@click.option("--verbose", "-v", is_flag=True, help="Show outlier tracks (worst/best quality)")
def check_quality(detailed: bool, verbose: bool):
    """Check audio quality of tracks in library."""
    from .quality import analyze_library

    config = Config()
    analyze_library(config.output_dir, detailed, verbose)


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


@cli.command("check-setup")
def check_setup():
    """Verify all dependencies are installed."""
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

        click.echo("✅ spotdl: Installed")
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

    # Check config
    try:
        config = Config()
        click.echo("✅ Configuration: config.yaml found")
    except SystemExit:
        click.echo("⚠️ Configuration: config.yaml not found")
        click.echo("   Copy config.example.yaml to config.yaml")

    click.echo()

    if all_ok:
        click.echo("🎉 All required dependencies are installed")
        click.echo()
        click.echo("Next steps:")
        click.echo("  1. Ensure config.yaml is set up")
        click.echo("  2. Run: track-manager download <url>")
        click.echo("  Tip: You can use the 'tm' alias instead of 'track-manager'")

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


@cli.command()
def init():
    """Initialize configuration file in ~/.config/track-manager/."""
    config_dir = Path.home() / ".config" / "track-manager"
    config_path = config_dir / "config.yaml"

    if config_path.exists():
        click.echo(f"✅ Config already exists: {config_path}")
        click.echo()
        click.echo("To reconfigure, either:")
        click.echo(f"  1. Edit: {config_path}")
        click.echo(f"  2. Delete and run 'track-manager init' again")
        return

    # Create config directory
    config_dir.mkdir(parents=True, exist_ok=True)

    # Copy example config
    example = Path(__file__).parent.parent / "config.example.yaml"
    if not example.exists():
        click.echo(f"❌ Example config not found at {example}", err=True)
        click.echo("This might happen with certain installation methods.", err=True)
        click.echo()
        click.echo("Manually create config.yaml with:", err=True)
        click.echo(f"  mkdir -p {config_dir}", err=True)
        click.echo(
            f"  curl https://raw.githubusercontent.com/AmalganOpen/track-manager/main/config.example.yaml -o {config_path}",
            err=True,
        )
        sys.exit(1)

    shutil.copy(example, config_path)

    click.echo(f"✅ Created config: {config_path}")
    click.echo()
    click.echo("📝 Configuration:")
    click.echo("  - YouTube/SoundCloud: Works immediately, no setup needed")
    click.echo("  - Spotify: Requires API credentials (optional)")
    click.echo()
    click.echo("🔑 To enable Spotify downloads:")
    click.echo("  1. Get credentials: https://developer.spotify.com/dashboard")
    click.echo("     (Create app → Copy Client ID & Secret)")
    click.echo(f"  2. Option A - Edit config: {config_path}")
    click.echo("  3. Option B - Set environment variables:")
    click.echo("     export SPOTIPY_CLIENT_ID='your_id'")
    click.echo("     export SPOTIPY_CLIENT_SECRET='your_secret'")
    click.echo()
    click.echo("✅ Ready! Try: track-manager download <url>")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
