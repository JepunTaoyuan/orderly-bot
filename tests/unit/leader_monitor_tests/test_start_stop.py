#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
啟動/停止監控測試

測試 LeaderMonitor 的啟動和停止流程。

測試覆蓋:
- 成功啟動監控
- 創建 WebSocket 客戶端
- 訂閱 topics
- 儲存憑證用於重連
- 失敗處理
- 乾淨關閉
- 非運行時停止
- 重複啟動防護

Total: 8 tests
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.core.leader_monitor import LeaderMonitor


class TestLeaderMonitorStartStop:
    """測試監控的啟動和停止"""

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_start_monitoring_success(self, mock_ws_client_class, mock_get_ws_manager):
        """Test successful monitoring start."""
        # Setup mocks
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        result = await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret",
            orderly_testnet=True
        )

        assert result is True
        assert monitor.is_monitoring is True
        assert monitor.health_metrics["success_count"] == 1
        assert monitor.health_metrics["last_success_time"] is not None

    @pytest.mark.asyncio
    async def test_start_monitoring_already_running(self):
        """Test starting when already monitoring."""
        monitor = LeaderMonitor(leader_id="leader_123")
        monitor.is_monitoring = True

        result = await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        assert result is True  # Returns True but doesn't re-initialize

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_start_monitoring_creates_websocket(self, mock_ws_client_class, mock_get_ws_manager):
        """Test WebSocket client creation."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret",
            orderly_testnet=True
        )

        # Verify WebSocket client was created
        mock_ws_client_class.assert_called_once()
        assert monitor.wss_client is not None

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_start_monitoring_subscribes_topics(self, mock_ws_client_class, mock_get_ws_manager):
        """Test topic subscriptions."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        # Verify subscriptions
        mock_ws_client.get_execution_report.assert_called_once()
        mock_ws_client.get_position.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_start_monitoring_saves_credentials(self, mock_ws_client_class, mock_get_ws_manager):
        """Test credentials are saved for reconnection."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret",
            orderly_testnet=True
        )

        assert monitor._ws_credentials is not None
        assert monitor._ws_credentials["orderly_key"] == "test_key"
        assert monitor._ws_credentials["orderly_secret"] == "test_secret"
        assert monitor._ws_credentials["orderly_testnet"] is True

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_start_monitoring_failure_handling(self, mock_ws_client_class, mock_get_ws_manager):
        """Test failure handling during start."""
        mock_ws_client_class.side_effect = Exception("WebSocket error")

        monitor = LeaderMonitor(leader_id="leader_123")

        result = await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        assert result is False
        assert monitor.is_monitoring is False
        assert monitor.health_metrics["error_count"] == 1
        assert monitor.health_metrics["last_error_time"] is not None

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    async def test_stop_monitoring_clean_shutdown(self, mock_get_ws_manager):
        """Test clean monitoring stop."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager

        monitor = LeaderMonitor(leader_id="leader_123")
        monitor.is_monitoring = True
        monitor.wss_client = MagicMock()

        await monitor.stop_monitoring()

        assert monitor.is_monitoring is False
        assert monitor._stop_event.is_set()
        monitor.wss_client.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_monitoring_when_not_running(self):
        """Test stopping when not monitoring."""
        monitor = LeaderMonitor(leader_id="leader_123")
        monitor.is_monitoring = False

        # Should not raise error
        await monitor.stop_monitoring()

        assert monitor.is_monitoring is False
