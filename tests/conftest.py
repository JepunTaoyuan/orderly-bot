#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest configuration and shared fixtures
"""

import pytest
import asyncio
import json
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, MagicMock
from typing import Dict, Any, Optional
import tempfile
import os

# Import project modules
from src.utils.error_codes import ErrorCode, GridTradingException
from src.core.grid_signal import Direction, OrderSide, TradingSignal


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_mongo_uri():
    """Mock MongoDB URI for testing."""
    return "mongodb://localhost:27017/test_grid_bot"


@pytest.fixture
def mock_orderly_credentials():
    """Mock Orderly API credentials."""
    return {
        "orderly_account_id": "test_account_123",
        "orderly_key": "test_key_123456789",
        "orderly_secret": "test_secret_123456789",
        "orderly_testnet": True
    }


@pytest.fixture
def sample_market_info():
    """Sample market information for testing."""
    return {
        "symbol": "PERP_BTC_USDC",
        "tick_size": Decimal("0.01"),
        "step_size": Decimal("0.0001"),
        "min_notional": Decimal("1.0"),
        "min_price": Decimal("0.01"),
        "max_price": Decimal("1000000"),
        "min_quantity": Decimal("0.0001"),
        "max_quantity": Decimal("1000")
    }


@pytest.fixture
def sample_grid_config():
    """Sample grid trading configuration."""
    return {
        "user_id": "test_user_123",
        "ticker": "PERP_BTC_USDC",
        "direction": Direction.BOTH,
        "current_price": 42500.0,
        "upper_bound": 45000.0,
        "lower_bound": 40000.0,
        "grid_levels": 6,
        "total_margin": 100.0,
        "stop_bot_price": 38000.0,
        "stop_top_price": 47000.0,
        "user_sig": "test_signature_123",
        "timestamp": 1234567890,
        "nonce": "test_nonce_123"
    }


@pytest.fixture
def sample_user_data():
    """Sample user data for testing."""
    return {
        "user_id": "test_user_123",
        "api_key": "test_api_key_123",
        "api_secret": "test_api_secret_123",
        "wallet_address": "0x1234567890123456789012345678901234567890",
        "evm_wallet_address": "0x1234567890123456789012345678901234567890"
    }


@pytest.fixture
def sample_trading_signal():
    """Sample trading signal for testing."""
    return TradingSignal(
        symbol="PERP_BTC_USDC",
        side=OrderSide.BUY,
        price=Decimal("42500.50"),
        size=Decimal("0.001"),
        signal_type="INITIAL",
        timestamp=1234567890.0
    )


@pytest.fixture
def mock_orderly_client():
    """Mock Orderly client for testing."""
    client = Mock()
    client.create_limit_order = AsyncMock(return_value={
        "success": True,
        "data": {"order_id": "test_order_123"}
    })
    client.create_market_order = AsyncMock(return_value={
        "success": True,
        "data": {"order_id": "test_market_order_123"}
    })
    client.cancel_order = AsyncMock(return_value={
        "success": True,
        "data": {"order_id": "test_order_123"}
    })
    client.cancel_all_orders = AsyncMock(return_value={
        "success": True,
        "data": {"cancelled_count": 5}
    })
    client.get_account_info = AsyncMock(return_value={
        "success": True,
        "data": {"account_id": "test_account_123", "balance": 1000.0}
    })
    client.get_positions = AsyncMock(return_value={
        "success": True,
        "data": {"rows": []}
    })
    client.get_orders = AsyncMock(return_value={
        "success": True,
        "data": {"rows": []}
    })
    return client


@pytest.fixture
def mock_websocket_client():
    """Mock WebSocket client for testing."""
    client = Mock()
    client.get_notifications = Mock()
    client.close = Mock()
    return client


@pytest.fixture
def mock_mongo_manager():
    """Mock MongoDB manager for testing."""
    manager = Mock()
    manager.get_user = AsyncMock(return_value={
        "user_id": "test_user_123",
        "api_key": "test_api_key_123",
        "api_secret": "test_api_secret_123",
        "wallet_address": "0x1234567890123456789012345678901234567890"
    })
    manager.create_user = AsyncMock(return_value=Mock(inserted_id="test_user_123"))
    manager.update_user = AsyncMock(return_value=Mock(
        matched_count=1, modified_count=1
    ))
    return manager


@pytest.fixture
def temp_db_file():
    """Create a temporary database file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
        json.dump({}, f)

    yield temp_file

    # Cleanup
    if os.path.exists(temp_file):
        os.unlink(temp_file)


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    logger = Mock()
    logger.info = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    logger.debug = Mock()
    logger.critical = Mock()
    return logger


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        "order_id": 12345,
        "symbol": "PERP_BTC_USDC",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 42500.50,
        "quantity": 0.001,
        "status": "FILLED"
    }


