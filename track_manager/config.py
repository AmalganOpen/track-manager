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
        """Load configuration from YAML file."""
        if self._initialized:
            return

        if config_path is None:
            # Look for config.yaml in multiple locations
            config_path = self._find_config()

        self.config_path = config_path
        self.config = self._load_config()
        self._initialized = True

    def _find_config(self) -> Path:
        """Find config file in multiple locations."""
        # 1. Current working directory (for development)
        cwd_config = Path.cwd() / "config.yaml"
        if cwd_config.exists():
            return cwd_config

        # 2. Package directory (for installed package)
        pkg_dir = Path(__file__).parent.parent
        pkg_config = pkg_dir / "config.yaml"
        if pkg_config.exists():
            return pkg_config

        # 3. User home directory
        home_config = Path.home() / ".config" / "track-manager" / "config.yaml"
        if home_config.exists():
            return home_config

        # 4. Fallback to package directory (will show error with helpful message)
        return pkg_config

    def _load_config(self) -> dict:
        """Load and parse config file."""
        if not self.config_path.exists():
            # Try config.example.yaml as fallback
            example_path = self.config_path.parent / "config.example.yaml"
            if example_path.exists():
                print(
                    f"Warning: config.yaml not found, using config.example.yaml",
                    file=sys.stderr,
                )
                self.config_path = example_path
            else:
                print(
                    f"Error: Configuration file not found: {self.config_path}",
                    file=sys.stderr,
                )
                print(
                    f"Copy config.example.yaml to config.yaml and customize",
                    file=sys.stderr,
                )
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
        return Path(self.get("output_dir", "~/Music/tm/tracks"))

    @property
    def failed_log(self) -> Path:
        """Get failed downloads log path."""
        return Path(self.get("failed_log", "~/Music/tm/failed-downloads.txt"))

    @property
    def metadata_csv(self) -> Path:
        """Get metadata review CSV path."""
        return Path(self.get("metadata_csv", "~/Music/tm/tracks-metadata-review.csv"))

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
