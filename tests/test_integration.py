#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易系統集成測試
測試完整的端到端工作流程
"""

import pytest
import asyncio
import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
from fastapi.testclient import TestClient

# 設置測試環境變數
import os
TEST_ENV_VARS = {
    "ORDERLY_KEY": "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
    "ORDERLY_SECRET": "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
    "ORDERLY_ACCOUNT_ID": "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0"
}

for key, value in TEST_ENV_VARS.items():
    os.environ[key] = value

from src.api.server import app
from src.core.grid_signal import Direction, OrderSide, TradingSignal
from src.utils.logging_config import get_logger

logger = get_logger("test_integration")

class TestGridTradingWorkflow:
    """測試完整的網格交易工作流程"""
    
    @pytest.fixture(scope="function")
    def client(self):
        """創建測試客戶端"""
        with TestClient(app) as test_client:
            yield test_client
    
    @pytest.fixture(scope="function")
    def mock_orderly_responses(self):
        """設置完整的 Orderly API 模擬響應"""
        responses = {
            "account_info": {
                "success": True,
                "data": {
                    "account_id": TEST_ENV_VARS["ORDERLY_ACCOUNT_ID"],
                    "total_collateral": "10000.0",
                    "free_collateral": "5000.0",
                    "total_collateral_value": 10000.0,
                    "free_collateral_value": 5000.0,
                    "total_pnl_24h": 150.0,
                    "total_unsettled_pnl": 0.0
                }
            },
            "positions": {
                "success": True,
                "data": {
                    "rows": [
                        {
                            "symbol": "PERP_BTC_USDC",
                            "position_qty": "0.0",
                            "cost_position": "0.0",
                            "last_sum_unitary_fundings": "0.0",
                            "pending_long_qty": "0.0",
                            "pending_short_qty": "0.0",
                            "settle_price": "42500.0",
                            "average_open_price": "0.0",
                            "unsettled_pnl": "0.0",
                            "mark_price": "42500.0",
                            "est_liq_price": None,
                            "timestamp": int(time.time() * 1000)
                        }
                    ]
                }
            },
            "create_order": {
                "success": True,
                "data": {
                    "order_id": 123456789,
                    "user_id": 12345,
                    "price": "42000.0",
                    "type": "LIMIT",
                    "quantity": "0.001",
                    "amount": None,
                    "visible": "0.001",
                    "executed": "0.0",
                    "total_fee": "0.0",
                    "fee_asset": "USDC",
                    "client_order_id": None,
                    "reduce_only": False,
                    "realized_pnl": None,
                    "average_executed_price": None,
                    "status": "NEW",
                    "side": "BUY",
                    "symbol": "PERP_BTC_USDC",
                    "created_time": int(time.time() * 1000),
                    "updated_time": int(time.time() * 1000)
                }
            },
            "cancel_orders": {
                "success": True,
                "data": {
                    "rows": []
                }
            },
            "get_orders": {
                "success": True,
                "data": {
                    "rows": [],
                    "meta": {
                        "total": 0,
                        "records_per_page": 25,
                        "current_page": 1
                    }
                }
            }
        }
        return responses
    
    @patch('src.core.client.OrderlyClient')
    @patch('src.core.grid_bot.WebsocketPrivateAPIClient')
    def test_complete_grid_trading_lifecycle(self, mock_ws_class, mock_client_class, 
                                           client, mock_orderly_responses):
        """測試完整的網格交易生命週期"""
        # 設置 Orderly 客戶端模擬
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # 設置所有必要的 API 響應
        mock_client.get_account_info.return_value = mock_orderly_responses["account_info"]
        mock_client.get_positions.return_value = mock_orderly_responses["positions"]
        mock_client.create_limit_order.return_value = mock_orderly_responses["create_order"]
        mock_client.cancel_all_orders.return_value = mock_orderly_responses["cancel_orders"]
        mock_client.get_orders.return_value = mock_orderly_responses["get_orders"]
        
        # 設置 WebSocket 模擬
        mock_ws = MagicMock()
        mock_ws_class.return_value = mock_ws
        
        # 1. 檢查服務器健康狀態
        health_response = client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json()["status"] == "healthy"
        
        # 2. 檢查就緒狀態
        ready_response = client.get("/health/ready")
        assert ready_response.status_code == 200
        assert ready_response.json()["status"] == "ready"
        
        # 3. 啟動網格交易
        start_config = {
            "ticker": "BTCUSDT",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_amount": 1000.0,
            "stop_bot_price": 38000.0,
            "stop_top_price": 47000.0,
            "user_id": "integration_test_user",
            "user_sig": "integration_test_signature"
        }
        
        start_response = client.post("/api/grid/start", json=start_config)
        assert start_response.status_code == 200
        
        start_data = start_response.json()
        assert start_data["status"] == "started"
        session_id = start_data["session_id"]
        assert session_id == "integration_test_user_BTCUSDT"
        
        # 4. 檢查會話狀態
        status_response = client.get(f"/api/grid/status/{session_id}")
        assert status_response.status_code == 200
        
        status_data = status_response.json()
        assert status_data["session_id"] == session_id
        assert "status" in status_data
        
        # 5. 列出所有會話
        sessions_response = client.get("/api/grid/sessions")
        assert sessions_response.status_code == 200
        
        sessions_data = sessions_response.json()
        assert "sessions" in sessions_data
        assert session_id in sessions_data["sessions"]
        
        # 6. 停止網格交易
        stop_config = {"session_id": session_id}
        stop_response = client.post("/api/grid/stop", json=stop_config)
        assert stop_response.status_code == 200
        
        stop_data = stop_response.json()
        assert stop_data["status"] == "stopped"
        assert stop_data["session_id"] == session_id
        
        # 7. 確認會話已停止
        final_status_response = client.get(f"/api/grid/status/{session_id}")
        assert final_status_response.status_code == 404
        
        # 8. 檢查指標已記錄
        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        
        metrics_data = metrics_response.json()
        assert "counters" in metrics_data
        # 應該有啟動和停止的計數
        counters = metrics_data["counters"]
        start_requests = [k for k in counters.keys() if "api.grid.start.requests" in k]
        stop_requests = [k for k in counters.keys() if "api.grid.stop.requests" in k]
        assert len(start_requests) > 0
        assert len(stop_requests) > 0
    
    @patch('src.core.client.OrderlyClient')
    def test_multiple_concurrent_sessions(self, mock_client_class, client, mock_orderly_responses):
        """測試多個並發交易會話"""
        # 設置模擬
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        mock_client.get_account_info.return_value = mock_orderly_responses["account_info"]
        mock_client.get_positions.return_value = mock_orderly_responses["positions"]
        mock_client.create_limit_order.return_value = mock_orderly_responses["create_order"]
        mock_client.cancel_all_orders.return_value = mock_orderly_responses["cancel_orders"]
        
        # 創建多個不同的交易會話
        configs = [
            {
                "ticker": "BTCUSDT",
                "direction": "LONG",
                "current_price": 42500.0,
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 4,
                "total_amount": 500.0,
                "user_id": "user1",
                "user_sig": "sig1"
            },
            {
                "ticker": "BTCUSDT",
                "direction": "SHORT",
                "current_price": 42500.0,
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 4,
                "total_amount": 500.0,
                "user_id": "user2",
                "user_sig": "sig2"
            },
            {
                "ticker": "BTCUSDT",
                "direction": "BOTH",
                "current_price": 42500.0,
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 6,
                "total_amount": 1000.0,
                "user_id": "user3",
                "user_sig": "sig3"
            }
        ]
        
        session_ids = []
        
        # 啟動所有會話
        for config in configs:
            response = client.post("/api/grid/start", json=config)
            assert response.status_code == 200
            
            data = response.json()
            assert data["status"] == "started"
            session_ids.append(data["session_id"])
        
        # 檢查所有會話都在運行
        sessions_response = client.get("/api/grid/sessions")
        assert sessions_response.status_code == 200
        
        sessions_data = sessions_response.json()
        for session_id in session_ids:
            assert session_id in sessions_data["sessions"]
            assert sessions_data["sessions"][session_id] == True  # 應該在運行
        
        # 停止所有會話
        for session_id in session_ids:
            stop_response = client.post("/api/grid/stop", json={"session_id": session_id})
            assert stop_response.status_code == 200
            
            stop_data = stop_response.json()
            assert stop_data["status"] == "stopped"
    
    @patch('src.core.client.OrderlyClient')
    def test_error_handling_scenarios(self, mock_client_class, client):
        """測試各種錯誤處理場景"""
        # 測試 API 錯誤
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        
        # 模擬 Orderly API 錯誤
        mock_client.get_account_info.side_effect = Exception("API 連接失敗")
        
        start_config = {
            "ticker": "BTCUSDT",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_amount": 1000.0,
            "user_id": "error_test_user",
            "user_sig": "error_test_sig"
        }
        
        # 應該返回錯誤
        start_response = client.post("/api/grid/start", json=start_config)
        assert start_response.status_code == 500
        
        error_data = start_response.json()
        assert "failed_to_start" in error_data["detail"]
    
    def test_api_validation_errors(self, client):
        """測試 API 參數驗證錯誤"""
        # 測試各種無效配置
        invalid_configs = [
            # 缺少必要欄位
            {"ticker": "BTCUSDT"},
            
            # 無效的價格邊界
            {
                "ticker": "BTCUSDT",
                "direction": "BOTH",
                "current_price": 50000.0,  # 超出邊界
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 6,
                "total_amount": 1000.0,
                "user_id": "test",
                "user_sig": "sig"
            },
            
            # 無效的網格數量
            {
                "ticker": "BTCUSDT",
                "direction": "BOTH",
                "current_price": 42500.0,
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 0,  # 無效
                "total_amount": 1000.0,
                "user_id": "test",
                "user_sig": "sig"
            },
            
            # 無效的總金額
            {
                "ticker": "BTCUSDT",
                "direction": "BOTH",
                "current_price": 42500.0,
                "upper_bound": 45000.0,
                "lower_bound": 40000.0,
                "grid_levels": 6,
                "total_amount": -100.0,  # 負值
                "user_id": "test",
                "user_sig": "sig"
            }
        ]
        
        for invalid_config in invalid_configs:
            response = client.post("/api/grid/start", json=invalid_config)
            assert response.status_code == 422, f"配置應該被拒絕: {invalid_config}"
            
            error_data = response.json()
            assert "detail" in error_data
    
    def test_session_id_conflicts(self, client):
        """測試會話 ID 衝突處理"""
        config = {
            "ticker": "BTCUSDT",
            "direction": "BOTH",
            "current_price": 42500.0,
            "upper_bound": 45000.0,
            "lower_bound": 40000.0,
            "grid_levels": 6,
            "total_amount": 1000.0,
            "user_id": "duplicate_user",
            "user_sig": "duplicate_sig"
        }
        
        with patch('src.utils.session_manager.SessionManager.create_session') as mock_create:
            # 第一次創建成功
            mock_create.return_value = True
            response1 = client.post("/api/grid/start", json=config)
            assert response1.status_code == 200
            assert response1.json()["status"] == "started"
            
            # 第二次創建失敗（已存在）
            mock_create.return_value = False
            response2 = client.post("/api/grid/start", json=config)
            assert response2.status_code == 200
            assert response2.json()["status"] == "already_running"

@pytest.mark.performance
class TestPerformance:
    """性能測試"""
    
    @pytest.fixture(scope="function")
    def client(self):
        with TestClient(app) as test_client:
            yield test_client
    
    def test_concurrent_api_calls(self, client):
        """測試並發 API 調用性能"""
        import concurrent.futures
        import time
        
        def make_health_check():
            response = client.get("/health")
            return response.status_code == 200
        
        # 並發發送多個健康檢查請求
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_health_check) for _ in range(50)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        
        end_time = time.time()
        elapsed = end_time - start_time
        
        # 所有請求都應該成功
        assert all(results)
        
        # 平均響應時間應該合理（50個請求在5秒內完成）
        assert elapsed < 5.0, f"並發請求耗時過長: {elapsed:.2f}秒"
        
        # 計算 RPS
        rps = len(results) / elapsed
        logger.info(f"健康檢查 RPS: {rps:.2f}")
        
        # 應該能處理至少 10 RPS
        assert rps >= 10.0
    
    def test_metrics_endpoint_performance(self, client):
        """測試指標端點性能"""
        # 先產生一些指標數據
        from src.utils.logging_config import metrics
        
        for i in range(1000):
            metrics.increment_counter(f"test.counter.{i % 10}")
            metrics.set_gauge(f"test.gauge.{i % 5}", float(i))
            metrics.record_histogram(f"test.histogram.{i % 3}", float(i))
        
        # 測試指標端點響應時間
        start_time = time.time()
        response = client.get("/metrics")
        end_time = time.time()
        
        assert response.status_code == 200
        
        elapsed = end_time - start_time
        assert elapsed < 1.0, f"指標端點響應過慢: {elapsed:.3f}秒"
        
        # 檢查響應數據
        data = response.json()
        assert len(data["counters"]) == 10
        assert len(data["gauges"]) == 5
        assert len(data["histograms"]) == 3

if __name__ == "__main__":
    # 運行集成測試
    pytest.main(["-v", "tests/test_integration.py", "--tb=short"])
