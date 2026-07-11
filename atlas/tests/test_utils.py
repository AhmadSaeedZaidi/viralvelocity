"""Tests for utility functions."""

import asyncio

import pytest
from atlas.utils import (
    KeyRing,
    QuotaExhaustedError,
    ResiliencyExecutor,
    validate_channel_id,
    validate_youtube_id,
)


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


@pytest.fixture
def fixed_key_rings(monkeypatch):
    """Pin deterministic key rings so resiliency tests don't depend on env."""

    class _StubSettings:
        key_rings = {
            "hunting": ["k1", "k2", "k3"],
            "tracking": ["k2"],
            "archeology": ["k3"],
        }

    monkeypatch.setattr("atlas.config.settings", _StubSettings)


def test_keyring_sessions_are_unique(fixed_key_rings):
    """Concurrent sessions must not share attempt state (regression test)."""
    kr = KeyRing("hunting")
    s1 = kr.start_session()
    s2 = kr.start_session()
    assert s1 != s2

    # Rotating s1 must not affect s2's key selection.
    kr.attempt_rotation(s1)
    assert kr.get_session_key(s1) == "k2"
    assert kr.get_session_key(s2) == "k1"


def test_resiliency_raises_quota_exhausted_not_sys_exit(fixed_key_rings):
    """Exhaustion raises QuotaExhaustedError, never calls sys.exit."""

    async def always_quota(key: str) -> None:
        raise RuntimeError("quota exceeded 429")

    async def run() -> str:
        ex = ResiliencyExecutor(KeyRing("hunting"), "tester")
        try:
            await ex.execute_async(always_quota)
        except QuotaExhaustedError:
            return "raised"
        except SystemExit:
            return "sysexit"
        return "none"

    assert asyncio.run(run()) == "raised"


def test_is_quota_error_not_triggered_by_bare_403(fixed_key_rings):
    """A private/region-blocked 403 is not a quota error (regression test)."""
    ex = ResiliencyExecutor(KeyRing("hunting"), "tester")
    assert ex._is_quota_error(RuntimeError("429 Too Many Requests")) is True
    assert ex._is_quota_error(RuntimeError("quotaExceeded")) is True
    assert ex._is_quota_error(RuntimeError("403 Forbidden: video is private")) is False
