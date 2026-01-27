#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for copy trading Pydantic models
"""

import pytest
from datetime import datetime
from pydantic import ValidationError

from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeResult,
    RiskLimits,
    CopyOrderSide,
    CopyOrderType,
    CopyTradeAction,
    CopyTradeStatus,
    FollowerConfig,
    CopyTradeRecord
)


class TestLeaderTradeEvent:
    """Test LeaderTradeEvent model."""

    def test_leader_trade_event_valid_creation(self):
        """Test creating a valid LeaderTradeEvent."""
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_456",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.BUY,
            order_type=CopyOrderType.MARKET,
            price=42500.0,
            quantity=0.1,
            action=CopyTradeAction.OPEN,
            timestamp=datetime.utcnow(),
            raw_data={"test": "data"}
        )

        assert event.leader_id == "leader_123"
        assert event.order_id == "order_456"
        assert event.symbol == "PERP_BTC_USDC"
        assert event.side == CopyOrderSide.BUY
        assert event.order_type == CopyOrderType.MARKET
        assert event.price == 42500.0
        assert event.quantity == 0.1
        assert event.action == CopyTradeAction.OPEN

    def test_leader_trade_event_field_validation(self):
        """Test field validation for LeaderTradeEvent."""
        # Test missing required field
        with pytest.raises(ValidationError):
            LeaderTradeEvent(
                leader_id="leader_123",
                # Missing order_id
                symbol="PERP_BTC_USDC",
                side=CopyOrderSide.BUY,
                order_type=CopyOrderType.MARKET,
                price=42500.0,
                quantity=0.1,
                action=CopyTradeAction.OPEN,
                timestamp=datetime.utcnow()
            )

    def test_leader_trade_event_enum_validation(self):
        """Test enum validation for LeaderTradeEvent."""
        # Valid enums should work
        event = LeaderTradeEvent(
            leader_id="leader_123",
            order_id="order_456",
            symbol="PERP_BTC_USDC",
            side=CopyOrderSide.SELL,
            order_type=CopyOrderType.LIMIT,
            price=42500.0,
            quantity=0.1,
            action=CopyTradeAction.CLOSE,
            timestamp=datetime.utcnow()
        )

        assert event.side == CopyOrderSide.SELL
        assert event.order_type == CopyOrderType.LIMIT
        assert event.action == CopyTradeAction.CLOSE


class TestCopyTradeResult:
    """Test CopyTradeResult model."""

    def test_copy_trade_result_success(self):
        """Test creating a successful CopyTradeResult."""
        result = CopyTradeResult(
            success=True,
            follower_id="follower_123",
            leader_order_id="leader_order_456",
            follower_order_id="follower_order_789",
            status=CopyTradeStatus.EXECUTED,
            executed_price=42500.50,
            executed_quantity=0.1,
            latency_ms=150
        )

        assert result.success is True
        assert result.follower_id == "follower_123"
        assert result.status == CopyTradeStatus.EXECUTED
        assert result.executed_price == 42500.50
        assert result.latency_ms == 150

    def test_copy_trade_result_failure(self):
        """Test creating a failed CopyTradeResult."""
        result = CopyTradeResult(
            success=False,
            follower_id="follower_123",
            leader_order_id="leader_order_456",
            status=CopyTradeStatus.FAILED,
            error_message="Insufficient balance"
        )

        assert result.success is False
        assert result.status == CopyTradeStatus.FAILED
        assert result.error_message == "Insufficient balance"
        assert result.follower_order_id is None

    def test_copy_trade_result_validation(self):
        """Test validation for CopyTradeResult."""
        # Test missing required fields
        with pytest.raises(ValidationError):
            CopyTradeResult(
                success=True,
                # Missing follower_id
                leader_order_id="leader_order_456",
                status=CopyTradeStatus.EXECUTED
            )


class TestRiskLimits:
    """Test RiskLimits model."""

    def test_risk_limits_valid_values(self):
        """Test creating RiskLimits with valid values."""
        limits = RiskLimits(
            max_per_trade_amount=1000.0,
            daily_max_loss=500.0,
            max_position_count=10,
            max_position_value=10000.0,
            max_single_position_ratio=0.3
        )

        assert limits.max_per_trade_amount == 1000.0
        assert limits.daily_max_loss == 500.0
        assert limits.max_position_count == 10
        assert limits.max_position_value == 10000.0
        assert limits.max_single_position_ratio == 0.3

    def test_risk_limits_default_values(self):
        """Test RiskLimits default values."""
        limits = RiskLimits()

        # Check that defaults are set (actual values depend on implementation)
        assert limits.max_per_trade_amount is not None
        assert limits.daily_max_loss is not None
        assert limits.max_position_count is not None
        assert limits.max_position_value is not None
        assert limits.max_single_position_ratio is not None

    def test_risk_limits_ratio_bounds_validation(self):
        """Test ratio bounds validation (>0, <=1)."""
        # Valid ratio
        limits = RiskLimits(max_single_position_ratio=0.5)
        assert limits.max_single_position_ratio == 0.5

        # Test boundary values (gt=0 means > 0, so 0.0 is invalid)
        limits_min = RiskLimits(max_single_position_ratio=0.01)  # minimum valid value
        assert limits_min.max_single_position_ratio == 0.01

        limits_max = RiskLimits(max_single_position_ratio=1.0)
        assert limits_max.max_single_position_ratio == 1.0


class TestFollowerConfig:
    """Test FollowerConfig model."""

    def test_follower_config_valid_copy_ratio(self):
        """Test valid copy ratio in FollowerConfig."""
        config = FollowerConfig(
            follower_id="follower_123",
            leader_id="leader_456",
            copy_ratio=1.0
        )
        assert config.copy_ratio == 1.0

    def test_follower_config_copy_ratio_too_low(self):
        """Test copy ratio < 0.1 raises ValidationError."""
        with pytest.raises(ValidationError):
            FollowerConfig(
                follower_id="follower_123",
                leader_id="leader_456",
                copy_ratio=0.05  # Too low
            )

    def test_follower_config_copy_ratio_too_high(self):
        """Test copy ratio > 10.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            FollowerConfig(
                follower_id="follower_123",
                leader_id="leader_456",
                copy_ratio=15.0  # Too high
            )

    def test_follower_config_complete_structure(self):
        """Test complete FollowerConfig structure."""
        config = FollowerConfig(
            follower_id="follower_123",
            leader_id="leader_456",
            copy_ratio=2.0,
            is_active=True
        )
        assert config.follower_id == "follower_123"
        assert config.leader_id == "leader_456"
        assert config.copy_ratio == 2.0
        assert config.is_active is True
        assert config.risk_limits is not None


