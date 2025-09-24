#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
組件單元測試
測試各個獨立組件的功能
"""

import pytest
import asyncio
import time
import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

# 設置測試環境變數
import os
TEST_ENV_VARS = {
    "ORDERLY_KEY": "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
    "ORDERLY_SECRET": "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
    "ORDERLY_ACCOUNT_ID": "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0"
}

for key, value in TEST_ENV_VARS.items():
    os.environ[key] = value

class TestOrderlyClient:
    """測試 Orderly 客戶端"""
    
    def test_client_initialization(self):
        """測試客戶端初始化"""
        from src.core.client import OrderlyClient
        
        client = OrderlyClient()
        assert client is not None
        assert client.client is not None
        assert client.retry_handler is not None
    
    def test_environment_variables_usage(self):
        """測試環境變數的使用"""
        from src.core.client import OrderlyClient
        
        # 確保客戶端使用了正確的環境變數
        with patch('src.core.client.RestAsync') as mock_rest:
            client = OrderlyClient()
            
            # 檢查 RestAsync 是否使用了正確的參數調用
            mock_rest.assert_called_once()
            call_kwargs = mock_rest.call_args[1]
            
            assert call_kwargs['orderly_key'] == TEST_ENV_VARS['ORDERLY_KEY']
            assert call_kwargs['orderly_secret'] == TEST_ENV_VARS['ORDERLY_SECRET']
            assert call_kwargs['orderly_account_id'] == TEST_ENV_VARS['ORDERLY_ACCOUNT_ID']
            assert call_kwargs['orderly_testnet'] == True
    
    @pytest.mark.asyncio
    async def test_create_limit_order(self):
        """測試創建限價訂單"""
        from src.core.client import OrderlyClient
        
        with patch('src.core.client.RestAsync') as mock_rest_class:
            mock_rest = AsyncMock()
            mock_rest_class.return_value = mock_rest
            
            # 設置模擬響應
            mock_rest.create_order.return_value = {
                "success": True,
                "data": {"order_id": 123456}
            }
            
            client = OrderlyClient()
            result = await client.create_limit_order(
                symbol="PERP_BTC_USDC",
                side="BUY",
                price=42000.0,
                quantity=0.001
            )
            
            assert result["success"] == True
            assert result["data"]["order_id"] == 123456
            
            # 檢查調用參數
            mock_rest.create_order.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                order_type="LIMIT",
                side="BUY",
                order_price=42000.0,
                order_quantity=0.001
            )
    
    @pytest.mark.asyncio
    async def test_create_market_order(self):
        """測試創建市價訂單"""
        from src.core.client import OrderlyClient
        
        with patch('src.core.client.RestAsync') as mock_rest_class:
            mock_rest = AsyncMock()
            mock_rest_class.return_value = mock_rest
            
            mock_rest.create_order.return_value = {
                "success": True,
                "data": {"order_id": 123457}
            }
            
            client = OrderlyClient()
            result = await client.create_market_order(
                symbol="PERP_BTC_USDC",
                side="SELL",
                quantity=0.001
            )
            
            assert result["success"] == True
            mock_rest.create_order.assert_called_once_with(
                symbol="PERP_BTC_USDC",
                order_type="MARKET",
                side="SELL",
                order_quantity=0.001
            )
    
    @pytest.mark.asyncio
    async def test_retry_mechanism(self):
        """測試重試機制"""
        from src.core.client import OrderlyClient
        
        with patch('src.core.client.RestAsync') as mock_rest_class:
            mock_rest = AsyncMock()
            mock_rest_class.return_value = mock_rest
            
            # 前兩次調用失敗，第三次成功
            mock_rest.create_order.side_effect = [
                Exception("網絡錯誤"),
                Exception("API 錯誤"),
                {"success": True, "data": {"order_id": 123458}}
            ]
            
            client = OrderlyClient()
            result = await client.create_limit_order(
                symbol="PERP_BTC_USDC",
                side="BUY",
                price=42000.0,
                quantity=0.001
            )
            
            # 應該重試成功
            assert result["success"] == True
            assert result["data"]["order_id"] == 123458
            
            # 應該調用了3次
            assert mock_rest.create_order.call_count == 3

class TestGridSignalGenerator:
    """測試網格訊號生成器"""
    
    @pytest.fixture
    def signal_generator(self):
        """創建訊號生成器"""
        from src.core.grid_signal import GridSignalGenerator, Direction
        
        return GridSignalGenerator(
            ticker="BTCUSDT",
            current_price=42500,
            direction=Direction.BOTH,
            upper_bound=45000,
            lower_bound=40000,
            grid_levels=6,
            total_amount=1000,
            stop_bot_price=38000,
            stop_top_price=47000
        )
    
    def test_initialization(self, signal_generator):
        """測試初始化"""
        assert signal_generator.ticker == "BTCUSDT"
        assert signal_generator.current_price == Decimal('42500')
        assert signal_generator.grid_levels == 6
        assert signal_generator.is_active == True
        assert len(signal_generator.grid_prices) > 0
    
    def test_grid_price_calculation(self, signal_generator):
        """測試網格價格計算"""
        prices = signal_generator.grid_prices
        
        # 檢查價格數量（應該是 grid_levels 個價格，不包括當前價格）
        assert len(prices) <= signal_generator.grid_levels
        
        # 檢查價格排序
        assert prices == sorted(prices)
        
        # 檢查價格範圍
        for price in prices:
            assert signal_generator.lower_bound <= price <= signal_generator.upper_bound
    
    def test_position_size_calculation(self, signal_generator):
        """測試倉位大小計算"""
        price = Decimal('42000')
        size = signal_generator._calculate_position_size(price)
        
        # 檢查計算邏輯：amount_per_grid / price
        expected_size = signal_generator.amount_per_grid / price
        assert abs(size - expected_size) < Decimal('0.000001')
    
    def test_signal_emission(self, signal_generator):
        """測試訊號發射"""
        from src.core.grid_signal import OrderSide
        
        received_signals = []
        
        def signal_callback(signal):
            received_signals.append(signal)
        
        signal_generator.signal_callback = signal_callback
        
        # 發射一個訊號
        signal = signal_generator._emit_signal(
            side=OrderSide.BUY,
            price=Decimal('42000'),
            size=Decimal('0.001'),
            signal_type="TEST"
        )
        
        assert signal.symbol == "BTCUSDT"
        assert signal.side == OrderSide.BUY
        assert signal.price == Decimal('42000')
        assert signal.size == Decimal('0.001')
        assert signal.signal_type == "TEST"
        assert len(received_signals) == 1
    
    def test_stop_conditions(self, signal_generator):
        """測試停損條件"""
        # 測試正常價格（不觸發停損）
        assert signal_generator.check_stop_conditions(Decimal('42000')) == False
        assert signal_generator.is_active == True
        
        # 測試下界停損
        assert signal_generator.check_stop_conditions(Decimal('37000')) == True
        assert signal_generator.is_active == False
        assert "下界停損" in signal_generator.stop_reason
        
        # 重新啟動
        signal_generator.restart_grid()
        assert signal_generator.is_active == True
        
        # 測試上界停損
        assert signal_generator.check_stop_conditions(Decimal('48000')) == True
        assert signal_generator.is_active == False
        assert "上界停損" in signal_generator.stop_reason
    
    def test_order_filled_handling(self, signal_generator):
        """測試訂單成交處理"""
        from src.core.grid_signal import TradingSignal, OrderSide
        
        received_signals = []
        
        def signal_callback(signal):
            received_signals.append(signal)
        
        signal_generator.signal_callback = signal_callback
        
        # 模擬訂單成交
        filled_signal = TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal('42000'),
            size=Decimal('0.001'),
            signal_type="FILLED"
        )
        
        signal_generator.on_order_filled(filled_signal)
        
        # 應該設置了第一次觸發
        assert signal_generator.first_trigger == True
        
        # 應該發射了取消訊號和新的掛單訊號
        signal_types = [signal.signal_type for signal in received_signals]
        assert "CANCEL_ALL" in signal_types
        assert "COUNTER" in signal_types

class TestMarketValidator:
    """測試市場驗證器"""
    
    @pytest.fixture
    def validator(self):
        """創建驗證器"""
        from src.utils.market_validator import MarketValidator
        return MarketValidator()
    
    def test_symbol_conversion(self, validator):
        """測試符號轉換"""
        assert validator._convert_symbol("BTCUSDT") == "PERP_BTC_USDC"
        assert validator._convert_symbol("ETHUSDT") == "PERP_ETH_USDC"
        assert validator._convert_symbol("UNKNOWN") == "UNKNOWN"
    
    def test_market_info_retrieval(self, validator):
        """測試市場信息獲取"""
        btc_info = validator.get_market_info("PERP_BTC_USDC")
        assert btc_info is not None
        assert btc_info.symbol == "PERP_BTC_USDC"
        assert btc_info.tick_size == Decimal("0.01")
        assert btc_info.step_size == Decimal("0.0001")
        
        # 測試不存在的符號
        unknown_info = validator.get_market_info("UNKNOWN_SYMBOL")
        assert unknown_info is None
    
    def test_price_normalization(self, validator):
        """測試價格標準化"""
        btc_info = validator.get_market_info("PERP_BTC_USDC")
        
        # 測試正常價格
        price = Decimal("42500.123")
        normalized = validator.normalize_price(price, btc_info)
        assert normalized == Decimal("42500.12")  # tick_size = 0.01
        
        # 測試邊界價格
        too_low = Decimal("0.001")
        normalized_low = validator.normalize_price(too_low, btc_info)
        assert normalized_low == btc_info.min_price
    
    def test_quantity_normalization(self, validator):
        """測試數量標準化"""
        btc_info = validator.get_market_info("PERP_BTC_USDC")
        
        # 測試正常數量
        quantity = Decimal("0.00123")
        normalized = validator.normalize_quantity(quantity, btc_info)
        assert normalized == Decimal("0.0012")  # step_size = 0.0001
        
        # 測試邊界數量
        too_small = Decimal("0.00001")
        normalized_small = validator.normalize_quantity(too_small, btc_info)
        assert normalized_small == btc_info.min_quantity
    
    def test_order_validation(self, validator):
        """測試訂單驗證"""
        from src.utils.market_validator import ValidationError
        
        # 測試有效訂單
        price, quantity = validator.validate_order(
            "PERP_BTC_USDC",
            Decimal("42500.123"),
            Decimal("0.00123")
        )
        assert price == Decimal("42500.12")
        assert quantity == Decimal("0.0012")
        
        # 測試名義價值太小的訂單
        with pytest.raises(ValidationError, match="名義價值.*小於最小值"):
            validator.validate_order(
                "PERP_BTC_USDC",
                Decimal("1.0"),
                Decimal("0.0001")
            )
        
        # 測試不支持的交易對
        with pytest.raises(ValidationError, match="不支持的交易對"):
            validator.validate_order(
                "UNKNOWN_SYMBOL",
                Decimal("100.0"),
                Decimal("1.0")
            )
    
    def test_config_validation(self, validator):
        """測試配置驗證"""
        from src.utils.market_validator import ValidationError
        from src.core.grid_signal import Direction
        
        # 測試有效配置
        valid_config = {
            "ticker": "BTCUSDT",
            "direction": Direction.BOTH,
            "current_price": 42500,
            "upper_bound": 45000,
            "lower_bound": 40000,
            "grid_levels": 6,
            "total_amount": 1000
        }
        
        validated = validator.validate_config(valid_config)
        assert validated is not None
        assert "_market_info" in validated
        assert "_orderly_symbol" in validated
        assert validated["_orderly_symbol"] == "PERP_BTC_USDC"
        
        # 測試無效配置
        invalid_configs = [
            # 空 ticker
            {**valid_config, "ticker": ""},
            
            # 無效價格
            {**valid_config, "current_price": 0},
            
            # 上界小於下界
            {**valid_config, "upper_bound": 40000, "lower_bound": 45000},
            
            # 當前價格超出範圍
            {**valid_config, "current_price": 50000},
            
            # 網格數量太少
            {**valid_config, "grid_levels": 1},
            
            # 總金額為負
            {**valid_config, "total_amount": -100}
        ]
        
        for invalid_config in invalid_configs:
            with pytest.raises(ValidationError):
                validator.validate_config(invalid_config)

class TestOrderTracker:
    """測試訂單追踪器"""
    
    @pytest.fixture
    def tracker(self):
        """創建追踪器"""
        from src.utils.order_tracker import OrderTracker
        return OrderTracker()
    
    def test_add_order(self, tracker):
        """測試添加訂單"""
        order_info = tracker.add_order(
            order_id=12345,
            symbol="PERP_BTC_USDC",
            side="BUY",
            order_type="LIMIT",
            price=Decimal('42000'),
            quantity=Decimal('0.001')
        )
        
        assert order_info.order_id == 12345
        assert order_info.symbol == "PERP_BTC_USDC"
        assert order_info.side == "BUY"
        assert order_info.original_price == Decimal('42000')
        assert order_info.original_quantity == Decimal('0.001')
        assert order_info.filled_quantity == Decimal('0')
        assert order_info.remaining_quantity == Decimal('0.001')
        
        # 檢查是否已添加到追踪器
        assert 12345 in tracker.orders
        assert tracker.get_order(12345) == order_info
    
    def test_add_fill(self, tracker):
        """測試添加成交記錄"""
        # 先添加訂單
        tracker.add_order(
            order_id=12345,
            symbol="PERP_BTC_USDC",
            side="BUY",
            order_type="LIMIT",
            price=Decimal('42000'),
            quantity=Decimal('0.001')
        )
        
        # 添加部分成交
        success = tracker.add_fill(
            order_id=12345,
            fill_id="fill_001",
            price=Decimal('42000'),
            quantity=Decimal('0.0005'),
            side="BUY"
        )
        
        assert success == True
        
        order_info = tracker.get_order(12345)
        assert order_info.filled_quantity == Decimal('0.0005')
        assert order_info.remaining_quantity == Decimal('0.0005')
        assert len(order_info.fills) == 1
        assert order_info.is_partially_filled() == True
        assert order_info.is_fully_filled() == False
        
        # 添加剩餘成交
        tracker.add_fill(
            order_id=12345,
            fill_id="fill_002",
            price=Decimal('42100'),
            quantity=Decimal('0.0005'),
            side="BUY"
        )
        
        order_info = tracker.get_order(12345)
        assert order_info.filled_quantity == Decimal('0.001')
        assert order_info.remaining_quantity == Decimal('0')
        assert len(order_info.fills) == 2
        assert order_info.is_fully_filled() == True
        
        # 檢查平均成交價格
        expected_avg = (Decimal('42000') * Decimal('0.0005') + 
                       Decimal('42100') * Decimal('0.0005')) / Decimal('0.001')
        assert order_info.average_fill_price == expected_avg
    
    def test_duplicate_fill_prevention(self, tracker):
        """測試重複成交記錄防護"""
        tracker.add_order(
            order_id=12345,
            symbol="PERP_BTC_USDC",
            side="BUY",
            order_type="LIMIT",
            price=Decimal('42000'),
            quantity=Decimal('0.001')
        )
        
        # 添加成交記錄
        success1 = tracker.add_fill(
            order_id=12345,
            fill_id="fill_001",
            price=Decimal('42000'),
            quantity=Decimal('0.0005'),
            side="BUY"
        )
        assert success1 == True
        
        # 嘗試添加重複的成交記錄
        success2 = tracker.add_fill(
            order_id=12345,
            fill_id="fill_001",  # 相同的 fill_id
            price=Decimal('42000'),
            quantity=Decimal('0.0003'),
            side="BUY"
        )
        assert success2 == False
        
        # 檢查數量沒有重複計算
        order_info = tracker.get_order(12345)
        assert order_info.filled_quantity == Decimal('0.0005')
        assert len(order_info.fills) == 1
    
    def test_statistics(self, tracker):
        """測試統計功能"""
        # 添加多個訂單
        tracker.add_order(1, "PERP_BTC_USDC", "BUY", "LIMIT", Decimal('42000'), Decimal('0.001'))
        tracker.add_order(2, "PERP_BTC_USDC", "SELL", "LIMIT", Decimal('43000'), Decimal('0.002'))
        tracker.add_order(3, "PERP_ETH_USDC", "BUY", "MARKET", Decimal('3000'), Decimal('0.01'))
        
        # 添加一些成交記錄
        tracker.add_fill(1, "fill_1", Decimal('42000'), Decimal('0.001'), "BUY")  # 完全成交
        tracker.add_fill(2, "fill_2", Decimal('43000'), Decimal('0.001'), "SELL")  # 部分成交
        
        stats = tracker.get_statistics()
        
        assert stats["total_orders"] == 3
        assert stats["filled_orders"] == 1  # 只有訂單1完全成交
        assert stats["active_orders"] == 2  # 訂單2部分成交，訂單3未成交
        assert stats["total_fills"] == 2
        assert stats["fill_rate"] == 1/3  # 1個完全成交 / 3個總訂單
        
        # 測試按狀態獲取訂單
        active_orders = tracker.get_active_orders()
        filled_orders = tracker.get_filled_orders()
        
        assert len(active_orders) == 2
        assert len(filled_orders) == 1
        assert filled_orders[0].order_id == 1
    
    def test_remove_order(self, tracker):
        """測試移除訂單"""
        # 添加訂單和成交記錄
        tracker.add_order(12345, "PERP_BTC_USDC", "BUY", "LIMIT", Decimal('42000'), Decimal('0.001'))
        tracker.add_fill(12345, "fill_001", Decimal('42000'), Decimal('0.0005'), "BUY")
        
        # 確認訂單和成交記錄存在
        assert 12345 in tracker.orders
        assert "fill_001" in tracker.fill_ids
        
        # 移除訂單
        success = tracker.remove_order(12345)
        assert success == True
        
        # 確認訂單和相關成交記錄已移除
        assert 12345 not in tracker.orders
        assert "fill_001" not in tracker.fill_ids
        
        # 嘗試移除不存在的訂單
        success2 = tracker.remove_order(99999)
        assert success2 == False
    
    def test_clear(self, tracker):
        """測試清空功能"""
        # 添加一些數據
        tracker.add_order(1, "PERP_BTC_USDC", "BUY", "LIMIT", Decimal('42000'), Decimal('0.001'))
        tracker.add_order(2, "PERP_BTC_USDC", "SELL", "LIMIT", Decimal('43000'), Decimal('0.002'))
        tracker.add_fill(1, "fill_1", Decimal('42000'), Decimal('0.001'), "BUY")
        
        # 確認有數據
        assert len(tracker.orders) == 2
        assert len(tracker.fill_ids) == 1
        
        # 清空
        tracker.clear()
        
        # 確認已清空
        assert len(tracker.orders) == 0
        assert len(tracker.fill_ids) == 0
        
        stats = tracker.get_statistics()
        assert stats["total_orders"] == 0
        assert stats["total_fills"] == 0

class TestLoggingAndMetrics:
    """測試日誌和指標系統"""
    
    def test_structured_logger_creation(self):
        """測試結構化日誌器創建"""
        from src.utils.logging_config import get_logger
        
        logger = get_logger("test_component")
        assert logger is not None
        assert logger.name == "test_component"
    
    def test_metrics_counter(self, reset_metrics):
        """測試指標計數器"""
        from src.utils.logging_config import metrics
        
        # 測試基本計數
        metrics.increment_counter("test.counter")
        metrics.increment_counter("test.counter", 5)
        
        data = metrics.get_metrics()
        assert data["counters"]["test.counter"] == 6
        
        # 測試帶標籤的計數
        metrics.increment_counter("test.tagged", tags={"type": "A"})
        metrics.increment_counter("test.tagged", tags={"type": "B"})
        metrics.increment_counter("test.tagged", tags={"type": "A"})
        
        data = metrics.get_metrics()
        assert data["counters"]["test.tagged[type=A]"] == 2
        assert data["counters"]["test.tagged[type=B]"] == 1
    
    def test_metrics_gauge(self, reset_metrics):
        """測試指標量表"""
        from src.utils.logging_config import metrics
        
        metrics.set_gauge("test.gauge", 42.5)
        metrics.set_gauge("test.gauge", 100.0)  # 覆蓋前值
        
        data = metrics.get_metrics()
        assert data["gauges"]["test.gauge"] == 100.0
    
    def test_metrics_histogram(self, reset_metrics):
        """測試指標直方圖"""
        from src.utils.logging_config import metrics
        
        # 記錄一系列值
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 2.0, 3.0, 4.0, 1.0, 5.0]
        for value in values:
            metrics.record_histogram("test.histogram", value)
        
        data = metrics.get_metrics()
        histogram = data["histograms"]["test.histogram"]
        
        assert histogram["count"] == 10
        assert histogram["min"] == 1.0
        assert histogram["max"] == 5.0
        assert histogram["avg"] == 3.0  # (1+2+3+4+5+2+3+4+1+5)/10
        assert histogram["p50"] >= 2.0  # 中位數
        assert histogram["p95"] >= 4.0  # 95分位數
        assert histogram["p99"] >= 5.0  # 99分位數
    
    def test_session_context(self):
        """測試會話上下文"""
        from src.utils.logging_config import (
            set_session_context, 
            clear_session_context,
            session_id_context,
            correlation_id_context
        )
        
        # 設置上下文
        set_session_context("test_session_123", "correlation_456")
        
        assert session_id_context.get() == "test_session_123"
        assert correlation_id_context.get() == "correlation_456"
        
        # 清除上下文
        clear_session_context()
        
        assert session_id_context.get() is None
        assert correlation_id_context.get() is None

if __name__ == "__main__":
    # 運行組件測試
    pytest.main(["-v", "tests/test_components.py", "--tb=short"])
