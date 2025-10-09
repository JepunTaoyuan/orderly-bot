#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
認證相關的裝飾器和輔助函數
"""

import functools
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from src.services.database_service import MongoManager
from src.auth.wallet_signature import WalletSignatureVerifier
from src.utils.logging_config import get_logger
from src.utils.error_codes import GridTradingException, ErrorCode

logger = get_logger("auth_decorators")

# 全局實例
wallet_verifier = WalletSignatureVerifier()
mongo_manager = None

def init_auth_dependencies(mongo_mgr: MongoManager, wallet_vrf: WalletSignatureVerifier):
    """初始化認證依賴"""
    global mongo_manager, wallet_verifier
    mongo_manager = mongo_mgr
    wallet_verifier = wallet_vrf

async def verify_wallet_signature_db(user_id: str, user_sig: str, timestamp: int, nonce: str) -> Dict[str, Any]:
    """
    統一的錢包簽名驗證邏輯

    Args:
        user_id: 用戶ID
        user_sig: 用戶簽名
        timestamp: 時間戳
        nonce: 隨機數

    Returns:
        驗證結果字典
    """
    global mongo_manager, wallet_verifier

    if not mongo_manager or not wallet_verifier:
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"reason": "認證服務未初始化"}
        )

    # 檢查用戶是否存在
    user_data = await mongo_manager.get_user(user_id)
    if not user_data:
        raise GridTradingException(
            error_code=ErrorCode.USER_NOT_FOUND,
            details={"user_id": user_id}
        )

    # 獲取錢包地址
    wallet_address = user_data.get('wallet_address')
    if not wallet_address:
        raise GridTradingException(
            error_code=ErrorCode.INVALID_SIGNATURE,
            details={"user_id": user_id, "reason": "wallet_address not found"}
        )

    # 檢測錢包類型並驗證簽名
    wallet_type = wallet_verifier.detect_wallet_type(wallet_address)

    if wallet_type == 'evm':
        is_valid = await wallet_verifier.verify_evm_signature(
            user_sig, wallet_address, timestamp, nonce
        )
    elif wallet_type == 'solana':
        is_valid = await wallet_verifier.verify_solana_signature(
            user_sig, wallet_address, timestamp, nonce
        )
    else:
        raise GridTradingException(
            error_code=ErrorCode.UNKNOWN_WALLET_TYPE,
            details={"user_id": user_id, "wallet_type": wallet_type}
        )

    if not is_valid:
        raise GridTradingException(
            error_code=ErrorCode.INVALID_SIGNATURE,
            details={"user_id": user_id, "wallet_type": wallet_type}
        )

    return {
        "valid": True,
        "user_id": user_id,
        "wallet_address": wallet_address,
        "wallet_type": wallet_type
    }

def wallet_auth_required():
    """
    錢包簽名驗證裝飾器
    自動從請求中提取簽名信息並驗證
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 查找 request 參數
            request = None
            config = None

            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                elif hasattr(arg, 'user_id') and hasattr(arg, 'user_sig'):
                    config = arg

            # 如果沒找到，嘗試從 kwargs 獲取
            if not request:
                request = kwargs.get('request')
            if not config:
                for key, value in kwargs.items():
                    if hasattr(value, 'user_id') and hasattr(value, 'user_sig'):
                        config = value
                        break

            if not request or not config:
                raise GridTradingException(
                    error_code=ErrorCode.INVALID_REQUEST,
                    details={"reason": "缺少必要的認證參數"}
                )

            # 執行簽名驗證
            try:
                verification_result = await verify_wallet_signature_db(
                    config.user_id,
                    config.user_sig,
                    config.timestamp,
                    config.nonce
                )
                # 將驗證結果添加到 kwargs
                kwargs['_auth_result'] = verification_result
            except GridTradingException:
                raise
            except Exception as e:
                logger.error(f"簽名驗證失敗: {e}")
                raise GridTradingException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    details={"reason": "簽名驗證異常"},
                    original_error=e
                )

            # 執行原函數
            return await func(*args, **kwargs)
        return wrapper
    return decorator

class WalletAuthContext:
    """錢包認證上下文管理器"""

    def __init__(self, user_id: str, user_sig: str, timestamp: int, nonce: str):
        self.user_id = user_id
        self.user_sig = user_sig
        self.timestamp = timestamp
        self.nonce = nonce
        self._verification_result = None

    async def __aenter__(self) -> Dict[str, Any]:
        """進入上下文時執行驗證"""
        self._verification_result = await verify_wallet_signature_db(
            self.user_id,
            self.user_sig,
            self.timestamp,
            self.nonce
        )
        return self._verification_result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文"""
        pass

# 便捷函數
async def get_authenticated_user(request: Request, config) -> Dict[str, Any]:
    """
    獲取已認證的用戶信息

    Args:
        request: FastAPI 請求對象
        config: 包含認證信息的配置對象

    Returns:
        用戶信息字典
    """
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ) as auth_result:
        return auth_result