#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 設置測試

LeaderMonitor 使用 Orderly 的 WebSocket API 來監控領導者交易。
需要正確創建 WebSocket 客戶端、訂閱 topics、註冊回調。

測試覆蓋:
- 使用憑證初始化 WebSocket
- 訂閱執行報告 (execution_report)
- 訂閱倉位更新 (position)
- 回調註冊
- WebSocket 管理器整合

Total: 5 tests
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from src.core.leader_monitor import LeaderMonitor


class TestWebSocketSetup:
    """測試 WebSocket 設置和配置"""

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_websocket_setup_with_credentials(self, mock_ws_client_class, mock_get_ws_manager):
        """Test WebSocket client is created with credentials."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_api_key",
            orderly_secret="test_api_secret",
            orderly_testnet=True
        )

        # Verify WebSocket client was created
        mock_ws_client_class.assert_called_once()

        # Verify credentials were saved for reconnection
        assert monitor._ws_credentials is not None
        assert monitor._ws_credentials["orderly_key"] == "test_api_key"
        assert monitor._ws_credentials["orderly_secret"] == "test_api_secret"
        assert monitor._ws_credentials["orderly_testnet"] is True

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_websocket_subscription_execution_report(self, mock_ws_client_class, mock_get_ws_manager):
        """Test subscription to execution_report topic."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        # Verify subscription to execution_report
        mock_ws_client.get_execution_report.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_websocket_subscription_position(self, mock_ws_client_class, mock_get_ws_manager):
        """Test subscription to position topic."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        # Verify subscription to position updates
        mock_ws_client.get_position.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_websocket_callback_registration(self, mock_ws_client_class, mock_get_ws_manager):
        """Test WebSocket callbacks are registered."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        # Verify WebSocket client was created and configured
        assert monitor.wss_client is not None
        assert monitor.wss_client == mock_ws_client

        # Verify subscriptions were made
        assert mock_ws_client.get_execution_report.called
        assert mock_ws_client.get_position.called

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_websocket_manager_integration(self, mock_ws_client_class, mock_get_ws_manager):
        """Test integration with WebSocket manager."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        # Verify WebSocket manager was retrieved
        mock_get_ws_manager.assert_called_once()

        # Verify monitoring started
        assert monitor.is_monitoring is True
        assert monitor.wss_client is not None
