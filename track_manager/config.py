"""Configuration management for track-manager."""

import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    print("Error: PyYAML not installed", file=sys.stderr)
    print("Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


class Config:
    """Track manager configuration."""

    _instance = None

    def __new__(cls, config_path: Optional[Path] = None):
        """Singleton pattern for config."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton for testing."""
        cls._instance = None

    def __init__(self, config_path: Optional[Path] = None):
        """Load configuration from YAML file.

        Args:
            config_path: Optional explicit path to a config file. When omitted,
                falls back to ``<repo>/config.yaml`` next to the package.
        """
        if self._initialized:
            return

        self.config_path = config_path or Path(__file__).parent.parent / "config.yaml"
        self.config = self._load_config()
        self._initialized = True

    def _load_config(self) -> dict:
        """Load and parse config file."""
        if not self.config_path.exists():
            print(
                f"Error: Configuration file not found: {self.config_path}",
                file=sys.stderr,
            )
            print(
                "Copy config.example.yaml to config.yaml and customize", file=sys.stderr
            )
            sys.exit(1)

        with open(self.config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Expand home directory in paths
        self._expand_paths(config)
        return config

    def _expand_paths(self, config: dict):
        """Expand ~ in path values."""
        for key, value in config.items():
            if isinstance(value, str) and value.startswith("~"):
                config[key] = os.path.expanduser(value)
            elif isinstance(value, dict):
                self._expand_paths(value)

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-separated key."""
        keys = key.split(".")
        value = self.config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    @property
    def output_dir(self) -> Path:
        """Get output directory path."""
        return Path(self.get("output_dir"))

    @property
    def failed_log(self) -> Path:
        """Get failed downloads log path."""
        path = self.get("failed_log")
        if path:
            return Path(path)
        return self.config_path.parent / "failed-downloads.txt"

    @property
    def spotdl_path(self) -> Optional[str]:
        """Get spotdl executable path."""
        path = self.get("spotdl.path", "")
        return path if path else None

    @property
    def default_format(self) -> str:
        """Get default download format."""
        return self.get("downloads.default_format", "auto")

    @property
    def playlist_threshold(self) -> int:
        """Get playlist confirmation threshold."""
        return self.get("downloads.playlist_confirmation_threshold", 50)

    @property
    def duplicate_handling(self) -> str:
        """Get duplicate handling mode."""
        return self.get("duplicates.handling", "interactive")

    @property
    def dabmusic_email(self) -> Optional[str]:
        """Get DAB Music email."""
        return self.get("dabmusic.email")

    @property
    def dabmusic_password(self) -> Optional[str]:
        """Get DAB Music password."""
        return self.get("dabmusic.password")

    @property
    def dabmusic_endpoint(self) -> str:
        """Get DAB Music endpoint."""
        return self.get("dabmusic.endpoint", "https://dabmusic.xyz")

    @property
    def soulseek_username(self) -> Optional[str]:
        """Soulseek username (empty/None disables Soulseek fallback)."""
        value = self.get("soulseek.username", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @property
    def soulseek_password(self) -> Optional[str]:
        """Soulseek password (empty/None disables Soulseek fallback)."""
        value = self.get("soulseek.password", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @property
    def soulseek_binary(self) -> Optional[str]:
        """Optional explicit path to sockseek/sldl (else search PATH)."""
        value = self.get("soulseek.binary", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @property
    def soulseek_timeout_seconds(self) -> int:
        """Subprocess timeout for a single Soulseek download."""
        raw = self.get("soulseek.timeout_seconds", 180)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 180

    @property
    def soulseek_length_tol_seconds(self) -> int:
        """Allowed duration delta (seconds) when matching Soulseek results."""
        raw = self.get("soulseek.length_tol_seconds", 3)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 3

    @property
    def soulseek_enabled(self) -> bool:
        """True when Soulseek credentials are configured."""
        return bool(self.soulseek_username and self.soulseek_password)

    @property
    def youtube_cookies_file(self) -> Optional[str]:
        """Path to Netscape-format cookies.txt for YouTube (age-restricted videos)."""
        path = self.get("youtube.cookies_file", "")
        return path if path else None

    @property
    def youtube_cookies_from_browser(self) -> Optional[str]:
        """Browser name to import YouTube cookies from (chrome, firefox, ...)."""
        name = self.get("youtube.cookies_from_browser", "")
        return name if name else None

    @property
    def youtube_player_clients(self) -> Optional[list[str]]:
        """Override yt-dlp YouTube player clients (e.g. ['web', 'ios']).

        Leave unset so yt-dlp picks current defaults. Pinning old clients
        such as mweb or tv_embedded often causes HTTP 403. Returns None to
        let yt-dlp choose.
        """
        clients = self.get("youtube.player_clients")
        if isinstance(clients, str):
            clients = [c.strip() for c in clients.split(",") if c.strip()]
        if isinstance(clients, list) and clients:
            return [str(c) for c in clients]
        return None

    @property
    def youtube_po_token(self) -> Optional[str]:
        """GVS PO token in yt-dlp's "<client>.gvs+<token>" format."""
        token = self.get("youtube.po_token", "")
        return token if token else None

    @property
    def instagram_cookies_file(self) -> Optional[str]:
        """Path to Netscape-format cookies.txt for Instagram."""
        path = self.get("instagram.cookies_file", "")
        return path if path else None

    @property
    def instagram_cookies_from_browser(self) -> Optional[str]:
        """Browser to import Instagram cookies from.

        Falls back to ``youtube.cookies_from_browser`` when unset: browser
        cookies cover every site, so a profile already logged into Instagram
        authenticates reels without a second setting.
        """
        name = self.get("instagram.cookies_from_browser", "")
        if name:
            return name
        return self.youtube_cookies_from_browser

    @property
    def metadata_csv(self) -> Path:
        """Get metadata review CSV path."""
        csv_path = self.get("metadata_csv", "tracks-metadata-review.csv")
        # If relative path, resolve relative to config directory
        csv_path = Path(csv_path)
        if not csv_path.is_absolute():
            csv_path = self.config_path.parent / csv_path
        return csv_path

    @property
    def songlink_timeout(self) -> int:
        """Get song.link API timeout in seconds."""
        return self.get("songlink.timeout", 20)

    @property
    def songlink_max_retries(self) -> int:
        """Get song.link API max retry attempts."""
        return self.get("songlink.max_retries", 3)

    @property
    def songlink_api_key(self) -> Optional[str]:
        """Odesli/song.link API key (optional).

        Official docs: no key required; a valid key raises rate limits.
        When set, it is sent as the ``key`` query param. Env vars
        SONGLINK_API_KEY and ODESLI_API_KEY take precedence over
        ``songlink.api_key`` / ``songlink.key`` in config.yaml.
        """
        for env_name in ("SONGLINK_API_KEY", "ODESLI_API_KEY"):
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        key = self.get("songlink.api_key") or self.get("songlink.key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        return None
