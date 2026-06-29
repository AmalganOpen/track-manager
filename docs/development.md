# Developer Guide

Guide for contributing to track-manager. For end-user setup and usage, see the [README](../README.md).

## Prerequisites

- Python **3.10–3.12** (see `.python-version`; CI tests all three)
- **ffmpeg** — required at runtime for audio encoding/conversion
- Optional: Spotify API credentials in `config.yaml` (only needed to test playlist/album flows)

## Getting Started

```bash
git clone <repo-url>
cd track-manager

pip install -e ".[dev]"

cp config.example.yaml config.yaml   # edit paths/credentials as needed
pre-commit install                   # optional but recommended
```

Verify the install:

```bash
track-manager check-setup
pytest tests/unit/ -q
```

Entry points: `track-manager` and `tm` both invoke `track_manager.cli:main`.

## Project Layout

```
track-manager/
├── track_manager/       # Application code
│   ├── cli.py           # Click CLI (all commands)
│   ├── downloader.py    # Download orchestration
│   ├── pipeline.py      # Shared encode → tag → finalize path
│   ├── duplicates.py    # Duplicate detection
│   ├── metadata.py      # Tagging and CSV review workflow
│   ├── songlink.py      # song.link / ISRC resolution
│   ├── tidal_public.py  # TIDAL public API
│   ├── audio.py         # Encoding, probing, ffmpeg helpers
│   └── config.py        # YAML configuration
├── tests/
│   ├── unit/            # Fast, isolated tests (CI matrix)
│   └── integration/     # Workflow tests (CI, Python 3.12)
├── docs/                # Architecture and style docs
├── scripts/             # Maintenance / CI helper scripts
├── config.example.yaml  # Template config (commit this, not config.yaml)
└── pyproject.toml       # Package metadata, black/isort/pytest config
```

`config.yaml`, `completions/`, coverage output, and local logs are gitignored.

## Architecture

Downloads follow a quality-first pipeline:

1. **Resolve** — song.link / ISRC lookup (`songlink.py`, `tidal_public.py`)
2. **Fetch** — Source-specific handlers in `downloader.py`
3. **Finalize** — Encode, probe, tag, embed metadata blob (`pipeline.py`, `audio.py`, `blob.py`)

Deep dives:

| Topic                  | Document                                                                       |
| ---------------------- | ------------------------------------------------------------------------------ |
| Full download flow     | [download-process.md](download-process.md)                                     |
| Duplicate detection    | [duplicate-detection-fingerprinting.md](duplicate-detection-fingerprinting.md) |
| CLI output conventions | [cli-style-guide.md](cli-style-guide.md)                                       |
| Track quality metadata | [track-quality.md](track-quality.md)                                           |
| TIDAL API endpoints    | [tidal-endpoints.md](tidal-endpoints.md)                                       |

When adding CLI output, follow [cli-style-guide.md](cli-style-guide.md) (emoji set, spacing, message categories).

## Tests

```bash
# All tests (with coverage — see pytest.ini / pyproject.toml)
pytest

# Match CI jobs
pytest tests/unit/ -v
pytest tests/integration/ -v

# Coverage report (HTML written to htmlcov/)
pytest --cov=track_manager --cov-report=html

# Wrapper script (passes through extra pytest args)
./run_tests.sh tests/unit/test_config_reader.py -v
```

**Unit tests** (`tests/unit/`) should stay fast and avoid network calls. **Integration tests** (`tests/integration/`) cover multi-step workflows (CLI, metadata CSV, duplicate detection).

Prefer integration tests over heavy mocking when behavior spans several modules.

## Code Style

Formatting is enforced with **black** and **isort** (profile `black`, line length 88). Config lives in `pyproject.toml`.

```bash
# Format before committing
black track_manager/ tests/
isort track_manager/ tests/

# Check without modifying (what CI runs)
black --check track_manager/ tests/
isort --check-only --profile black track_manager/ tests/
```

### Pre-commit

```bash
pip install pre-commit   # included in .[dev]
pre-commit install
pre-commit run --all-files   # run all hooks manually
```

Hooks mirror CI: black and isort on Python files.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on pushes to `main`/`develop` and PRs to `main`:

| Job             | What it does                                       |
| --------------- | -------------------------------------------------- |
| **test**        | Unit tests on Python 3.10, 3.11, 3.12              |
| **integration** | Integration tests on 3.12                          |
| **lint**        | `black --check`, `isort --check-only`, bandit scan |
| **build**       | `python -m build` + `twine check`                  |

Run the same checks locally before opening a PR:

```bash
pip install -e ".[dev]"
pytest tests/unit/ -v && pytest tests/integration/ -v
black --check track_manager/ tests/
isort --check-only --profile black track_manager/ tests/
python -m build
```

## Contributing

1. Branch from `main` (use descriptive branch names, e.g. `feat/retry-failed-dry-run`).
2. Make focused changes; keep diffs small and readable.
3. Add or update tests for behavior you change.
4. Run formatters and tests locally (or rely on pre-commit).
5. Open a PR against `main` — CI must pass.

Do not commit `config.yaml`, credentials, or generated artifacts (`htmlcov/`, `dist/`, `.coverage`).
