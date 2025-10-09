#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock utilities and helper functions for testing
"""

from unittest.mock import Mock, AsyncMock, MagicMock
from decimal import Decimal
from typing import Dict, Any, List, Optional
import json
import time


class MockOrderlyRestAPI:
    """Mock Orderly REST API client."""

    def __init__(self):
        self.orders = {}
        self.account_info = {
            "account_id": "test_account_123",
            "balance": 1000.0,
            "available_balance": 950.0
        }
        self.positions = {"rows": []}
        self.order_counter = 1000

    async def create_order(self, symbol: str, order_type: str, side: str,
                          order_price: Optional[float] = None,
                          order_quantity: Optional[float] = None,
                          **kwargs) -> Dict[str, Any]:
        """Mock create order endpoint."""
        order_id = str(self.order_counter)
        self.order_counter += 1

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "order_type": order_type,
            "side": side,
            "order_price": order_price,
            "order_quantity": order_quantity,
            "status": "NEW" if order_type == "LIMIT" else "FILLED"
        }

        self.orders[order_id] = order

        return {
            "success": True,
            "data": {"order_id": order_id}
        }

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Mock cancel order endpoint."""
        if order_id in self.orders:
            self.orders[order_id]["status"] = "CANCELLED"
            return {
                "success": True,
                "data": {"order_id": order_id}
            }
        return {
            "success": False,
            "error": "Order not found"
        }

    async def cancel_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Mock cancel all orders endpoint."""
        cancelled_count = 0
        for order_id, order in self.orders.items():
            if symbol is None or order["symbol"] == symbol:
                if order["status"] not in ["FILLED", "CANCELLED"]:
                    order["status"] = "CANCELLED"
                    cancelled_count += 1

        return {
            "success": True,
            "data": {"cancelled_count": cancelled_count}
        }

    async def get_account_information(self) -> Dict[str, Any]:
        """Mock get account info endpoint."""
        return {
            "success": True,
            "data": self.account_info
        }

    async def get_all_positions_info(self) -> Dict[str, Any]:
        """Mock get positions endpoint."""
        return {
            "success": True,
            "data": {"rows": self.positions["rows"]}
        }

    async def get_orders(self, **kwargs) -> Dict[str, Any]:
        """Mock get orders endpoint."""
        orders_list = list(self.orders.values())

        # Apply filters if provided
        if kwargs.get("symbol"):
            orders_list = [o for o in orders_list if o["symbol"] == kwargs["symbol"]]
        if kwargs.get("status"):
            orders_list = [o for o in orders_list if o["status"] == kwargs["status"]]

        return {
            "success": True,
            "data": {"rows": orders_list}
        }


class MockWebSocketAPI:
    """Mock WebSocket API client."""

    def __init__(self):
        self.messages = []
        self.subscriptions = []
        self.is_connected = False

    def get_notifications(self):
        """Mock get notifications method."""
        self.is_connected = True

    def on_message(self, ws, message):
        """Mock message handler."""
        self.messages.append(message)

    def on_error(self, ws, error):
        """Mock error handler."""
        pass

    def on_close(self, ws):
        """Mock close handler."""
        self.is_connected = False

    def close(self):
        """Mock close method."""
        self.is_connected = False

    def simulate_fill_message(self, order_data: Dict[str, Any]):
        """Simulate a WebSocket fill message."""
        fill_message = {
            "topic": "notifications",
            "data": {
                "messageType": "ORDER_FILLED",
                "contentRaw": {
                    "orderId": order_data["order_id"],
                    "executedPrice": order_data.get("executed_price", order_data.get("price")),
                    "executedQuantity": order_data.get("executed_quantity", order_data.get("quantity")),
                    "side": order_data["side"],
                    "symbol": order_data.get("symbol", "PERP_BTC_USDC"),
                    "executedTimestamp": int(time.time() * 1000)
                }
            }
        }

        if self.on_message:
            self.on_message(None, json.dumps(fill_message))


class MockMongoDB:
    """Mock MongoDB database operations."""

    def __init__(self):
        self.users = {}
        self.collections = {}

    def get_collection(self, name: str):
        """Mock get collection method."""
        if name not in self.collections:
            self.collections[name] = MockCollection()
        return self.collections[name]

    async def create_user(self, user_id: str, api_key: str, api_secret: str,
                         wallet_address: str) -> Mock:
        """Mock create user method."""
        user_data = {
            "user_id": user_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "wallet_address": wallet_address
        }
        self.users[user_id] = user_data

        result = Mock()
        result.inserted_id = user_id
        return result

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Mock get user method."""
        return self.users.get(user_id)

    async def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Mock:
        """Mock update user method."""
        if user_id in self.users:
            self.users[user_id].update(update_data)

            result = Mock()
            result.matched_count = 1
            result.modified_count = 1
            return result

        result = Mock()
        result.matched_count = 0
        result.modified_count = 0
        return result


