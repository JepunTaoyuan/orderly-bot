#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日誌裝飾器
提供統一的日誌記錄功能
"""

import functools
import time
import traceback
from typing import Any, Optional, Callable, Dict
from src.utils.logging_config import get_logger
from src.utils.error_codes import GridTradingException

def log_execution(
    logger_name: str = None,
    log_args: bool = False,
    log_result: bool = False,
    log_exception: bool = True,
    event_type: str = None
):
    """
    記錄函數執行的日誌裝飾器

    Args:
        logger_name: 日誌記錄器名稱
        log_args: 是否記錄參數
        log_result: 是否記錄返回值
        log_exception: 是否記錄異常詳情
        event_type: 事件類型標記
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger_instance = get_logger(logger_name or func.__module__)
            func_name = func.__name__

            # 準備日誌數據
            log_data = {
                "function": func_name,
                "module": func.__module__
            }

            if event_type:
                log_data["event_type"] = event_type

            # 記錄參數
            if log_args:
                # 過濾敏感參數
                safe_args = _filter_sensitive_data(args)
                safe_kwargs = _filter_sensitive_data(kwargs)
                log_data.update({
                    "args_count": len(safe_args),
                    "kwargs": list(safe_kwargs.keys())
                })

            start_time = time.time()

            try:
                logger_instance.info(f"開始執行: {func_name}", extra=log_data)

                # 執行函數
                result = await func(*args, **kwargs)

                # 計算執行時間
                elapsed = time.time() - start_time
                log_data["duration"] = round(elapsed, 3)

                # 記錄返回值
                if log_result:
                    log_data["result_type"] = type(result).__name__
                    if isinstance(result, dict):
                        log_data["result_keys"] = list(result.keys())

                logger_instance.info(
                    f"執行成功: {func_name} (耗時: {elapsed:.3f}s)",
                    extra=log_data
                )

                return result

            except GridTradingException as e:
                # 記錄自定義異常
                elapsed = time.time() - start_time
                log_data.update({
                    "duration": round(elapsed, 3),
                    "error_code": e.error_code.value,
                    "error_details": e.details
                })

                logger_instance.error(
                    f"執行失敗(自定義異常): {func_name} - {e.error_detail.message}",
                    extra=log_data
                )
                raise

            except Exception as e:
                # 記錄其他異常
                elapsed = time.time() - start_time
                log_data.update({
                    "duration": round(elapsed, 3),
                    "exception_type": type(e).__name__,
                    "exception_message": str(e)
                })

                if log_exception:
                    log_data["traceback"] = traceback.format_exc()

                logger_instance.error(
                    f"執行失敗: {func_name} - {str(e)}",
                    extra=log_data,
                    exc_info=log_exception
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger_instance = get_logger(logger_name or func.__module__)
            func_name = func.__name__

            # 準備日誌數據
            log_data = {
                "function": func_name,
                "module": func.__module__
            }

            if event_type:
                log_data["event_type"] = event_type

            # 記錄參數
            if log_args:
                safe_args = _filter_sensitive_data(args)
                safe_kwargs = _filter_sensitive_data(kwargs)
                log_data.update({
                    "args_count": len(safe_args),
                    "kwargs": list(safe_kwargs.keys())
                })

            start_time = time.time()

            try:
                logger_instance.info(f"開始執行: {func_name}", extra=log_data)

                # 執行函數
                result = func(*args, **kwargs)

                # 計算執行時間
                elapsed = time.time() - start_time
                log_data["duration"] = round(elapsed, 3)

                # 記錄返回值
                if log_result:
                    log_data["result_type"] = type(result).__name__
                    if isinstance(result, dict):
                        log_data["result_keys"] = list(result.keys())

                logger_instance.info(
                    f"執行成功: {func_name} (耗時: {elapsed:.3f}s)",
                    extra=log_data
                )

                return result

            except Exception as e:
                # 記錄異常
                elapsed = time.time() - start_time
                log_data.update({
                    "duration": round(elapsed, 3),
                    "exception_type": type(e).__name__,
                    "exception_message": str(e)
                })

                if log_exception:
                    log_data["traceback"] = traceback.format_exc()

                logger_instance.error(
                    f"執行失敗: {func_name} - {str(e)}",
                    extra=log_data,
                    exc_info=log_exception
                )
                raise

        # 根據函數類型返回對應的包裝器
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator

def log_api_call(
    logger_name: str = None,
    log_request_body: bool = False,
    log_response_body: bool = False
):
    """
    API調用日誌裝飾器
    專門用於記錄API端點的調用

    Args:
        logger_name: 日誌記錄器名稱
        log_request_body: 是否記錄請求體
        log_response_body: 是否記錄響應體
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            logger_instance = get_logger(logger_name or "api")

            # 提取請求信息
            request = None
            for arg in args:
                if hasattr(arg, 'method') and hasattr(arg, 'url'):
                    request = arg
                    break

            if request:
                log_data = {
                    "event_type": "api_call",
                    "method": request.method,
                    "path": request.url.path,
                    "query": str(request.url.query) if request.url.query else None,
                    "client_ip": request.client.host if request.client else None
                }

                # 記錄請求頭（過濾敏感信息）
                headers = dict(request.headers)
                _filter_sensitive_headers(headers)
                log_data["headers"] = headers

                # 記錄請求體
                if log_request_body and hasattr(request, '_json'):
                    log_data["request_body"] = _filter_sensitive_data(request._json)
            else:
                log_data = {
                    "event_type": "api_call",
                    "function": func.__name__
                }

            start_time = time.time()

            try:
                logger_instance.info(
                    f"API調用開始: {request.method if request else 'Unknown'} {request.url.path if request else func.__name__}",
                    extra=log_data
                )

                # 執行函數
                result = await func(*args, **kwargs)

                # 計算響應時間
                elapsed = time.time() - start_time
                log_data["duration"] = round(elapsed, 3)

                # 記錄響應
                if log_response_body and isinstance(result, dict):
                    log_data["response_body"] = _filter_sensitive_data(result)

                logger_instance.info(
                    f"API調用成功: {request.method if request else 'Unknown'} {request.url.path if request else func.__name__} (耗時: {elapsed:.3f}s)",
                    extra=log_data
                )

                return result

            except Exception as e:
                elapsed = time.time() - start_time
                log_data.update({
                    "duration": round(elapsed, 3),
                    "error": str(e),
                    "status_code": getattr(e, 'status_code', 500)
                })

                logger_instance.error(
                    f"API調用失敗: {request.method if request else 'Unknown'} {request.url.path if request else func.__name__}",
                    extra=log_data
                )
                raise

        return wrapper
    return decorator

def log_performance(
    threshold_ms: float = 1000,
    logger_name: str = None
):
    """
    性能監控日誌裝飾器
    當執行時間超過閾值時記錄警告

    Args:
        threshold_ms: 時間閾值（毫秒）
        logger_name: 日誌記錄器名稱
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger_instance = get_logger(logger_name or func.__module__)

            start_time = time.time()
            result = await func(*args, **kwargs)
            elapsed_ms = (time.time() - start_time) * 1000

            if elapsed_ms > threshold_ms:
                logger_instance.warning(
                    f"性能警告: {func.__name__} 執行時間過長",
                    extra={
                        "function": func.__name__,
                        "duration_ms": round(elapsed_ms, 2),
                        "threshold_ms": threshold_ms
                    }
                )

            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger_instance = get_logger(logger_name or func.__module__)

            start_time = time.time()
            result = func(*args, **kwargs)
            elapsed_ms = (time.time() - start_time) * 1000

            if elapsed_ms > threshold_ms:
                logger_instance.warning(
                    f"性能警告: {func.__name__} 執行時間過長",
                    extra={
                        "function": func.__name__,
                        "duration_ms": round(elapsed_ms, 2),
                        "threshold_ms": threshold_ms
                    }
                )

            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator

# 輔助函數
def _filter_sensitive_data(data: Any) -> Any:
    """過濾敏感數據"""
    sensitive_fields = {
        'password', 'secret', 'key', 'token', 'signature',
        'api_key', 'api_secret', 'private_key', 'mnemonic'
    }

    if isinstance(data, dict):
        filtered = {}
        for k, v in data.items():
            if any(field in k.lower() for field in sensitive_fields):
                filtered[k] = "***"
            else:
                filtered[k] = _filter_sensitive_data(v)
        return filtered
    elif isinstance(data, (list, tuple)):
        return [_filter_sensitive_data(item) for item in data]
    else:
        return data

def _filter_sensitive_headers(headers: dict) -> None:
    """過濾敏感請求頭"""
    sensitive_headers = {
        'authorization', 'cookie', 'x-api-key', 'x-auth-token'
    }

    for header in list(headers.keys()):
        if header.lower() in sensitive_headers:
            headers[header] = "***"

# 導入必要的模組
import asyncio