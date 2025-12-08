#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 速率限制保護器
防止因API調用過於頻繁而觸發rate limit
"""

import asyncio
import time
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from collections import deque
from src.utils.logging_config import get_logger

logger = get_logger("rate_limit_protector")


@dataclass
class RateLimitConfig:
    """速率限制配置"""

    # 基本限制
    requests_per_minute: int = 120  # 每分鐘最大請求數
    requests_per_second: int = 10   # 每秒最大請求數

    # 保護機制
    enable_adaptive_throttling: bool = True  # 啟用自適應節流
    enable_backpressure: bool = True         # 啟用背壓控制
    safety_margin: float = 0.8               # 安全邊界（使用80%的限制）

    # 觸發後處理
    rate_limit_backoff_seconds: int = 60     # 觸發rate limit後的退避時間
    max_backoff_seconds: int = 300           # 最大退避時間
    backoff_multiplier: float = 2.0          # 退避倍數

    # 監控設置
    monitoring_window_seconds: int = 60      # 監控窗口時間
    alert_threshold: float = 0.7             # 警報閾值（70%）


class RateLimitProtector:
    """速率限制保護器"""

    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()

        # 請求追蹤
        self.request_times: deque = deque()
        self.second_buckets: deque = deque()  # 每秒請求計數

        # 狀態追蹤
        self.is_rate_limited = False
        self.rate_limit_start_time = 0
        self.consecutive_violations = 0

        # 自適應控制
        self.current_rate_limit = self.config.requests_per_minute
        self.adaptive_factor = 1.0

        # 統計信息
        self.stats = {
            'total_requests': 0,
            'rate_limit_hits': 0,
            'throttled_requests': 0,
            'adaptive_adjustments': 0,
            'last_request_time': 0
        }

    async def acquire(self, weight: int = 1) -> bool:
        """
        獲取請求許可

        Args:
            weight: 請求權重（某些API調用可能權重更高）

        Returns:
            是否獲得許可
        """
        current_time = time.time()
        self._cleanup_old_requests(current_time)

        # 檢查是否在rate limit退避期
        if self._is_in_backoff_period(current_time):
            logger.debug(f"在rate limit退避期內，拒絕請求")
            self.stats['throttled_requests'] += 1
            return False

        # 檢查速率限制
        if not self._can_make_request(current_time, weight):
            if self.config.enable_adaptive_throttling:
                await self._adaptive_throttle(current_time)

            logger.warning(f"請求被速率限制阻止",
                         data={
                             'current_rate': len(self.request_times),
                             'limit': self.current_rate_limit * self.config.safety_margin,
                             'weight': weight
                         })
            self.stats['throttled_requests'] += 1
            return False

        # 記錄請求
        self._record_request(current_time, weight)
        self.stats['total_requests'] += 1
        self.stats['last_request_time'] = current_time

        return True

    async def execute_with_protection(self, func: Callable, *args, **kwargs):
        """
        執行帶有速率限制保護的函數

        Args:
            func: 要執行的函數
            *args, **kwargs: 函數參數

        Returns:
            函數執行結果
        """
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            # 獲取請求許可
            if not await self.acquire():
                wait_time = min(self.config.rate_limit_backoff_seconds * (2 ** retry_count),
                               self.config.max_backoff_seconds)
                logger.info(f"等待 {wait_time} 秒後重試")
                await asyncio.sleep(wait_time)
                retry_count += 1
                continue

            try:
                # 執行函數
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                return result

            except Exception as e:
                # 檢查是否為rate limit錯誤
                if self._is_rate_limit_error(e):
                    logger.warning(f"檢測到rate limit錯誤: {e}")
                    self._handle_rate_limit_error()
                    retry_count += 1

                    if retry_count < max_retries:
                        wait_time = min(self.config.rate_limit_backoff_seconds * (2 ** retry_count),
                                       self.config.max_backoff_seconds)
                        await asyncio.sleep(wait_time)
                    continue
                else:
                    # 非rate limit錯誤，直接拋出
                    raise

        raise Exception(f"達到最大重試次數 {max_retries}")

    def _cleanup_old_requests(self, current_time: float):
        """清理過期的請求記錄"""
        cutoff_time = current_time - self.config.monitoring_window_seconds

        # 清理分鐘級請求記錄
        while self.request_times and self.request_times[0] < cutoff_time:
            self.request_times.popleft()

        # 清理秒級請求記錄
        while self.second_buckets and len(self.second_buckets) > 60:
            self.second_buckets.popleft()

    def _can_make_request(self, current_time: float, weight: int) -> bool:
        """檢查是否可以發送請求"""

        # 檢查每分鐘限制
        minute_limit = self.current_rate_limit * self.config.safety_margin
        if len(self.request_times) + weight > minute_limit:
            return False

        # 檢查每秒限制
        current_second = int(current_time)
        while len(self.second_buckets) > 0 and self.second_buckets[-1][0] < current_second:
            self.second_buckets.append((current_second, 0))

        if not self.second_buckets or self.second_buckets[-1][0] != current_second:
            self.second_buckets.append((current_second, 0))

        second_count = sum(count for _, count in self.second_buckets)
        second_limit = self.config.requests_per_second * self.config.safety_margin

        if second_count + weight > second_limit:
            return False

        return True

    async def _adaptive_throttle(self, current_time: float):
        """自適應節流"""
        if not self.config.enable_adaptive_throttling:
            await asyncio.sleep(0.1)
            return

        # 計算當前使用率
        usage_rate = len(self.request_times) / self.current_rate_limit

        if usage_rate > self.config.alert_threshold:
            # 降低速率限制
            new_limit = max(
                int(self.current_rate_limit * 0.9),  # 降低10%
                self.config.requests_per_minute // 2  # 但不低於基本限制的一半
            )

            if new_limit != self.current_rate_limit:
                logger.warning(f"自適應降低速率限制: {self.current_rate_limit} -> {new_limit}")
                self.current_rate_limit = new_limit
                self.adaptive_factor = new_limit / self.config.requests_per_minute
                self.stats['adaptive_adjustments'] += 1

        # 計算等待時間
        wait_time = (usage_rate - self.config.alert_threshold) * 2.0
        wait_time = max(wait_time, 0.1)  # 最小等待0.1秒

        await asyncio.sleep(wait_time)

    def _record_request(self, current_time: float, weight: int):
        """記錄請求"""
        for _ in range(weight):
            self.request_times.append(current_time)

        # 更新秒級計數
        current_second = int(current_time)
        if not self.second_buckets or self.second_buckets[-1][0] != current_second:
            self.second_buckets.append((current_second, weight))
        else:
            self.second_buckets[-1] = (current_second, self.second_buckets[-1][1] + weight)

    def _is_in_backoff_period(self, current_time: float) -> bool:
        """檢查是否在退避期"""
        return (self.is_rate_limited and
                current_time - self.rate_limit_start_time < self.config.rate_limit_backoff_seconds)

    def _is_rate_limit_error(self, error: Exception) -> bool:
        """檢查是否為rate limit錯誤"""
        error_str = str(error).lower()
        rate_limit_indicators = [
            'rate limit', '429', '-1003', 'too many requests',
            'exceeded the rate limit', 'frequency limit'
        ]
        return any(indicator in error_str for indicator in rate_limit_indicators)

    def _handle_rate_limit_error(self):
        """處理rate limit錯誤"""
        self.is_rate_limited = True
        self.rate_limit_start_time = time.time()
        self.consecutive_violations += 1
        self.stats['rate_limit_hits'] += 1

        # 進一步降低速率限制
        if self.config.enable_adaptive_throttling:
            reduction_factor = 0.5 ** self.consecutive_violations  # 指數降低
            new_limit = max(
                int(self.current_rate_limit * reduction_factor),
                self.config.requests_per_minute // 4  # 最低為基本限制的25%
            )

            if new_limit != self.current_rate_limit:
                logger.warning(f"Rate limit觸發，降低速率限制: {self.current_rate_limit} -> {new_limit}")
                self.current_rate_limit = new_limit
                self.stats['adaptive_adjustments'] += 1

    def reset(self):
        """重置保護器狀態"""
        self.request_times.clear()
        self.second_buckets.clear()
        self.is_rate_limited = False
        self.rate_limit_start_time = 0
        self.consecutive_violations = 0
        self.current_rate_limit = self.config.requests_per_minute
        self.adaptive_factor = 1.0

        logger.info("速率限制保護器已重置")

    def get_status(self) -> Dict[str, Any]:
        """獲取保護器狀態"""
        current_time = time.time()
        self._cleanup_old_requests(current_time)

        return {
            'is_rate_limited': self.is_rate_limited,
            'consecutive_violations': self.consecutive_violations,
            'current_rate_limit': self.current_rate_limit,
            'adaptive_factor': self.adaptive_factor,
            'recent_requests': len(self.request_times),
            'usage_rate': len(self.request_times) / self.current_rate_limit if self.current_rate_limit > 0 else 0,
            'stats': self.stats.copy(),
            'backoff_remaining': max(0, self.config.rate_limit_backoff_seconds - (current_time - self.rate_limit_start_time)) if self.is_rate_limited else 0
        }


# 全局保護器實例
_protectors: Dict[str, RateLimitProtector] = {}


def get_rate_limiter(name: str = "default", config: Optional[RateLimitConfig] = None) -> RateLimitProtector:
    """獲取或創建速率限制保護器"""
    if name not in _protectors:
        _protectors[name] = RateLimitProtector(config)
    return _protectors[name]


def reset_all_protectors():
    """重置所有保護器"""
    for protector in _protectors.values():
        protector.reset()
    logger.info("所有速率限制保護器已重置")