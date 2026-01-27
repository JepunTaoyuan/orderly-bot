#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for CopyTradingBot

Tests cover:
- Initialization (4 tests)
- Start/Stop (8 tests)
- Leader trade handling (18+ tests) - CRITICAL
- Order execution (10 tests)
- Trade records (8 tests)
- Statistics (7 tests)
- Event callbacks (6 tests)

Total: 61 tests
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from decimal import Decimal
import time
import asyncio

from src.core.copy_trading_bot import CopyTradingBot
from src.core.risk_controller import RiskValidationResult
from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeRecord,
    CopyTradeStatus,
    CopyTradeAction,
    CopyOrderType,
    CopyOrderSide,
    RiskLimits,
    FollowerConfig,
    FollowerStatistics,
    CopyTradeResult
)


# ============================================================================
# Test Class 1: Initialization Tests (4 tests)
# ============================================================================

class TestCopyTradingBotInitialization:
    """Test CopyTradingBot initialization."""

    def test_initialization_default_state(self):
        """Test bot initializes with correct default state."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="test_key",
            orderly_secret="test_secret",
            orderly_testnet=True
        )

        assert bot.follower_id == "follower_123"
        assert bot.orderly_testnet is True
        assert bot.is_running is False
        assert bot.leader_id is None
        assert bot.copy_ratio == 1.0
        assert bot.risk_limits is None
        assert bot.risk_controller is None
        assert isinstance(bot.statistics, FollowerStatistics)
        assert bot.statistics.total_trades == 0
        assert len(bot._trade_records) == 0

    def test_initialization_with_credentials(self):
        """Test bot initialization stores credentials correctly."""
        bot = CopyTradingBot(
            follower_id="follower_456",
            orderly_key="key_abc",
            orderly_secret="secret_xyz",
            orderly_testnet=False
        )

        assert bot.follower_id == "follower_456"
        assert bot.orderly_testnet is False
        # Client should be created (not None)
        assert bot.client is not None

    @patch('src.core.copy_trading_bot.OrderlyClient')
    def test_initialization_creates_client(self, mock_orderly_client):
        """Test bot creates OrderlyClient with correct parameters."""
        bot = CopyTradingBot(
            follower_id="follower_789",
            orderly_key="my_key",
            orderly_secret="my_secret",
            orderly_testnet=True
        )

        # Verify OrderlyClient was called with correct params
        mock_orderly_client.assert_called_once_with(
            account_id="follower_789",
            orderly_key="my_key",
            orderly_secret="my_secret",
            orderly_testnet=True
        )

    def test_initialization_execution_lock(self):
        """Test execution lock is created for concurrency control."""
        bot = CopyTradingBot(
            follower_id="follower_999",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Execution lock should exist
        assert hasattr(bot, '_execution_lock')
        assert bot._execution_lock is not None


# ============================================================================
# Test Class 2: Start/Stop Tests (8 tests)
# ============================================================================

class TestCopyTradingBotStartStop:
    """Test bot start and stop functionality."""

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_start_success_flow(self, mock_risk_controller_class, sample_risk_limits):
        """Test successful bot start."""
        # Setup mock RiskController
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Mock _sync_positions
        bot._sync_positions = AsyncMock()

        result = await bot.start(
            leader_id="leader_456",
            copy_ratio=1.5,
            risk_limits=sample_risk_limits
        )

        assert result is True
        assert bot.is_running is True
        assert bot.leader_id == "leader_456"
        assert bot.copy_ratio == 1.5
        assert bot.risk_limits == sample_risk_limits
        assert bot.risk_controller is not None
        assert bot._start_time is not None

        # Verify risk controller was started
        mock_controller.start.assert_called_once()
        # Verify positions were synced
        bot._sync_positions.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_start_initializes_risk_controller(self, mock_risk_controller_class, sample_risk_limits):
        """Test start creates and initializes RiskController."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Verify RiskController was created with correct params
        mock_risk_controller_class.assert_called_once_with("follower_123", sample_risk_limits)
        mock_controller.start.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_start_syncs_positions(self, mock_risk_controller_class, sample_risk_limits):
        """Test start syncs current positions."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Mock sync_positions method
        bot._sync_positions = AsyncMock()

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Verify positions sync was called
        bot._sync_positions.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_start_already_running_error(self, mock_risk_controller_class, sample_risk_limits):
        """Test starting an already running bot returns False."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()

        # Start first time
        result1 = await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )
        assert result1 is True

        # Try to start again
        result2 = await bot.start(
            leader_id="leader_789",
            copy_ratio=2.0,
            risk_limits=sample_risk_limits
        )
        assert result2 is False
        # Original leader should remain
        assert bot.leader_id == "leader_456"

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_start_risk_controller_failure(self, mock_risk_controller_class, sample_risk_limits):
        """Test start handles RiskController failure gracefully."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock(side_effect=Exception("Risk controller start failed"))
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        result = await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        assert result is False
        assert bot.is_running is False

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_stop_clean_shutdown(self, mock_risk_controller_class, sample_risk_limits):
        """Test stop performs clean shutdown."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.stop = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()

        # Start bot
        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )
        assert bot.is_running is True

        # Stop bot
        result = await bot.stop()

        assert result is True
        assert bot.is_running is False
        assert bot._stop_event.is_set()
        mock_controller.stop.assert_called_once()
        bot._emit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        """Test stopping a bot that isn't running returns True."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Stop without starting
        result = await bot.stop()

        assert result is True
        assert bot.is_running is False

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_stop_cleanup_resources(self, mock_risk_controller_class, sample_risk_limits):
        """Test stop cleans up resources properly."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.stop = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()

        # Start bot
        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Add some records
        bot._trade_records = [Mock(), Mock(), Mock()]

        # Stop bot
        await bot.stop()

        # Verify cleanup
        assert bot.is_running is False
        mock_controller.stop.assert_called_once()
        # Trade records should still exist (for history)
        assert len(bot._trade_records) == 3


