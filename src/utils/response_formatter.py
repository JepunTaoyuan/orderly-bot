#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一響應格式化模塊
提供標準化的成功和失敗響應格式
"""

from typing import Any, Optional, Dict, Union
from fastapi import HTTPException
from src.utils.error_codes import GridTradingException
from src.utils.logging_config import get_logger

logger = get_logger("response_formatter")

class ResponseFormatter:
    """統一響應格式化器"""

    @staticmethod
    def success(
        data: Optional[Any] = None,
        message: Optional[str] = None,
        status_code: int = 200
    ) -> Dict[str, Any]:
        """
        格式化成功響應

        Args:
            data: 響應數據
            message: 成功訊息
            status_code: HTTP狀態碼

        Returns:
            格式化的響應字典
        """
        response = {
            "success": True,
            "status_code": status_code,
            "timestamp": int(__import__('time').time())
        }

        if data is not None:
            response["data"] = data

        if message:
            response["message"] = message

        return response

    @staticmethod
    def error(
        error_code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500,
        user_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        格式化錯誤響應

        Args:
            error_code: 錯誤碼
            message: 錯誤訊息（內部）
            details: 錯誤詳情
            status_code: HTTP狀態碼
            user_message: 用戶友好訊息

        Returns:
            格式化的錯誤響應字典
        """
        response = {
            "success": False,
            "error_code": error_code,
            "message": message,
            "status_code": status_code,
            "timestamp": int(__import__('time').time())
        }

        if details:
            response["details"] = details

        if user_message:
            response["user_message"] = user_message

        return response

    @staticmethod
    def from_exception(
        exception: Exception,
        include_original: bool = False
    ) -> Dict[str, Any]:
        """
        從異常生成響應

        Args:
            exception: 異常對象
            include_original: 是否包含原始異常訊息

        Returns:
            格式化的錯誤響應字典
        """
        if isinstance(exception, GridTradingException):
            # 自定義異常
            response = exception.to_dict()
            response["success"] = False
            response["timestamp"] = int(__import__('time').time())

            if not include_original and "original_error" in response:
                del response["original_error"]

            return response

        # 其他異常
        logger.error(f"未處理的異常: {type(exception).__name__}: {exception}")

        return ResponseFormatter.error(
            error_code="E1000",
            message="Internal server error",
            user_message="系統發生錯誤，請稍後重試",
            status_code=500,
            details={"exception_type": type(exception).__name__} if include_original else None
        )

# 便捷函數
def success_response(data: Optional[Any] = None, message: Optional[str] = None) -> Dict[str, Any]:
    """創建成功響應"""
    return ResponseFormatter.success(data=data, message=message)

def error_response(
    error_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    status_code: int = 500
) -> Dict[str, Any]:
    """創建錯誤響應"""
    return ResponseFormatter.error(
        error_code=error_code,
        message=message,
        details=details,
        status_code=status_code
    )

def handle_exception(exception: Exception) -> Dict[str, Any]:
    """處理異常並返回錯誤響應"""
    return ResponseFormatter.from_exception(exception)

# 裝飾器
def api_response(data_field: str = "data"):
    """
    API響應裝飾器
    自動格式化函數返回值為標準響應格式

    Args:
        data_field: 數據字段名稱
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                result = await func(*args, **kwargs)

                # 如果已經是標準格式，直接返回
                if isinstance(result, dict) and "success" in result:
                    return result

                # 格式化為成功響應
                return success_response(data={data_field: result} if data_field else result)

            except GridTradingException as e:
                # 處理自定義異常
                logger.error(f"API異常: {e.error_code.value} - {e}")
                return handle_exception(e)

            except Exception as e:
                # 處理其他異常
                logger.error(f"未處理的API異常: {type(e).__name__}: {e}")
                return handle_exception(e)

        return wrapper
    return decorator

# 特定響應類型
def paginated_response(items: list, total: int, page: int, per_page: int, **kwargs) -> Dict[str, Any]:
    """分頁響應"""
    return success_response(data={
        "items": items,
        "pagination": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page
        },
        **kwargs
    })

def list_response(items: list, **kwargs) -> Dict[str, Any]:
    """列表響應"""
    return success_response(data={
        "items": items,
        "count": len(items),
        **kwargs
    })

def status_response(status: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """狀態響應"""
    return success_response(data={
        "status": status,
        **(details or {})
    })