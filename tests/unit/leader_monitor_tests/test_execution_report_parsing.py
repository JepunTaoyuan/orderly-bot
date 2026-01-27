#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
執行報告解析測試 - CRITICAL

這是 LeaderMonitor 最關鍵的功能，負責將 WebSocket 接收到的
execution_report 解析成 LeaderTradeEvent。

測試覆蓋:
- FILLED 和 PARTIAL_FILL 狀態處理
- MARKET 和 LIMIT 訂單類型
- BUY 和 SELL 方向
- OPEN 和 CLOSE 動作判斷
- 錯誤處理和邊界條件

Total: 15 tests
"""

import pytest
from datetime import datetime

from src.core.leader_monitor import LeaderMonitor
from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeAction,
    CopyOrderType,
    CopyOrderSide
)


class TestExecutionReportParsing:
    """測試執行報告解析 - 最關鍵的功能"""

    def test_parse_filled_status(self):
        """Test parsing FILLED status report."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED",
            "timestamp": 1609459200000
        }

        event = monitor._parse_execution_report(data)

        assert event is not None
        assert event.order_id == "order_789"
        assert event.symbol == "PERP_BTC_USDC"
        assert event.side == CopyOrderSide.BUY
        assert event.order_type == CopyOrderType.MARKET
        assert event.price == 50000.0
        assert event.quantity == 0.1
        assert event.leader_id == "leader_123"

    def test_parse_partial_fill_status(self):
        """Test parsing PARTIAL_FILL status."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_ETH_USDC",
            "side": "SELL",
            "type": "LIMIT",
            "executedPrice": 3000.0,
            "executedQty": 0.5,
            "status": "PARTIAL_FILL"
        }

        event = monitor._parse_execution_report(data)

        assert event is not None
        assert event.quantity == 0.5
        assert event.side == CopyOrderSide.SELL

    def test_parse_market_order(self):
        """Test parsing market order."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.order_type == CopyOrderType.MARKET

    def test_parse_limit_order(self):
        """Test parsing limit order."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "LIMIT",
            "executedPrice": 49500.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.order_type == CopyOrderType.LIMIT
        assert event.price == 49500.0

    def test_parse_buy_side(self):
        """Test parsing BUY side."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.side == CopyOrderSide.BUY

    def test_parse_sell_side(self):
        """Test parsing SELL side."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "SELL",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.side == CopyOrderSide.SELL

    def test_parse_open_action_default(self):
        """Test default action is OPEN."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.action == CopyTradeAction.OPEN

    def test_parse_reduce_only_triggers_close(self):
        """Test reduceOnly flag triggers CLOSE action."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "SELL",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED",
            "reduceOnly": True
        }

        event = monitor._parse_execution_report(data)

        assert event.action == CopyTradeAction.CLOSE

    def test_parse_missing_symbol_returns_none(self):
        """Test handling missing symbol."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "side": "BUY",
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event is None

    def test_parse_zero_quantity_returns_none(self):
        """Test handling zero quantity."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "executedQty": 0,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event is None

    def test_parse_invalid_data_returns_none(self):
        """Test handling invalid data."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {"invalid": "data"}

        event = monitor._parse_execution_report(data)

        assert event is None

    def test_parse_preserves_raw_data(self):
        """Test raw data is preserved."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED",
            "custom_field": "custom_value"
        }

        event = monitor._parse_execution_report(data)

        assert event.raw_data == data
        assert event.raw_data["custom_field"] == "custom_value"

    def test_parse_avgprice_fallback(self):
        """Test avgPrice fallback when executedPrice missing."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "avgPrice": 49000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.price == 49000.0

    def test_parse_case_insensitive_side(self):
        """Test side parsing is case insensitive."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "buy",  # lowercase
            "type": "MARKET",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.side == CopyOrderSide.BUY

    def test_parse_invalid_order_type_defaults_market(self):
        """Test invalid order type defaults to MARKET."""
        monitor = LeaderMonitor(leader_id="leader_123")

        data = {
            "orderId": "order_789",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "UNKNOWN_TYPE",
            "executedPrice": 50000.0,
            "executedQty": 0.1,
            "status": "FILLED"
        }

        event = monitor._parse_execution_report(data)

        assert event.order_type == CopyOrderType.MARKET
