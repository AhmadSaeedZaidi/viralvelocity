"""
Tests for Invidious instance management and streamer fallback logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from maia.painter.streamer import FALLBACK_INSTANCES, InstanceManager, StealthVideoStreamer


class TestInstanceManager:
    """Tests for Invidious InstanceManager."""

    @pytest.mark.asyncio
    async def test_fetch_instances_filters_correctly(self) -> None:
        """Verify strict filtering: https, api, cors, no onion."""
        mock_api_response = [
            [
                "good.example.com",
                {
                    "type": "https",
                    "api": True,
                    "cors": True,
                    "uri": "https://good.example.com",
                },
            ],
            [
                "noapi.example.com",
                {
                    "type": "https",
                    "api": False,
                    "cors": True,
                    "uri": "https://noapi.example.com",
                },
            ],
            [
                "nocors.example.com",
                {
                    "type": "https",
                    "api": True,
                    "cors": False,
                    "uri": "https://nocors.example.com",
                },
            ],
            [
                "http.example.com",
                {
                    "type": "http",
                    "api": True,
                    "cors": True,
                    "uri": "http://http.example.com",
                },
            ],
            [
                "onion.example.onion",
                {
                    "type": "https",
                    "api": True,
                    "cors": True,
                    "uri": "https://onion.example.onion",
                },
            ],
            [
                "also-good.example.org",
                {
                    "type": "https",
                    "api": True,
                    "cors": True,
                    "uri": "https://also-good.example.org/",
                },
            ],
        ]

        manager = InstanceManager()

        with patch("maia.painter.streamer.aiohttp.ClientSession") as MockSession:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_api_response)

            mock_get_ctx = MagicMock()
            mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get.return_value = mock_get_ctx
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            MockSession.return_value = mock_session

            result = await manager._fetch_instances_from_api()

        assert len(result) == 2
        assert "https://good.example.com" in result
        # Trailing slash should be stripped
        assert "https://also-good.example.org" in result

    @pytest.mark.asyncio
    async def test_fetch_instances_handles_api_500(self) -> None:
        """Verify empty list on API 500 error."""
        manager = InstanceManager()

        with patch("maia.painter.streamer.aiohttp.ClientSession") as MockSession:
            mock_resp = AsyncMock()
            mock_resp.status = 500

            mock_get_ctx = MagicMock()
            mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get.return_value = mock_get_ctx
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            MockSession.return_value = mock_session

            result = await manager._fetch_instances_from_api()

        assert result == []

    @pytest.mark.asyncio
    async def test_deadman_switch_on_api_failure(self) -> None:
        """Verify hardcoded fallback when API is unreachable."""
        manager = InstanceManager()

        with patch("maia.painter.streamer.aiohttp.ClientSession") as MockSession:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(side_effect=Exception("Network down"))
            mock_session.__aexit__ = AsyncMock(return_value=None)
            MockSession.return_value = mock_session

            instance = await manager.get_instance()

        # Should have activated deadman switch
        assert instance in FALLBACK_INSTANCES
        assert manager.pool_size > 0

    @pytest.mark.asyncio
    async def test_blacklist_rotation(self) -> None:
        """Verify blacklisted instances are skipped."""
        manager = InstanceManager()
        manager._instances = ["https://a.com", "https://b.com"]
        manager._last_refresh = 9999999999.0  # Far future, skip refresh

        manager.mark_bad("https://a.com")

        instance = await manager.get_instance()
        assert instance == "https://b.com"

    @pytest.mark.asyncio
    async def test_blacklist_reset_when_all_exhausted(self) -> None:
        """Verify blacklist resets when all instances are blacklisted."""
        manager = InstanceManager()
        manager._instances = ["https://a.com", "https://b.com"]
        manager._last_refresh = 9999999999.0

        manager.mark_bad("https://a.com")
        manager.mark_bad("https://b.com")

        instance = await manager.get_instance()
        assert instance in ["https://a.com", "https://b.com"]
        assert manager.pool_size == 2  # Blacklist was cleared

    @pytest.mark.asyncio
    async def test_pool_caches_until_refresh(self) -> None:
        """Verify instances are cached and not re-fetched within the refresh interval."""
        manager = InstanceManager()
        manager._instances = ["https://cached.example.com"]
        manager._last_refresh = 9999999999.0  # Far future

        instance = await manager.get_instance()
        assert instance == "https://cached.example.com"

    def test_reset(self) -> None:
        """Verify reset clears all state."""
        manager = InstanceManager()
        manager._instances = ["https://a.com"]
        manager._blacklist = {"https://b.com"}
        manager._last_refresh = 999.0

        manager.reset()

        assert manager._instances == []
        assert manager._blacklist == set()
        assert manager._last_refresh == 0.0


class TestStealthVideoStreamer:
    """Tests for the cascading extraction logic."""

    def test_extract_heatmap_peaks(self) -> None:
        """Test heatmap peak extraction (unchanged interface)."""
        streamer = StealthVideoStreamer()
        heatmap = [
            {"start_time": 10.0, "end_time": 11.0, "value": 0.5},
            {"start_time": 25.0, "end_time": 26.0, "value": 0.9},
            {"start_time": 50.0, "end_time": 51.0, "value": 0.3},
            {"start_time": 75.0, "end_time": 76.0, "value": 0.8},
            {"start_time": 100.0, "end_time": 101.0, "value": 0.7},
        ]
        peaks = streamer.extract_heatmap_peaks(heatmap, top_n=3)
        assert len(peaks) == 3
        assert peaks[0] == 25.0
        assert peaks[1] == 75.0
        assert peaks[2] == 100.0

    def test_extract_heatmap_peaks_empty(self) -> None:
        """Test empty heatmap returns empty list."""
        streamer = StealthVideoStreamer()
        assert streamer.extract_heatmap_peaks([]) == []
        assert streamer.extract_heatmap_peaks([], top_n=10) == []

    def test_cookies_path_validation(self) -> None:
        """Test non-existent cookies path is handled gracefully."""
        streamer = StealthVideoStreamer(cookies_path="/nonexistent/cookies.txt")
        assert streamer.cookies_path is None

    def test_base_options_include_mp4_format(self) -> None:
        """Test Format Trap: base options force mp4 for OpenCV compatibility."""
        streamer = StealthVideoStreamer()
        opts = streamer._get_base_options()
        assert opts["format"] == "best[ext=mp4]/best"
        assert opts["force_ipv4"] is True

    def test_resolve_stream_url_from_url_field(self) -> None:
        """Test stream URL is resolved from top-level 'url' field."""
        info = {"url": "https://stream.example.com/v.mp4", "formats": []}
        result = StealthVideoStreamer._resolve_stream_url(info)
        assert result == "https://stream.example.com/v.mp4"

    def test_resolve_stream_url_from_formats(self) -> None:
        """Test stream URL is resolved from formats list as fallback."""
        info = {
            "url": None,
            "formats": [
                {"ext": "webm", "url": "https://bad.com/v.webm", "height": 720},
                {"ext": "mp4", "url": "https://good.com/v.mp4", "height": 480},
                {"ext": "mp4", "url": "https://best.com/v.mp4", "height": 720},
            ],
        }
        result = StealthVideoStreamer._resolve_stream_url(info)
        assert result == "https://best.com/v.mp4"

    def test_resolve_stream_url_returns_none_when_no_mp4(self) -> None:
        """Test stream URL returns None when no playable format exists."""
        info = {"formats": [{"ext": "webm", "url": "https://bad.com/v.webm"}]}
        result = StealthVideoStreamer._resolve_stream_url(info)
        assert result is None

    def test_extract_info_tries_invidious_first(self) -> None:
        """Test that extract_info attempts Invidious before direct strategies."""
        streamer = StealthVideoStreamer()
        mock_info = {
            "url": "https://stream.example.com/v.mp4",
            "duration": 120,
            "chapters": [],
            "heatmap": [],
        }

        with patch("maia.painter.streamer.asyncio") as mock_asyncio:
            mock_asyncio.get_running_loop.side_effect = RuntimeError("no loop")
            mock_asyncio.run.return_value = mock_info

            result = streamer.extract_info("dQw4w9WgXcQ")

        assert result["url"] == "https://stream.example.com/v.mp4"
        mock_asyncio.run.assert_called_once()

    def test_extract_info_falls_back_to_direct(self) -> None:
        """Test fallback to direct yt-dlp when Invidious returns None."""
        streamer = StealthVideoStreamer()
        mock_info = {
            "url": "https://direct.example.com/v.mp4",
            "duration": 100,
        }

        with (
            patch("maia.painter.streamer.asyncio") as mock_asyncio,
            patch.object(streamer, "_try_direct_strategies", return_value=mock_info) as mock_direct,
        ):
            mock_asyncio.get_running_loop.side_effect = RuntimeError("no loop")
            mock_asyncio.run.return_value = None  # Invidious returns None

            result = streamer.extract_info("dQw4w9WgXcQ")

        assert result["url"] == "https://direct.example.com/v.mp4"
        mock_direct.assert_called_once_with("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_extract_info_falls_back_on_invidious_exception(self) -> None:
        """Test fallback to direct yt-dlp when Invidious raises an exception."""
        streamer = StealthVideoStreamer()
        mock_info = {
            "url": "https://direct.example.com/v.mp4",
            "duration": 100,
        }

        with (
            patch("maia.painter.streamer.asyncio") as mock_asyncio,
            patch.object(streamer, "_try_direct_strategies", return_value=mock_info) as mock_direct,
        ):
            mock_asyncio.get_running_loop.side_effect = RuntimeError("no loop")
            mock_asyncio.run.side_effect = Exception("network failure")

            result = streamer.extract_info("dQw4w9WgXcQ")

        assert result["url"] == "https://direct.example.com/v.mp4"
        mock_direct.assert_called_once()

    @pytest.mark.asyncio
    async def test_try_invidious_rotates_on_failure(self) -> None:
        """Test that _try_invidious rotates instances on failure."""
        streamer = StealthVideoStreamer()
        streamer.MAX_INVIDIOUS_ATTEMPTS = 2

        call_count = 0
        instances_used = []

        async def mock_get_instance() -> str:
            nonlocal call_count
            call_count += 1
            instance = f"https://instance-{call_count}.example.com"
            instances_used.append(instance)
            return instance

        with (
            patch("maia.painter.streamer._instance_manager") as mock_manager,
            patch.object(streamer, "_fetch_sync", side_effect=Exception("extraction failed")),
        ):
            mock_manager.get_instance = mock_get_instance
            mock_manager.mark_bad = MagicMock()

            result = await streamer._try_invidious("test_id")

        assert result is None
        assert len(instances_used) == 2
        assert mock_manager.mark_bad.call_count == 2
