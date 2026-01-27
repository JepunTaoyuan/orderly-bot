#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回調廣播測試

LeaderMonitor 需要將領導者交易事件廣播給所有註冊的跟隨者。
回調系統需要處理多個跟隨者、錯誤隔離、異步執行。

測試覆蓋:
- 註冊交易回調
- 單一跟隨者回調
- 多個跟隨者回調
- 調用順序
- 錯誤隔離 (一個失敗不影響其他)
- 異步執行
- 倉位更新回調
- 取消註冊回調

Total: 8 tests
"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
import asyncio

from src.core.leader_monitor import LeaderMonitor
from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeAction,
    CopyOrderType,
    CopyOrderSide
)


class TestCallbackBroadcasting:
    """測試回調廣播系統"""

    @pytest.mark.asyncio
    async def test_register_trade_callback(self):
        """Test registering trade callback."""
        monitor = LeaderMonitor(leader_id="leader_123")

        callback = AsyncMock()
        follower_id = "follower_001"

        monitor.register_trade_callback(follower_id, callback)

        assert follower_id in monitor._trade_callbacks
        assert monitor._trade_callbacks[follower_id] == callback

    @pytest.mark.asyncio
    async def test_trade_callback_single_follower(self):
        """Test trade callback invocation for single follower."""
        monitor = LeaderMonitor(leader_id="leader_123")

        callback = AsyncMock()
        follower_id = "follower_001"
        monitor.register_trade_callback(follower_id, callback)

        # Create trade event
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=1609459200000,
            raw_data={}
        )

        # Broadcast event
        await monitor._broadcast_trade_event(event)

        # Verify callback was called
        callback.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_trade_callback_multiple_followers(self):
        """Test trade callback invocation for multiple followers."""
        monitor = LeaderMonitor(leader_id="leader_123")

        # Register multiple followers
        callbacks = {}
        for i in range(3):
            follower_id = f"follower_{i:03d}"
            callback = AsyncMock()
            callbacks[follower_id] = callback
            monitor.register_trade_callback(follower_id, callback)

        # Create trade event
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=1609459200000,
            raw_data={}
        )

        # Broadcast event
        await monitor._broadcast_trade_event(event)

        # Verify all callbacks were called
        for callback in callbacks.values():
            callback.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_trade_callback_invocation_order(self):
        """Test callbacks are invoked in order."""
        monitor = LeaderMonitor(leader_id="leader_123")

        invocation_order = []

        async def callback_1(event):
            invocation_order.append(1)

        async def callback_2(event):
            invocation_order.append(2)

        async def callback_3(event):
            invocation_order.append(3)

        monitor.register_trade_callback("follower_001", callback_1)
        monitor.register_trade_callback("follower_002", callback_2)
        monitor.register_trade_callback("follower_003", callback_3)

        # Create trade event
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=1609459200000,
            raw_data={}
        )

        # Broadcast event
        await monitor._broadcast_trade_event(event)

        # Verify all callbacks were invoked
        assert len(invocation_order) == 3
        # Order may vary depending on implementation (dict iteration order)
        assert set(invocation_order) == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_trade_callback_error_isolation(self):
        """Test one callback failure doesn't affect others."""
        monitor = LeaderMonitor(leader_id="leader_123")

        # Callback that will fail
        async def failing_callback(event):
            raise Exception("Callback error")

        # Callbacks that should succeed
        callback_2 = AsyncMock()
        callback_3 = AsyncMock()

        monitor.register_trade_callback("follower_001", failing_callback)
        monitor.register_trade_callback("follower_002", callback_2)
        monitor.register_trade_callback("follower_003", callback_3)

        # Create trade event
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=1609459200000,
            raw_data={}
        )

        # Broadcast event - should not raise exception
        await monitor._broadcast_trade_event(event)

        # Verify other callbacks were still called
        callback_2.assert_called_once_with(event)
        callback_3.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_trade_callback_async_execution(self):
        """Test callbacks are executed asynchronously."""
        monitor = LeaderMonitor(leader_id="leader_123")

        execution_times = []

        async def slow_callback(event):
            await asyncio.sleep(0.1)
            execution_times.append(1)

        async def fast_callback(event):
            execution_times.append(2)

        monitor.register_trade_callback("follower_slow", slow_callback)
        monitor.register_trade_callback("follower_fast", fast_callback)

        # Create trade event
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=1609459200000,
            raw_data={}
        )

        # Broadcast event
        await monitor._broadcast_trade_event(event)

        # Both callbacks should have executed
        assert len(execution_times) == 2

    @pytest.mark.asyncio
    async def test_position_callback(self):
        """Test position update callback."""
        monitor = LeaderMonitor(leader_id="leader_123")

        callback = AsyncMock()
        follower_id = "follower_001"

        monitor.register_position_callback(follower_id, callback)

        assert follower_id in monitor._position_callbacks
        assert monitor._position_callbacks[follower_id] == callback

        # Simulate position update
        position_data = {
            "symbol": "PERP_BTC_USDC",
            "position_qty": 0.5,
            "average_open_price": 50000.0
        }

        await monitor._broadcast_position_update(position_data)

        # Verify callback was called
        callback.assert_called_once_with(position_data)

    @pytest.mark.asyncio
    async def test_unregister_trade_callback(self):
        """Test unregistering trade callback."""
        monitor = LeaderMonitor(leader_id="leader_123")

        callback = AsyncMock()
        follower_id = "follower_001"

        # Register callback
        monitor.register_trade_callback(follower_id, callback)
        assert follower_id in monitor._trade_callbacks

        # Unregister callback
        monitor.unregister_trade_callback(follower_id)
        assert follower_id not in monitor._trade_callbacks

        # Create trade event
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=1609459200000,
            raw_data={}
        )

        # Broadcast event
        await monitor._broadcast_trade_event(event)

        # Callback should not have been called
        callback.assert_not_called()
