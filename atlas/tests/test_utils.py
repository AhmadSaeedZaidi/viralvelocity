"""Tests for utility functions."""
import pytest

from atlas.utils import validate_channel_id, validate_youtube_id


def test_validate_youtube_id_valid():
    """Test valid YouTube video IDs."""
    assert validate_youtube_id("dQw4w9WgXcQ") is True
    assert validate_youtube_id("jNQXAC9IVRw") is True


def test_validate_youtube_id_invalid():
    """Test invalid YouTube video IDs."""
    assert validate_youtube_id("") is False
    assert validate_youtube_id("short") is False
    assert validate_youtube_id("toolongvideoidentifier") is False
    assert validate_youtube_id("invalid!@#") is False
    assert validate_youtube_id(None) is False


def test_validate_channel_id_valid():
    """Test valid YouTube channel IDs."""
    assert validate_channel_id("UCuAXFkgsw1L7xaCfnd5JJOw") is True


def test_validate_channel_id_invalid():
    """Test invalid YouTube channel IDs."""
    assert validate_channel_id("") is False
    assert validate_channel_id("UCshort") is False
    assert validate_channel_id("notstartwithuc_butcorrectlen") is False
    assert validate_channel_id("UC!@#$%^&*()_invalid!!") is False
    assert validate_channel_id(None) is False