@pytest.fixture
def sample_fill_event():
    """Sample WebSocket fill event for testing."""
    return {
        "order_id": 12345,
        "executed_price": 42500.50,
        "executed_quantity": 0.001,
        "side": "BUY",
        "fill_id": "12345_42500.50_0.001_1234567890"
    }


@pytest.fixture
def mock_market_validator():
    """Mock market validator for testing."""
    validator = Mock()
    validator.validate_order = Mock(return_value=(
        Decimal("42500.50"), Decimal("0.001")
    ))
    validator.validate_config = Mock(return_value={
        "ticker": "PERP_BTC_USDC",
        "direction": Direction.BOTH,
        "_market_info": Mock(),
        "_orderly_symbol": "PERP_BTC_USDC"
    })
    return validator


@pytest.fixture
def sample_wallet_signature():
    """Sample wallet signature for testing."""
    return "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"


@pytest.fixture
def sample_challenge():
    """Sample challenge for wallet signature verification."""
    return {
        "challenge": "test_challenge_123456",
        "timestamp": 1234567890,
        "nonce": "test_nonce_123"
    }


# Test data fixtures for different scenarios
@pytest.fixture(params=[
    Direction.LONG,
    Direction.SHORT,
    Direction.BOTH
])
def all_directions(request):
    """Provide all possible trading directions."""
    return request.param


@pytest.fixture(params=[
    OrderSide.BUY,
    OrderSide.SELL
])
def all_order_sides(request):
    """Provide all possible order sides."""
    return request.param


@pytest.fixture
def error_response_scenarios():
    """Various error response scenarios for testing."""
    return [
        {"success": False, "error": "Invalid symbol"},
        {"success": False, "error": "Insufficient balance"},
        {"success": False, "error": "Rate limit exceeded"},
        {"success": False, "error": "Network timeout"},
    ]


# Async test helper
@pytest.fixture
def async_test():
    """Helper for running async test functions."""
    def run_async(coro):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro)
    return run_async


# Environment variables for testing
@pytest.fixture(autouse=True)
def set_test_env_vars():
    """Set environment variables for testing."""
    os.environ["MONGODB_URI"] = "mongodb://localhost:27017/test_grid_bot"
    os.environ["UVICORN_HOST"] = "127.0.0.1"
    os.environ["UVICORN_PORT"] = "8001"
    os.environ["LOG_LEVEL"] = "DEBUG"

    yield

    # Cleanup
    for key in ["MONGODB_URI", "UVICORN_HOST", "UVICORN_PORT", "LOG_LEVEL"]:
        if key in os.environ:
            del os.environ[key]


# Mock response fixtures
@pytest.fixture
def mock_successful_api_response():
    """Standard successful API response."""
    return {
        "success": True,
        "data": {"result": "success"},
        "timestamp": 1234567890
    }


@pytest.fixture
def mock_failed_api_response():
    """Standard failed API response."""
    return {
        "success": False,
        "error": "Test error message",
        "error_code": "E1000",
        "timestamp": 1234567890
    }


# ============================================================================
# Copy Trading Fixtures
# ============================================================================

@pytest.fixture
def mock_copy_trading_websocket():
    """Mock WebSocket client for Leader monitoring with execution reports."""
    client = Mock()
    client.get_execution_report = Mock()
    client.get_position = Mock()
    client.stop = Mock()
    client.close = Mock()
    client.on_message = None
    client.on_error = None
    client.on_close = None
    client.is_connected = True
    return client


@pytest.fixture
def sample_leader_trade_event():
    """Sample leader trade event for testing."""
    from datetime import datetime
    from src.models.copy_trading import LeaderTradeEvent, CopyOrderSide, CopyOrderType, CopyTradeAction

    return LeaderTradeEvent(
        leader_id="leader_123",
        order_id="order_456789",
        symbol="PERP_BTC_USDC",
        side=CopyOrderSide.BUY,
        order_type=CopyOrderType.MARKET,
        price=42500.0,
        quantity=0.1,
        action=CopyTradeAction.OPEN,
        timestamp=datetime.utcnow(),
        raw_data={"orderId": "order_456789", "status": "FILLED"}
    )


