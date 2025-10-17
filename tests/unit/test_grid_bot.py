#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for grid bot module
"""

import pytest
import asyncio
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.core.grid_bot import GridTradingBot
from src.core.grid_signal import Direction, OrderSide, TradingSignal


class TestGridTradingBot:
    """Test GridTradingBot class."""

    def test_grid_trading_bot_initialization(self):
        """Test GridTradingBot initialization."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        assert bot.client is not None
        assert bot.signal_generator is None
        assert bot.active_orders == {}
        assert bot.grid_orders == {}
        assert bot.is_running == False
        assert bot.wss_client is None
        assert bot.event_queue is None
        assert bot.profit_tracker is None

    def test_convert_side(self):
        """Test side conversion helper method."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        assert bot._convert_side(OrderSide.BUY) == "BUY"
        assert bot._convert_side(OrderSide.SELL) == "SELL"

    def test_safe_close_ws(self):
        """Test safe WebSocket closing."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        # Test with no WebSocket client
        bot._safe_close_ws()  # Should not raise error

        # Test with WebSocket client that has close method
        mock_ws = Mock()
        mock_ws.close = Mock()
        bot.wss_client = mock_ws

        bot._safe_close_ws()
        mock_ws.close.assert_called_once()

        # Test with WebSocket client that has different close method
        mock_ws2 = Mock()
        del mock_ws2.close
        mock_ws2.disconnect = Mock()
        bot.wss_client = mock_ws2

        bot._safe_close_ws()
        mock_ws2.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_websocket(self):
        """Test WebSocket setup."""
        with patch('src.core.grid_bot.WebsocketPrivateAPIClient') as mock_ws_class:
            mock_ws = Mock()
            mock_ws_class.return_value = mock_ws

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot._setup_websocket(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            mock_ws_class.assert_called_once()
            assert bot.wss_client == mock_ws

    @pytest.mark.asyncio
    async def test_setup_websocket_error_handling(self):
        """Test WebSocket setup error handling."""
        with patch('src.core.grid_bot.WebsocketPrivateAPIClient') as mock_ws_class:
            mock_ws_class.side_effect = Exception("WebSocket setup failed")

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            # Should not raise error, just log warning
            bot._setup_websocket(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            # WebSocket client should remain None
            assert bot.wss_client is None

    @pytest.mark.asyncio
    async def test_handle_order_filled_success(self):
        """Test successful order fill handling."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            with patch('src.core.grid_bot.GridSignalGenerator') as mock_signal_class:
                # Setup mocks
                mock_client = Mock()
                mock_client_class.return_value = mock_client

                mock_signal = Mock()
                mock_signal_class.return_value = mock_signal
                mock_signal.on_order_filled = Mock()

                bot = GridTradingBot(
                    account_id="test_account_123",
                    orderly_key="test_key_123",
                    orderly_secret="test_secret_123",
                    orderly_testnet=True
                )

                bot.signal_generator = mock_signal
                bot.active_orders[12345] = {
                    "price": Decimal("42500.50"),
                    "side": "BUY",
                    "quantity": Decimal("0.001")
                }

                await bot._handle_order_filled(
                    order_id=12345,
                    executed_price=42500.50,
                    executed_quantity=0.001,
                    side="BUY"
                )

                # Should call signal generator
                mock_signal.on_order_filled.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_order_filled_no_matching_order(self):
        """Test order fill handling for non-matching order."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot.active_orders = {}  # No active orders

            # Should not raise error
            await bot._handle_order_filled(
                order_id=99999,
                executed_price=42500.50,
                executed_quantity=0.001,
                side="BUY"
            )

    @pytest.mark.asyncio
    async def test_create_grid_order_success(self):
        """Test successful grid order creation."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.create_limit_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "grid_order_123"}
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            # Mock market info and signal generator
            bot.signal_generator = Mock()
            bot.signal_generator.quantity_per_grid = Decimal("0.001")
            bot.market_info = Mock()
            bot.market_info.symbol = "PERP_BTC_USDC"

            await bot._create_grid_order(42500.50, "BUY")

            assert 12345 in bot.active_orders
            assert 42500.50 in bot.grid_orders
            assert bot.grid_orders[42500.50] == 12345

    @pytest.mark.asyncio
    async def test_create_grid_order_duplicate(self):
        """Test creating duplicate grid order."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.create_limit_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "grid_order_123"}
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot.signal_generator = Mock()
            bot.signal_generator.quantity_per_grid = Decimal("0.001")
            bot.market_info = Mock()
            bot.market_info.symbol = "PERP_BTC_USDC"

            # Create first order
            await bot._create_grid_order(42500.50, "BUY")
            assert bot.grid_orders[42500.50] == 12345

            # Try to create duplicate order
            await bot._create_grid_order(42500.50, "BUY")
            # Should skip without creating new order

            # Still only one order
            assert len(bot.active_orders) == 1
            assert bot.grid_orders[42500.50] == 12345

    @pytest.mark.asyncio
    async def test_create_grid_order_api_failure(self):
        """Test grid order creation API failure."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.create_limit_order = AsyncMock(side_effect=Exception("API Error"))
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot.signal_generator = Mock()
            bot.signal_generator.quantity_per_grid = Decimal("0.001")
            bot.market_info = Mock()
            bot.market_info.symbol = "PERP_BTC_USDC"

            # Should not raise error, just clean up
            await bot._create_grid_order(42500.50, "BUY")

            # Order should not be added
            assert len(bot.active_orders) == 0
            assert 42500.50 not in bot.grid_orders

    @pytest.mark.asyncio
    async def test_handle_signal_event_initial_signal(self):
        """Test handling INITIAL signal event."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.create_limit_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "initial_order_123"}
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot.market_info = Mock()
            bot.market_info.symbol = "PERP_BTC_USDC"

            signal = TradingSignal(
                symbol="PERP_BTC_USDC",
                side=OrderSide.BUY,
                price=Decimal("42500.50"),
                size=Decimal("0.001"),
                signal_type="INITIAL"
            )

            await bot._handle_signal_event(signal)

            # Should create order
            assert len(bot.active_orders) == 1

    @pytest.mark.asyncio
    async def test_handle_signal_event_market_open(self):
        """Test handling MARKET_OPEN signal event."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.create_market_order = AsyncMock(return_value={
                "success": True,
                "data": {"order_id": "market_order_123"}
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot.profit_tracker = Mock()
            bot.profit_tracker.add_trade = Mock()

            signal = TradingSignal(
                symbol="PERP_BTC_USDC",
                side=OrderSide.BUY,
                price=Decimal("42500.50"),
                size=Decimal("0.001"),
                signal_type="MARKET_OPEN"
            )

            await bot._handle_signal_event(signal)

            # Should create market order
            mock_client.create_market_order.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                side="BUY",
                quantity=0.001
            )

            # Should record trade in profit tracker
            bot.profit_tracker.add_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_signal_event_stop_signal(self):
        """Test handling STOP signal event."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        signal = TradingSignal(
            symbol="PERP_BTC_USDC",
            side=OrderSide.BUY,
            price=Decimal("0"),
            size=Decimal("0"),
            signal_type="STOP"
        )

        await bot._handle_signal_event(signal)

        # Should set is_running to False
        assert bot.is_running == False

    @pytest.mark.asyncio
    async def test_handle_signal_event_cancel_all(self):
        """Test handling CANCEL_ALL signal event."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.cancel_all_orders = AsyncMock(return_value={
                "success": True,
                "data": {"cancelled_count": 3}
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            # Add some active orders
            bot.active_orders[123] = {"price": 42500}
            bot.active_orders[456] = {"price": 42600}
            bot.grid_orders[42500] = 123
            bot.grid_orders[42600] = 456

            signal = TradingSignal(
                symbol="PERP_BTC_USDC",
                side=OrderSide.BUY,
                price=Decimal("0"),
                size=Decimal("0"),
                signal_type="CANCEL_ALL"
            )

            await bot._handle_signal_event(signal)

            # Should cancel all orders
            mock_client.cancel_all_orders.assert_called_once_with("PERP_BTC_USDC")

            # Should clear active orders
            assert len(bot.active_orders) == 0
            assert len(bot.grid_orders) == 0

    @pytest.mark.asyncio
    async def test_handle_signal_event_bot_not_running(self):
        """Test handling signal when bot is not running."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        bot.is_running = False

        signal = TradingSignal(
            symbol="PERP_BTC_USDC",
            side=OrderSide.BUY,
            price=Decimal("42500.50"),
            size=Decimal("0.001"),
            signal_type="INITIAL"
        )

        await bot._handle_signal_event(signal)

        # Should not create any orders
        assert len(bot.active_orders) == 0

    @pytest.mark.asyncio
    async def test_signal_handler(self):
        """Test signal handler method."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        # Create mock event queue
        mock_event_queue = Mock()
        mock_event_queue.add_event = AsyncMock()
        bot.event_queue = mock_event_queue

        signal = TradingSignal(
            symbol="PERP_BTC_USDC",
            side=OrderSide.BUY,
            price=Decimal("42500.50"),
            size=Decimal("0.001"),
            signal_type="INITIAL"
        )

        await bot.signal_handler(signal)

        # Should add event to queue
        mock_event_queue.add_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_handler_no_event_queue(self):
        """Test signal handler without event queue."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        # Mock the _handle_signal_event method
        bot._handle_signal_event = AsyncMock()

        signal = TradingSignal(
            symbol="PERP_BTC_USDC",
            side=OrderSide.BUY,
            price=Decimal("42500.50"),
            size=Decimal("0.001"),
            signal_type="INITIAL"
        )

        await bot.signal_handler(signal)

        # Should call _handle_signal_event directly
        bot._handle_signal_event.assert_called_once_with(signal)

    @pytest.mark.asyncio
    async def test_start_grid_trading_success(self):
        """Test successful grid trading start."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            with patch('src.core.grid_bot.GridSignalGenerator') as mock_signal_class:
                with patch('src.core.grid_bot.SessionEventQueue') as mock_event_queue_class:
                    with patch('src.core.grid_bot.MongoManager') as mock_mongo_class:
                        with patch('src.core.grid_bot.ProfitTracker') as mock_profit_tracker_class:
                            # Setup mocks
                            mock_client = Mock()
                            mock_client_class.return_value = mock_client

                            mock_signal = Mock()
                            mock_signal.setup_initial_grid = Mock()
                            mock_signal_class.return_value = mock_signal

                            mock_event_queue = Mock()
                            mock_event_queue.start = AsyncMock()
                            mock_event_queue_class.return_value = mock_event_queue

                            mock_mongo = Mock()
                            mock_mongo.get_user = AsyncMock(return_value={
                                "user_id": "test_user",
                                "api_key": "test_key",
                                "api_secret": "test_secret",
                                "wallet_address": "0x123..."
                            })
                            mock_mongo_class.return_value = mock_mongo

                            mock_profit_tracker = Mock()
                            mock_profit_tracker_class.return_value = mock_profit_tracker

                            bot = GridTradingBot(
                                account_id="test_user",
                                orderly_key="test_key",
                                orderly_secret="test_secret",
                                orderly_testnet=True
                            )

                            config = {
                                "user_id": "test_user",
                                "ticker": "BTCUSDT",
                                "direction": Direction.BOTH,
                                "current_price": 42500.0,
                                "upper_bound": 45000.0,
                                "lower_bound": 40000.0,
                                "grid_levels": 6,
                                "total_margin": 100.0
                            }

                            await bot.start_grid_trading(config)

                            # Verify bot is running
                            assert bot.is_running == True
                            assert bot.signal_generator == mock_signal
                            assert bot.profit_tracker == mock_profit_tracker

                            # Verify setup was called
                            mock_signal.setup_initial_grid.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_grid_trading_user_not_found(self):
        """Test grid trading start when user not found."""
        with patch('src.core.grid_bot.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value=None)
            mock_mongo_class.return_value = mock_mongo

            bot = GridTradingBot(
                account_id="test_user",
                orderly_key="test_key",
                orderly_secret="test_secret",
                orderly_testnet=True
            )

            config = {
                "user_id": "nonexistent_user",
                "ticker": "BTCUSDT",
                "direction": Direction.BOTH,
                "current_price": 42500.0,
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 6,
                "total_margin": 100.0
            }

            with pytest.raises(Exception):
                await bot.start_grid_trading(config)

    @pytest.mark.asyncio
    async def test_stop_grid_trading(self):
        """Test stopping grid trading."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        # Mock components
        bot.signal_generator = Mock()
        bot.signal_generator.stop_by_signal = Mock()
        bot.event_queue = Mock()
        bot.event_queue.stop = AsyncMock()
        bot.order_tracker = Mock()
        bot.order_tracker.clear = Mock()
        bot.wss_client = Mock()
        bot._safe_close_ws = Mock()

        bot.is_running = True

        await bot.stop_grid_trading()

        # Verify bot is stopped
        assert bot.is_running == False

        # Verify cleanup was called
        bot.signal_generator.stop_by_signal.assert_called_once()
        bot.event_queue.stop.assert_called_once()
        bot.order_tracker.clear.assert_called_once()
        bot._safe_close_ws.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_status(self):
        """Test getting bot status."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.get_account_info = AsyncMock(return_value={
                "success": True,
                "data": {"account_id": "test_account", "balance": 1000.0}
            })
            mock_client.get_positions = AsyncMock(return_value={
                "success": True,
                "data": {"rows": []}
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            bot.is_running = True
            bot.active_orders = {123: {"price": 42500}}
            bot.grid_orders = {42500: 123}
            bot.order_tracker = Mock()
            bot.order_tracker.get_statistics = Mock(return_value={
                "total_orders": 1,
                "active_orders": 1
            })
            bot.event_queue = Mock()
            bot.event_queue.get_queue_size = Mock(return_value=0)

            status = await bot.get_status()

            assert status["is_running"] == True
            assert status["active_orders_count"] == 1
            assert status["account_info"]["success"] == True
            assert status["positions"]["success"] == True

    @pytest.mark.asyncio
    async def test_get_profit_report(self):
        """Test getting profit report."""
        with patch('src.core.grid_bot.OrderlyClient') as mock_client_class:
            mock_client = Mock()
            mock_client.get_positions = AsyncMock(return_value={
                "success": True,
                "data": {
                    "rows": [
                        {
                            "symbol": "PERP_BTC_USDC",
                            "mark_price": "42500.50"
                        }
                    ]
                }
            })
            mock_client_class.return_value = mock_client

            bot = GridTradingBot(
                account_id="test_account_123",
                orderly_key="test_key_123",
                orderly_secret="test_secret_123",
                orderly_testnet=True
            )

            # Mock profit tracker
            mock_profit_tracker = Mock()
            mock_profit_tracker.get_summary = Mock(return_value={
                "realized_pnl": "50.25 USDT",
                "unrealized_pnl": "10.50 USDT",
                "total_pnl": "60.75 USDT"
            })
            mock_profit_tracker.get_trade_history = Mock(return_value=[])
            mock_profit_tracker.get_closed_positions = Mock(return_value=[])
            mock_profit_tracker.get_open_positions = Mock(return_value=[])

            bot.profit_tracker = mock_profit_tracker
            bot.session_id = "test_session"

            report = await bot.get_profit_report()

            assert "summary" in report
            assert "trade_history" in report
            assert "closed_positions" in report
            assert "open_positions" in report

            mock_profit_tracker.get_summary.assert_called_once()
            mock_profit_tracker.get_trade_history.assert_called_once()
            mock_profit_tracker.get_closed_positions.assert_called_once()
            mock_profit_tracker.get_open_positions.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_profit_report_no_tracker(self):
        """Test getting profit report when no tracker exists."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        report = await bot.get_profit_report()

        assert "error" in report
        assert report["error"] == "利潤追蹤器未初始化"

    @pytest.mark.asyncio
    async def test_websocket_fill_event_handling(self):
        """Test WebSocket fill event handling."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        # Mock components
        bot.event_queue = Mock()
        bot.event_queue.add_event = AsyncMock()
        bot.active_orders = {12345: {"price": Decimal("42500.50"), "side": "BUY"}}
        bot.order_tracker = Mock()
        bot.order_tracker.add_fill = Mock()
        bot.order_tracker.get_order = Mock(return_value=Mock(is_fully_filled=Mock(return_value=True)))
        bot.signal_generator = Mock()
        bot.signal_generator.on_order_filled = Mock()
        bot.profit_tracker = Mock()
        bot.profit_tracker.add_trade = Mock()

        fill_data = {
            "order_id": 12345,
            "executed_price": 42500.50,
            "executed_quantity": 0.001,
            "side": "BUY",
            "fill_id": "12345_42500.50_0.001_1234567890"
        }

        await bot._handle_order_filled_event(fill_data)

        # Should add event to queue
        bot.event_queue.add_event.assert_called_once()

        # Should record trade
        bot.profit_tracker.add_trade.assert_called_once()

        # Should update order tracker
        bot.order_tracker.add_fill.assert_called_once()

        # Should check if order is fully filled
        bot.order_tracker.get_order.assert_called_once_with(12345)

        # Should notify signal generator
        bot.signal_generator.on_order_filled.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_fill_event_duplicate_prevention(self):
        """Test WebSocket fill event duplicate prevention."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        bot.event_queue = Mock()
        bot.event_queue.add_event = AsyncMock()
        bot.processed_fills = {}  # Empty processed fills

        fill_data = {
            "order_id": 12345,
            "executed_price": 42500.50,
            "executed_quantity": 0.001,
            "side": "BUY",
            "fill_id": "12345_42500.50_0.001_1234567890"
        }

        # Process first fill
        await bot._handle_order_filled_event(fill_data)

        # Add to processed fills
        bot.processed_fills["12345_42500.50_0.001_1234567890"] = 1234567890.0

        # Process duplicate fill
        await bot._handle_order_filled_event(fill_data)

        # Should only add one event to queue
        assert bot.event_queue.add_event.call_count == 1

    @pytest.mark.asyncio
    async def test_cleanup_old_fills(self):
        """Test cleanup of old fill records."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        # Add some old fills
        import time
        current_time = time.time()
        old_time = current_time - 400  # 400 seconds ago (more than TTL)

        bot.processed_fills = {
            "old_fill": old_time,
            "recent_fill": current_time
        }

        bot.PROCESSED_FILLS_TTL = 300  # 5 minutes

        bot._cleanup_old_fills()

        # Should remove old fill
        assert "old_fill" not in bot.processed_fills
        assert "recent_fill" in bot.processed_fills

    @pytest.mark.asyncio
    async def test_cleanup_old_fills_max_size(self):
        """Test cleanup when max size is exceeded."""
        bot = GridTradingBot(
            account_id="test_account_123",
            orderly_key="test_key_123",
            orderly_secret="test_secret_123",
            orderly_testnet=True
        )

        bot.PROCESSED_FILLS_MAX_SIZE = 3

        # Add many fills
        import time
        current_time = time.time()
        for i in range(5):
            bot.processed_fills[f"fill_{i}"] = current_time - i

        bot._cleanup_old_fills()

        # Should maintain max size
        assert len(bot.processed_fills) <= 3