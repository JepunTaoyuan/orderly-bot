#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一錯誤碼系統
定義所有可能的錯誤類型和對應的錯誤碼
"""

from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass


class ErrorCode(Enum):
    """錯誤碼枚舉"""
    # 通用錯誤 (1000-1999)
    UNKNOWN_ERROR = "E1000"
    INVALID_REQUEST = "E1001"
    MISSING_PARAMETER = "E1002"
    INVALID_PARAMETER = "E1003"
    INTERNAL_SERVER_ERROR = "E1004"
    
    # 認證錯誤 (2000-2999)
    UNAUTHORIZED = "E2000"
    INVALID_CREDENTIALS = "E2001"
    TOKEN_EXPIRED = "E2002"
    INVALID_SIGNATURE = "E2003"
    
    # 會話管理錯誤 (3000-3999)
    SESSION_NOT_FOUND = "E3000"
    SESSION_ALREADY_EXISTS = "E3001"
    SESSION_CREATE_FAILED = "E3002"
    SESSION_STOP_FAILED = "E3003"
    UNKNOWN_WALLET_TYPE = "E3004"
    
    # 交易相關錯誤 (4000-4999)
    INVALID_SYMBOL = "E4000"
    INVALID_PRICE = "E4001"
    INVALID_QUANTITY = "E4002"
    INSUFFICIENT_BALANCE = "E4003"
    ORDER_CREATE_FAILED = "E4004"
    ORDER_CANCEL_FAILED = "E4005"
    MARKET_CLOSED = "E4006"
    
    # 網格配置錯誤 (5000-5999)
    INVALID_GRID_CONFIG = "E5000"
    INVALID_PRICE_BOUNDS = "E5001"
    INVALID_GRID_LEVELS = "E5002"
    INVALID_TOTAL_AMOUNT = "E5003"
    PRICE_OUT_OF_BOUNDS = "E5004"
    
    # 外部服務錯誤 (6000-6999)
    ORDERLY_API_ERROR = "E6000"
    ORDERLY_CONNECTION_ERROR = "E6001"
    ORDERLY_RATE_LIMIT = "E6002"
    ORDERLY_TIMEOUT = "E6003"
    
    # 用戶管理錯誤 (7000-7999)
    USER_ALREADY_EXISTS = "E7000"
    USER_NOT_FOUND = "E7001"
    USER_CREATION_FAILED = "E7002"
    USER_UPDATE_FAILED = "E7003"


@dataclass
class ErrorDetail:
    """錯誤詳情"""
    code: ErrorCode
    message: str
    description: str
    http_status: int = 500
    user_message: Optional[str] = None  # 用戶友好的錯誤訊息


# 錯誤碼對應的詳細信息
ERROR_DETAILS: Dict[ErrorCode, ErrorDetail] = {
    # 通用錯誤
    ErrorCode.UNKNOWN_ERROR: ErrorDetail(
        code=ErrorCode.UNKNOWN_ERROR,
        message="Unknown error occurred",
        description="An unexpected error occurred in the system",
        http_status=500,
        user_message="系統發生未知錯誤，請稍後重試"
    ),
    ErrorCode.INVALID_REQUEST: ErrorDetail(
        code=ErrorCode.INVALID_REQUEST,
        message="Invalid request format",
        description="The request format is invalid or malformed",
        http_status=400,
        user_message="請求格式不正確"
    ),
    ErrorCode.MISSING_PARAMETER: ErrorDetail(
        code=ErrorCode.MISSING_PARAMETER,
        message="Missing required parameter",
        description="One or more required parameters are missing",
        http_status=400,
        user_message="缺少必要參數"
    ),
    ErrorCode.INVALID_PARAMETER: ErrorDetail(
        code=ErrorCode.INVALID_PARAMETER,
        message="Invalid parameter value",
        description="One or more parameters have invalid values",
        http_status=400,
        user_message="參數值不正確"
    ),
    ErrorCode.INTERNAL_SERVER_ERROR: ErrorDetail(
        code=ErrorCode.INTERNAL_SERVER_ERROR,
        message="Internal server error",
        description="An internal server error occurred",
        http_status=500,
        user_message="伺服器內部錯誤，請稍後重試"
    ),
    
    # 會話管理錯誤
    ErrorCode.SESSION_NOT_FOUND: ErrorDetail(
        code=ErrorCode.SESSION_NOT_FOUND,
        message="Session not found",
        description="The requested session does not exist",
        http_status=404,
        user_message="找不到指定的交易會話"
    ),
    ErrorCode.SESSION_ALREADY_EXISTS: ErrorDetail(
        code=ErrorCode.SESSION_ALREADY_EXISTS,
        message="Session already exists",
        description="A session with the same ID already exists",
        http_status=409,
        user_message="交易會話已存在"
    ),
    ErrorCode.SESSION_CREATE_FAILED: ErrorDetail(
        code=ErrorCode.SESSION_CREATE_FAILED,
        message="Failed to create session",
        description="Failed to create a new trading session",
        http_status=500,
        user_message="創建交易會話失敗"
    ),
    ErrorCode.SESSION_STOP_FAILED: ErrorDetail(
        code=ErrorCode.SESSION_STOP_FAILED,
        message="Failed to stop session",
        description="Failed to stop the trading session",
        http_status=500,
        user_message="停止交易會話失敗"
    ),
    
    # 交易相關錯誤
    ErrorCode.INVALID_SYMBOL: ErrorDetail(
        code=ErrorCode.INVALID_SYMBOL,
        message="Invalid trading symbol",
        description="The specified trading symbol is not supported",
        http_status=400,
        user_message="不支援的交易對"
    ),
    ErrorCode.INVALID_PRICE: ErrorDetail(
        code=ErrorCode.INVALID_PRICE,
        message="Invalid price",
        description="The specified price is invalid or out of range",
        http_status=400,
        user_message="價格不正確"
    ),
    ErrorCode.INVALID_QUANTITY: ErrorDetail(
        code=ErrorCode.INVALID_QUANTITY,
        message="Invalid quantity",
        description="The specified quantity is invalid or out of range",
        http_status=400,
        user_message="數量不正確"
    ),
    ErrorCode.ORDER_CREATE_FAILED: ErrorDetail(
        code=ErrorCode.ORDER_CREATE_FAILED,
        message="Failed to create order",
        description="Failed to create the trading order",
        http_status=500,
        user_message="創建訂單失敗"
    ),
    
    # 網格配置錯誤
    ErrorCode.INVALID_GRID_CONFIG: ErrorDetail(
        code=ErrorCode.INVALID_GRID_CONFIG,
        message="Invalid grid configuration",
        description="The grid trading configuration is invalid",
        http_status=400,
        user_message="網格配置不正確"
    ),
    ErrorCode.INVALID_PRICE_BOUNDS: ErrorDetail(
        code=ErrorCode.INVALID_PRICE_BOUNDS,
        message="Invalid price bounds",
        description="The upper and lower price bounds are invalid",
        http_status=400,
        user_message="價格邊界設定不正確"
    ),
    ErrorCode.INVALID_GRID_LEVELS: ErrorDetail(
        code=ErrorCode.INVALID_GRID_LEVELS,
        message="Invalid grid levels",
        description="The number of grid levels is invalid",
        http_status=400,
        user_message="網格層數設定不正確"
    ),
    ErrorCode.INVALID_TOTAL_AMOUNT: ErrorDetail(
        code=ErrorCode.INVALID_TOTAL_AMOUNT,
        message="Invalid total amount",
        description="The total investment amount is invalid",
        http_status=400,
        user_message="總投入金額不正確"
    ),
    ErrorCode.PRICE_OUT_OF_BOUNDS: ErrorDetail(
        code=ErrorCode.PRICE_OUT_OF_BOUNDS,
        message="Price out of bounds",
        description="The current price is outside the specified bounds",
        http_status=400,
        user_message="當前價格超出設定範圍"
    ),
    
    # 外部服務錯誤
    ErrorCode.ORDERLY_API_ERROR: ErrorDetail(
        code=ErrorCode.ORDERLY_API_ERROR,
        message="Orderly API error",
        description="An error occurred while calling Orderly API",
        http_status=502,
        user_message="交易所API錯誤"
    ),
    ErrorCode.ORDERLY_CONNECTION_ERROR: ErrorDetail(
        code=ErrorCode.ORDERLY_CONNECTION_ERROR,
        message="Orderly connection error",
        description="Failed to connect to Orderly API",
        http_status=503,
        user_message="無法連接到交易所"
    ),
    ErrorCode.ORDERLY_RATE_LIMIT: ErrorDetail(
        code=ErrorCode.ORDERLY_RATE_LIMIT,
        message="Orderly rate limit exceeded",
        description="Orderly API rate limit has been exceeded",
        http_status=429,
        user_message="請求過於頻繁，請稍後重試"
    ),
    
    # 用戶管理錯誤
    ErrorCode.USER_ALREADY_EXISTS: ErrorDetail(
        code=ErrorCode.USER_ALREADY_EXISTS,
        message="User already exists",
        description="A user with the same ID already exists",
        http_status=409,
        user_message="用戶已存在"
    ),
    ErrorCode.USER_NOT_FOUND: ErrorDetail(
        code=ErrorCode.USER_NOT_FOUND,
        message="User not found",
        description="The requested user does not exist",
        http_status=404,
        user_message="找不到指定的用戶"
    ),
    ErrorCode.USER_CREATION_FAILED: ErrorDetail(
        code=ErrorCode.USER_CREATION_FAILED,
        message="Failed to create user",
        description="Failed to create a new user",
        http_status=500,
        user_message="創建用戶失敗"
    ),
    ErrorCode.USER_UPDATE_FAILED: ErrorDetail(
        code=ErrorCode.USER_UPDATE_FAILED,
        message="Failed to update user",
        description="Failed to update user information",
        http_status=500,
        user_message="更新用戶信息失敗"
    ),
}


class GridTradingException(Exception):
    """網格交易自定義異常"""
    
    def __init__(self, error_code: ErrorCode, details: Optional[Dict[str, Any]] = None, 
                 original_error: Optional[Exception] = None):
        self.error_code = error_code
        self.error_detail = ERROR_DETAILS.get(error_code, ERROR_DETAILS[ErrorCode.UNKNOWN_ERROR])
        self.details = details or {}
        self.original_error = original_error
        
        super().__init__(self.error_detail.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典格式"""
        result = {
            "error_code": self.error_code.value,
            "message": self.error_detail.message,
            "user_message": self.error_detail.user_message,
            "description": self.error_detail.description,
        }
        
        if self.details:
            result["details"] = self.details
            
        if self.original_error:
            result["original_error"] = str(self.original_error)
            
        return result
    
    def get_http_status(self) -> int:
        """獲取對應的HTTP狀態碼"""
        return self.error_detail.http_status


def get_error_detail(error_code: ErrorCode) -> ErrorDetail:
    """獲取錯誤詳情"""
    return ERROR_DETAILS.get(error_code, ERROR_DETAILS[ErrorCode.UNKNOWN_ERROR])