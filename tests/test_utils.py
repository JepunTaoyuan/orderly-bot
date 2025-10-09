#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test utility functions and helpers
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional
import json
import time


def async_test(coro):
    """Decorator to run async test functions."""
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(coro(*args, **kwargs))
    return wrapper


def assert_dicts_equal(actual: Dict[str, Any], expected: Dict[str, Any],
                      ignore_keys: List[str] = None):
    """Assert two dictionaries are equal, ignoring specified keys."""
    if ignore_keys is None:
        ignore_keys = []

    actual_clean = {k: v for k, v in actual.items() if k not in ignore_keys}
    expected_clean = {k: v for k, v in expected.items() if k not in ignore_keys}

    assert actual_clean == expected_clean, f"Dictionaries not equal: {actual_clean} vs {expected_clean}"


def assert_lists_equal_unordered(actual: List[Any], expected: List[Any]):
    """Assert two lists contain the same elements regardless of order."""
    assert len(actual) == len(expected), f"Lists have different lengths: {len(actual)} vs {len(expected)}"
    assert all(item in expected for item in actual), f"Lists contain different elements: {actual} vs {expected}"


def convert_decimals_to_floats(obj: Any) -> Any:
    """Convert Decimal values to floats in nested data structures."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimals_to_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals_to_floats(item) for item in obj]
    else:
        return obj


def wait_for_condition(condition_func, timeout: float = 5.0, interval: float = 0.1) -> bool:
    """Wait for a condition to become true."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if condition_func():
            return True
        time.sleep(interval)
    return False


def create_test_config(**overrides) -> Dict[str, Any]:
    """Create a test configuration with optional overrides."""
    default_config = {
        "user_id": "test_user_123",
        "ticker": "PERP_BTC_USDC",
        "direction": "BOTH",
        "current_price": 42500.0,
        "upper_bound": 45000.0,
        "lower_bound": 40000.0,
        "grid_levels": 6,
        "total_margin": 100.0,
        "stop_bot_price": 38000.0,
        "stop_top_price": 47000.0,
        "user_sig": "test_signature_123",
        "timestamp": int(time.time()),
        "nonce": "test_nonce_123"
    }

    default_config.update(overrides)
    return default_config


def load_test_fixture(fixture_name: str) -> Any:
    """Load a test fixture from the fixtures directory."""
    import os

    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", f"{fixture_name}.json")

    try:
        with open(fixture_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Fixture {fixture_name} not found at {fixture_path}")


def validate_signal_structure(signal: Dict[str, Any]) -> bool:
    """Validate the structure of a trading signal."""
    required_fields = ["symbol", "side", "price", "size", "signal_type", "timestamp"]

    if not all(field in signal for field in required_fields):
        return False

    if signal["side"] not in ["BUY", "SELL"]:
        return False

    if signal["signal_type"] not in ["INITIAL", "COUNTER", "STOP", "MARKET_OPEN", "CANCEL_ALL"]:
        return False

    return True


def create_mock_event_queue():
    """Create a mock event queue for testing."""
    from unittest.mock import Mock
    from src.utils.event_queue import Event, EventType

    queue = Mock()
    queue.add_event = Mock()
    queue.start = Mock()
    queue.stop = Mock()
    queue.get_queue_size = Mock(return_value=0)

    return queue


def compare_trading_signals(signal1: Dict[str, Any], signal2: Dict[str, Any],
                           tolerance: float = 0.0001) -> bool:
    """Compare two trading signals with tolerance for numeric values."""
    keys_to_compare = ["symbol", "side", "signal_type"]

    for key in keys_to_compare:
        if signal1.get(key) != signal2.get(key):
            return False

    # Compare numeric values with tolerance
    for key in ["price", "size"]:
        val1 = float(signal1.get(key, 0))
        val2 = float(signal2.get(key, 0))
        if abs(val1 - val2) > tolerance:
            return False

    return True


def generate_test_orders(count: int, symbol: str = "PERP_BTC_USDC") -> List[Dict[str, Any]]:
    """Generate a list of test orders."""
    orders = []
    for i in range(count):
        order = {
            "order_id": 1000 + i,
            "symbol": symbol,
            "side": "BUY" if i % 2 == 0 else "SELL",
            "order_type": "LIMIT",
            "price": 42500.0 + (i * 10),
            "quantity": 0.001,
            "status": "NEW",
            "timestamp": int(time.time()) + i
        }
        orders.append(order)

    return orders


def calculate_expected_grid_prices(current_price: float, upper_bound: float,
                                 lower_bound: float, grid_levels: int) -> List[float]:
    """Calculate expected grid prices for testing."""
    prices = []

    # Calculate prices below current
    levels_below = grid_levels // 2
    if grid_levels % 2 == 0:
        levels_below = grid_levels // 2

    price_step_below = (current_price - lower_bound) / (levels_below + 1)
    for i in range(1, levels_below + 1):
        price = current_price - (i * price_step_below)
        if price >= lower_bound:
            prices.append(price)

    # Calculate prices above current
    levels_above = grid_levels - levels_below
    price_step_above = (upper_bound - current_price) / (levels_above + 1)
    for i in range(1, levels_above + 1):
        price = current_price + (i * price_step_above)
        if price <= upper_bound:
            prices.append(price)

    return sorted(prices)


def assert_api_response_structure(response: Dict[str, Any], should_have_data: bool = True):
    """Assert the structure of an API response."""
    assert "success" in response, "Response should have 'success' field"

    if should_have_data:
        assert "data" in response, "Response should have 'data' field when successful"

    if response.get("success") and should_have_data:
        assert isinstance(response["data"], (dict, list)), "Data should be dict or list"


def validate_error_response(response: Dict[str, Any], expected_error_code: str = None):
    """Validate the structure of an error response."""
    assert not response.get("success"), "Error response should have success=False"
    assert "error_code" in response, "Error response should have error_code"

    if expected_error_code:
        assert response["error_code"] == expected_error_code, f"Expected error code {expected_error_code}, got {response['error_code']}"


class AsyncContextManagerMock:
    """Mock async context manager for testing."""

    def __init__(self, return_value=None, side_effect=None):
        self.return_value = return_value
        self.side_effect = side_effect
        self.enter_called = False
        self.exit_called = False

    async def __aenter__(self):
        self.enter_called = True
        if self.side_effect:
            raise self.side_effect
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exit_called = True
        return False


def create_mock_session_manager():
    """Create a mock session manager for testing."""
    from unittest.mock import Mock

    manager = Mock()
    manager.create_session = Mock(return_value=True)
    manager.stop_session = Mock(return_value=True)
    manager.get_session_status = Mock(return_value={"is_running": True})
    manager.list_sessions = Mock(return_value={"session_1": True, "session_2": False})

    return manager


def assert_grid_config_valid(config: Dict[str, Any]):
    """Assert that a grid configuration is valid."""
    required_fields = [
        "user_id", "ticker", "direction", "current_price",
        "upper_bound", "lower_bound", "grid_levels", "total_margin"
    ]

    for field in required_fields:
        assert field in config, f"Missing required field: {field}"

    # Validate logical constraints
    assert config["lower_bound"] < config["upper_bound"], "Lower bound must be less than upper bound"
    assert config["lower_bound"] <= config["current_price"] <= config["upper_bound"], "Current price must be within bounds"
    assert config["grid_levels"] >= 2, "Grid levels must be at least 2"
    assert config["total_margin"] > 0, "Total margin must be positive"
    assert config["direction"] in ["LONG", "SHORT", "BOTH"], "Invalid direction"