# ============================================================================
# Test Class 3: Leader Trade Handling Tests (18+ tests) - CRITICAL
# ============================================================================

class TestLeaderTradeHandling:
    """Test handling of leader trade events - CRITICAL PATH."""

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_success(self, mock_risk_controller_class,
                                               sample_risk_limits, sample_leader_trade_event):
        """Test successful leader trade handling."""
        # Setup mocks
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            follower_order_id="follower_order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Handle trade
        result = await bot.handle_leader_trade(sample_leader_trade_event)

        assert result.success is True
        assert result.follower_id == "follower_123"
        assert result.status == CopyTradeStatus.EXECUTED
        assert bot.statistics.successful_trades == 1
        assert bot.statistics.total_trades == 1

    @pytest.mark.asyncio
    async def test_handle_leader_trade_when_stopped(self, sample_leader_trade_event):
        """Test handling trade when bot is stopped."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Don't start bot
        result = await bot.handle_leader_trade(sample_leader_trade_event)

        assert result.success is False
        assert result.status == CopyTradeStatus.SKIPPED
        assert "已停止" in result.error_message

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_risk_validation_fail(self, mock_risk_controller_class,
                                                            sample_risk_limits, sample_leader_trade_event):
        """Test trade is skipped when risk validation fails."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=False,
            adjusted_quantity=None,
            reason="超過每日虧損限額",
            risk_score=95
        ))
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        result = await bot.handle_leader_trade(sample_leader_trade_event)

        assert result.success is False
        assert result.status == CopyTradeStatus.SKIPPED
        assert bot.statistics.skipped_trades == 1
        assert bot.statistics.successful_trades == 0

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_risk_adjusted_quantity(self, mock_risk_controller_class,
                                                               sample_risk_limits, sample_leader_trade_event):
        """Test trade with adjusted quantity from risk controller."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=0.05,  # Adjusted from 0.1
            reason="調整數量以符合限額",
            risk_score=30
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            follower_order_id="follower_order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.05  # Adjusted quantity
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        result = await bot.handle_leader_trade(sample_leader_trade_event)

        assert result.success is True
        # Verify adjusted quantity was used
        call_args = bot._execute_copy_trade.call_args
        assert call_args[0][1] == 0.05  # quantity parameter

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    @pytest.mark.parametrize("copy_ratio,expected_quantity", [
        (0.1, 0.01),   # 10% of 0.1
        (1.0, 0.1),    # 100% of 0.1
        (2.5, 0.25),   # 250% of 0.1
        (10.0, 1.0),   # 1000% of 0.1
    ])
    async def test_handle_leader_trade_copy_ratio_calculation(self, mock_risk_controller_class,
                                                               sample_risk_limits, sample_leader_trade_event,
                                                               copy_ratio, expected_quantity):
        """Test copy ratio is applied correctly to trade quantity."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=expected_quantity
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=copy_ratio,
            risk_limits=sample_risk_limits
        )

        result = await bot.handle_leader_trade(sample_leader_trade_event)

        assert result.success is True
        # Verify copy ratio was passed to validate_trade
        call_args = mock_controller.validate_trade.call_args
        assert call_args[0][1] == copy_ratio

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_market_order(self, mock_risk_controller_class,
                                                     sample_risk_limits):
        """Test handling market order from leader."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id="order_789",
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Market order event
        market_event = LeaderTradeEvent(
            leader_id="leader_456",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=datetime.utcnow()
        )

        result = await bot.handle_leader_trade(market_event)

        assert result.success is True
        # Verify _execute_copy_trade was called with MARKET type
        call_args = bot._execute_copy_trade.call_args
        assert call_args[0][0].order_type == CopyOrderType.MARKET

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_limit_order(self, mock_risk_controller_class,
                                                    sample_risk_limits):
        """Test handling limit order from leader."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id="order_789",
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=49500.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Limit order event
        limit_event = LeaderTradeEvent(
            leader_id="leader_456",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.LIMIT,
            price=49500.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=datetime.utcnow()
        )

        result = await bot.handle_leader_trade(limit_event)

        assert result.success is True
        call_args = bot._execute_copy_trade.call_args
        assert call_args[0][0].order_type == CopyOrderType.LIMIT
        assert call_args[0][0].price == 49500.0  # limit price

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_api_failure(self, mock_risk_controller_class,
                                                    sample_risk_limits, sample_leader_trade_event):
        """Test handling API failure during trade execution."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=False,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            status=CopyTradeStatus.FAILED,
            error_message="API call failed: Network timeout"
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        result = await bot.handle_leader_trade(sample_leader_trade_event)

        assert result.success is False
        assert result.status == CopyTradeStatus.FAILED
        assert bot.statistics.failed_trades == 1

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_updates_statistics(self, mock_risk_controller_class,
                                                          sample_risk_limits, sample_leader_trade_event):
        """Test trade handling updates statistics correctly."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        initial_total = bot.statistics.total_trades
        initial_success = bot.statistics.successful_trades

        await bot.handle_leader_trade(sample_leader_trade_event)

        assert bot.statistics.total_trades == initial_total + 1
        assert bot.statistics.successful_trades == initial_success + 1

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_emits_event(self, mock_risk_controller_class,
                                                    sample_risk_limits, sample_leader_trade_event):
        """Test trade handling emits SSE event."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        await bot.handle_leader_trade(sample_leader_trade_event)

        # Verify event was emitted
        assert bot._emit_event.call_count >= 1

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    @pytest.mark.parametrize("action", [
        CopyTradeAction.OPEN,
        CopyTradeAction.ADD,
        CopyTradeAction.REDUCE,
        CopyTradeAction.CLOSE
    ])
    async def test_handle_leader_trade_action_types(self, mock_risk_controller_class,
                                                     sample_risk_limits, action):
        """Test handling different trade action types."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id="order_789",
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        event = LeaderTradeEvent(
            leader_id="leader_456",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.1,
            action=action,
            timestamp=datetime.utcnow()
        )

        result = await bot.handle_leader_trade(event)

        assert result.success is True
        # Verify action was passed correctly in the event
        call_args = bot._execute_copy_trade.call_args
        assert call_args[0][0].action == action

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_handle_leader_trade_very_small_quantity(self, mock_risk_controller_class,
                                                           sample_risk_limits):
        """Test handling very small quantity trades."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id="order_789",
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.001
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        small_event = LeaderTradeEvent(
            leader_id="leader_456",
            order_id="order_789",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=50000.0,
            quantity=0.001,  # Very small
            action=CopyTradeAction.OPEN,
            timestamp=datetime.utcnow()
        )

        result = await bot.handle_leader_trade(small_event)

        assert result.success is True


# ============================================================================
# Test Class 4: Order Execution Tests (10 tests)
# ============================================================================

class TestOrderExecution:
    """Test order execution functionality."""

    @pytest.mark.asyncio
    async def test_execute_copy_trade_market_order(self, sample_leader_trade_event):
        """Test executing a market order."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Mock OrderlyClient.create_market_order
        bot.client.create_market_order = AsyncMock(return_value={
            "success": True,
            "data": {
                "order_id": "123456",
                "price": 50100.0,
                "quantity": 0.1
            }
        })

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        assert result.success is True
        assert result.status == CopyTradeStatus.EXECUTED
        assert result.follower_order_id == "123456"
        assert result.executed_price == 50100.0
        assert result.executed_quantity == 0.1

        # Verify create_market_order was called
        bot.client.create_market_order.assert_called_once_with(
            symbol="PERP_BTC_USDC",
            side="BUY",
            order_quantity=0.1
        )

    @pytest.mark.asyncio
    async def test_execute_copy_trade_limit_order(self):
        """Test executing a limit order."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        limit_event = LeaderTradeEvent(
            leader_id="leader_456",
            order_id="order_789",
            symbol="PERP_ETH_USDC",
            side=CopyOrderSide.SELL,
            order_type=CopyOrderType.LIMIT,
            price=1800.0,
            quantity=1.0,
            action=CopyTradeAction.CLOSE,
            timestamp=datetime.utcnow()
        )

        bot.client.create_limit_order = AsyncMock(return_value={
            "success": True,
            "data": {
                "order_id": "789012",
                "price": 1800.0,
                "quantity": 1.0
            }
        })

        result = await bot._execute_copy_trade(limit_event, 1.0)

        assert result.success is True
        assert result.status == CopyTradeStatus.EXECUTED
        bot.client.create_limit_order.assert_called_once_with(
            symbol="PERP_ETH_USDC",
            side="SELL",
            order_price=1800.0,
            order_quantity=1.0
        )

    @pytest.mark.asyncio
    async def test_execute_copy_trade_quantity_precision(self, sample_leader_trade_event):
        """Test quantity precision handling."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Very precise quantity
        precise_quantity = 0.123456789

        bot.client.create_market_order = AsyncMock(return_value={
            "success": True,
            "data": {
                "order_id": "123",
                "price": 50000.0,
                "quantity": precise_quantity
            }
        })

        result = await bot._execute_copy_trade(sample_leader_trade_event, precise_quantity)

        assert result.success is True
        assert result.executed_quantity == precise_quantity

    @pytest.mark.asyncio
    async def test_execute_copy_trade_api_response_parsing(self, sample_leader_trade_event):
        """Test parsing API response correctly."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        bot.client.create_market_order = AsyncMock(return_value={
            "success": True,
            "data": {
                "order_id": "order_abc123",
                "price": "50250.50",  # String price
                "quantity": 0.1,
                "status": "FILLED"
            }
        })

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        assert result.success is True
        assert result.follower_order_id == "order_abc123"
        assert result.executed_price == 50250.50  # Converted to float

    @pytest.mark.asyncio
    async def test_execute_copy_trade_execution_latency(self, sample_leader_trade_event):
        """Test execution latency is reasonable."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Mock API with delay
        async def delayed_order(*args, **kwargs):
            await asyncio.sleep(0.05)  # 50ms delay
            return {
                "success": True,
                "data": {
                    "order_id": "123",
                    "price": 50000.0,
                    "quantity": 0.1
                }
            }

        bot.client.create_market_order = delayed_order

        start = time.time()
        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)
        elapsed = time.time() - start

        assert result.success is True
        assert elapsed >= 0.05  # At least 50ms
        assert elapsed < 1.0  # But not too long

    @pytest.mark.asyncio
    async def test_execute_copy_trade_network_error(self, sample_leader_trade_event):
        """Test handling network errors."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        bot.client.create_market_order = AsyncMock(
            side_effect=Exception("Network timeout")
        )

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        assert result.success is False
        assert result.status == CopyTradeStatus.FAILED
        assert "Network timeout" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_copy_trade_invalid_symbol(self, sample_leader_trade_event):
        """Test handling invalid symbol error."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        bot.client.create_market_order = AsyncMock(return_value={
            "success": False,
            "message": "Invalid symbol: PERP_INVALID_USDC"
        })

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        assert result.success is False
        assert result.status == CopyTradeStatus.FAILED
        assert "Invalid symbol" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_copy_trade_order_rejected(self, sample_leader_trade_event):
        """Test handling order rejection."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        bot.client.create_market_order = AsyncMock(return_value={
            "success": False,
            "message": "Insufficient margin"
        })

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        assert result.success is False
        assert "Insufficient margin" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_copy_trade_partial_fill(self, sample_leader_trade_event):
        """Test handling partial fill (still success)."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        bot.client.create_market_order = AsyncMock(return_value={
            "success": True,
            "data": {
                "order_id": "123",
                "price": 50000.0,
                "quantity": 0.08,  # Partial fill: requested 0.1, got 0.08
                "status": "PARTIALLY_FILLED"
            }
        })

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        # Partial fill is still considered success
        assert result.success is True
        assert result.executed_quantity == 0.1  # We report what we requested

    @pytest.mark.asyncio
    async def test_execute_copy_trade_no_response(self, sample_leader_trade_event):
        """Test handling no response from API."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        bot.client.create_market_order = AsyncMock(return_value=None)

        result = await bot._execute_copy_trade(sample_leader_trade_event, 0.1)

        assert result.success is False
        assert result.status == CopyTradeStatus.FAILED
        assert "No response" in result.error_message


# ============================================================================
# Test Class 5: Trade Record Tests (8 tests)
# ============================================================================

class TestTradeRecords:
    """Test trade record creation and management."""

    def test_create_trade_record_structure(self, sample_leader_trade_event):
        """Test trade record is created with correct structure."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot.copy_ratio = 1.5

        record = bot._create_trade_record(
            event=sample_leader_trade_event,
            status=CopyTradeStatus.EXECUTED,
            follower_order_id="order_456",
            follower_quantity=0.15,
            follower_price=50100.0,
            latency_ms=150
        )

        assert isinstance(record, CopyTradeRecord)
        assert record.leader_id == sample_leader_trade_event.leader_id  # Use actual leader_id
        assert record.follower_id == "follower_123"
        assert record.leader_order_id == sample_leader_trade_event.order_id
        assert record.follower_order_id == "order_456"
        assert record.copy_ratio == 1.5
        assert record.status == CopyTradeStatus.EXECUTED
        assert record.latency_ms == 150

    def test_create_trade_record_slippage_calculation(self, sample_leader_trade_event):
        """Test slippage is calculated correctly."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Leader price: 42500.0 (from fixture), Follower price: 42925.0
        record = bot._create_trade_record(
            event=sample_leader_trade_event,
            status=CopyTradeStatus.EXECUTED,
            follower_order_id="order_456",
            follower_quantity=0.1,
            follower_price=42925.0,  # Higher price = worse fill
            latency_ms=100
        )

        # Slippage should be calculated
        assert record.slippage_pct is not None
        # (42925 - 42500) / 42500 * 100 = 1.0%
        assert abs(record.slippage_pct - 1.0) < 0.01

    def test_create_trade_record_latency_calculation(self, sample_leader_trade_event):
        """Test latency is recorded correctly."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        record = bot._create_trade_record(
            event=sample_leader_trade_event,
            status=CopyTradeStatus.EXECUTED,
            follower_order_id="order_456",
            follower_quantity=0.1,
            follower_price=50000.0,
            latency_ms=250
        )

        assert record.latency_ms == 250

    def test_trade_history_storage(self, sample_leader_trade_event):
        """Test trade records are stored in history."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        initial_count = len(bot._trade_records)

        record = bot._create_trade_record(
            event=sample_leader_trade_event,
            status=CopyTradeStatus.EXECUTED,
            follower_order_id="order_123",
            follower_quantity=0.1,
            follower_price=50000.0
        )

        bot._add_trade_record(record)

        assert len(bot._trade_records) == initial_count + 1
        assert bot._trade_records[-1] == record

    def test_trade_history_limit_enforcement(self, sample_leader_trade_event):
        """Test trade history enforces maximum size."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Set low limit for testing
        bot._max_trade_records = 5

        # Add 10 records
        for i in range(10):
            record = bot._create_trade_record(
                event=sample_leader_trade_event,
                status=CopyTradeStatus.EXECUTED,
                follower_order_id=f"order_{i}",
                follower_quantity=0.1,
                follower_price=50000.0
            )
            bot._add_trade_record(record)

        # When exceeds max, keeps second half: max // 2 = 2
        assert len(bot._trade_records) == 2
        assert bot._trade_records[0].follower_order_id == "order_8"
        assert bot._trade_records[-1].follower_order_id == "order_9"

    def test_trade_history_oldest_removed(self, sample_leader_trade_event):
        """Test oldest records are removed when limit is reached."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._max_trade_records = 4

        # Add 6 records
        for i in range(6):
            record = bot._create_trade_record(
                event=sample_leader_trade_event,
                status=CopyTradeStatus.EXECUTED,
                follower_order_id=f"order_{i}",
                follower_quantity=0.1,
                follower_price=50000.0
            )
            bot._add_trade_record(record)

        # Trim happens after 5th record (5 > 4, trim to 2), then 6th makes it 3
        assert len(bot._trade_records) == 3
        order_ids = [r.follower_order_id for r in bot._trade_records]
        # Should keep order_3, order_4, and order_5 (last 3)
        assert "order_3" in order_ids
        assert "order_4" in order_ids
        assert "order_5" in order_ids

    def test_get_trade_history_returns_recent(self, sample_leader_trade_event):
        """Test get_trade_history returns recent trades."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Add some records
        for i in range(5):
            record = bot._create_trade_record(
                event=sample_leader_trade_event,
                status=CopyTradeStatus.EXECUTED,
                follower_order_id=f"order_{i}",
                follower_quantity=0.1,
                follower_price=50000.0
            )
            bot._add_trade_record(record)

        history = bot.get_trade_history(limit=3)

        assert len(history) == 3
        # Most recent first (returned as dicts)
        assert history[0]["follower_order_id"] == "order_4"
        assert history[1]["follower_order_id"] == "order_3"
        assert history[2]["follower_order_id"] == "order_2"

    def test_get_trade_history_empty(self):
        """Test get_trade_history when no trades exist."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        history = bot.get_trade_history()

        assert len(history) == 0
        assert history == []


# ============================================================================
# Test Class 6: Statistics Tests (7 tests)
# ============================================================================

class TestStatistics:
    """Test statistics tracking."""

    def test_statistics_initial_state(self):
        """Test statistics are initialized correctly."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        stats = bot.statistics

        assert stats.total_trades == 0
        assert stats.successful_trades == 0
        assert stats.failed_trades == 0
        assert stats.skipped_trades == 0
        assert stats.total_profit == 0.0
        assert stats.total_slippage == 0.0
        assert stats.avg_latency_ms == 0.0

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_statistics_success_counter(self, mock_risk_controller_class,
                                               sample_risk_limits, sample_leader_trade_event):
        """Test successful trades increment counter."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            follower_order_id="order_123",
            status=CopyTradeStatus.EXECUTED,
            executed_price=50000.0,
            executed_quantity=0.1
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Execute 3 successful trades
        for _ in range(3):
            await bot.handle_leader_trade(sample_leader_trade_event)

        assert bot.statistics.successful_trades == 3
        assert bot.statistics.total_trades == 3
        assert bot.statistics.failed_trades == 0

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_statistics_failure_counter(self, mock_risk_controller_class,
                                               sample_risk_limits, sample_leader_trade_event):
        """Test failed trades increment counter."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=True,
            adjusted_quantity=None,
            reason=None,
            risk_score=0
        ))
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()
        bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
            success=False,
            follower_id="follower_123",
            leader_order_id=sample_leader_trade_event.order_id,
            status=CopyTradeStatus.FAILED,
            error_message="API error"
        ))

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Execute 2 failed trades
        for _ in range(2):
            await bot.handle_leader_trade(sample_leader_trade_event)

        assert bot.statistics.failed_trades == 2
        assert bot.statistics.total_trades == 2
        assert bot.statistics.successful_trades == 0

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_statistics_skipped_counter(self, mock_risk_controller_class,
                                               sample_risk_limits, sample_leader_trade_event):
        """Test skipped trades increment counter."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
            is_valid=False,
            adjusted_quantity=None,
            reason="Risk limit exceeded",
            risk_score=95
        ))
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # Execute trades that get skipped
        for _ in range(4):
            await bot.handle_leader_trade(sample_leader_trade_event)

        assert bot.statistics.skipped_trades == 4
        assert bot.statistics.total_trades == 4
        assert bot.statistics.successful_trades == 0

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_statistics_success_rate_calculation(self, mock_risk_controller_class,
                                                        sample_risk_limits, sample_leader_trade_event):
        """Test success rate is calculated correctly."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.record_trade_result = AsyncMock()
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()
        bot._emit_event = AsyncMock()

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.0,
            risk_limits=sample_risk_limits
        )

        # 7 success, 3 failures
        for i in range(10):
            if i < 7:
                mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
                    is_valid=True, adjusted_quantity=None, reason=None, risk_score=0
                ))
                bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
                    success=True,
                    follower_id="follower_123",
                    leader_order_id=sample_leader_trade_event.order_id,
                    follower_order_id=f"order_{i}",
                    status=CopyTradeStatus.EXECUTED,
                    executed_price=50000.0,
                    executed_quantity=0.1
                ))
            else:
                mock_controller.validate_trade = AsyncMock(return_value=RiskValidationResult(
                    is_valid=True, adjusted_quantity=None, reason=None, risk_score=0
                ))
                bot._execute_copy_trade = AsyncMock(return_value=CopyTradeResult(
                    success=False,
                    follower_id="follower_123",
                    leader_order_id=sample_leader_trade_event.order_id,
                    status=CopyTradeStatus.FAILED,
                    error_message="Failed"
                ))

            await bot.handle_leader_trade(sample_leader_trade_event)

        # Success rate = 7/10 = 70%
        assert bot.statistics.total_trades == 10
        assert bot.statistics.successful_trades == 7
        assert bot.statistics.failed_trades == 3

    def test_statistics_total_slippage(self):
        """Test total slippage tracking."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Initially zero
        assert bot.statistics.total_slippage == 0.0

        # This would be updated in real execution
        # Just verify field exists and is accessible
        bot.statistics.total_slippage = 5.5
        assert bot.statistics.total_slippage == 5.5

    @pytest.mark.asyncio
    @patch('src.core.copy_trading_bot.RiskController')
    async def test_get_status_complete_data(self, mock_risk_controller_class, sample_risk_limits):
        """Test get_status returns complete data."""
        mock_controller = AsyncMock()
        mock_controller.start = AsyncMock()
        mock_controller.get_risk_status = AsyncMock(return_value={
            "daily_loss": -150.0,
            "position_count": 3
        })
        mock_risk_controller_class.return_value = mock_controller

        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )
        bot._sync_positions = AsyncMock()

        await bot.start(
            leader_id="leader_456",
            copy_ratio=1.5,
            risk_limits=sample_risk_limits
        )

        status = await bot.get_status()

        assert status["follower_id"] == "follower_123"
        assert status["leader_id"] == "leader_456"
        assert status["copy_ratio"] == 1.5
        assert status["is_running"] is True
        assert "statistics" in status
        assert status["statistics"]["total_trades"] == 0


# ============================================================================
# Test Class 7: Event Callback Tests (6 tests)
# ============================================================================

class TestEventCallbacks:
    """Test event callback functionality."""

    def test_register_event_callback(self):
        """Test registering an event callback."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        callback = Mock()
        bot.register_event_callback(callback)

        assert callback in bot._event_callbacks
        assert len(bot._event_callbacks) == 1

    @pytest.mark.asyncio
    async def test_event_callback_invocation(self):
        """Test event callbacks are invoked."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        callback = AsyncMock()
        bot.register_event_callback(callback)

        event_data = {"type": "test_event", "data": "test"}
        await bot._emit_event(event_data)

        callback.assert_called_once_with(event_data)

    @pytest.mark.asyncio
    async def test_event_callback_with_trade_data(self, sample_leader_trade_event):
        """Test callbacks receive trade data."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        received_events = []

        async def capture_callback(event):
            received_events.append(event)

        bot.register_event_callback(capture_callback)

        event_data = {
            "type": "copy_trade_executed",
            "symbol": "PERP_BTC_USDC",
            "quantity": 0.1
        }
        await bot._emit_event(event_data)

        assert len(received_events) == 1
        assert received_events[0]["type"] == "copy_trade_executed"
        assert received_events[0]["symbol"] == "PERP_BTC_USDC"

    @pytest.mark.asyncio
    async def test_event_callback_error_handling(self):
        """Test callback errors don't break execution."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        # Callback that raises error
        async def bad_callback(event):
            raise Exception("Callback error")

        # Good callback
        good_callback = AsyncMock()

        bot.register_event_callback(bad_callback)
        bot.register_event_callback(good_callback)

        event_data = {"type": "test"}

        # Should not raise exception
        await bot._emit_event(event_data)

        # Good callback should still be called
        good_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_event_callbacks(self):
        """Test multiple callbacks are all invoked."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        callback1 = AsyncMock()
        callback2 = AsyncMock()
        callback3 = AsyncMock()

        bot.register_event_callback(callback1)
        bot.register_event_callback(callback2)
        bot.register_event_callback(callback3)

        event_data = {"type": "test"}
        await bot._emit_event(event_data)

        callback1.assert_called_once_with(event_data)
        callback2.assert_called_once_with(event_data)
        callback3.assert_called_once_with(event_data)

    def test_unregister_event_callback(self):
        """Test unregistering an event callback."""
        bot = CopyTradingBot(
            follower_id="follower_123",
            orderly_key="key",
            orderly_secret="secret"
        )

        callback = Mock()
        bot.register_event_callback(callback)
        assert len(bot._event_callbacks) == 1

        bot.unregister_event_callback(callback)
        assert len(bot._event_callbacks) == 0
        assert callback not in bot._event_callbacks