class TestCopyTradeRecord:
    """Test CopyTradeRecord model."""

    def test_copy_trade_record_slippage_calculation(self):
        """Test slippage calculation."""
        record = CopyTradeRecord(
            leader_id="leader_123",
            follower_id="follower_456",
            leader_order_id="order_789",
            symbol="PERP_BTC_USDC",
            action=CopyTradeAction.OPEN,
            order_type=CopyOrderType.MARKET,
            side=CopyOrderSide.BUY,
            leader_price=42500.0,
            leader_quantity=0.1,
            follower_price=42550.0,  # $50 slippage
            follower_quantity=0.1,
            copy_ratio=1.0,
            leader_timestamp=datetime.utcnow()
        )

        record.calculate_slippage()
        assert record.slippage == 50.0
        assert abs(record.slippage_pct - 0.1176) < 0.01  # ~0.1176%

    def test_copy_trade_record_latency_calculation(self):
        """Test latency calculation."""
        leader_time = datetime(2024, 1, 1, 12, 0, 0)
        follower_time = datetime(2024, 1, 1, 12, 0, 0, 150000)  # +150ms

        record = CopyTradeRecord(
            leader_id="leader_123",
            follower_id="follower_456",
            leader_order_id="order_789",
            symbol="PERP_BTC_USDC",
            action=CopyTradeAction.OPEN,
            order_type=CopyOrderType.MARKET,
            side=CopyOrderSide.BUY,
            leader_price=42500.0,
            leader_quantity=0.1,
            copy_ratio=1.0,
            leader_timestamp=leader_time,
            follower_timestamp=follower_time
        )

        record.calculate_latency()
        assert record.latency_ms == 150

    def test_copy_trade_record_timestamp_handling(self):
        """Test timestamp handling in CopyTradeRecord."""
        now = datetime.utcnow()
        record = CopyTradeRecord(
            leader_id="leader_123",
            follower_id="follower_456",
            leader_order_id="order_789",
            symbol="PERP_BTC_USDC",
            action=CopyTradeAction.OPEN,
            order_type=CopyOrderType.MARKET,
            side=CopyOrderSide.BUY,
            leader_price=42500.0,
            leader_quantity=0.1,
            copy_ratio=1.0,
            leader_timestamp=now
        )

        assert record.leader_timestamp == now
        assert record.created_at is not None


class TestEnums:
    """Test copy trading enums."""

    def test_copy_trade_action_values(self):
        """Test CopyTradeAction enum values."""
        assert CopyTradeAction.OPEN.value == "open"
        assert CopyTradeAction.ADD.value == "add"
        assert CopyTradeAction.REDUCE.value == "reduce"
        assert CopyTradeAction.CLOSE.value == "close"

    def test_copy_order_side_values(self):
        """Test CopyOrderSide enum values."""
        assert CopyOrderSide.BUY.value == "BUY"
        assert CopyOrderSide.SELL.value == "SELL"

    def test_copy_order_type_values(self):
        """Test CopyOrderType enum values."""
        assert CopyOrderType.MARKET.value == "MARKET"
        assert CopyOrderType.LIMIT.value == "LIMIT"

    def test_copy_trade_status_values(self):
        """Test CopyTradeStatus enum values."""
        assert hasattr(CopyTradeStatus, "EXECUTED")
        assert hasattr(CopyTradeStatus, "FAILED")
        assert hasattr(CopyTradeStatus, "SKIPPED")
