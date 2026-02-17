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

    def __init__(self):
        """Load configuration from YAML file."""
        if self._initialized:
            return

        self.config_path = Path(__file__).parent.parent / "config.yaml"
        self.config = self._load_config()
        self._initialized = True

    def _load_config(self) -> dict:
        """Load and parse config file."""
        if not self.config_path.exists():
            print(f"Error: Configuration file not found: {self.config_path}", file=sys.stderr)
            print("Copy config.example.yaml to config.yaml and customize", file=sys.stderr)
            sys.exit(1)

        with open(self.config_path) as f:
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
