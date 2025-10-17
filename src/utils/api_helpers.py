#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 輔助工具
提取重複的邏輯，如錯誤處理、日誌記錄等
"""

import asyncio
from typing import Callable, Any, Dict, Optional
from functools import wraps
from src.utils.logging_config import get_logger
from src.utils.error_codes import GridTradingException, ErrorCode

logger = get_logger("api_helpers")


def with_retry_and_logging(operation_name: str, success_message: str = None, error_code: ErrorCode = ErrorCode.INTERNAL_SERVER_ERROR):
    """
    裝飾器：為 API 操作添加重試機制和日誌記錄
    
    Args:
        operation_name: 操作名稱，用於日誌記錄
        success_message: 成功時的日誌訊息模板
        error_code: 失敗時的錯誤碼
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs) -> Any:
            # 記錄開始日誌
            logger.info(f"開始{operation_name}", event_type=f"{operation_name.lower()}_start", data={
                "args": str(args)[:200],  # 限制日誌長度
                "kwargs": {k: str(v)[:100] for k, v in kwargs.items()}
            })
            
            try:
                # 執行操作
                result = await func(self, *args, **kwargs)
                
                # 記錄成功日誌
                success_msg = success_message or f"{operation_name}成功"
                logger.info(success_msg, event_type=f"{operation_name.lower()}_success", data={
                    "result_type": type(result).__name__
                })
                
                return result
                
            except GridTradingException:
                # 重新拋出自定義異常
                raise
            except Exception as e:
                # 記錄錯誤日誌
                logger.error(f"{operation_name}失敗", event_type=f"{operation_name.lower()}_error", data={
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                
                # 轉換為自定義異常
                raise GridTradingException(
                    error_code=error_code,
                    details={
                        "operation": operation_name,
                        "original_error": str(e)
                    },
                    original_error=e
                )
        
        return wrapper
    return decorator


def with_orderly_api_handling(operation_name: str):
    """
    裝飾器：為 Orderly API 調用添加統一的錯誤處理
    
    Args:
        operation_name: 操作名稱
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs) -> Any:
            logger.info(f"調用 Orderly API: {operation_name}")
            
            async def _api_call():
                response = await func(self, *args, **kwargs)
                return response
            
            try:
                # 使用重試處理器
                response = await self.retry_handler.retry_async(_api_call)
                logger.info(f"Orderly API 調用成功: {operation_name}")
                return response
                
            except Exception as e:
                logger.error(f"Orderly API 調用失敗: {operation_name}, 錯誤: {e}")
                
                # 根據錯誤類型選擇適當的錯誤碼
                if "rate limit" in str(e).lower():
                    error_code = ErrorCode.ORDERLY_RATE_LIMIT
                elif "connection" in str(e).lower() or "timeout" in str(e).lower():
                    error_code = ErrorCode.ORDERLY_CONNECTION_ERROR
                else:
                    error_code = ErrorCode.ORDERLY_API_ERROR
                
                raise GridTradingException(
                    error_code=error_code,
                    details={
                        "operation": operation_name,
                        "api_error": str(e)
                    },
                    original_error=e
                )
        
        return wrapper
    return decorator


class SessionContextManager:
    """會話上下文管理器"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
    
    def __enter__(self):
        from src.utils.logging_config import set_session_context
        set_session_context(self.session_id)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        from src.utils.logging_config import clear_session_context
        clear_session_context()


def validate_session_id(session_id: str) -> str:
    """
    驗證會話 ID 格式
    
    Args:
        session_id: 會話 ID
        
    Returns:
        驗證後的會話 ID
        
    Raises:
        GridTradingException: 如果會話 ID 格式不正確
    """
    if not session_id or not isinstance(session_id, str):
        raise GridTradingException(
            error_code=ErrorCode.INVALID_PARAMETER,
            details={"parameter": "session_id", "value": session_id}
        )
    
    if len(session_id.strip()) == 0:
        raise GridTradingException(
            error_code=ErrorCode.INVALID_PARAMETER,
            details={"parameter": "session_id", "reason": "empty"}
        )
    
    return session_id.strip()


def create_session_id(user_id: str, ticker: str) -> str:
    """
    創建標準化的會話 ID
    
    Args:
        user_id: 用戶 ID
        ticker: 交易對符號
        
    Returns:
        會話 ID
    """
    if not user_id or not ticker:
        raise GridTradingException(
            error_code=ErrorCode.MISSING_PARAMETER,
            details={"missing": [p for p in ["user_id", "ticker"] if not locals()[p]]}
        )
    
    return f"{user_id.strip()}_{ticker.strip()}"


def format_api_response(data: Any, success: bool = True, message: str = None) -> Dict[str, Any]:
    """
    格式化 API 響應
    
    Args:
        data: 響應數據
        success: 是否成功
        message: 響應訊息
        
    Returns:
        格式化的響應
    """
    response = {
        "success": success,
        "timestamp": asyncio.get_event_loop().time()
    }
    
    if data is not None:
        response["data"] = data
    
    if message:
        response["message"] = message
    
    return response


def extract_error_details(error: Exception) -> Dict[str, Any]:
    """
    從異常中提取詳細信息
    
    Args:
        error: 異常對象
        
    Returns:
        錯誤詳情字典
    """
    return {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "error_module": getattr(error, "__module__", "unknown")
    }