@pytest.fixture
def sample_execution_report():
    """Sample WebSocket execution report data."""
    import time
    return {
        "topic": "executionreport",
        "data": {
            "orderId": "order_123456",
            "symbol": "PERP_BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "status": "FILLED",
            "executedPrice": 42500.50,
            "executedQty": 0.1,
            "avgPrice": 42500.50,
            "timestamp": int(time.time() * 1000),
            "reduceOnly": False
        }
    }


@pytest.fixture
def sample_follower_config():
    """Sample follower configuration."""
    return {
        "follower_id": "follower_123",
        "leader_id": "leader_456",
        "copy_ratio": 1.0,
        "orderly_key": "test_key_follower",
        "orderly_secret": "test_secret_follower",
        "orderly_testnet": True
    }


@pytest.fixture(params=[0.1, 1.0, 2.5, 10.0])
def parametrized_copy_ratios(request):
    """Test different copy ratios."""
    return request.param


@pytest.fixture
def sample_risk_limits():
    """Sample risk limits configuration."""
    from src.models.copy_trading import RiskLimits

    return RiskLimits(
        max_per_trade_amount=1000.0,
        daily_max_loss=500.0,
        max_position_count=10,
        max_position_value=10000.0,
        max_single_position_ratio=0.3
    )


@pytest.fixture
def strict_risk_limits():
    """Strict risk limits for testing edge cases."""
    from src.models.copy_trading import RiskLimits

    return RiskLimits(
        max_per_trade_amount=100.0,
        daily_max_loss=50.0,
        max_position_count=3,
        max_position_value=1000.0,
        max_single_position_ratio=0.2
    )


@pytest.fixture
def sample_copy_trade_result():
    """Sample copy trade execution result."""
    from src.models.copy_trading import CopyTradeResult, CopyTradeStatus

    return CopyTradeResult(
        success=True,
        follower_id="follower_123",
        leader_order_id="leader_order_456",
        follower_order_id="follower_order_789",
        status=CopyTradeStatus.EXECUTED,
        executed_price=42500.50,
        executed_quantity=0.1,
        latency_ms=150
    )


@pytest.fixture
def sample_positions_data():
    """Sample positions data from API."""
    return {
        "success": True,
        "data": {
            "rows": [
                {
                    "symbol": "PERP_BTC_USDC",
                    "position_qty": "0.5",
                    "average_open_price": "42000.0",
                    "mark_price": "42500.0",
                    "unsettled_pnl": "250.0"
                },
                {
                    "symbol": "PERP_ETH_USDC",
                    "position_qty": "-0.3",
                    "average_open_price": "2800.0",
                    "mark_price": "2750.0",
                    "unsettled_pnl": "-50.0"
                }
            ]
        }
    }


@pytest.fixture
def mock_risk_controller():
    """Mock RiskController for testing."""
    from src.models.copy_trading import ValidationResult

    controller = Mock()
    controller.validate_trade = AsyncMock(return_value=ValidationResult(
        is_valid=True,
        reason="",
        adjusted_quantity=None,
        risk_score=0
    ))
    controller.record_trade_result = AsyncMock()
    controller.sync_positions = AsyncMock()
    controller.start = AsyncMock()
    controller.stop = AsyncMock()
    controller.get_risk_status = Mock(return_value={
        "daily_loss": 0.0,
        "position_count": 0,
        "total_position_value": 0.0
    })
    return controller


@pytest.fixture
def mock_leader_monitor():
    """Mock LeaderMonitor for testing."""
    monitor = Mock()
    monitor.start_monitoring = AsyncMock()
    monitor.stop_monitoring = AsyncMock()
    monitor.register_trade_callback = Mock()
    monitor.is_monitoring = False
    monitor.get_health_status = Mock(return_value={
        "is_connected": True,
        "trades_processed": 0,
        "errors": 0
    })
    return monitor


@pytest.fixture
def mock_copy_trading_bot():
    """Mock CopyTradingBot for testing."""
    bot = Mock()
    bot.start = AsyncMock()
    bot.stop = AsyncMock()
    bot.handle_leader_trade = AsyncMock()
    bot.is_running = False
    bot.get_status = Mock(return_value={
        "is_running": False,
        "trades_copied": 0,
        "success_rate": 0.0
    })
    bot.get_trade_history = Mock(return_value=[])
    return bot