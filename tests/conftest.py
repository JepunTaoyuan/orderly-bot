#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pytest 配置文件
包含共享的測試設置和 fixtures
"""

import pytest
import asyncio
import os
import logging
from unittest.mock import patch, AsyncMock

# 設置測試環境變數（使用硬編碼值）
TEST_ENV_VARS = {
    "ORDERLY_KEY": "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
    "ORDERLY_SECRET": "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
    "ORDERLY_ACCOUNT_ID": "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0",
    "TESTING": "true"
}

def pytest_configure(config):
    """Pytest 配置"""
    # 設置測試環境變數
    for key, value in TEST_ENV_VARS.items():
        os.environ[key] = value
    
    # 配置日誌
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 禁用第三方庫的詳細日誌
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    # 註冊自定義標記
    config.addinivalue_line(
        "markers", "performance: 標記性能測試"
    )
    config.addinivalue_line(
        "markers", "integration: 標記集成測試"
    )
    config.addinivalue_line(
        "markers", "slow: 標記慢速測試"
    )
    config.addinivalue_line(
        "markers", "unit: 標記單元測試"
    )

def pytest_unconfigure(config):
    """Pytest 清理"""
    # 清理環境變數
    for key in TEST_ENV_VARS.keys():
        os.environ.pop(key, None)

@pytest.fixture(scope="session")
def event_loop():
    """為異步測試提供事件循環"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="function")
def reset_metrics():
    """重置指標收集器"""
    from src.utils.logging_config import metrics
    metrics.reset()
    yield
    metrics.reset()

@pytest.fixture(scope="function")
def mock_websocket():
    """模擬 WebSocket 連接"""
    with patch('src.core.grid_bot.WebsocketPrivateAPIClient') as mock_ws_class:
        mock_ws = AsyncMock()
        mock_ws_class.return_value = mock_ws
        mock_ws.get_notifications.return_value = None
        mock_ws.close.return_value = None
        yield mock_ws

@pytest.fixture(scope="function")
def mock_orderly_client():
    """模擬 Orderly 客戶端的完整響應"""
    with patch('src.core.client.OrderlyClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # 設置預設響應
        mock_client.get_account_info.return_value = {
            "success": True,
            "data": {
                "account_id": TEST_ENV_VARS["ORDERLY_ACCOUNT_ID"],
                "total_collateral": "10000.0",
                "free_collateral": "5000.0",
                "total_collateral_value": 10000.0,
                "free_collateral_value": 5000.0
            }
        }
        
        mock_client.get_positions.return_value = {
            "success": True,
            "data": {
                "rows": [
                    {
                        "symbol": "PERP_BTC_USDC",
                        "position_qty": "0.0",
                        "cost_position": "0.0",
                        "average_open_price": "0.0",
                        "unsettled_pnl": "0.0",
                        "mark_price": "42500.0"
                    }
                ]
            }
        }
        
        mock_client.create_limit_order.return_value = {
            "success": True,
            "data": {
                "order_id": 123456789,
                "price": "42000.0",
                "quantity": "0.001",
                "side": "BUY",
                "symbol": "PERP_BTC_USDC",
                "status": "NEW",
                "type": "LIMIT"
            }
        }
        
        mock_client.create_market_order.return_value = {
            "success": True,
            "data": {
                "order_id": 123456790,
                "quantity": "0.001",
                "side": "BUY",
                "symbol": "PERP_BTC_USDC",
                "status": "FILLED",
                "type": "MARKET"
            }
        }
        
        mock_client.cancel_order.return_value = {
            "success": True,
            "data": {"order_id": 123456789}
        }
        
        mock_client.cancel_all_orders.return_value = {
            "success": True,
            "data": {"cancelled": 0}
        }
        
        mock_client.get_orders.return_value = {
            "success": True,
            "data": {
                "rows": [],
                "meta": {"total": 0}
            }
        }
        
        yield mock_client

@pytest.fixture(scope="function")
def sample_grid_config():
    """樣本網格配置"""
    return {
        "ticker": "BTCUSDT",
        "direction": "BOTH",
        "current_price": 42500.0,
        "upper_bound": 45000.0,
        "lower_bound": 40000.0,
        "grid_levels": 6,
        "total_amount": 1000.0,
        "stop_bot_price": 38000.0,
        "stop_top_price": 47000.0,
        "user_id": "test_user_123",
        "user_sig": "test_signature_456"
    }

@pytest.fixture(scope="function")
def sample_trading_signals():
    """樣本交易訊號"""
    from src.core.grid_signal import TradingSignal, OrderSide
    from decimal import Decimal
    
    return [
        TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal('42000'),
            size=Decimal('0.001'),
            signal_type="INITIAL"
        ),
        TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            price=Decimal('43000'),
            size=Decimal('0.001'),
            signal_type="INITIAL"
        ),
        TradingSignal(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            price=Decimal('41500'),
            size=Decimal('0.001'),
            signal_type="COUNTER"
        )
    ]

@pytest.fixture(scope="function")
def temporary_session_manager():
    """創建臨時的會話管理器用於測試"""
    from src.utils.session_manager import SessionManager
    
    session_manager = SessionManager()
    yield session_manager
    
    # 清理所有會話
    import asyncio
    asyncio.create_task(session_manager.stop_all_sessions())

# 自定義標記
def pytest_collection_modifyitems(config, items):
    """修改測試收集，添加自定義標記"""
    for item in items:
        # 為異步測試添加標記
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(pytest.mark.asyncio)
        
        # 為性能測試添加標記
        if "performance" in item.nodeid.lower():
            item.add_marker(pytest.mark.performance)
        
        # 為集成測試添加標記
        if "integration" in item.nodeid.lower():
            item.add_marker(pytest.mark.integration)

# 添加命令行選項
def pytest_addoption(parser):
    """添加命令行選項"""
    parser.addoption(
        "--run-performance",
        action="store_true",
        default=False,
        help="運行性能測試"
    )
    
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="運行集成測試"
    )

# 跳過條件
def pytest_runtest_setup(item):
    """測試運行前的設置"""
    # 根據命令行選項跳過測試
    if "performance" in [mark.name for mark in item.iter_markers()]:
        if not item.config.getoption("--run-performance"):
            pytest.skip("需要 --run-performance 標誌來運行性能測試")
    
    if "integration" in [mark.name for mark in item.iter_markers()]:
        if not item.config.getoption("--run-integration"):
            pytest.skip("需要 --run-integration 標誌來運行集成測試")
