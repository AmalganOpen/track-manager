# Integration Tests

This directory contains integration tests for Track Manager. These tests verify that different components work together correctly in real-world scenarios.

## Test Coverage

### test_download_workflow.py
Tests complete download workflows from URL to file creation:
- Source detection (Spotify, YouTube, SoundCloud, Instagram, Direct URLs)
- Routing to correct handlers
- Format selection (auto, mp3, m4a)
- Custom output directories
- Output directory creation

**Key scenarios:**
- Download from each supported source
- Format conversion and selection
- Output path handling

### test_duplicate_detection.py
Tests duplicate detection across various scenarios:
- Same format duplicates (MP3 vs MP3)
- Cross-format duplicates (MP3 vs M4A)
- Metadata normalization (removing junk patterns)
- Feature variations (feat., ft., featuring)
- Interactive handling (skip, keep, replace)
- Non-duplicates (remixes, different versions)
- Automated modes (skip, keep)

**Key scenarios:**
- Artist + title matching
- Junk pattern removal: "[Official Video]", "(Audio)", etc.
- Case-insensitive matching
- User interaction prompts
- Library-wide scanning

### test_metadata_workflow.py
Tests the complete metadata management workflow:
- Flagging files for review
- CSV file creation and management
- Applying corrections from CSV
- Partial corrections (only processing rows with suggestions)
- Dry run mode
- Metadata quality verification
- CSV cleanup when empty

**Key scenarios:**
- CSV creation on first flag
- Appending to existing CSV
- Updating file metadata
- Removing processed rows
- Keeping pending rows

### test_error_handling.py
Tests error handling and recovery:
- Failed download logging
- Continuation after failures
- Invalid URLs
- Missing metadata
- Corrupt audio files
- Network timeouts
- Permission errors
- Disk full scenarios
- API rate limits
- Multiple failures

**Key scenarios:**
- Graceful degradation
- Error logging
- Process continuation
- Multiple error types

### test_cli_integration.py
Tests CLI commands end-to-end:
- Download command with options
- Check-duplicates command
- Verify-metadata command
- Apply-metadata command
- Check-setup command
- Help and version output
- Error handling in CLI

**Key scenarios:**
- Command-line argument parsing
- Option handling
- User-facing output
- Exit codes

## Running Integration Tests

### Run all integration tests
```bash
pytest tests/integration/
```

### Run specific test file
```bash
pytest tests/integration/test_download_workflow.py
```

### Run specific test
```bash
pytest tests/integration/test_duplicate_detection.py::TestDuplicateDetection::test_cross_format_duplicate
```

### Run with verbose output
```bash
pytest tests/integration/ -v
```

### Run with coverage
```bash
pytest tests/integration/ --cov=track_manager --cov-report=html
```

## Test Strategy

### Mocking External Dependencies
Integration tests mock external API calls to:
- Make tests fast and reliable
- Avoid hitting real APIs during CI/CD
- Ensure repeatable test results
- Test error conditions

**What's mocked:**
- `spotdl` - Spotify downloads
- `yt-dlp` - YouTube/SoundCloud downloads
- `requests` - Direct URL downloads
- User input prompts (for duplicate handling)

**What's NOT mocked:**
- File system operations
- Metadata reading/writing with mutagen
- CSV file operations
- Internal logic and data flow

### Test Fixtures
Common fixtures are defined in `conftest.py`:
- `temp_output_dir` - Temporary directory for downloads
- `temp_config_file` - Temporary config file
- `test_config` - Config instance for testing
- `create_test_audio_file` - Factory for creating valid audio files with metadata
- `mock_spotify_download` - Mock spotdl
- `mock_ytdlp_download` - Mock yt-dlp
- `mock_requests_download` - Mock requests

### Test Audio Files
Tests create minimal valid audio files using mutagen:
- MP3 files with ID3 tags
- M4A files with MP4 tags
- Can specify artist and title metadata
- Used for testing metadata extraction and duplicate detection

## Writing New Integration Tests

### Best Practices

1. **Use fixtures** - Leverage existing fixtures from conftest.py
2. **Mock external calls** - Don't hit real APIs
3. **Test complete workflows** - Test multiple components together
4. **Test both success and failure** - Include error scenarios
5. **Clean up** - Use temporary directories (automatic with pytest)
6. **Descriptive names** - Test names should explain what they test
7. **Document scenarios** - Add docstrings explaining the test

### Example Test

```python
def test_my_workflow(self, test_config, temp_output_dir, create_test_audio_file):
    """Test description of what this tests."""
    # Setup
    test_file = temp_output_dir / "test.mp3"
    create_test_audio_file(test_file, artist="Artist", title="Song", format='mp3')
    
    # Execute
    with patch('some.module.function') as mock_fn:
        result = perform_operation(test_file)
    
    # Assert
    assert result is not None
    mock_fn.assert_called_once()
```

## Optional: Real API Tests

For manual validation with real APIs, you can create tests marked with `@pytest.mark.real_api`:

```python
@pytest.mark.real_api
def test_real_spotify_download():
    """Test with real Spotify API (run manually)."""
    # Test that actually hits Spotify
    pass
```

Run only these tests with:
```bash
pytest -m real_api
```

Skip them by default with:
```bash
pytest -m "not real_api"
```

## CI/CD Integration

Integration tests should run in CI/CD pipelines:
```bash
# In GitHub Actions, GitLab CI, etc.
pytest tests/integration/ --cov=track_manager --cov-report=xml
```

All external dependencies are mocked, so no API keys or credentials needed.

## Troubleshooting

### Tests fail with "module not found"
```bash
pip install -e .  # Install package in development mode
```

### Tests fail with "fixture not found"
Check that `conftest.py` is in the integration directory.

### Import errors for mutagen
```bash
pip install mutagen  # Required for test audio file creation
```

### Mocks not working
Verify the import path in the patch decorator matches your module structure.

## Future Enhancements

Potential additions to integration tests:
- Playlist download workflows (multiple files)
- Large playlist handling (threshold testing)
- Concurrent download scenarios
- Progress bar integration
- Config file loading and validation
- Cross-platform path handling
- Locale/encoding edge cases
