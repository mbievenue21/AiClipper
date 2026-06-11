"""URL source detection for reference clip downloads."""

from __future__ import annotations

import pytest

from worker.media.source_url import detect_source_type


def test_detect_youtube():
    assert detect_source_type("https://www.youtube.com/shorts/abc123") == "youtube"
    assert detect_source_type("https://youtu.be/abc123") == "youtube"


def test_detect_twitch():
    assert detect_source_type("https://www.twitch.tv/videos/123") == "twitch"
    assert detect_source_type("https://clips.twitch.tv/Foo-bar") == "twitch"


def test_detect_invalid():
    with pytest.raises(ValueError):
        detect_source_type("not-a-url")
