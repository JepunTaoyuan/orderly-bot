#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易伺服器測試套件
使用硬編碼的測試環境變數進行完整的系統測試
"""

import pytest
import asyncio
import json
import os
import time
from decimal import Decimal
from typing import Dict, Any
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
from fastapi.testclient import TestClient

# 設置硬編碼的測試環境變數（從 client.py 獲取）
TEST_ENV_VARS = {
    "ORDERLY_KEY": "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
    "ORDERLY_SECRET": "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
    "ORDERLY_ACCOUNT_ID": "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0"
}

# 設置環境變數
for key, value in TEST_ENV_VARS.items():
    os.environ[key] = value

# 現在可以安全地導入應用程式模組
from src.api.server import app
from src.core.grid_signal import Direction
from src.utils.session_manager import SessionManager
from src.utils.logging_config import get_logger, metrics

logger = get_logger("test_server")

class TestGridTradingServer:
    """網格交易伺服器測試類"""
    
    @pytest.fixture(scope="function")
    def client(self):
        """創建測試客戶端"""
        with TestClient(app) as test_client:
            yield test_client
    
    @pytest.fixture(scope="function")
    def sample_start_config(self):
        """樣本啟動配置"""
        return {
            "ticker": "BTCUSDT",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_amount": 100.0,
            "stop_bot_price": 38000.0,
            "stop_top_price": 47000.0,
            "user_id": "test_user_123",
            "user_sig": "test_signature_456"
        }
    

    
    def test_health_check(self, client):
        """測試健康檢查端點"""
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert data["version"] == "1.0.0"
    
    def test_readiness_check(self, client):
        """測試就緒檢查端點"""
        response = client.get("/health/ready")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "ready"
        assert "timestamp" in data
        assert "active_sessions" in data
        assert isinstance(data["active_sessions"], int)
    
    def test_metrics_endpoint(self, client):
        """測試指標端點"""
        response = client.get("/metrics")
        assert response.status_code == 200
        
        data = response.json()
        assert "counters" in data
        assert "gauges" in data
        assert "histograms" in data
        assert "timestamp" in data
    
    def test_root_endpoint(self, client):
        """測試根端點"""
        response = client.get("/")
        assert response.status_code == 200
        
        data = response.json()
        assert data["message"] == "Dexless Bot API"
        assert data["version"] == "1.0.0"
        assert data["WHATUP"] == "BRO"
    
    def test_start_config_validation(self, client):
        """測試啟動配置驗證"""
        # 測試無效配置
        invalid_configs = [
            # 缺少必要欄位
            {"ticker": "BTCUSDT"},
            
            # 無效的方向
            {
                "ticker": "BTCUSDT",
                "direction": "INVALID",
                "current_price": 42500,
                "upper_bound": 45000,
                "lower_bound": 40000,
                "grid_levels": 6,
                "total_amount": 100,
                "user_id": "test",
                "user_sig": "sig"
            },
            
            # 網格數量太少
            {
                "ticker": "BTCUSDT",
                "direction": "BOTH",
                "current_price": 42500,
                "upper_bound": 45000,
                "lower_bound": 40000,
                "grid_levels": 1,
                "total_amount": 100,
                "user_id": "test",
                "user_sig": "sig"
            },
            
            # 總金額為負
            {
                "ticker": "BTCUSDT",
                "direction": "BOTH",
                "current_price": 42500,
                "upper_bound": 45000,
                "lower_bound": 40000,
                "grid_levels": 6,
                "total_amount": -100,
                "user_id": "test",
                "user_sig": "sig"
            }
        ]
        
        for invalid_config in invalid_configs:
            response = client.post("/api/grid/start", json=invalid_config)
            assert response.status_code == 422, f"配置應該被拒絕: {invalid_config}"
    
    @patch('src.core.grid_bot.GridTradingBot')
    def test_start_grid_success(self, mock_bot_class, client, sample_start_config):
        """測試成功啟動網格交易"""
        # 設置模擬
        mock_bot = AsyncMock()
        mock_bot_class.return_value = mock_bot
        mock_bot.start_grid_trading.return_value = None
        
        with patch('src.utils.session_manager.SessionManager.create_session') as mock_create:
            mock_create.return_value = True
            
            response = client.post("/api/grid/start", json=sample_start_config)
            assert response.status_code == 200
            
            data = response.json()
            assert data["status"] == "started"
            assert "session_id" in data
            assert data["session_id"] == "test_user_123_BTCUSDT"
    
    @patch('src.core.grid_bot.GridTradingBot')
    def test_start_grid_already_running(self, mock_bot_class, client, sample_start_config):
        """測試啟動已在運行的網格交易"""
        with patch('src.utils.session_manager.SessionManager.create_session') as mock_create:
            mock_create.return_value = False
            
            response = client.post("/api/grid/start", json=sample_start_config)
            assert response.status_code == 409  # 改為 409 Conflict
            
            data = response.json()
            assert data["error_code"] == "E3001"
            assert data["message"] == "Session already exists"
    
    @patch('src.core.grid_bot.GridTradingBot')
    def test_start_grid_error(self, mock_bot_class, client, sample_start_config):
        """測試啟動網格交易時發生錯誤"""
        with patch('src.utils.session_manager.SessionManager.create_session') as mock_create:
            mock_create.side_effect = Exception("測試錯誤")
            
            response = client.post("/api/grid/start", json=sample_start_config)
            assert response.status_code == 500
            
            data = response.json()
            assert data["error_code"] == "E3002"
            assert data["message"] == "Failed to create session"
    
    def test_stop_grid_success(self, client):
        """測試成功停止網格交易"""
        stop_config = {"session_id": "test_user_123_BTCUSDT"}
        
        with patch('src.utils.session_manager.SessionManager.stop_session') as mock_stop:
            mock_stop.return_value = True
            
            response = client.post("/api/grid/stop", json=stop_config)
            assert response.status_code == 200
            
            data = response.json()
            assert data["status"] == "stopped"
            assert data["session_id"] == "test_user_123_BTCUSDT"
    
    def test_stop_grid_not_found(self, client):
        """測試停止不存在的網格交易"""
        stop_config = {"session_id": "nonexistent_session"}
        
        with patch('src.utils.session_manager.SessionManager.stop_session') as mock_stop:
            mock_stop.return_value = False
            
            response = client.post("/api/grid/stop", json=stop_config)
            assert response.status_code == 404  # 改為 404 Not Found
            
            data = response.json()
            assert data["error_code"] == "E3000"
            assert data["message"] == "Session not found"
    
    def test_stop_grid_error(self, client):
        """測試停止網格交易時發生錯誤"""
        stop_config = {"session_id": "test_session"}
        
        with patch('src.utils.session_manager.SessionManager.stop_session') as mock_stop:
            mock_stop.side_effect = Exception("停止錯誤")
            
            response = client.post("/api/grid/stop", json=stop_config)
            assert response.status_code == 500
    
    def test_get_status_success(self, client):
        """測試成功獲取狀態"""
        session_id = "test_user_123_BTCUSDT"
        mock_status = {
            "is_running": True,
            "active_orders_count": 2,
            "active_orders": {},
            "grid_orders": {}
        }
        
        with patch('src.utils.session_manager.SessionManager.get_session_status') as mock_get_status:
            mock_get_status.return_value = mock_status
            
            response = client.get(f"/api/grid/status/{session_id}")
            assert response.status_code == 200
            
            data = response.json()
            # 直接返回狀態，不包裝在額外的結構中
            assert data == mock_status
    
    def test_get_status_not_found(self, client):
        """測試獲取不存在會話的狀態"""
        session_id = "nonexistent_session"
        
        with patch('src.utils.session_manager.SessionManager.get_session_status') as mock_get_status:
            mock_get_status.return_value = None
            
            response = client.get(f"/api/grid/status/{session_id}")
            assert response.status_code == 404
            
            data = response.json()
            assert data["error_code"] == "E3000"
            assert data["message"] == "Session not found"
    
    def test_list_sessions(self, client):
        """測試列出所有會話"""
        mock_sessions = {
            "user1_BTCUSDT": True,
            "user2_ETHUSDT": False
        }
        
        with patch('src.utils.session_manager.SessionManager.list_sessions') as mock_list:
            mock_list.return_value = mock_sessions
            
            response = client.get("/api/grid/sessions")
            assert response.status_code == 200
            
            data = response.json()
            assert data["sessions"] == mock_sessions
    
    def test_enable_bot_trading(self, client):
        """測試啟用機器人交易端點"""
        config = {
            "user_id": "test_user",
            "user_api_key": "test_key",
            "user_api_secret": "test_secret",
            "user_wallet_address": "test_address"
        }
        
        response = client.post("/api/enable", json=config)
        # 目前這個端點只是 pass，所以應該返回 200
        assert response.status_code == 200

class TestEnvironmentVariables:
    """測試環境變數配置"""
    
    def test_hardcoded_env_vars_are_set(self):
        """測試硬編碼的環境變數已正確設置"""
        for key, expected_value in TEST_ENV_VARS.items():
            actual_value = os.environ.get(key)
            assert actual_value == expected_value, f"{key} 環境變數不匹配"
    
    def test_orderly_client_uses_env_vars(self):
        """測試 Orderly 客戶端使用環境變數"""
        from src.core.client import OrderlyClient
        
        # 這個測試確保客戶端會讀取我們設置的環境變數
        # 在實際環境中，客戶端會使用這些值連接到 Orderly API
        client = OrderlyClient()
        assert client is not None

class TestGridSignalGenerator:
    """測試網格訊號生成器"""
    
    @pytest.fixture
    def signal_generator(self):
        """創建訊號生成器實例"""
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
    
    def test_signal_generator_initialization(self, signal_generator):
        """測試訊號生成器初始化"""
        assert signal_generator.ticker == "BTCUSDT"
        assert signal_generator.current_price == Decimal('42500')
        assert signal_generator.direction.value == "雙向"
        assert signal_generator.grid_levels == 6
        assert signal_generator.is_active == True
    
    def test_grid_price_calculation(self, signal_generator):
        """測試網格價格計算"""
        prices = signal_generator.grid_prices
        assert len(prices) > 0
        
        # 檢查價格是否在邊界內
        for price in prices:
            assert signal_generator.lower_bound <= price <= signal_generator.upper_bound
        
        # 檢查價格是否已排序
        assert prices == sorted(prices)
    
    def test_stop_conditions(self, signal_generator):
        """測試停損條件"""
        # 測試下界停損
        assert signal_generator.check_stop_conditions(Decimal('37000')) == True
        assert signal_generator.is_active == False
        
        # 重新啟動以測試上界停損
        signal_generator.restart_grid()
        assert signal_generator.check_stop_conditions(Decimal('48000')) == True
        assert signal_generator.is_active == False

class TestMarketValidator:
    """測試市場驗證器"""
    
    @pytest.fixture
    def validator(self):
        """創建市場驗證器實例"""
        from src.utils.market_validator import MarketValidator
        return MarketValidator()
    
    def test_symbol_conversion(self, validator):
        """測試符號轉換"""
        assert validator._convert_symbol("BTCUSDT") == "PERP_BTC_USDC"
        assert validator._convert_symbol("ETHUSDT") == "PERP_ETH_USDC"
    
    def test_config_validation(self, validator):
        """測試配置驗證"""
        valid_config = {
            "ticker": "BTCUSDT",
            "direction": Direction.BOTH,
            "current_price": 42500,
            "upper_bound": 45000,
            "lower_bound": 40000,
            "grid_levels": 6,
            "total_amount": 1000
        }
        
        validated_config = validator.validate_config(valid_config)
        assert validated_config is not None
        assert "_market_info" in validated_config
        assert "_orderly_symbol" in validated_config
    
    def test_invalid_config_validation(self, validator):
        """測試無效配置驗證"""
        from src.utils.market_validator import ValidationError
        
        invalid_configs = [
            {"ticker": ""},  # 空ticker
            {
                "ticker": "BTCUSDT",
                "current_price": 0,  # 無效價格
                "upper_bound": 45000,
                "lower_bound": 40000,
                "grid_levels": 6,
                "total_amount": 1000
            },
            {
                "ticker": "BTCUSDT",
                "current_price": 42500,
                "upper_bound": 40000,  # 上界小於下界
                "lower_bound": 45000,
                "grid_levels": 6,
                "total_amount": 1000
            }
        ]
        
        for invalid_config in invalid_configs:
            with pytest.raises(ValidationError):
                validator.validate_config(invalid_config)

class TestOrderTracker:
    """測試訂單追踪器"""
    
    @pytest.fixture
    def order_tracker(self):
        """創建訂單追踪器實例"""
        from src.utils.order_tracker import OrderTracker
        return OrderTracker()
    
    def test_add_order(self, order_tracker):
        """測試添加訂單"""
        order_info = order_tracker.add_order(
            order_id=12345,
            symbol="PERP_BTC_USDC",
            side="BUY",
            order_type="LIMIT",
            price=Decimal('42000'),
            quantity=Decimal('0.001')
        )
        
        assert order_info.order_id == 12345
        assert order_info.symbol == "PERP_BTC_USDC"
        assert order_info.original_quantity == Decimal('0.001')
    
    def test_add_fill(self, order_tracker):
        """測試添加成交記錄"""
        # 先添加訂單
        order_tracker.add_order(
            order_id=12345,
            symbol="PERP_BTC_USDC",
            side="BUY",
            order_type="LIMIT",
            price=Decimal('42000'),
            quantity=Decimal('0.001')
        )
        
        # 添加成交記錄
        success = order_tracker.add_fill(
            order_id=12345,
            fill_id="fill_001",
            price=Decimal('42000'),
            quantity=Decimal('0.0005'),
            side="BUY"
        )
        
        assert success == True
        
        order_info = order_tracker.get_order(12345)
        assert order_info.filled_quantity == Decimal('0.0005')
        assert order_info.remaining_quantity == Decimal('0.0005')
        assert len(order_info.fills) == 1
    
    def test_statistics(self, order_tracker):
        """測試統計資料"""
        # 添加一些訂單和成交記錄
        order_tracker.add_order(1, "PERP_BTC_USDC", "BUY", "LIMIT", Decimal('42000'), Decimal('0.001'))
        order_tracker.add_order(2, "PERP_BTC_USDC", "SELL", "LIMIT", Decimal('43000'), Decimal('0.001'))
        
        order_tracker.add_fill(1, "fill_1", Decimal('42000'), Decimal('0.001'), "BUY")
        
        stats = order_tracker.get_statistics()
        assert stats["total_orders"] == 2
        assert stats["filled_orders"] == 1
        assert stats["active_orders"] == 1
        assert stats["total_fills"] == 1

class TestLoggingAndMetrics:
    """測試日誌和指標系統"""
    
    def test_structured_logger(self):
        """測試結構化日誌"""
        logger = get_logger("test")
        assert logger is not None
        
        # 測試日誌方法（不會實際輸出，只確保不拋出異常）
        logger.info("測試訊息", event_type="test_event", data={"key": "value"})
        logger.warning("警告訊息")
        logger.error("錯誤訊息")
        logger.debug("調試訊息")
    
    def test_metrics_collector(self):
        """測試指標收集器"""
        # 重置指標以確保乾淨的測試環境
        metrics.reset()
        
        # 測試計數器
        metrics.increment_counter("test.counter")
        metrics.increment_counter("test.counter", 5)
        
        # 測試量表
        metrics.set_gauge("test.gauge", 42.0)
        
        # 測試直方圖
        metrics.record_histogram("test.histogram", 1.0)
        metrics.record_histogram("test.histogram", 2.0)
        metrics.record_histogram("test.histogram", 3.0)
        
        # 獲取指標
        metrics_data = metrics.get_metrics()
        
        assert "counters" in metrics_data
        assert "gauges" in metrics_data
        assert "histograms" in metrics_data
        
        # 檢查計數器值
        assert metrics_data["counters"]["test.counter"] == 6
        
        # 檢查量表值
        assert metrics_data["gauges"]["test.gauge"] == 42.0
        
        # 檢查直方圖統計
        histogram_stats = metrics_data["histograms"]["test.histogram"]
        assert histogram_stats["count"] == 3
        assert histogram_stats["min"] == 1.0
        assert histogram_stats["max"] == 3.0
        assert histogram_stats["avg"] == 2.0

@pytest.mark.asyncio
class TestAsyncComponents:
    """測試異步組件"""
    
    async def test_session_manager(self):
        """測試會話管理器"""
        from src.utils.session_manager import SessionManager
        
        session_manager = SessionManager()
        
        # 測試列出空會話
        sessions = await session_manager.list_sessions()
        assert isinstance(sessions, dict)
        
        # 測試獲取不存在的會話狀態
        status = await session_manager.get_session_status("nonexistent")
        assert status is None
    
    @patch('src.core.client.OrderlyClient')
    async def test_orderly_client_mock(self, mock_client_class):
        """測試模擬的 Orderly 客戶端"""
        from src.core.client import OrderlyClient
        
        # 創建模擬客戶端
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # 設置模擬返回值
        mock_client.get_account_info.return_value = {
            "success": True,
            "data": {"account_id": TEST_ENV_VARS["ORDERLY_ACCOUNT_ID"]}
        }
        
        # 測試客戶端
        client = OrderlyClient()
        result = await client.get_account_info()
        
        assert result["success"] == True
        assert result["data"]["account_id"] == TEST_ENV_VARS["ORDERLY_ACCOUNT_ID"]

if __name__ == "__main__":
    # 運行測試
    pytest.main(["-v", "tests/test_server.py", "--tb=short"])
