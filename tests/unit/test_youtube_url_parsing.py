#!/usr/bin/env python3
"""Test YouTube URL parsing logic."""

from urllib.parse import urlparse, parse_qs
from typing import Literal, Optional

URLType = Literal["video", "playlist", "video_in_playlist"]


def parse_youtube_url(url: str) -> tuple[URLType, Optional[str], Optional[str]]:
    """Parse a YouTube URL to determine its type.
    
    Args:
        url: YouTube URL to parse
        
    Returns:
        Tuple of (url_type, video_id, playlist_id)
        - url_type: "video", "playlist", or "video_in_playlist"
        - video_id: Video ID if present
        - playlist_id: Playlist ID if present
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    
    # Extract IDs
    video_id = query.get("v", [None])[0]
    playlist_id = query.get("list", [None])[0]
    
    # Check path for playlist URLs
    if "/playlist" in parsed.path and playlist_id:
        return "playlist", None, playlist_id
    
    # Video with playlist context
    if video_id and playlist_id:
        return "video_in_playlist", video_id, playlist_id
    
    # Plain video
    if video_id:
        return "video", video_id, None
    
    # Fallback - treat as video
    return "video", None, None


# Test cases
test_urls = [
    # Regular YouTube - Plain video URLs
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
    ("https://youtu.be/dQw4w9WgXcQ", "video", None, None),  # Short URL (no query params)
    
    # Regular YouTube - Plain playlist URLs
    ("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf", "playlist", None, "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"),
    
    # Regular YouTube - Mixed URLs (video in playlist context)
    ("https://www.youtube.com/watch?v=mgomHKRTlVc&list=PLdo7RntGOYQeqbdSB07bu1tOU05HWFZS9&index=1", "video_in_playlist", "mgomHKRTlVc", "PLdo7RntGOYQeqbdSB07bu1tOU05HWFZS9"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtest123&index=5", "video_in_playlist", "dQw4w9WgXcQ", "PLtest123"),
    
    # YouTube Music - Plain video URLs
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "video", "dQw4w9WgXcQ", None),
    ("https://music.youtube.com/watch?v=abc123xyz", "video", "abc123xyz", None),
    
    # YouTube Music - Plain playlist URLs
    ("https://music.youtube.com/playlist?list=RDCLAK5uy_test123", "playlist", None, "RDCLAK5uy_test123"),
    ("https://music.youtube.com/playlist?list=PLtest456", "playlist", None, "PLtest456"),
    
    # YouTube Music - Mixed URLs (video in playlist context)
    ("https://music.youtube.com/watch?v=abc123&list=RDAMVMabc123", "video_in_playlist", "abc123", "RDAMVMabc123"),
    ("https://music.youtube.com/watch?v=xyz789&list=RDCLAK5uy_test&index=3", "video_in_playlist", "xyz789", "RDCLAK5uy_test"),
]

print("Testing YouTube URL parsing...")
print("=" * 80)

all_passed = True
for url, expected_type, expected_video_id, expected_playlist_id in test_urls:
    url_type, video_id, playlist_id = parse_youtube_url(url)
    
    passed = (
        url_type == expected_type and
        video_id == expected_video_id and
        playlist_id == expected_playlist_id
    )
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status}")
    print(f"URL: {url}")
    print(f"Expected: type={expected_type}, video_id={expected_video_id}, playlist_id={expected_playlist_id}")
    print(f"Got:      type={url_type}, video_id={video_id}, playlist_id={playlist_id}")
    
    if not passed:
        all_passed = False

print("\n" + "=" * 80)
if all_passed:
    print("✅ All tests passed!")
    print(f"\nTested {len(test_urls)} URLs across:")
    print("  - Regular YouTube (videos, playlists, mixed)")
    print("  - YouTube Music (videos, playlists, mixed)")
else:
    print("❌ Some tests failed")
    import sys
    sys.exit(1)
