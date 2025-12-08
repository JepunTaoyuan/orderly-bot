#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試速率限制優化功能
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock
from src.utils.rate_limit_protector import RateLimitProtector, RateLimitConfig


class TestRateLimitOptimization:

    def test_rate_limit_config(self):
        """測試速率限制配置"""
        config = RateLimitConfig(
            requests_per_minute=60,
            requests_per_second=5,
            safety_margin=0.8
        )

        assert config.requests_per_minute == 60
        assert config.requests_per_second == 5
        assert config.safety_margin == 0.8
        assert config.enable_adaptive_throttling is True

    @pytest.mark.asyncio
    async def test_basic_rate_limiting(self):
        """測試基本速率限制功能"""
        config = RateLimitConfig(
            requests_per_minute=10,
            requests_per_second=2,
            safety_margin=0.8
        )
        protector = RateLimitProtector(config)

        # 應該能夠發送請求
        assert await protector.acquire() is True
        assert await protector.acquire() is True

        # 快速發送多個請求應該被限制
        quick_requests = []
        for _ in range(10):
            quick_requests.append(await protector.acquire())

        # 只有前幾個請求應該通過
        assert sum(quick_requests) <= 4  # 考慮安全邊界

    @pytest.mark.asyncio
    async def test_adaptive_throttling(self):
        """測試自適應節流"""
        config = RateLimitConfig(
            requests_per_minute=20,
            requests_per_second=3,
            safety_margin=0.7,
            enable_adaptive_throttling=True
        )
        protector = RateLimitProtector(config)

        # 快速發送請求觸發自適應節流
        successful_requests = 0
        for i in range(15):
            if await protector.acquire():
                successful_requests += 1
            if i % 3 == 0:  # 每3個請求稍微等待
                await asyncio.sleep(0.1)

        # 自適應節流應該降低實際限制
        status = protector.get_status()
        assert status['adaptive_factor'] < 1.0

    @pytest.mark.asyncio
    async def test_rate_limit_error_detection(self):
        """測試速率限制錯誤檢測"""
        config = RateLimitConfig()
        protector = RateLimitProtector(config)

        # 模擬rate limit錯誤
        rate_limit_error = Exception("429 rate limit exceeded")
        assert protector._is_rate_limit_error(rate_limit_error) is True

        # 模擬其他錯誤
        other_error = Exception("invalid request")
        assert protector._is_rate_limit_error(other_error) is False

    @pytest.mark.asyncio
    async def test_backoff_mechanism(self):
        """測試退避機制"""
        config = RateLimitConfig(
            rate_limit_backoff_seconds=2
        )
        protector = RateLimitProtector(config)

        # 觸發rate limit
        protector._handle_rate_limit_error()

        # 應該進入退避期
        assert protector.is_rate_limited is True
        assert await protector.acquire() is False

        # 等待退避期結束
        await asyncio.sleep(2.1)

        # 應該能夠再次請求
        assert protector.is_rate_limited is False
        assert await protector.acquire() is True

    @pytest.mark.asyncio
    async def test_execute_with_protection(self):
        """測試受保護的執行功能"""
        config = RateLimitConfig()
        protector = RateLimitProtector(config)

        # 模擬成功的API調用
        mock_func = AsyncMock(return_value="success")
        result = await protector.execute_with_protection(mock_func)
        assert result == "success"
        mock_func.assert_called_once()

        # 模擬rate limit錯誤
        mock_func_fail = AsyncMock(side_effect=Exception("429 rate limit exceeded"))

        with pytest.raises(Exception):
            await protector.execute_with_protection(mock_func_fail, max_retries=2)

        # 應該有多次重試
        assert mock_func_fail.call_count >= 2

    def test_get_status(self):
        """測試狀態獲取"""
        config = RateLimitConfig()
        protector = RateLimitProtector(config)

        status = protector.get_status()

        assert 'is_rate_limited' in status
        assert 'current_rate_limit' in status
        assert 'usage_rate' in status
        assert 'stats' in status
        assert isinstance(status['stats'], dict)

    def test_reset_functionality(self):
        """測試重置功能"""
        config = RateLimitConfig()
        protector = RateLimitProtector(config)

        # 添加一些請求記錄
        protector.request_times.append(time.time())
        protector.current_rate_limit = 50

        # 重置
        protector.reset()

        # 驗證重置後狀態
        assert len(protector.request_times) == 0
        assert protector.current_rate_limit == config.requests_per_minute
        assert protector.is_rate_limited is False


if __name__ == "__main__":
    pytest.main([__file__])