#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化測試

測試 LeaderMonitor 的初始化狀態和配置。

測試覆蓋:
- 預設初始化狀態
- 健康指標初始化
- 重連配置常數

Total: 3 tests
"""

import pytest

from src.core.leader_monitor import LeaderMonitor


class TestLeaderMonitorInitialization:
    """測試 LeaderMonitor 初始化"""

    def test_initialization_default_state(self):
        """Test default initialization state."""
        monitor = LeaderMonitor(leader_id="leader_123")

        assert monitor.leader_id == "leader_123"
        assert monitor.wss_client is None
        assert monitor.is_monitoring is False
        assert len(monitor._trade_callbacks) == 0
        assert len(monitor._position_callbacks) == 0
        assert len(monitor._processed_orders) == 0
        assert monitor._reconnect_attempts == 0

    def test_initialization_health_metrics(self):
        """Test health metrics initialization."""
        monitor = LeaderMonitor(leader_id="leader_123")

        assert monitor.health_metrics["last_success_time"] is None
        assert monitor.health_metrics["last_error_time"] is None
        assert monitor.health_metrics["total_attempts"] == 0
        assert monitor.health_metrics["success_count"] == 0
        assert monitor.health_metrics["error_count"] == 0
        assert monitor.health_metrics["trades_processed"] == 0

    def test_initialization_reconnect_config(self):
        """Test reconnection configuration constants."""
        monitor = LeaderMonitor(leader_id="leader_123")

        assert monitor.WS_RECONNECT_MAX_RETRIES == 8
        assert monitor.WS_RECONNECT_BASE_DELAY == 3
        assert monitor.WS_RECONNECT_MAX_DELAY == 120
        assert monitor.WS_CONNECTION_TIMEOUT == 45
