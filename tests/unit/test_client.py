#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for Orderly client module
"""

import pytest
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch
from src.core.client import OrderlyClient


class TestOrderlyClient:
    """Test OrderlyClient class."""

    def test_orderly_client_initialization(self):
        """Test OrderlyClient initialization."""
        client = OrderlyClient(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        assert client.client is not None
        assert hasattr(client, 'retry_handler')
        assert hasattr(client, 'client')

    def test_orderly_client_mainnet(self):
        """Test OrderlyClient initialization for mainnet."""
        client = OrderlyClient(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=False
        )

        assert client is not None

    @pytest.mark.asyncio
    async def test_create_limit_order_success(self):
        """Test successful limit order creation."""
        with patch('src.core.client.RestAsync') as mock_rest:
            # Setup mock
            mock_client = Mock()
            mock_client.create_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "test_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.create_limit_order(
                symbol="PERP_BTC_USDC",
                side="BUY",
                price=42500.50,
                quantity=0.001
            )

            assert result["success"] == True
            assert result["data"]["order_id"] == "test_order_123"

            # Verify the mock was called with correct parameters
            mock_client.create_order.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                order_type="LIMIT",
                side="BUY",
                order_price=42500.50,
                order_quantity=0.001
            )

    @pytest.mark.asyncio
    async def test_create_limit_order_failure(self):
        """Test limit order creation failure."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.create_order = AsyncMock(side_effect=Exception("API Error"))
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            with pytest.raises(Exception):
                await client.create_limit_order(
                    symbol="PERP_BTC_USDC",
                    side="BUY",
                    price=42500.50,
                    quantity=0.001
                )

    @pytest.mark.asyncio
    async def test_create_market_order_success(self):
        """Test successful market order creation."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.create_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "test_market_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.create_market_order(
                symbol="PERP_BTC_USDC",
                side="SELL",
                quantity=0.001
            )

            assert result["success"] == True
            assert result["data"]["order_id"] == "test_market_order_123"

            mock_client.create_order.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                order_type="MARKET",
                side="SELL",
                order_quantity=0.001
            )

    @pytest.mark.asyncio
    async def test_cancel_order_success(self):
        """Test successful order cancellation."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.cancel_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "test_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.cancel_order(
                symbol="PERP_BTC_USDC",
                order_id="test_order_123"
            )

            assert result["success"] == True
            assert result["data"]["order_id"] == "test_order_123"

            mock_client.cancel_order.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                order_id="test_order_123"
            )

    @pytest.mark.asyncio
    async def test_cancel_all_orders_success(self):
        """Test successful cancellation of all orders."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.cancel_orders = AsyncMock(return_value={
                "success": True,
                "data": {"cancelled_count": 5}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.cancel_all_orders("PERP_BTC_USDC")

            assert result["success"] == True
            assert result["data"]["cancelled_count"] == 5

            mock_client.cancel_orders.assert_called_once_with(symbol="PERP_BTC_USDC")

    @pytest.mark.asyncio
    async def test_cancel_all_orders_no_symbol(self):
        """Test cancellation of all orders without specifying symbol."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.cancel_orders = AsyncMock(return_value={
                "success": True,
                "data": {"cancelled_count": 10}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.cancel_all_orders()

            assert result["success"] == True
            assert result["data"]["cancelled_count"] == 10

            mock_client.cancel_orders.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_get_account_info_success(self):
        """Test successful account info retrieval."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_account_information = AsyncMock(return_value={
                "success": True,
                "data": {
                    "account_id": "test_account_123",
                    "balance": 1000.0,
                    "available_balance": 950.0
                }
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.get_account_info()

            assert result["success"] == True
            assert result["data"]["account_id"] == "test_account_123"
            assert result["data"]["balance"] == 1000.0

            mock_client.get_account_information.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_positions_success(self):
        """Test successful positions retrieval."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_all_positions_info = AsyncMock(return_value=[
                {
                    "symbol": "PERP_BTC_USDC",
                    "position_qty": "0.001",
                    "mark_price": "42500.50",
                    "unrealized_pnl": "2.50"
                }
            ])
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.get_positions()

            assert result["success"] == True
            assert len(result["data"]["rows"]) == 1
            assert result["data"]["rows"][0]["symbol"] == "PERP_BTC_USDC"

            mock_client.get_all_positions_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_positions_method_not_available(self):
        """Test positions retrieval when method is not available."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            # Method doesn't exist
            delattr(mock_client, 'get_all_positions_info')
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.get_positions()

            # Should return empty positions gracefully
            assert result["success"] == True
            assert result["data"]["rows"] == []

    @pytest.mark.asyncio
    async def test_get_orders_success(self):
        """Test successful orders retrieval."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_orders = AsyncMock(return_value={
                "success": True,
                "data": {
                    "rows": [
                        {
                            "order_id": "order_1",
                            "symbol": "PERP_BTC_USDC",
                            "side": "BUY",
                            "order_type": "LIMIT",
                            "price": 42500.50,
                            "quantity": 0.001,
                            "status": "FILLED"
                        },
                        {
                            "order_id": "order_2",
                            "symbol": "PERP_BTC_USDC",
                            "side": "SELL",
                            "order_type": "LIMIT",
                            "price": 42600.00,
                            "quantity": 0.001,
                            "status": "NEW"
                        }
                    ]
                }
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.get_orders()

            assert result["success"] == True
            assert len(result["data"]["rows"]) == 2
            assert result["data"]["rows"][0]["order_id"] == "order_1"

            mock_client.get_orders.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_get_orders_with_filters(self):
        """Test orders retrieval with filters."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_orders = AsyncMock(return_value={
                "success": True,
                "data": {
                    "rows": [
                        {
                            "order_id": "order_1",
                            "symbol": "PERP_BTC_USDC",
                            "status": "FILLED"
                        }
                    ]
                }
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.get_orders(
                symbol="PERP_BTC_USDC",
                status="FILLED"
            )

            assert result["success"] == True
            assert len(result["data"]["rows"]) == 1

            mock_client.get_orders.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                status="FILLED"
            )

    @pytest.mark.asyncio
    async def test_close_position_no_positions(self):
        """Test closing position when no positions exist."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_positions = AsyncMock(return_value={
                "success": True,
                "data": {"rows": []}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.close_position("PERP_BTC_USDC")

            assert result["success"] == False
            assert result["message"] == "未找到持倉"

    @pytest.mark.asyncio
    async def test_close_position_with_positions(self):
        """Test closing position with existing position."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_all_positions_info = AsyncMock(return_value={
                "success": True,
                "data": {
                    "rows": [
                        {
                            "symbol": "PERP_BTC_USDC",
                            "position_qty": "0.001",
                            "mark_price": "42500.50"
                        }
                    ]
                }
            })
            mock_client.create_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "close_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.close_position("PERP_BTC_USDC")

            assert result["success"] == True
            assert result["data"]["order_id"] == "close_order_123"

            # Should create market order
            mock_client.create_order.assert_called_once()
            call_args = mock_client.create_order.call_args[1]
            assert call_args["symbol"] == "PERP_BTC_USDC"
            assert call_args["side"] == "SELL"  # Close long position
            assert call_args["order_quantity"] == 0.001

    @pytest.mark.asyncio
    async def test_close_position_partial_quantity(self):
        """Test closing position with specific quantity."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_all_positions_info = AsyncMock(return_value={
                "success": True,
                "data": {
                    "rows": [
                        {
                            "symbol": "PERP_BTC_USDC",
                            "position_qty": "0.002",
                            "mark_price": "42500.50"
                        }
                    ]
                }
            })
            mock_client.create_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "close_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.close_position("PERP_BTC_USDC", quantity=0.001)

            assert result["success"] == True

            # Should close specified quantity
            call_args = mock_client.create_order.call_args[1]
            assert call_args["order_quantity"] == 0.001

    @pytest.mark.asyncio
    async def test_close_position_short_position(self):
        """Test closing short position."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.get_all_positions_info = AsyncMock(return_value={
                "success": True,
                "data": {
                    "rows": [
                        {
                            "symbol": "PERP_BTC_USDC",
                            "position_qty": "-0.001",  # Short position
                            "mark_price": "42500.50"
                        }
                    ]
                }
            })
            mock_client.create_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "close_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            result = await client.close_position("PERP_BTC_USDC")

            assert result["success"] == True

            # Should create BUY order to close short position
            call_args = mock_client.create_order.call_args[1]
            assert call_args["side"] == "BUY"

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test error handling in client methods."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.create_order = AsyncMock(side_effect=Exception("Connection timeout"))
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            with pytest.raises(Exception):
                await client.create_limit_order(
                    symbol="PERP_BTC_USDC",
                    side="BUY",
                    price=42500.50,
                    quantity=0.001
                )

    def test_retry_handler_initialization(self):
        """Test that retry handler is properly initialized."""
        client = OrderlyClient(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        assert client.retry_handler is not None
        assert hasattr(client.retry_handler, 'config')

    @pytest.mark.asyncio
    async def test_parameter_validation(self):
        """Test parameter validation in API calls."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()
            mock_client.create_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "test_order_123"}
            })
            mock_rest.return_value = mock_client

            client = OrderlyClient(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            # Test with valid parameters
            result = await client.create_limit_order(
                symbol="PERP_BTC_USDC",
                side="BUY",
                price=42500.50,
                quantity=0.001
            )

            assert result["success"] == True

            # Verify correct parameters were passed
            call_args = mock_client.create_order.call_args[1]
            assert call_args["symbol"] == "PERP_BTC_USDC"
            assert call_args["side"] == "BUY"
            assert call_args["order_price"] == 42500.50
            assert call_args["order_quantity"] == 0.001

    @pytest.mark.asyncio
    async def test_response_format_standardization(self):
        """Test that responses are properly standardized."""
        with patch('src.core.client.RestAsync') as mock_rest:
            mock_client = Mock()

            # Test different response formats
            test_cases = [
                # Standard response
                {"success": True, "data": {"order_id": "123"}},
                # Response without success field
                {"order_id": "123"},
                # Response with different structure
                {"result": "success", "order": {"id": "123"}}
            ]

            for response_data in test_cases:
                mock_client.create_order = AsyncMock(return_value=response_data)
                mock_rest.return_value = mock_client

                client = OrderlyClient(
                    account_id="test_account_123",
                    orderly_key="test_key_123",
                    orderly_secret="test_secret_123",
                    orderly_testnet=True
                )

                result = await client.create_limit_order(
                    symbol="PERP_BTC_USDC",
                    side="BUY",
                    price=42500.50,
                    quantity=0.001
                )

                # Should handle different response formats gracefully
                assert result is not None