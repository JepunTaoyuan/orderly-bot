#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for grid signal generator module
"""

import pytest
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch
from src.core.grid_signal import (
    GridSignalGenerator, Direction, OrderSide, TradingSignal, GridType
)


class TestDirection:
    """Test Direction enum."""

    def test_direction_values(self):
        """Test Direction enum values."""
        assert Direction.LONG.value == "做多"
        assert Direction.SHORT.value == "做空"
        assert Direction.BOTH.value == "雙向"

    def test_direction_properties(self):
        """Test Direction properties."""
        assert Direction.LONG != Direction.SHORT
        assert Direction.SHORT != Direction.BOTH
        assert Direction.BOTH != Direction.LONG


class TestOrderSide:
    """Test OrderSide enum."""

    def test_order_side_values(self):
        """Test OrderSide enum values."""
        assert OrderSide.BUY.value == "買入"
        assert OrderSide.SELL.value == "賣出"


class TestTradingSignal:
    """Test TradingSignal dataclass."""

    def test_trading_signal_creation(self):
        """Test TradingSignal creation."""
        signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal("42500.50"),
            size=Decimal("0.001"),
            signal_type="INITIAL"
        )

        assert signal.symbol == "BTCUSDT"
        assert signal.side == OrderSide.BUY
        assert signal.price == Decimal("42500.50")
        assert signal.size == Decimal("0.001")
        assert signal.signal_type == "INITIAL"
        assert signal.timestamp is not None  # Should be auto-generated

    def test_trading_signal_with_timestamp(self):
        """Test TradingSignal with custom timestamp."""
        custom_timestamp = 1234567890.123
        signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            price=Decimal("42600.75"),
            size=Decimal("0.002"),
            signal_type="COUNTER",
            timestamp=custom_timestamp
        )

        assert signal.timestamp == custom_timestamp


class TestGridSignalGenerator:
    """Test GridSignalGenerator class."""

    def test_initialization_long_direction(self):
        """Test GridSignalGenerator initialization with LONG direction."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.LONG,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        assert generator.ticker == "BTCUSDT"
        assert generator.direction == Direction.LONG
        assert generator.current_price == Decimal("42500.0")
        assert generator.upper_bound == Decimal("45000.0")
        assert generator.lower_bound == Decimal("40000.0")
        assert generator.grid_levels == 6
        assert generator.total_margin == Decimal("100.0")
        assert generator.is_active == True
        assert generator.first_trigger == False

    def test_initialization_short_direction(self):
        """Test GridSignalGenerator initialization with SHORT direction."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.SHORT,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=4,
            total_margin=100.0
        )

        assert generator.direction == Direction.SHORT
        assert generator.grid_levels == 4

    def test_initialization_both_direction(self):
        """Test GridSignalGenerator initialization with BOTH direction."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=8,
            total_margin=100.0
        )

        assert generator.direction == Direction.BOTH
        assert generator.grid_levels == 8

    def test_initialization_with_stop_prices(self):
        """Test GridSignalGenerator with stop prices."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            stop_bot_price=38000.0,
            stop_top_price=47000.0
        )

        assert generator.stop_bot_price == Decimal("38000.0")
        assert generator.stop_top_price == Decimal("47000.0")

    def test_calculate_grid_prices_even_levels(self):
        """Test grid price calculation with even number of levels."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        prices = generator.grid_prices

        # Should have 6 grid prices (3 below, 3 above current price)
        assert len(prices) == 6

        # Check that prices are sorted
        assert all(prices[i] <= prices[i+1] for i in range(len(prices)-1))

        # Check that current price is not included
        assert Decimal("42500.0") not in prices

        # Check price bounds
        assert prices[0] >= generator.lower_bound
        assert prices[-1] <= generator.upper_bound

    def test_calculate_grid_prices_odd_levels(self):
        """Test grid price calculation with odd number of levels."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=7,
            total_margin=100.0
        )

        prices = generator.grid_prices

        # Should have 7 grid prices (3 below, 4 above current price)
        assert len(prices) == 7

    def test_find_closest_price_index(self):
        """Test finding closest price index."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Test finding existing price
        closest_index = generator._find_closest_price_index(generator.grid_prices[0])
        assert closest_index == 0

        # Test finding price between existing prices
        middle_price = (generator.grid_prices[0] + generator.grid_prices[1]) / 2
        closest_index = generator._find_closest_price_index(middle_price)
        assert closest_index in [0, 1]

    def test_setup_long_grid(self):
        """Test LONG grid setup."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.LONG,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Check initial margin allocation
        assert generator.initial_margin == Decimal("50.0")
        assert generator.grid_margin == Decimal("50.0")

        # Check initial position size
        expected_position_size = Decimal("50.0") / Decimal("42500.0")
        assert abs(generator.initial_position_size - expected_position_size) < Decimal("0.000001")

        # Check quantity per grid is calculated
        assert generator.quantity_per_grid > 0

    def test_setup_short_grid(self):
        """Test SHORT grid setup."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.SHORT,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Check initial margin allocation
        assert generator.initial_margin == Decimal("50.0")
        assert generator.grid_margin == Decimal("50.0")

        # Check initial position size
        expected_position_size = Decimal("50.0") / Decimal("42500.0")
        assert abs(generator.initial_position_size - expected_position_size) < Decimal("0.000001")

    def test_setup_both_grid(self):
        """Test BOTH grid setup."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # No initial position for BOTH strategy
        assert generator.initial_margin == Decimal("0")
        assert generator.grid_margin == Decimal("100.0")
        assert generator.initial_position_size == Decimal("0")

    def test_emit_signal(self):
        """Test signal emission."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        signal = generator._emit_signal(
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            size=Decimal("0.001"),
            signal_type="INITIAL"
        )

        assert signal.symbol == "BTCUSDT"
        assert signal.side == OrderSide.BUY
        assert signal.price == Decimal("42400.00")
        assert signal.size == Decimal("0.001")
        assert signal.signal_type == "INITIAL"

        # Check callback was called
        assert len(callback_calls) == 1
        assert callback_calls[0] == signal

    def test_stop_grid(self):
        """Test stopping the grid."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        assert generator.is_active == True

        generator.stop_grid("manual stop")

        assert generator.is_active == False
        assert generator.stop_reason == "manual stop"

        # Check stop signal was emitted
        assert len(callback_calls) == 1
        assert callback_calls[0].signal_type == "STOP"

    def test_check_stop_conditions(self):
        """Test stop condition checking."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            stop_bot_price=38000.0,
            stop_top_price=47000.0
        )

        # Test normal price (should not stop)
        should_stop = generator.check_stop_conditions(Decimal("42500.00"))
        assert should_stop == False
        assert generator.is_active == True

        # Test price below stop_bot_price
        should_stop = generator.check_stop_conditions(Decimal("37500.00"))
        assert should_stop == True
        assert generator.is_active == False
        assert "觸及下界停損價格 38000" in generator.stop_reason

        # Test price above stop_top_price
        generator.is_active = True  # Reset for next test
        should_stop = generator.check_stop_conditions(Decimal("47500.00"))
        assert should_stop == True
        assert generator.is_active == False
        assert "觸及上界停損價格 47000" in generator.stop_reason

    def test_check_stop_conditions_already_stopped(self):
        """Test stop conditions when already stopped."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Manually stop first
        generator.stop_grid("test stop")

        # Check conditions - should return True but not change state
        should_stop = generator.check_stop_conditions(Decimal("35000.00"))
        assert should_stop == True
        assert generator.stop_reason == "test stop"  # Should not change

    def test_calculate_position_size(self):
        """Test position size calculation."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.LONG,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Should return the calculated quantity per grid
        position_size = generator._calculate_position_size()
        assert position_size == generator.quantity_per_grid

    def test_setup_initial_grid_long(self):
        """Test initial grid setup for LONG strategy."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.LONG,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=4,
            total_margin=100.0,
            signal_callback=test_callback
        )

        generator.setup_initial_grid()

        # Should have: 1 market open + 4 grid orders = 5 signals
        assert len(callback_calls) == 5

        # Check market open signal
        market_signal = callback_calls[0]
        assert market_signal.signal_type == "MARKET_OPEN"
        assert market_signal.side == OrderSide.BUY
        assert market_signal.size == generator.initial_position_size

        # Check grid signals
        grid_signals = callback_calls[1:]
        buy_signals = [s for s in grid_signals if s.side == OrderSide.BUY]
        sell_signals = [s for s in grid_signals if s.side == OrderSide.SELL]

        # LONG strategy: buy below current price, sell above current price
        assert len(buy_signals) > 0  # Should have buy orders below current price
        assert len(sell_signals) > 0  # Should have sell orders above current price

    def test_setup_initial_grid_short(self):
        """Test initial grid setup for SHORT strategy."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.SHORT,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=4,
            total_margin=100.0,
            signal_callback=test_callback
        )

        generator.setup_initial_grid()

        # Should have: 1 market open + 4 grid orders = 5 signals
        assert len(callback_calls) == 5

        # Check market open signal
        market_signal = callback_calls[0]
        assert market_signal.signal_type == "MARKET_OPEN"
        assert market_signal.side == OrderSide.SELL
        assert market_signal.size == generator.initial_position_size

    def test_setup_initial_grid_both(self):
        """Test initial grid setup for BOTH strategy."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=4,
            total_margin=100.0,
            signal_callback=test_callback
        )

        generator.setup_initial_grid()

        # BOTH strategy: no market open, just 2 grid orders (buy below, sell above)
        assert len(callback_calls) == 2

        # Should have one buy and one sell
        buy_signals = [s for s in callback_calls if s.side == OrderSide.BUY]
        sell_signals = [s for s in callback_calls if s.side == OrderSide.SELL]

        assert len(buy_signals) == 1
        assert len(sell_signals) == 1

        # Buy should be below current price, sell should be above
        assert buy_signals[0].price < Decimal("42500.0")
        assert sell_signals[0].price > Decimal("42500.0")

    def test_on_order_filled_first_trigger(self):
        """Test handling first order fill."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        # Simulate first fill
        filled_signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            size=Decimal("0.001"),
            signal_type="FILLED"
        )

        generator.on_order_filled(filled_signal)

        # Should set first_trigger to True
        assert generator.first_trigger == True

        # Should set current_pointer to closest price index
        assert generator.current_pointer >= 0

        # Should emit cancel all signal
        cancel_signals = [s for s in callback_calls if s.signal_type == "CANCEL_ALL"]
        assert len(cancel_signals) == 1

        # Should emit counter signals (both buy and sell in BOTH direction)
        counter_signals = [s for s in callback_calls if s.signal_type == "COUNTER"]
        assert len(counter_signals) == 2  # BOTH mode generates both buy and sell signals

    def test_on_order_filled_subsequent_trigger(self):
        """Test handling subsequent order fills."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        # Set first trigger manually
        generator.first_trigger = True
        generator.current_pointer = 2  # Simulate being at middle index

        # Simulate subsequent fill
        filled_signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            price=Decimal("42600.00"),
            size=Decimal("0.001"),
            signal_type="FILLED"
        )

        generator.on_order_filled(filled_signal)

        # Should emit cancel all signal
        cancel_signals = [s for s in callback_calls if s.signal_type == "CANCEL_ALL"]
        assert len(cancel_signals) == 1

        # Should emit counter signals (both buy and sell in BOTH direction)
        counter_signals = [s for s in callback_calls if s.signal_type == "COUNTER"]
        assert len(counter_signals) == 2  # BOTH mode generates both buy and sell signals

    def test_generate_counter_signal_long_buy_filled(self):
        """Test counter signal generation when buy is filled in LONG strategy."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.LONG,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        generator.first_trigger = True
        generator.current_pointer = 2  # Middle index

        # LONG strategy: buy filled -> place sell above
        filled_signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            size=Decimal("0.001"),
            signal_type="FILLED"
        )

        # Manually add filled signal for counter generation
        callback_calls.clear()
        generator._generate_counter_signal(filled_signal)

        # Should emit sell signal above current position
        sell_signals = [s for s in callback_calls if s.side == OrderSide.SELL]
        assert len(sell_signals) == 1
        assert sell_signals[0].signal_type == "COUNTER"

    def test_generate_counter_signal_short_sell_filled(self):
        """Test counter signal generation when sell is filled in SHORT strategy."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.SHORT,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        generator.first_trigger = True
        generator.current_pointer = 2  # Middle index

        # SHORT strategy: sell filled -> place buy below
        filled_signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            price=Decimal("42600.00"),
            size=Decimal("0.001"),
            signal_type="FILLED"
        )

        # Manually add filled signal for counter generation
        callback_calls.clear()
        generator._generate_counter_signal(filled_signal)

        # Should emit buy signal below current position
        buy_signals = [s for s in callback_calls if s.side == OrderSide.BUY]
        assert len(buy_signals) == 1
        assert buy_signals[0].signal_type == "COUNTER"

    def test_generate_counter_signal_both_directions(self):
        """Test counter signal generation in BOTH strategy."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        generator.first_trigger = True
        generator.current_pointer = 2  # Middle index

        # Both strategy: any fill -> place buy below and sell above
        filled_signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            size=Decimal("0.001"),
            signal_type="FILLED"
        )

        # Manually add filled signal for counter generation
        callback_calls.clear()
        generator._generate_counter_signal(filled_signal)

        # Should emit both buy and sell signals
        buy_signals = [s for s in callback_calls if s.side == OrderSide.BUY]
        sell_signals = [s for s in callback_calls if s.side == OrderSide.SELL]

        assert len(buy_signals) == 1
        assert len(sell_signals) == 1
        assert all(s.signal_type == "COUNTER" for s in callback_calls)

    def test_get_status(self):
        """Test getting generator status."""
        callback_calls = []

        def test_callback(signal):
            callback_calls.append(signal)

        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=test_callback
        )

        # Status should be logged (side effect)
        generator.get_status()

        # Check basic properties
        assert generator.is_active == True
        assert generator.first_trigger == False

    def test_restart_grid(self):
        """Test restarting the grid."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Stop the grid
        generator.stop_grid("test stop")
        assert generator.is_active == False
        assert generator.stop_reason == "test stop"

        # Restart the grid
        generator.restart_grid()
        assert generator.is_active == True
        assert generator.stop_reason is None

    def test_restart_already_running(self):
        """Test restarting an already running grid."""
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0
        )

        # Should be running initially
        assert generator.is_active == True

        # Restart should not change anything
        generator.restart_grid()
        assert generator.is_active == True
        assert generator.stop_reason is None

    def test_edge_cases_extreme_parameters(self):
        """Test edge cases with extreme parameters."""
        # Very small grid
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=42510.0,  # Very tight range
            lower_bound=42490.0,
            grid_levels=2,
            total_margin=10.0
        )

        assert len(generator.grid_prices) >= 1

        # Very large grid
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=100000.0,
            lower_bound=1000.0,
            grid_levels=20,
            total_margin=10000.0
        )

        assert len(generator.grid_prices) == 20
        assert generator.grid_prices[0] >= Decimal("1000.0")
        assert generator.grid_prices[-1] <= Decimal("100000.0")

    def test_async_callback_handling(self):
        """Test handling of async callback functions."""
        async_calls = []

        async def async_callback(signal):
            async_calls.append(signal)

        # Note: In actual implementation, async callbacks are handled via create_task
        # This test verifies the structure is ready for async callbacks
        generator = GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500.0,
            direction=Direction.BOTH,
            upper_bound=45000.0,
            lower_bound=40000.0,
            grid_levels=6,
            total_margin=100.0,
            signal_callback=async_callback
        )

        # The callback should be callable
        assert callable(generator.signal_callback)

        # Emit signal (this would normally trigger the async callback)
        signal = generator._emit_signal(
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            size=Decimal("0.001"),
            signal_type="INITIAL"
        )

        assert signal is not None


class TestGeometricGridSignalGenerator:
    """等比網格信號生成器測試"""
    
    def test_geometric_grid_initialization(self):
        """測試等比網格正確初始化"""
        generator = GridSignalGenerator(
            ticker="PERP_BTC_USDC",
            current_price=50000,
            direction=Direction.BOTH,
            upper_bound=60000,
            lower_bound=40000,
            grid_levels=10,
            total_margin=1000,
            grid_type=GridType.GEOMETRIC,
            grid_ratio=0.02
        )
        
        assert generator.grid_type == GridType.GEOMETRIC
        assert generator.grid_ratio == Decimal('0.02')
        assert len(generator.grid_prices) == 10
    
    def test_geometric_grid_missing_ratio(self):
        """測試等比網格缺少 grid_ratio 參數時拋出錯誤"""
        with pytest.raises(ValueError, match="等比網格必須提供 grid_ratio 參數"):
            GridSignalGenerator(
                ticker="PERP_BTC_USDC",
                current_price=50000,
                direction=Direction.BOTH,
                upper_bound=60000,
                lower_bound=40000,
                grid_levels=10,
                total_margin=1000,
                grid_type=GridType.GEOMETRIC
            )
    
    def test_geometric_grid_invalid_ratio_zero(self):
        """測試等比網格 grid_ratio 為 0 時拋出錯誤"""
        with pytest.raises(ValueError, match="grid_ratio 必須大於 0"):
            GridSignalGenerator(
                ticker="PERP_BTC_USDC",
                current_price=50000,
                direction=Direction.BOTH,
                upper_bound=60000,
                lower_bound=40000,
                grid_levels=10,
                total_margin=1000,
                grid_type=GridType.GEOMETRIC,
                grid_ratio=0
            )
    
    def test_geometric_grid_invalid_ratio_negative(self):
        """測試等比網格 grid_ratio 為負數時拋出錯誤"""
        with pytest.raises(ValueError, match="grid_ratio 必須大於 0"):
            GridSignalGenerator(
                ticker="PERP_BTC_USDC",
                current_price=50000,
                direction=Direction.BOTH,
                upper_bound=60000,
                lower_bound=40000,
                grid_levels=10,
                total_margin=1000,
                grid_type=GridType.GEOMETRIC,
                grid_ratio=-0.01
            )
    
    def test_geometric_grid_invalid_ratio_too_large(self):
        """測試等比網格 grid_ratio 大於等於 1 時拋出錯誤"""
        with pytest.raises(ValueError, match="grid_ratio 必須小於 1"):
            GridSignalGenerator(
                ticker="PERP_BTC_USDC",
                current_price=50000,
                direction=Direction.BOTH,
                upper_bound=60000,
                lower_bound=40000,
                grid_levels=10,
                total_margin=1000,
                grid_type=GridType.GEOMETRIC,
                grid_ratio=1.0
            )
    
    def test_geometric_grid_price_calculation(self):
        """測試等比網格價格計算"""
        generator = GridSignalGenerator(
            ticker="PERP_BTC_USDC",
            current_price=50000,
            direction=Direction.BOTH,
            upper_bound=60000,
            lower_bound=40000,
            grid_levels=6,
            total_margin=1000,
            grid_type=GridType.GEOMETRIC,
            grid_ratio=0.05  # 5%
        )
        
        prices = generator.grid_prices
        assert len(prices) == 6
        
        # 檢查價格是否按等比數列排列
        # 等比網格的實現是：下方價格 = current_price * (1 - ratio)^i，上方價格 = current_price * (1 + ratio)^i
        current_price = Decimal('50000')
        ratio = Decimal('0.05')
        
        # 分離上方和下方價格
        below_prices = [p for p in prices if p < current_price]
        above_prices = [p for p in prices if p > current_price]
        
        # 檢查下方價格的等比關係
        if len(below_prices) > 1:
            below_prices.sort()  # 從低到高排序
            for i in range(len(below_prices) - 1):
                # 下方價格應該是遞增的等比數列
                ratio_actual = float(below_prices[i+1] / below_prices[i])
                expected_ratio = float((Decimal('1') - ratio) ** -1)  # 因為是從低到高，所以是倒數關係
                assert abs(ratio_actual - expected_ratio) < 0.01, f"下方價格比率不正確: {ratio_actual}, 期望: {expected_ratio}"
        
        # 檢查上方價格的等比關係
        if len(above_prices) > 1:
            above_prices.sort()  # 從低到高排序
            for i in range(len(above_prices) - 1):
                # 上方價格應該是遞增的等比數列
                ratio_actual = float(above_prices[i+1] / above_prices[i])
                expected_ratio = float(Decimal('1') + ratio)
                assert abs(ratio_actual - expected_ratio) < 0.01, f"上方價格比率不正確: {ratio_actual}, 期望: {expected_ratio}"
    
    def test_geometric_grid_position_size_calculation(self):
        """測試等比網格倉位大小計算"""
        generator = GridSignalGenerator(
            ticker="PERP_BTC_USDC",
            current_price=50000,
            direction=Direction.BOTH,
            upper_bound=60000,
            lower_bound=40000,
            grid_levels=10,
            total_margin=1000,
            grid_type=GridType.GEOMETRIC,
            grid_ratio=0.02
        )
        
        # 測試等比網格的動態倉位計算
        price1 = Decimal('45000')
        price2 = Decimal('55000')
        
        size1 = generator._calculate_position_size(price1)
        size2 = generator._calculate_position_size(price2)
        
        # 等比網格應該根據價格動態調整倉位大小
        # 價格越高，倉位應該越小（保持相同的投資金額）
        assert size1 > size2
        
        # 檢查投資金額是否相近（允許一定誤差）
        investment1 = size1 * price1
        investment2 = size2 * price2
        assert abs(investment1 - investment2) < Decimal('1')  # 允許1 USDT誤差
    
    def test_geometric_grid_setup_initial_grid(self):
        """測試等比網格初始網格設置"""
        generator = GridSignalGenerator(
            ticker="PERP_BTC_USDC",
            current_price=50000,
            direction=Direction.BOTH,
            upper_bound=60000,
            lower_bound=40000,
            grid_levels=10,
            total_margin=1000,
            grid_type=GridType.GEOMETRIC,
            grid_ratio=0.02
        )
        
        # 檢查是否正確設置了網格參數
        assert generator.grid_type == GridType.GEOMETRIC
        assert generator.grid_ratio == Decimal('0.02')
        assert generator.quantity_per_grid > 0  # 應該有基礎數量
    
    def test_arithmetic_grid_with_ratio_should_fail(self):
        """測試等差網格提供 grid_ratio 參數時不應該拋出錯誤（因為會被忽略）"""
        # 等差網格提供 grid_ratio 參數應該被忽略，不拋出錯誤
        generator = GridSignalGenerator(
            ticker="PERP_BTC_USDC",
            current_price=50000,
            direction=Direction.BOTH,
            upper_bound=60000,
            lower_bound=40000,
            grid_levels=10,
            total_margin=1000,
            grid_type=GridType.ARITHMETIC,
            grid_ratio=0.02  # 這個參數會被忽略
        )
        
        assert generator.grid_type == GridType.ARITHMETIC
        # 等差網格不使用 grid_ratio
    
    def test_default_grid_type_is_arithmetic(self):
        """測試默認網格類型是等差網格"""
        generator = GridSignalGenerator(
            ticker="PERP_BTC_USDC",
            current_price=50000,
            direction=Direction.BOTH,
            upper_bound=60000,
            lower_bound=40000,
            grid_levels=10,
            total_margin=1000
        )
        
        assert generator.grid_type == GridType.ARITHMETIC