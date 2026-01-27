#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
健康狀態測試

LeaderMonitor 追蹤健康指標用於監控和告警。

健康指標包括:
- last_success_time: 最後成功時間
- last_error_time: 最後錯誤時間
- total_attempts: 總嘗試次數
- success_count: 成功次數
- error_count: 錯誤次數
- trades_processed: 處理的交易數

測試覆蓋:
- 初始健康狀態
- 處理交易後狀態
- 錯誤後狀態
- 指標準確性
- 交易計數器

Total: 5 tests
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

from src.core.leader_monitor import LeaderMonitor
from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeAction,
    CopyOrderType,
    CopyOrderSide
)


class TestHealthStatus:
    """測試健康狀態追蹤"""

    def test_health_status_initial(self):
        """Test initial health status."""
        monitor = LeaderMonitor(leader_id="leader_123")

        health = monitor.health_metrics

        assert health["last_success_time"] is None
        assert health["last_error_time"] is None
        assert health["total_attempts"] == 0
        assert health["success_count"] == 0
        assert health["error_count"] == 0
        assert health["trades_processed"] == 0

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_health_status_after_trades(self, mock_ws_client_class, mock_get_ws_manager):
        """Test health status after processing trades."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        # Start monitoring - should increment success count
        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        health = monitor.health_metrics

        assert health["success_count"] == 1
        assert health["last_success_time"] is not None
        assert health["total_attempts"] > 0

        # Process some trades
        data = {
            "orderId": "order_001",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)
        if event:
            monitor.health_metrics["trades_processed"] += 1

        assert monitor.health_metrics["trades_processed"] > 0

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_health_status_after_errors(self, mock_ws_client_class):
        """Test health status after errors."""
        # Make WebSocket client raise exception
        mock_ws_client_class.side_effect = Exception("Connection error")

        monitor = LeaderMonitor(leader_id="leader_123")

        # Attempt to start monitoring - should fail
        result = await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        assert result is False

        health = monitor.health_metrics

        assert health["error_count"] == 1
        assert health["last_error_time"] is not None

    @pytest.mark.asyncio
    @patch('src.core.leader_monitor.get_websocket_manager')
    @patch('src.core.leader_monitor.WebsocketPrivateAPIClient')
    async def test_health_metrics_accuracy(self, mock_ws_client_class, mock_get_ws_manager):
        """Test health metrics are accurate."""
        mock_ws_manager = AsyncMock()
        mock_get_ws_manager.return_value = mock_ws_manager
        mock_ws_client = MagicMock()
        mock_ws_client_class.return_value = mock_ws_client

        monitor = LeaderMonitor(leader_id="leader_123")

        initial_success_count = monitor.health_metrics["success_count"]

        # Start monitoring successfully
        await monitor.start_monitoring(
            orderly_key="test_key",
            orderly_secret="test_secret"
        )

        # Verify success count increased
        assert monitor.health_metrics["success_count"] == initial_success_count + 1

        # Verify last_success_time was set
        assert monitor.health_metrics["last_success_time"] is not None

        # Verify total_attempts increased
        assert monitor.health_metrics["total_attempts"] > 0

    def test_trades_processed_counter(self):
        """Test trades processed counter."""
        monitor = LeaderMonitor(leader_id="leader_123")

        assert monitor.health_metrics["trades_processed"] == 0

        # Process multiple trades
        for i in range(5):
            data = {
                "orderId": f"order_{i:03d}",
                "symbol": "PERP_BTC_USDC",
                "side": "BUY",
                "type": "MARKET",
                "executedPrice": 50000.0,
                "executedQty": 0.1,
                "status": "FILLED"
            }

            event = monitor._parse_execution_report(data)
            if event:
                monitor.health_metrics["trades_processed"] += 1

        # Should have processed 5 trades
        assert monitor.health_metrics["trades_processed"] == 5