class MockCollection:
    """Mock MongoDB collection."""

    def __init__(self):
        self.data = {}
        self.counter = 1

    async def insert_one(self, document: Dict[str, Any]) -> Mock:
        """Mock insert_one method."""
        doc_id = str(self.counter)
        self.counter += 1
        self.data[doc_id] = document

        result = Mock()
        result.inserted_id = doc_id
        return result

    async def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Mock find_one method."""
        for doc in self.data.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]) -> Mock:
        """Mock update_one method."""
        for doc_id, doc in self.data.items():
            if all(doc.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    doc.update(update["$set"])

                result = Mock()
                result.matched_count = 1
                result.modified_count = 1
                return result

        result = Mock()
        result.matched_count = 0
        result.modified_count = 0
        return result


def create_mock_position(symbol: str, quantity: float, price: float) -> Dict[str, Any]:
    """Create a mock position for testing."""
    return {
        "symbol": symbol,
        "position_qty": str(quantity),
        "mark_price": str(price),
        "entry_price": str(price),
        "unrealized_pnl": str(0.0)
    }


def create_mock_order(order_id: int, symbol: str, side: str, price: float,
                     quantity: float, status: str = "NEW") -> Dict[str, Any]:
    """Create a mock order for testing."""
    return {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "order_type": "LIMIT",
        "price": price,
        "quantity": quantity,
        "status": status,
        "timestamp": int(time.time())
    }


def create_mock_fill_event(order_id: int, symbol: str, side: str,
                          price: float, quantity: float) -> Dict[str, Any]:
    """Create a mock fill event for testing."""
    return {
        "order_id": order_id,
        "executed_price": price,
        "executed_quantity": quantity,
        "side": side,
        "symbol": symbol,
        "executed_timestamp": int(time.time() * 1000),
        "fill_id": f"{order_id}_{price}_{quantity}_{int(time.time())}"
    }


def create_mock_wallet_signature(wallet_address: str, message: str,
                               signature: str = None) -> Dict[str, Any]:
    """Create a mock wallet signature for testing."""
    if signature is None:
        signature = f"0x{'0' * 64}"

    return {
        "wallet_address": wallet_address,
        "message": message,
        "signature": signature,
        "timestamp": int(time.time())
    }


def assert_decimal_equal(actual: Decimal, expected: Decimal, tolerance: Decimal = Decimal('0.0001')):
    """Assert two Decimal values are equal within tolerance."""
    diff = abs(actual - expected)
    assert diff <= tolerance, f"Decimal values not equal: {actual} vs {expected}, diff: {diff}"


def create_sample_grid_prices(current_price: float, upper_bound: float,
                             lower_bound: float, grid_levels: int) -> List[Decimal]:
    """Create sample grid prices for testing."""
    grid_prices = []

    # Calculate grid spacing
    price_range = upper_bound - lower_bound
    grid_spacing = price_range / (grid_levels + 1)

    # Generate grid prices (excluding current price)
    for i in range(1, grid_levels + 1):
        price = lower_bound + i * grid_spacing
        if abs(price - current_price) > 0.01:  # Don't include current price
            grid_prices.append(Decimal(str(price)))

    return sorted(grid_prices)


class MockMetrics:
    """Mock metrics collector."""

    def __init__(self):
        self.counters = {}
        self.gauges = {}
        self.histograms = {}

    def increment_counter(self, name: str, tags: Dict[str, str] = None):
        """Mock increment counter."""
        key = f"{name}_{sorted(tags.items()) if tags else ''}"
        self.counters[key] = self.counters.get(key, 0) + 1

    def set_gauge(self, name: str, value: float, tags: Dict[str, str] = None):
        """Mock set gauge."""
        key = f"{name}_{sorted(tags.items()) if tags else ''}"
        self.gauges[key] = value

    def record_histogram(self, name: str, value: float, tags: Dict[str, str] = None):
        """Mock record histogram."""
        key = f"{name}_{sorted(tags.items()) if tags else ''}"
        if key not in self.histograms:
            self.histograms[key] = []
        self.histograms[key].append(value)

    def get_metrics(self) -> Dict[str, Any]:
        """Get all metrics."""
        return {
            "counters": self.counters,
            "gauges": self.gauges,
            "histograms": self.histograms
        }