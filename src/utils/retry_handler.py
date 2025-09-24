#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重試處理器 - 支持指數退避和抖動
"""

import asyncio
import logging
import random
import time
from typing import Callable, Any, Optional, Type, Union
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)

class ErrorType(Enum):
    """錯誤類型"""
    TRANSIENT = "transient"      # 暫時性錯誤，可重試
    PERMANENT = "permanent"      # 永久性錯誤，不可重試
    RATE_LIMIT = "rate_limit"    # 速率限制錯誤
    UNKNOWN = "unknown"          # 未知錯誤

@dataclass
class RetryConfig:
    """重試配置"""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    jitter_ratio: float = 0.1

class RetryHandler:
    """重試處理器"""
    
    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        
        # 錯誤分類規則
        self.error_classifiers = {
            # 網絡相關錯誤 - 暫時性
            "ConnectionError": ErrorType.TRANSIENT,
            "TimeoutError": ErrorType.TRANSIENT,
            "ConnectTimeout": ErrorType.TRANSIENT,
            "ReadTimeout": ErrorType.TRANSIENT,
            
            # HTTP錯誤碼分類
            "429": ErrorType.RATE_LIMIT,  # Too Many Requests
            "500": ErrorType.TRANSIENT,   # Internal Server Error
            "502": ErrorType.TRANSIENT,   # Bad Gateway
            "503": ErrorType.TRANSIENT,   # Service Unavailable
            "504": ErrorType.TRANSIENT,   # Gateway Timeout
            
            "400": ErrorType.PERMANENT,   # Bad Request
            "401": ErrorType.PERMANENT,   # Unauthorized
            "403": ErrorType.PERMANENT,   # Forbidden
            "404": ErrorType.PERMANENT,   # Not Found
        }
    
    def classify_error(self, error: Exception) -> ErrorType:
        """
        分類錯誤類型
        
        Args:
            error: 異常對象
            
        Returns:
            錯誤類型
        """
        error_name = type(error).__name__
        error_str = str(error)
        
        # 檢查錯誤名稱
        if error_name in self.error_classifiers:
            return self.error_classifiers[error_name]
        
        # 檢查錯誤消息中的HTTP狀態碼
        for code, error_type in self.error_classifiers.items():
            if code.isdigit() and code in error_str:
                return error_type
        
        # 檢查常見的暫時性錯誤關鍵詞
        transient_keywords = [
            "timeout", "connection", "network", "temporary",
            "unavailable", "overload", "busy"
        ]
        
        error_lower = error_str.lower()
        for keyword in transient_keywords:
            if keyword in error_lower:
                return ErrorType.TRANSIENT
        
        return ErrorType.UNKNOWN
    
    def should_retry(self, error: Exception, attempt: int) -> bool:
        """
        判斷是否應該重試
        
        Args:
            error: 異常對象
            attempt: 當前嘗試次數
            
        Returns:
            是否應該重試
        """
        if attempt >= self.config.max_attempts:
            return False
        
        error_type = self.classify_error(error)
        
        # 永久性錯誤不重試
        if error_type == ErrorType.PERMANENT:
            return False
        
        # 暫時性錯誤和未知錯誤可以重試
        return error_type in [ErrorType.TRANSIENT, ErrorType.RATE_LIMIT, ErrorType.UNKNOWN]
    
    def calculate_delay(self, attempt: int, error_type: ErrorType = ErrorType.TRANSIENT) -> float:
        """
        計算重試延遲
        
        Args:
            attempt: 嘗試次數（從1開始）
            error_type: 錯誤類型
            
        Returns:
            延遲秒數
        """
        # 指數退避
        delay = self.config.base_delay * (self.config.exponential_base ** (attempt - 1))
        
        # 速率限制錯誤使用更長的延遲
        if error_type == ErrorType.RATE_LIMIT:
            delay *= 3
        
        # 限制最大延遲
        delay = min(delay, self.config.max_delay)
        
        # 添加抖動
        if self.config.jitter:
            jitter_amount = delay * self.config.jitter_ratio
            jitter = random.uniform(-jitter_amount, jitter_amount)
            delay += jitter
        
        return max(0, delay)
    
    async def retry_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        異步重試執行函數
        
        Args:
            func: 要執行的異步函數
            *args: 位置參數
            **kwargs: 關鍵字參數
            
        Returns:
            函數執行結果
            
        Raises:
            最後一次執行的異常
        """
        last_error = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                start_time = time.time()
                result = await func(*args, **kwargs)
                
                # 記錄成功
                if attempt > 1:
                    elapsed = time.time() - start_time
                    logger.info(
                        f"重試成功: 嘗試次數={attempt}, 耗時={elapsed:.2f}s, 函數={func.__name__}"
                    )
                
                return result
                
            except Exception as error:
                last_error = error
                error_type = self.classify_error(error)
                
                logger.warning(
                    f"執行失敗: 嘗試={attempt}/{self.config.max_attempts}, "
                    f"錯誤類型={error_type.value}, 錯誤={error}, 函數={func.__name__}"
                )
                
                # 檢查是否應該重試
                if not self.should_retry(error, attempt):
                    logger.error(f"不可重試的錯誤或達到最大嘗試次數: {error}")
                    raise error
                
                # 計算延遲並等待
                if attempt < self.config.max_attempts:
                    delay = self.calculate_delay(attempt, error_type)
                    logger.info(f"等待 {delay:.2f}s 後重試...")
                    await asyncio.sleep(delay)
        
        # 所有嘗試都失敗了
        logger.error(f"重試失敗: 所有 {self.config.max_attempts} 次嘗試都失敗")
        raise last_error
    
    def retry_sync(self, func: Callable, *args, **kwargs) -> Any:
        """
        同步重試執行函數
        
        Args:
            func: 要執行的同步函數
            *args: 位置參數
            **kwargs: 關鍵字參數
            
        Returns:
            函數執行結果
            
        Raises:
            最後一次執行的異常
        """
        last_error = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                start_time = time.time()
                result = func(*args, **kwargs)
                
                # 記錄成功
                if attempt > 1:
                    elapsed = time.time() - start_time
                    logger.info(
                        f"重試成功: 嘗試次數={attempt}, 耗時={elapsed:.2f}s, 函數={func.__name__}"
                    )
                
                return result
                
            except Exception as error:
                last_error = error
                error_type = self.classify_error(error)
                
                logger.warning(
                    f"執行失敗: 嘗試={attempt}/{self.config.max_attempts}, "
                    f"錯誤類型={error_type.value}, 錯誤={error}, 函數={func.__name__}"
                )
                
                # 檢查是否應該重試
                if not self.should_retry(error, attempt):
                    logger.error(f"不可重試的錯誤或達到最大嘗試次數: {error}")
                    raise error
                
                # 計算延遲並等待
                if attempt < self.config.max_attempts:
                    delay = self.calculate_delay(attempt, error_type)
                    logger.info(f"等待 {delay:.2f}s 後重試...")
                    time.sleep(delay)
        
        # 所有嘗試都失敗了
        logger.error(f"重試失敗: 所有 {self.config.max_attempts} 次嘗試都失敗")
        raise last_error

# 裝飾器版本
def retry_with_backoff(config: Optional[RetryConfig] = None):
    """
    重試裝飾器
    
    Args:
        config: 重試配置
    """
    handler = RetryHandler(config)
    
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                return await handler.retry_async(func, *args, **kwargs)
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                return handler.retry_sync(func, *args, **kwargs)
            return sync_wrapper
    
    return decorator
