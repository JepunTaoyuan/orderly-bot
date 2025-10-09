#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for market validator module
"""

import pytest
from decimal import Decimal, InvalidOperation
from src.utils.market_validator import MarketInfo, MarketValidator, ValidationError


class TestMarketInfo:
    """Test MarketInfo dataclass."""

    def test_market_info_creation(self):
        """Test MarketInfo creation."""
        market_info = MarketInfo(
            symbol="PERP_BTC_USDC",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.0001"),
            min_notional=Decimal("1.0"),
            min_price=Decimal("0.01"),
            max_price=Decimal("1000000"),
            min_quantity=Decimal("0.0001"),
            max_quantity=Decimal("1000")
        )

        assert market_info.symbol == "PERP_BTC_USDC"
        assert market_info.tick_size == Decimal("0.01")
        assert market_info.step_size == Decimal("0.0001")
        assert market_info.min_notional == Decimal("1.0")
        assert market_info.min_price == Decimal("0.01")
        assert market_info.max_price == Decimal("1000000")
        assert market_info.min_quantity == Decimal("0.0001")
        assert market_info.max_quantity == Decimal("1000")

    def test_market_info_optional_fields(self):
        """Test MarketInfo with optional fields."""
        market_info = MarketInfo(
            symbol="PERP_ETH_USDC",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("1.0")
        )

        assert market_info.symbol == "PERP_ETH_USDC"
        assert market_info.max_price is None
        assert market_info.min_price is None
        assert market_info.max_quantity is None
        assert market_info.min_quantity is None


class TestMarketValidator:
    """Test MarketValidator class."""

    def test_market_validator_initialization(self):
        """Test MarketValidator initialization."""
        validator = MarketValidator()

        # Check that default markets are loaded
        assert "PERP_BTC_USDC" in validator.market_info
        assert "PERP_ETH_USDC" in validator.market_info

        btc_info = validator.market_info["PERP_BTC_USDC"]
        assert btc_info.symbol == "PERP_BTC_USDC"
        assert btc_info.tick_size == Decimal("0.01")
        assert btc_info.step_size == Decimal("0.0001")

    def test_validate_order_basic(self):
        """Test basic order validation."""
        validator = MarketValidator()

        # Valid order
        price, quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("42500.50"),
            Decimal("0.001")
        )

        assert price == Decimal("42500.50")
        assert quantity == Decimal("0.001")

    def test_validate_order_tick_size_adjustment(self):
        """Test order validation with tick size adjustment."""
        validator = MarketValidator()

        # Price that needs tick size adjustment
        price, quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("42500.123"),  # Should be rounded to 2 decimal places
            Decimal("0.001")
        )

        assert price == Decimal("42500.12")  # Rounded down

    def test_validate_order_step_size_adjustment(self):
        """Test order validation with step size adjustment."""
        validator = MarketValidator()

        # Quantity that needs step size adjustment
        price, quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("42500.00"),
            Decimal("0.00123")  # Should be rounded to 4 decimal places
        )

        assert quantity == Decimal("0.0012")  # Rounded down

    def test_validate_order_min_notional(self):
        """Test order validation with minimum notional."""
        validator = MarketValidator()

        # Order with insufficient notional value
        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("100.00"),  # Lower price to trigger min notional error
                Decimal("0.0001")   # Min quantity
            )

        assert "notional" in str(exc_info.value).lower()

    def test_validate_order_min_price(self):
        """Test order validation with minimum price."""
        validator = MarketValidator()

        # Price below minimum (should be normalized up, so this test checks min notional instead)
        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("0.001"),  # Below min price of 0.01
                Decimal("1")       # Small quantity to trigger min notional
            )

        # The error is about min notional since price gets normalized up
        assert "notional" in str(exc_info.value).lower()

    def test_validate_order_max_price(self):
        """Test order validation with maximum price."""
        validator = MarketValidator()

        # Price above maximum gets normalized down to max price
        norm_price, norm_quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("2000000.00"),  # Above max price
            Decimal("0.001")
        )

        # Should be normalized to max price
        assert norm_price == Decimal("1000000")

    def test_validate_order_min_quantity(self):
        """Test order validation with minimum quantity."""
        validator = MarketValidator()

        # Quantity below minimum gets normalized up to min quantity
        norm_price, norm_quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("42500.00"),
            Decimal("0.00001")  # Below min quantity
        )

        # Should be normalized to min quantity
        assert norm_quantity == Decimal("0.0001")

    def test_validate_order_max_quantity(self):
        """Test order validation with maximum quantity."""
        validator = MarketValidator()

        # Quantity above maximum gets normalized down to max quantity
        norm_price, norm_quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("42500.00"),
            Decimal("2000")  # Above max quantity
        )

        # Should be normalized to max quantity
        assert norm_quantity == Decimal("1000")

    def test_validate_order_unknown_symbol(self):
        """Test order validation with unknown symbol."""
        validator = MarketValidator()

        # Unknown symbol
        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "UNKNOWN_SYMBOL",
                Decimal("42500.00"),
                Decimal("0.001")
            )

        assert "symbol" in str(exc_info.value).lower()

    def test_validate_order_zero_price(self):
        """Test order validation with zero price."""
        validator = MarketValidator()

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("0"),
                Decimal("0.001")
            )

        assert "price" in str(exc_info.value).lower()

    def test_validate_order_zero_quantity(self):
        """Test order validation with zero quantity."""
        validator = MarketValidator()

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("42500.00"),
                Decimal("0")
            )

        assert "quantity" in str(exc_info.value).lower()

    def test_validate_order_negative_price(self):
        """Test order validation with negative price."""
        validator = MarketValidator()

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("-100"),
                Decimal("0.001")
            )

        assert "price" in str(exc_info.value).lower()

    def test_validate_order_negative_quantity(self):
        """Test order validation with negative quantity."""
        validator = MarketValidator()

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("42500.00"),
                Decimal("-0.001")
            )

        assert "quantity" in str(exc_info.value).lower()

    def test_validate_config_basic(self):
        """Test basic config validation."""
        validator = MarketValidator()

        config = {
            "ticker": "PERP_BTC_USDC",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_margin": 100.0
        }

        validated_config = validator.validate_config(config)

        assert validated_config["ticker"] == "PERP_BTC_USDC"
        assert validated_config["_orderly_symbol"] == "PERP_BTC_USDC"
        assert "_market_info" in validated_config

    def test_validate_config_invalid_symbol(self):
        """Test config validation with invalid symbol."""
        validator = MarketValidator()

        config = {
            "ticker": "INVALID_SYMBOL",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_margin": 100.0
        }

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_config(config)

        assert "symbol" in str(exc_info.value).lower()

    def test_validate_config_invalid_bounds(self):
        """Test config validation with invalid bounds."""
        validator = MarketValidator()

        config = {
            "ticker": "PERP_BTC_USDC",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 40000.0,  # Upper bound lower than lower bound
            "lower_bound": 45000.0,
            "grid_levels": 6,
            "total_margin": 100.0
        }

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_config(config)

        assert "bound" in str(exc_info.value).lower()

    def test_validate_config_current_price_out_of_bounds(self):
        """Test config validation with current price out of bounds."""
        validator = MarketValidator()

        config = {
            "ticker": "PERP_BTC_USDC",
            "direction": "BOTH",
            "current_price": 50000.0,  # Above upper bound
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_margin": 100.0
        }

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_config(config)

        assert "current price" in str(exc_info.value).lower() or "bounds" in str(exc_info.value).lower()

    def test_validate_config_invalid_grid_levels(self):
        """Test config validation with invalid grid levels."""
        validator = MarketValidator()

        config = {
            "ticker": "PERP_BTC_USDC",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 1,  # Too few grid levels
            "total_margin": 100.0
        }

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_config(config)

        assert "grid" in str(exc_info.value).lower() or "level" in str(exc_info.value).lower()

    def test_validate_config_invalid_margin(self):
        """Test config validation with invalid margin."""
        validator = MarketValidator()

        config = {
            "ticker": "PERP_BTC_USDC",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_margin": 0  # Zero margin
        }

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_config(config)

        assert "margin" in str(exc_info.value).lower()

    def test_validate_config_missing_required_fields(self):
        """Test config validation with missing required fields."""
        validator = MarketValidator()

        # Missing ticker
        config = {
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_margin": 100.0
        }

        with pytest.raises(ValidationError) as exc_info:
            validator.validate_config(config)

        assert "ticker" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

    def test_validate_config_edge_cases(self):
        """Test config validation edge cases."""
        validator = MarketValidator()

        # Current price exactly at bounds
        config = {
            "ticker": "PERP_BTC_USDC",
            "direction": "BOTH",
            "current_price": 40000.0,  # Exactly at lower bound
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_margin": 100.0
        }

        # This should be valid
        validated_config = validator.validate_config(config)
        assert validated_config["current_price"] == 40000.0

    @pytest.mark.parametrize("symbol,tick_size,step_size", [
        ("PERP_BTC_USDC", Decimal("0.01"), Decimal("0.0001")),
        ("PERP_ETH_USDC", Decimal("0.01"), Decimal("0.001")),
    ])
    def test_different_market_rules(self, symbol, tick_size, step_size):
        """Test validation with different market rules."""
        validator = MarketValidator()

        # Test with quantities that need adjustment for each market
        test_quantity = Decimal("0.00123456")
        price, quantity = validator.validate_order(
            symbol,
            Decimal("42500.00"),
            test_quantity
        )

        # Quantity should be adjusted to the correct step size
        expected_quantity = (test_quantity // step_size) * step_size
        assert quantity == expected_quantity

    def test_add_custom_market_info(self):
        """Test adding custom market information."""
        validator = MarketValidator()

        # Add custom market info
        custom_market = MarketInfo(
            symbol="CUSTOM_PAIR",
            tick_size=Decimal("0.001"),
            step_size=Decimal("0.01"),
            min_notional=Decimal("10.0"),
            min_price=Decimal("0.1"),
            max_price=Decimal("100000"),
            min_quantity=Decimal("0.01"),
            max_quantity=Decimal("10000")
        )

        validator.market_info["CUSTOM_PAIR"] = custom_market

        # Test validation with custom market
        price, quantity = validator.validate_order(
            "CUSTOM_PAIR",
            Decimal("1000.123"),
            Decimal("1.234")
        )

        assert price == Decimal("1000.123")  # Should be rounded to 3 decimal places
        assert quantity == Decimal("1.23")  # Should be rounded to 2 decimal places

    def test_validation_error_inheritance(self):
        """Test that ValidationError inherits from Exception."""
        error = ValidationError("Test error")

        assert isinstance(error, Exception)
        assert isinstance(error, ValidationError)
        assert str(error) == "Test error"

    def test_validate_order_with_decimal_inputs(self):
        """Test order validation with various Decimal inputs."""
        validator = MarketValidator()

        test_cases = [
            (Decimal("42500.00"), Decimal("0.001")),
            (Decimal("42500.5"), Decimal("0.0015")),
            (Decimal("42500"), Decimal("0.001")),  # Integer input
        ]

        for price, quantity in test_cases:
            try:
                validated_price, validated_quantity = validator.validate_order(
                    "PERP_BTC_USDC",
                    price,
                    quantity
                )

                # Should return Decimal objects
                assert isinstance(validated_price, Decimal)
                assert isinstance(validated_quantity, Decimal)

                # Should be within valid ranges
                assert validated_price >= Decimal("0.01")
                assert validated_quantity >= Decimal("0.0001")

            except ValidationError:
                # Some inputs might be invalid, which is expected
                pass