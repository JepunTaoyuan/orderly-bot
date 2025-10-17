"""
SlowAPI 依賴注入模組
提供基於 SlowAPI 的速率限制依賴
"""

from fastapi import Request, Depends, HTTPException
from slowapi.util import get_remote_address
from src.utils.slowapi_limiter import limiter, RATE_LIMITS, get_slowapi_rate_limiter
from src.utils.logging_config import get_logger

logger = get_logger("slowapi_dependencies")


def apply_rate_limit(limit: str, key_func=None):
    """
    應用速率限制的依賴函數

    Args:
        limit: 速率限制字符串，如 "100/minute"
        key_func: 自定義 key 函數，默認為 get_remote_address

    Returns:
        依賴函數
    """
    async def rate_limit_dependency(request: Request):
        slowapi_limiter = get_slowapi_rate_limiter()

        # 使用指定的 key 函數或默認的 IP 地址
        if key_func:
            key = key_func(request)
        else:
            key = get_remote_address(request)

        try:
            # 檢查速率限制
            await limiter.check(lambda: key, limit)
        except Exception as e:
            logger.error(f"速率限制檢查失敗: {e}")
            # 如果檢查失敗，繼續處理請求，避免服務中斷

        return {"rate_limit_applied": True, "limit": limit, "key": key}

    return rate_limit_dependency


# 常用的速率限制依賴
async def global_rate_limit(request: Request):
    """全局速率限制依賴"""
    return await apply_rate_limit(RATE_LIMITS['global'])(request)


async def user_rate_limit(request: Request):
    """用戶速率限制依賴"""
    def get_user_key(request: Request):
        user_id = request.headers.get("X-User-ID")
        return user_id if user_id else get_remote_address(request)

    return await apply_rate_limit(RATE_LIMITS['per_user'], get_user_key)(request)


async def session_rate_limit(request: Request):
    """會話速率限制依賴"""
    def get_session_key(request: Request):
        session_id = request.headers.get("X-Session-ID")
        return session_id if session_id else get_remote_address(request)

    return await apply_rate_limit(RATE_LIMITS['per_user'], get_session_key)(request)


async def auth_rate_limit(request: Request):
    """認證端點速率限制依賴"""
    def get_auth_key(request: Request):
        user_agent = request.headers.get("User-Agent", "")
        return f"{get_remote_address(request)}:{hash(user_agent)}"

    return await apply_rate_limit(RATE_LIMITS['auth'], get_auth_key)(request)


async def trading_rate_limit(request: Request):
    """交易操作速率限制依賴"""
    def get_trading_key(request: Request):
        user_id = request.headers.get("X-User-ID", "anonymous")
        session_id = request.headers.get("X-Session-ID", "no-session")
        return f"trading:{user_id}:{session_id}:{get_remote_address(request)}"

    return await apply_rate_limit(RATE_LIMITS['trading'], get_trading_key)(request)


async def status_check_rate_limit(request: Request):
    """狀態檢查速率限制依賴"""
    return await apply_rate_limit(RATE_LIMITS['status_check'])(request)


async def grid_control_rate_limit(request: Request):
    """網格控制速率限制依賴"""
    def get_grid_key(request: Request):
        user_id = request.headers.get("X-User-ID", "anonymous")
        session_id = request.headers.get("X-Session-ID", "no-session")
        return f"grid:{user_id}:{session_id}:{get_remote_address(request)}"

    return await apply_rate_limit(RATE_LIMITS['grid_control'], get_grid_key)(request)


# 端點類型檢測函數
def get_endpoint_type(request: Request) -> str:
    """
    根據請求路徑確定端點類型

    Args:
        request: FastAPI 請求

    Returns:
        str: 端點類型
    """
    path = request.url.path.lower()

    if '/api/user/enable' in path or '/api/auth/' in path:
        return 'auth'
    elif '/api/grid/start' in path or '/api/grid/stop' in path:
        return 'grid_control'
    elif '/api/grid/status' in path:
        return 'status_check'
    elif '/api/trading/' in path:
        return 'trading'
    else:
        return 'default'


# 自動速率限制依賴
async def auto_rate_limit(request: Request):
    """
    根據端點類型自動應用速率限制

    Args:
        request: FastAPI 請求

    Returns:
        dict: 速率限制信息
    """
    endpoint_type = get_endpoint_type(request)

    if endpoint_type == 'auth':
        return await auth_rate_limit(request)
    elif endpoint_type == 'grid_control':
        return await grid_control_rate_limit(request)
    elif endpoint_type == 'status_check':
        return await status_check_rate_limit(request)
    elif endpoint_type == 'trading':
        return await trading_rate_limit(request)
    else:
        return await global_rate_limit(request)


# 網格交易特定的速率限制依賴
async def grid_operation_rate_limit(request: Request):
    """網格操作專用速率限制"""
    # 對網格操作使用更嚴格的限制
    def get_grid_operation_key(request: Request):
        user_id = request.headers.get("X-User-ID", "anonymous")
        ip = get_remote_address(request)
        return f"grid_ops:{user_id}:{ip}"

    return await apply_rate_limit("20/minute", get_grid_operation_key)(request)