#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
彈性處理器 - 提供重試、斷路器等彈性功能
"""

import asyncio
import random
import time
from typing import Callable, Any, Optional, List, Type, Union
from functools import wraps
from dataclasses import dataclass
from enum import Enum
from src.utils.logging_config import get_logger
from src.utils.error_codes import GridTradingException, ErrorCode

logger = get_logger("resilient_handler")

class BackoffStrategy(Enum):
    """退避策略"""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    FIBONACCI = "fibonacci"

@dataclass
class RetryConfig:
    """重試配置"""
    max_attempts: int = 3
    base_delay: float = 1.0  # 基礎延遲（秒）
    max_delay: float = 60.0  # 最大延遲（秒）
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    backoff_multiplier: float = 2.0  # 退避乘數
    jitter: bool = True  # 添加隨機抖動
    retryable_exceptions: List[Type[Exception]] = None
    non_retryable_exceptions: List[Type[Exception]] = None

    def __post_init__(self):
        if self.retryable_exceptions is None:
            self.retryable_exceptions = [
                ConnectionError,
                TimeoutError,
                GridTradingException,
            ]
        if self.non_retryable_exceptions is None:
            self.non_retryable_exceptions = [
                ValueError,
                TypeError,
                PermissionError,
            ]

class CircuitBreaker:
    """斷路器實現"""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def __call__(self, func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.state == "OPEN":
                if time.time() - self.last_failure_time < self.recovery_timeout:
                    raise GridTradingException(
                        error_code=ErrorCode.CIRCUIT_BREAKER_OPEN,
                        details={"function": func.__name__}
                    )
                else:
                    self.state = "HALF_OPEN"
                    logger.info(f"斷路器進入半開狀態: {func.__name__}")

            try:
                result = await func(*args, **kwargs)

                # 成功時重置
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                    self.failure_count = 0
                    logger.info(f"斷路器關閉: {func.__name__}")

                return result

            except self.expected_exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()

                if self.failure_count >= self.failure_threshold:
                    self.state = "OPEN"
                    logger.error(
                        f"斷路器開啟: {func.__name__}",
                        extra={"failure_count": self.failure_count}
                    )

                raise

        return wrapper

class RetryHandler:
    """重試處理器"""

    @staticmethod
    def calculate_delay(attempt: int, config: RetryConfig) -> float:
        """計算延遲時間"""
        if config.backoff_strategy == BackoffStrategy.FIXED:
            delay = config.base_delay
        elif config.backoff_strategy == BackoffStrategy.LINEAR:
            delay = config.base_delay * attempt
        elif config.backoff_strategy == BackoffStrategy.EXPONENTIAL:
            delay = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
        elif config.backoff_strategy == BackoffStrategy.FIBONACCI:
            delay = config.base_delay * RetryHandler._fibonacci(attempt)
        else:
            delay = config.base_delay

        # 限制最大延遲
        delay = min(delay, config.max_delay)

        # 添加抖動
        if config.jitter:
            jitter_amount = delay * 0.1
            delay += random.uniform(-jitter_amount, jitter_amount)

        return max(0, delay)

    @staticmethod
    def _fibonacci(n: int) -> int:
        """計算斐波那契數列"""
        if n <= 1:
            return 1
        a, b = 1, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b

    @staticmethod
    async def sleep_with_jitter(delay: float):
        """帶抖動的睡眠"""
        await asyncio.sleep(delay)

    @staticmethod
    def is_retryable_exception(exception: Exception, config: RetryConfig) -> bool:
        """判斷異常是否可重試"""
        # 檢查非重試異常
        for exc_type in config.non_retryable_exceptions:
            if isinstance(exception, exc_type):
                return False

        # 檢查重試異常
        for exc_type in config.retryable_exceptions:
            if isinstance(exception, exc_type):
                return True

        # 特殊處理 GridTradingException
        if isinstance(exception, GridTradingException):
            # 某些錯誤碼不應重試
            non_retryable_codes = {
                ErrorCode.INVALID_REQUEST,
                ErrorCode.INVALID_SIGNATURE,
                ErrorCode.USER_NOT_FOUND,
                ErrorCode.USER_ALREADY_EXISTS,
                ErrorCode.SESSION_ALREADY_EXISTS,
                ErrorCode.SESSION_NOT_FOUND,
            }
            if exception.error_code in non_retryable_codes:
                return False

        # 默認不重試未知異常
        return False

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL,
    backoff_multiplier: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Optional[List[Type[Exception]]] = None,
    non_retryable_exceptions: Optional[List[Type[Exception]]] = None,
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    重試裝飾器

    Args:
        max_attempts: 最大重試次數
        base_delay: 基礎延遲
        max_delay: 最大延遲
        backoff_strategy: 退避策略
        backoff_multiplier: 退避乘數
        jitter: 是否添加抖動
        retryable_exceptions: 可重試的異常類型
        non_retryable_exceptions: 不可重試的異常類型
        on_retry: 重試時的回調函數
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        backoff_strategy=backoff_strategy,
        backoff_multiplier=backoff_multiplier,
        jitter=jitter,
        retryable_exceptions=retryable_exceptions,
        non_retryable_exceptions=non_retryable_exceptions
    )

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return await func(*args, **kwargs)

                except Exception as e:
                    last_exception = e

                    # 檢查是否是最後一次嘗試
                    if attempt == config.max_attempts:
                        logger.error(
                            f"達到最大重試次數 ({config.max_attempts})，放棄重試",
                            extra={
                                "function": func.__name__,
                                "attempt": attempt,
                                "error": str(e)
                            }
                        )
                        raise

                    # 檢查是否可重試
                    if not RetryHandler.is_retryable_exception(e, config):
                        logger.warning(
                            f"異常不可重試，直接拋出",
                            extra={
                                "function": func.__name__,
                                "error": str(e)
                            }
                        )
                        raise

                    # 計算延遲
                    delay = RetryHandler.calculate_delay(attempt, config)

                    # 執行重試回調
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception as callback_error:
                            logger.error(f"重試回調執行失敗: {callback_error}")

                    # 記錄重試
                    logger.warning(
                        f"第 {attempt} 次嘗試失敗，{delay:.2f}秒後重試",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "max_attempts": config.max_attempts,
                            "delay": delay,
                            "error": str(e)
                        }
                    )

                    # 等待後重試
                    await RetryHandler.sleep_with_jitter(delay)

            # 理論上不會到達這裡
            raise last_exception

        return wrapper
    return decorator

# 預定義的重試配置
API_RETRY_CONFIG = RetryConfig(
    max_attempts=3,
    base_delay=0.5,
    max_delay=10.0,
    backoff_strategy=BackoffStrategy.EXPONENTIAL,
    jitter=True
)

DATABASE_RETRY_CONFIG = RetryConfig(
    max_attempts=5,
    base_delay=0.1,
    max_delay=5.0,
    backoff_strategy=BackoffStrategy.EXPONENTIAL,
    jitter=True
)

WEBSOCKET_RETRY_CONFIG = RetryConfig(
    max_attempts=10,
    base_delay=1.0,
    max_delay=30.0,
    backoff_strategy=BackoffStrategy.FIBONACCI,
    jitter=True
)

# 便捷裝飾器
api_retry = retry(**API_RETRY_CONFIG.__dict__)
database_retry = retry(**DATABASE_RETRY_CONFIG.__dict__)
websocket_retry = retry(**WEBSOCKET_RETRY_CONFIG.__dict__)