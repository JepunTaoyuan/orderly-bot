#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for profit tracker module
"""

import pytest
import json
from decimal import Decimal
from datetime import datetime
from src.core.profit_tracker import (
    ProfitTracker, CurrentPosition, GridStats, OrderSide
)


class TestOrderSide:
    """Test OrderSide enum."""

    def test_order_side_values(self):
        """Test OrderSide enum values."""
        assert OrderSide.BUY.value == "買入"
        assert OrderSide.SELL.value == "賣出"

    def test_order_side_representation(self):
        """Test OrderSide string representation."""
        assert str(OrderSide.BUY) == "OrderSide.BUY"
        assert str(OrderSide.SELL) == "OrderSide.SELL"


class TestTrade:
    """Test Trade dataclass."""

    def test_trade_creation_with_manual_id(self):
        """Test Trade creation with manual trade_id."""
        trade = Trade(
            timestamp=1234567890.0,
            side=OrderSide.BUY,
            price=Decimal("42500.50"),
            quantity=Decimal("0.001"),
            cost=Decimal("42.50"),
            fee=Decimal("0.0425"),
            trade_id="manual_trade_123"
        )

        assert trade.timestamp == 1234567890.0
        assert trade.side == OrderSide.BUY
        assert trade.price == Decimal("42500.50")
        assert trade.quantity == Decimal("0.001")
        assert trade.cost == Decimal("42.50")
        assert trade.fee == Decimal("0.0425")
        assert trade.trade_id == "manual_trade_123"

    def test_trade_creation_auto_id(self):
        """Test Trade creation with automatic trade_id generation."""
        trade = Trade(
            timestamp=1234567890.0,
            side=OrderSide.SELL,
            price=Decimal("42600.75"),
            quantity=Decimal("0.002"),
            cost=Decimal("85.20"),
            fee=Decimal("0.0852")
        )

        expected_id = "1234567890_賣出_42600.75"
        assert trade.trade_id == expected_id

    def test_trade_defaults(self):
        """Test Trade default values."""
        trade = Trade(
            timestamp=1234567890.0,
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            cost=Decimal("42.50")
        )

        assert trade.fee == Decimal('0')
        assert trade.trade_id  # Should be auto-generated


class TestPosition:
    """Test Position dataclass."""

    def test_position_creation(self):
        """Test Position creation."""
        position = Position(
            buy_price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            buy_timestamp=1234567890.0,
            buy_cost=Decimal("42.50")
        )

        assert position.buy_price == Decimal("42500.00")
        assert position.quantity == Decimal("0.001")
        assert position.buy_timestamp == 1234567890.0
        assert position.buy_cost == Decimal("42.50")
        assert position.matched == False
        assert position.sell_price is None
        assert position.sell_timestamp is None
        assert position.sell_revenue is None
        assert position.realized_pnl is None

    def test_position_with_sell_info(self):
        """Test Position with sell information."""
        position = Position(
            buy_price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            buy_timestamp=1234567890.0,
            buy_cost=Decimal("42.50"),
            matched=True,
            sell_price=Decimal("42700.00"),
            sell_timestamp=1234567950.0,
            sell_revenue=Decimal("42.70"),
            realized_pnl=Decimal("0.20")
        )

        assert position.matched == True
        assert position.sell_price == Decimal("42700.00")
        assert position.sell_timestamp == 1234567950.0
        assert position.sell_revenue == Decimal("42.70")
        assert position.realized_pnl == Decimal("0.20")


class TestGridStats:
    """Test GridStats dataclass."""

    def test_grid_stats_defaults(self):
        """Test GridStats default values."""
        stats = GridStats()

        assert stats.total_trades == 0
        assert stats.buy_trades == 0
        assert stats.sell_trades == 0
        assert stats.realized_pnl == Decimal('0')
        assert stats.unrealized_pnl == Decimal('0')
        assert stats.total_pnl == Decimal('0')
        assert stats.winning_trades == 0
        assert stats.losing_trades == 0
        assert stats.win_rate == Decimal('0')
        assert stats.total_buy_cost == Decimal('0')
        assert stats.total_sell_revenue == Decimal('0')
        assert stats.total_fees == Decimal('0')
        assert stats.avg_profit_per_trade == Decimal('0')
        assert stats.avg_win == Decimal('0')
        assert stats.avg_loss == Decimal('0')
        assert stats.max_win == Decimal('0')
        assert stats.max_loss == Decimal('0')
        assert stats.current_position_qty == Decimal('0')
        assert stats.current_position_cost == Decimal('0')
        assert stats.avg_entry_price == Decimal('0')

    def test_grid_stats_custom_values(self):
        """Test GridStats with custom values."""
        stats = GridStats(
            total_trades=10,
            realized_pnl=Decimal("150.50"),
            winning_trades=6,
            losing_trades=4,
            total_fees=Decimal("5.25")
        )

        assert stats.total_trades == 10
        assert stats.realized_pnl == Decimal("150.50")
        assert stats.winning_trades == 6
        assert stats.losing_trades == 4
        assert stats.total_fees == Decimal("5.25")


class TestProfitTracker:
    """Test ProfitTracker class."""

    def test_profit_tracker_initialization(self):
        """Test ProfitTracker initialization."""
        tracker = ProfitTracker("BTCUSDT")

        assert tracker.symbol == "BTCUSDT"
        assert tracker.fee_rate == Decimal("0.001")
        assert len(tracker.trades) == 0
        assert len(tracker.open_positions) == 0
        assert len(tracker.closed_positions) == 0
        assert isinstance(tracker.stats, GridStats)

    def test_profit_tracker_custom_fee_rate(self):
        """Test ProfitTracker with custom fee rate."""
        custom_fee = Decimal("0.0005")
        tracker = ProfitTracker("ETHUSDT", custom_fee)

        assert tracker.fee_rate == custom_fee

    def test_add_buy_trade(self):
        """Test adding a buy trade."""
        tracker = ProfitTracker("BTCUSDT")

        trade = tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )

        assert len(tracker.trades) == 1
        assert len(tracker.open_positions) == 1
        assert len(tracker.closed_positions) == 0

        # Check trade details
        expected_cost = Decimal("42500.00") * Decimal("0.001") * (Decimal("1") + tracker.fee_rate)
        assert trade.side == OrderSide.BUY
        assert trade.price == Decimal("42500.00")
        assert trade.quantity == Decimal("0.001")
        assert trade.cost == expected_cost
        assert trade.fee == expected_cost - Decimal("42.50")

        # Check position created
        position = tracker.open_positions[0]
        assert position.buy_price == Decimal("42500.00")
        assert position.quantity == Decimal("0.001")
        assert position.buy_cost == expected_cost

    def test_add_sell_trade_complete_match(self):
        """Test adding a sell trade that completely matches a buy position."""
        tracker = ProfitTracker("BTCUSDT")

        # Add buy trade
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )

        # Add matching sell trade
        sell_trade = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567950.0
        )

        assert len(tracker.trades) == 2
        assert len(tracker.open_positions) == 0
        assert len(tracker.closed_positions) == 1

        # Check closed position
        closed_position = tracker.closed_positions[0]
        assert closed_position.buy_price == Decimal("42500.00")
        assert closed_position.sell_price == Decimal("42700.00")
        assert closed_position.quantity == Decimal("0.001")
        assert closed_position.matched == True
        assert closed_position.realized_pnl is not None

    def test_add_sell_trade_partial_match(self):
        """Test adding a sell trade that partially matches buy positions."""
        tracker = ProfitTracker("BTCUSDT")

        # Add multiple buy trades
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            quantity=Decimal("0.0015"),
            timestamp=1234567895.0
        )

        # Add partial sell trade
        sell_trade = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42600.00"),
            quantity=Decimal("0.002"),
            timestamp=1234567950.0
        )

        # Partial match should consume from first position, then second
        # First position: 0.001 matched, second position: 0.001 matched (0.002 total)
        # Remaining: 0.0005 in second position
        assert len(tracker.trades) == 3
        assert len(tracker.open_positions) == 1  # One position remaining
        assert len(tracker.closed_positions) == 2  # Two positions partially closed

        # Check remaining position
        remaining_position = tracker.open_positions[0]
        assert remaining_position.quantity == Decimal("0.0005")

    def test_add_sell_trade_no_positions(self):
        """Test adding sell trade when no buy positions exist."""
        tracker = ProfitTracker("BTCUSDT")

        # Should still add the trade but no position matching
        sell_trade = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567950.0
        )

        assert len(tracker.trades) == 1
        assert len(tracker.open_positions) == 0
        assert len(tracker.closed_positions) == 0

    def test_fee_calculation(self):
        """Test fee calculation in trades."""
        tracker = ProfitTracker("BTCUSDT", Decimal("0.002"))  # 0.2% fee

        buy_trade = tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("50000.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )

        # Cost should include fee: 50000 * 0.001 * (1 + 0.002) = 50.10
        expected_cost = Decimal("50.10")
        expected_fee = Decimal("0.10")

        assert buy_trade.cost == expected_cost
        assert buy_trade.fee == expected_fee

    def test_calculate_unrealized_pnl(self):
        """Test unrealized PnL calculation."""
        tracker = ProfitTracker("BTCUSDT")

        # Add buy position
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )

        # Calculate unrealized PnL at higher price
        current_price = Decimal("43000.00")
        unrealized = tracker.calculate_unrealized_pnl(current_price)

        # Expected: (43000 * 0.001 * (1 - 0.001)) - 42.525
        expected_revenue = Decimal("43.00") * (Decimal("1") - tracker.fee_rate)
        expected_cost = Decimal("42.50") * (Decimal("1") + tracker.fee_rate)
        expected_unrealized = expected_revenue - expected_cost

        assert unrealized == expected_unrealized.quantize(Decimal("0.01"))
        assert tracker.stats.unrealized_pnl == unrealized

    def test_calculate_unrealized_pnl_empty_positions(self):
        """Test unrealized PnL calculation with no positions."""
        tracker = ProfitTracker("BTCUSDT")

        unrealized = tracker.calculate_unrealized_pnl(Decimal("43000.00"))
        assert unrealized == Decimal("0")

    def test_get_summary(self):
        """Test getting profit summary."""
        tracker = ProfitTracker("BTCUSDT")

        # Add some trades
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )
        tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567950.0
        )

        summary = tracker.get_summary()

        assert "symbol" in summary
        assert summary["symbol"] == "BTCUSDT"
        assert "fee_rate" in summary
        assert "total_trades" in summary
        assert summary["total_trades"] == 2
        assert "buy_trades" in summary
        assert "sell_trades" in summary
        assert summary["buy_trades"] == 1
        assert summary["sell_trades"] == 1

    def test_get_summary_with_current_price(self):
        """Test getting profit summary with current price for unrealized PnL."""
        tracker = ProfitTracker("BTCUSDT")

        # Add unmatched buy position
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )

        current_price = Decimal("43000.00")
        summary = tracker.get_summary(current_price)

        assert "unrealized_pnl" in summary
        # Should show unrealized profit since price went up
        assert Decimal(summary["unrealized_pnl"].replace(" USDT", "")) > 0

    def test_get_trade_history(self):
        """Test getting trade history."""
        tracker = ProfitTracker("BTCUSDT")

        # Add trades with different timestamps
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )
        tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567950.0
        )

        history = tracker.get_trade_history()

        assert len(history) == 2
        assert "timestamp" in history[0]
        assert "side" in history[0]
        assert "price" in history[0]
        assert "quantity" in history[0]
        assert "cost" in history[0]
        assert "fee" in history[0]

        # Check that timestamp is formatted
        assert ":" in history[0]["timestamp"]  # Should be formatted datetime

    def test_get_trade_history_with_limit(self):
        """Test getting trade history with limit."""
        tracker = ProfitTracker("BTCUSDT")

        # Add multiple trades
        for i in range(5):
            tracker.add_trade(
                side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL,
                price=Decimal(f"425{i}0.00"),
                quantity=Decimal("0.001"),
                timestamp=1234567890.0 + i
            )

        # Get limited history
        limited_history = tracker.get_trade_history(limit=3)
        assert len(limited_history) == 3

        # Get full history
        full_history = tracker.get_trade_history()
        assert len(full_history) == 5

    def test_get_closed_positions(self):
        """Test getting closed positions."""
        tracker = ProfitTracker("BTCUSDT")

        # Create matched positions
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )
        tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567950.0
        )

        closed_positions = tracker.get_closed_positions()

        assert len(closed_positions) == 1
        position = closed_positions[0]
        assert "buy_time" in position
        assert "buy_price" in position
        assert "sell_time" in position
        assert "sell_price" in position
        assert "quantity" in position
        assert "realized_pnl" in position
        assert "pnl_pct" in position

        # Check percentage calculation
        pnl_pct = position["pnl_pct"]
        assert "%" in pnl_pct
        assert pnl_pct.replace("%", "").replace(".", "").replace("-", "").isdigit()

    def test_get_open_positions(self):
        """Test getting open positions."""
        tracker = ProfitTracker("BTCUSDT")

        # Add unmatched buy
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )

        open_positions = tracker.get_open_positions()

        assert len(open_positions) == 1
        position = open_positions[0]
        assert "buy_time" in position
        assert "buy_price" in position
        assert "quantity" in position
        assert "buy_cost" in position

    def test_statistics_calculation(self):
        """Test statistics calculation after multiple trades."""
        tracker = ProfitTracker("BTCUSDT")

        # Add series of trades that create profit
        trades_data = [
            (OrderSide.BUY, "42500.00", "0.001", 1000),
            (OrderSide.BUY, "42400.00", "0.001", 1005),
            (OrderSide.SELL, "42600.00", "0.001", 1010),
            (OrderSide.SELL, "42700.00", "0.001", 1015)
        ]

        for side, price, quantity, timestamp in trades_data:
            tracker.add_trade(
                side=side,
                price=Decimal(price),
                quantity=Decimal(quantity),
                timestamp=float(timestamp)
            )

        stats = tracker.stats

        assert stats.total_trades == 4
        assert stats.buy_trades == 2
        assert stats.sell_trades == 2
        assert stats.total_fees > 0
        assert stats.realized_pnl != 0

        # Win rate should be calculated
        assert stats.win_rate >= 0
        assert stats.win_rate <= 100

    def test_export_to_json(self):
        """Test exporting data to JSON."""
        tracker = ProfitTracker("BTCUSDT")

        # Add some trades
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567890.0
        )
        tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1234567950.0
        )

        import tempfile
        import os

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name

        try:
            tracker.export_to_json(temp_file)

            # Verify file was created and contains expected data
            assert os.path.exists(temp_file)

            with open(temp_file, 'r') as f:
                data = json.load(f)

            assert "summary" in data
            assert "trade_history" in data
            assert "closed_positions" in data
            assert "open_positions" in data

        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    def test_fifo_matching(self):
        """Test First-In-First-Out position matching."""
        tracker = ProfitTracker("BTCUSDT")

        # Add buys in chronological order
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.001"),
            timestamp=1000.0
        )
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42400.00"),
            quantity=Decimal("0.001"),
            timestamp=1001.0
        )
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42300.00"),
            quantity=Decimal("0.001"),
            timestamp=1002.0
        )

        # Add sell that should match first buy
        sell_trade = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42600.00"),
            quantity=Decimal("0.001"),
            timestamp=1003.0
        )

        # First position should be closed (42500.00 buy)
        assert len(tracker.closed_positions) == 1
        assert tracker.closed_positions[0].buy_price == Decimal("42500.00")

        # Two positions should remain
        assert len(tracker.open_positions) == 2
        assert tracker.open_positions[0].buy_price == Decimal("42400.00")
        assert tracker.open_positions[1].buy_price == Decimal("42300.00")

    def test_multiple_partial_sells(self):
        """Test multiple partial sells against the same position."""
        tracker = ProfitTracker("BTCUSDT")

        # Add large buy position
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0.003"),
            timestamp=1000.0
        )

        # Add multiple small sells
        sell1 = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42600.00"),
            quantity=Decimal("0.001"),
            timestamp=1001.0
        )

        sell2 = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42700.00"),
            quantity=Decimal("0.001"),
            timestamp=1002.0
        )

        sell3 = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42800.00"),
            quantity=Decimal("0.001"),
            timestamp=1003.0
        )

        # All three sells should have been processed
        assert len(tracker.trades) == 4  # 1 buy + 3 sells
        assert len(tracker.closed_positions) == 3  # Three closed positions
        assert len(tracker.open_positions) == 0  # No remaining positions

    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        tracker = ProfitTracker("BTCUSDT")

        # Test with zero quantity
        trade = tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.00"),
            quantity=Decimal("0"),
            timestamp=1234567890.0
        )

        assert trade.quantity == Decimal("0")

        # Test with very small quantities
        trade = tracker.add_trade(
            side=OrderSide.SELL,
            price=Decimal("42500.00"),
            quantity=Decimal("0.000001"),
            timestamp=1234567950.0
        )

        assert trade.quantity == Decimal("0.000001")

    def test_decimal_precision(self):
        """Test decimal precision handling."""
        tracker = ProfitTracker("BTCUSDT")

        # Test high precision prices
        tracker.add_trade(
            side=OrderSide.BUY,
            price=Decimal("42500.12345678"),
            quantity=Decimal("0.00123456"),
            timestamp=1234567890.0
        )

        trade = tracker.trades[0]
        assert trade.price == Decimal("42500.12345678")
        assert trade.quantity == Decimal("0.00123456")

        # Check calculations maintain precision
        expected_cost = Decimal("42500.12345678") * Decimal("0.00123456") * (Decimal("1") + tracker.fee_rate)
        assert abs(trade.cost - expected_cost) < Decimal("0.0001")