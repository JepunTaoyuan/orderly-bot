"""
基於 SlowAPI 的速率限制器
為網格交易機器人 API 提供速率限制保護
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, HTTPException, status
from typing import Callable
import asyncio
from src.utils.logging_config import get_logger

logger = get_logger("slowapi_limiter")


# 創建 Limiter 實例，使用內存存儲（不需要 Redis）
limiter = Limiter(key_func=get_remote_address)


class SlowAPIRateLimiter:
    """SlowAPI 速率限制器包裝器"""

    def __init__(self):
        self.limiter = limiter
        self._setup_error_handler()

    def _setup_error_handler(self):
        """設置速率限制超出的錯誤處理"""
        # 自定義錯誤處理函數
        async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
            # 記錄速率限制事件
            client_ip = get_remote_address(request)

            logger.warning(f"速率限制觸發: {exc.detail}", data={
                "path": request.url.path,
                "method": request.method,
                "ip": client_ip,
                "user_agent": request.headers.get("User-Agent", ""),
                "limit_detail": exc.detail
            })

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "message": exc.detail,
                    "retry_after": 60  # 建議 60 秒後重試
                }
            )

        # 註冊錯誤處理器（這需要在 FastAPI 應用中註冊）
        self.custom_error_handler = custom_rate_limit_handler

    def get_limiter(self) -> Limiter:
        """獲取 SlowAPI Limiter 實例"""
        return self.limiter

    def get_status(self, user_id: str = None, ip: str = None):
        """
        獲取速率限制狀態（簡化版）

        Args:
            user_id: 用戶ID（SlowAPI 主要基於 IP，所以這裡簡化）
            ip: IP 地址

        Returns:
            dict: 狀態信息
        """
        # SlowAPI 的內部狀態較難直接獲取，這裡提供基本狀態信息
        return {
            "limiter_type": "SlowAPI",
            "storage": "memory",
            "key_func": "get_remote_address",
            "note": "詳細狀態需要通過 SlowAPI 內部機制獲取"
        }


# 全局實例
slowapi_rate_limiter = SlowAPIRateLimiter()


def get_slowapi_rate_limiter() -> SlowAPIRateLimiter:
    """獲取全局 SlowAPI 速率限制器實例"""
    return slowapi_rate_limiter


# 常用的速率限制裝飾器
def limit_global(limit: str):
    """全局速率限制"""
    return limiter.limit(limit)

def limit_per_user(limit: str):
    """每用戶速率限制（基於用戶ID header）"""
    def get_user_id(request: Request):
        return request.headers.get("X-User-ID", get_remote_address(request))
    return limiter.limit(limit, key_func=get_user_id)

def limit_per_session(limit: str):
    """每會話速率限制（基於 session ID）"""
    def get_session_id(request: Request):
        return request.headers.get("X-Session-ID", get_remote_address(request))
    return limiter.limit(limit, key_func=get_session_id)

def limit_auth_endpoint(limit: str):
    """認證端點速率限制"""
    def get_auth_key(request: Request):
        # 對於認證端點，使用 IP 和用戶代理的組合作為 key
        user_agent = request.headers.get("User-Agent", "")
        return f"{get_remote_address(request)}:{hash(user_agent)}"
    return limiter.limit(limit, key_func=get_auth_key)

def limit_trading_operation(limit: str):
    """交易操作速率限制"""
    def get_trading_key(request: Request):
        # 對於交易操作，使用更嚴格的 key 策略
        user_id = request.headers.get("X-User-ID", "anonymous")
        session_id = request.headers.get("X-Session-ID", "no-session")
        return f"trading:{user_id}:{session_id}:{get_remote_address(request)}"
    return limiter.limit(limit, key_func=get_trading_key)


# 速率限制配置（針對網格交易機器人的特性）
RATE_LIMITS = {
    'global': '1000/minute',           # 全局：每分鐘1000次
    'per_user': '600/minute',          # 每用戶：每分鐘600次
    'per_ip': '600/minute',            # 每IP：每分鐘600次（通過默認的 get_remote_address 實現）
    'auth': '120/minute',              # 認證端點：每分鐘120次
    'trading': '60/minute',            # 交易操作：每分鐘60次
    'status_check': '300/minute',      # 狀態檢查：每分鐘300次
    'grid_control': '30/minute',       # 網格控制：每分鐘30次
}


# 便捷的裝飾器工廠函數
def create_global_rate_limit():
    """創建全局速率限制裝飾器"""
    return limit_global(RATE_LIMITS['global'])

def create_user_rate_limit():
    """創建用戶速率限制裝飾器"""
    return limit_per_user(RATE_LIMITS['per_user'])

def create_auth_rate_limit():
    """創建認證速率限制裝飾器"""
    return limit_auth_endpoint(RATE_LIMITS['auth'])

def create_trading_rate_limit():
    """創建交易操作速率限制裝飾器"""
    return limit_trading_operation(RATE_LIMITS['trading'])

def create_status_check_rate_limit():
    """創建狀態檢查速率限制裝飾器"""
    return limit_global(RATE_LIMITS['status_check'])

def create_grid_control_rate_limit():
    """創建網格控制速率限制裝飾器"""
    return limit_trading_operation(RATE_LIMITS['grid_control'])