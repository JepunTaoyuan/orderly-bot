#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI 伺服器 (MVP)
提供三個 API：
- POST /api/grid/start  啟動網格交易
- POST /api/grid/stop   停止網格交易
- GET  /api/grid/status 取得狀態

串接現有的 GridTradingBot。
"""

import asyncio
import time
from typing import Any, Optional

from dotenv import load_dotenv
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from pydantic import model_validator
from contextlib import asynccontextmanager

from src.core.grid_signal import Direction
from src.services.session_service import SessionManager
from src.utils.logging_config import configure_logging, get_logger, metrics, set_session_context
from src.utils.error_codes import GridTradingException, ErrorCode
from src.utils.market_validator import ValidationError
from src.utils.api_helpers import SessionContextManager, validate_session_id, create_session_id
from src.services.database_connection import DatabaseManager
from fastapi.middleware.cors import CORSMiddleware
from src.auth.wallet_signature import WalletSignatureVerifier
from src.auth.auth_decorators import init_auth_dependencies, WalletAuthContext
from src.utils.resilient_handler import api_retry
from src.utils.slowapi_limiter import get_slowapi_rate_limiter, limiter, RATE_LIMITS
from slowapi.errors import RateLimitExceeded
from src.utils.slowapi_dependencies import auto_rate_limit
from src.core.grid_signal import GridType
from src.utils.websocket_manager import start_websocket_manager, stop_websocket_manager
from src.utils.system_monitor import start_system_monitor, stop_system_monitor, get_system_monitor
from src.utils.error_recovery import start_error_recovery, stop_error_recovery, get_error_recovery_manager, ErrorSeverity
from src.utils.mongodb_health import start_mongodb_health_monitoring, stop_mongodb_health_monitoring


load_dotenv()

# 配置日誌
configure_logging(level="INFO", format_json=True)
logger = get_logger("main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用啟動時的初始化"""
    try:
        # 初始化統一數據庫連接
        await db_manager.initialize(os.getenv("MONGODB_URI"))
        logger.info("統一數據庫連接已初始化")

        # 啟動系統監控器
        await start_system_monitor()
        logger.info("系統監控器已啟動")

        # 啟動錯誤恢復機制
        await start_error_recovery()
        logger.info("錯誤恢復機制已啟動")

        # 啟動 WebSocket 管理器
        await start_websocket_manager()
        logger.info("WebSocket 管理器已啟動")

        # 初始化錢包驗證器的數據庫連接
        database = await db_manager.get_database()
        wallet_verifier.initialize_with_database(database)
        await wallet_verifier.ensure_indexes()

        # 初始化認證依賴 - 使用統一的 mongo manager
        mongo_manager = await db_manager.get_mongo_manager()
        init_auth_dependencies(mongo_manager, wallet_verifier)
        logger.info("錢包驗證器初始化完成")

        # 啟動 MongoDB 健康監控
        await start_mongodb_health_monitoring(db_manager)
        logger.info("MongoDB 健康監控已啟動")

        # 初始化速率限制器（SlowAPI）
        slowapi_limiter = get_slowapi_rate_limiter()
        logger.info("SlowAPI 速率限制器初始化完成")

        # 記錄速率限制配置
        logger.info("速率限制配置", data={
            "global_limit": RATE_LIMITS['global'],
            "per_user_limit": RATE_LIMITS['per_user'],
            "auth_limit": RATE_LIMITS['auth'],
            "trading_limit": RATE_LIMITS['trading'],
            "grid_control_limit": RATE_LIMITS['grid_control']
        })

        logger.info("應用初始化完成")

    except Exception as e:
        logger.error(f"安全組件初始化失敗: {e}")
        # 初始化失敗不應該阻止應用啟動

    # 應用運行期間
    yield

    # 應用關閉時的清理
    logger.info("應用正在關閉，執行清理操作...")

    # 停止系統監控器
    try:
        await stop_system_monitor()
        logger.info("系統監控器已停止")
    except Exception as e:
        logger.error(f"停止系統監控器失敗: {e}")

    # 停止錯誤恢復機制
    try:
        await stop_error_recovery()
        logger.info("錯誤恢復機制已停止")
    except Exception as e:
        logger.error(f"停止錯誤恢復機制失敗: {e}")

    # 停止 MongoDB 健康監控
    try:
        await stop_mongodb_health_monitoring()
        logger.info("MongoDB 健康監控已停止")
    except Exception as e:
        logger.error(f"停止 MongoDB 健康監控失敗: {e}")

    # 停止 WebSocket 管理器
    try:
        await stop_websocket_manager()
        logger.info("WebSocket 管理器已停止")
    except Exception as e:
        logger.error(f"停止 WebSocket 管理器失敗: {e}")

    # 關閉數據庫連接
    try:
        await db_manager.close()
        logger.info("數據庫連接已關閉")
    except Exception as e:
        logger.error(f"關閉數據庫連接失敗: {e}")

app = FastAPI(title="Grid Trading Server", version="1.0.0", lifespan=lifespan)

# 錢包簽名驗證器
wallet_verifier = WalletSignatureVerifier()

#For test
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://myapp.com", "http://localhost:5174", "http://localhost:5173", "http://localhost:5175", "https://orderly-front-delta.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全域會話管理器
session_manager = SessionManager()

# 全域統一數據庫管理器
db_manager = DatabaseManager()

# 全域異常處理器
@app.exception_handler(GridTradingException)
async def grid_trading_exception_handler(request: Request, exc: GridTradingException):
    """處理網格交易自定義異常"""
    logger.error("網格交易異常", event_type="grid_trading_error", data={
        "error_code": exc.error_code.value,
        "message": exc.error_detail.message,
        "details": exc.details,
        "path": request.url.path
    })
    
    return JSONResponse(
        status_code=exc.get_http_status(),
        content=exc.to_dict()
    )

@app.exception_handler(RateLimitExceeded)
async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
    """處理速率限制超出錯誤"""
    slowapi_limiter = get_slowapi_rate_limiter()

    # 使用自定義錯誤處理器
    if hasattr(slowapi_limiter, 'custom_error_handler'):
        return await slowapi_limiter.custom_error_handler(request, exc)

    # 默認處理
    logger.warning(f"速率限制觸發: {exc.detail}", data={
        "path": request.url.path,
        "method": request.method,
        "ip": request.client.host if request.client else "unknown"
    })

    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "message": str(exc.detail),
            "retry_after": 60
        }
    )

@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    """處理驗證錯誤"""
    logger.error("驗證錯誤", event_type="validation_error", data={
        "message": str(exc),
        "path": request.url.path
    })
    
    grid_exc = GridTradingException(
        error_code=ErrorCode.INVALID_GRID_CONFIG,
        details={"validation_error": str(exc)}
    )
    
    return JSONResponse(
        status_code=grid_exc.get_http_status(),
        content=grid_exc.to_dict()
    )

class RegisterConfig(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user123",
            "user_api_key": "user123",
            "user_api_secret": "user123",
        }
    })
    
    user_id: str
    user_api_key: str
    user_api_secret: str

@app.post("/api/user/enable")
@limiter.limit(RATE_LIMITS['auth'])
@api_retry
async def enable_bot_trading(request: Request, config: RegisterConfig):
    """啟用機器人交易 儲存用戶資料進database"""
    try:
        # 檢查用戶是否已存在
        if not await mongo_manager.get_user(config.user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_NOT_FOUND,
                details={"user_id": config.user_id}
            )  
        
        config.user_api_key = "ed25519:" + config.user_api_key
        config.user_api_secret = "ed25519:" + config.user_api_secret

        # 更新用戶API密鑰對
        result = await mongo_manager.update_user_api_key_pair(
            config.user_id,
            config.user_api_key,
            config.user_api_secret,
        )

        if not result.modified_count:
            raise GridTradingException(
                error_code=ErrorCode.USER_API_KEY_PAIR_UPDATE_FAILED,
                details={"user_id": config.user_id}
            )

        return {"success": True, "data": {"user_id": config.user_id}}
        
    except GridTradingException:
        raise
    except Exception as e:
        logger.error("更新用戶API密鑰對失敗", event_type="user_api_key_pair_update_error", data={
            "user_id": config.user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.USER_API_KEY_PAIR_UPDATE_FAILED,
            details={"user_id": config.user_id},
            original_error=e
        )

@app.get("/api/user/check_api_key/{user_id}")
@limiter.limit(RATE_LIMITS['auth'])
@api_retry
async def check_user_api_key(request: Request, user_id: str):
    """檢查用戶API密鑰是否存在"""
    try:
        # 檢查用戶是否已存在
        if not await mongo_manager.get_user(user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_NOT_FOUND,
                details={"user_id": user_id}
            )  

        # 檢查用戶API密鑰是否存在
        api_key_exist = await mongo_manager.check_user_api_key_exist(user_id)
        return {"success": True, "data": api_key_exist}
        
    except GridTradingException:
        raise
    except Exception as e:
        logger.error("檢查用戶API密鑰是否存在失敗", event_type="user_api_key_pair_check_error", data={
            "user_id": user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.USER_API_KEY_PAIR_CHECK_FAILED,
            details={"user_id": user_id},
            original_error=e
        )

class StartConfig(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "ticker": "PERP_ETH_USDC",
            "direction": "BOTH",
            "current_price": 42500,
            "upper_bound": 45000,
            "lower_bound": 40000,
            "grid_type": "ARITHMETIC",
            "grid_ratio": 0.5,
            "grid_levels": 6,
            "total_margin": 100,
            "stop_bot_price": 38000,
            "stop_top_price": 47000,
            "user_id": "user123",
            "user_sig": "user123sig",
            "timestamp": 1234567890,
            "nonce": "random_nonce"
        }
    })

    ticker: str = Field(
        ..., 
        pattern=r"^PERP_[A-Z]+_USDC$"
    )
    direction: str = Field(..., pattern="^(LONG|SHORT|BOTH)$")
    current_price: float = Field(..., gt=0)
    upper_bound: float = Field(..., gt=0)
    lower_bound: float = Field(..., gt=0)
    grid_type: str = Field("ARITHMETIC", pattern="^(ARITHMETIC|GEOMETRIC)$")
    grid_ratio: Optional[float] = Field(None, gt=0, lt=1)
    grid_levels: int = Field(..., ge=2)
    total_margin: float = Field(..., gt=0)
    stop_bot_price: Optional[float] = Field(None, gt=0)
    stop_top_price: Optional[float] = Field(None, gt=0)
    user_id: str = Field(..., min_length=1)
    user_sig: str = Field(..., min_length=1)
    timestamp: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self):
        # 價格邏輯驗證
        if self.lower_bound >= self.upper_bound:
            raise ValueError("lower_bound must be less than upper_bound")
        if not (self.lower_bound <= self.current_price <= self.upper_bound):
            raise ValueError("當前價格必須在上下界範圍內")
        
        # 停損價格驗證
        if self.stop_bot_price and self.stop_bot_price >= self.lower_bound:
            raise ValueError("stop_bot_price must be less than lower_bound")
        if self.stop_top_price and self.stop_top_price <= self.upper_bound:
            raise ValueError("stop_top_price must be greater than upper_bound")
            
        return self

    @model_validator(mode="after")
    def validate_grid_type(self):
        if self.grid_type == "GEOMETRIC" and self.grid_ratio is None:
            raise ValueError("等比網格必須提供 grid_ratio")
        return self

    def to_internal(self) -> dict:
        # 轉 Direction 枚舉
        dir_map = {
            "LONG": Direction.LONG,
            "SHORT": Direction.SHORT,
            "BOTH": Direction.BOTH,
        }

        type_map = {
            "ARITHMETIC": GridType.ARITHMETIC,
            "GEOMETRIC": GridType.GEOMETRIC,
        }

        direction_enum = dir_map[self.direction]
        grid_type_enum = type_map[self.grid_type]
        return {
            "ticker": self.ticker,
            "direction": direction_enum,
            "current_price": self.current_price,
            "upper_bound": self.upper_bound,
            "lower_bound": self.lower_bound,
            "grid_type": grid_type_enum,
            "grid_ratio": self.grid_ratio,
            "grid_levels": self.grid_levels,
            "total_margin": self.total_margin,
            "stop_bot_price": self.stop_bot_price,
            "stop_top_price": self.stop_top_price,
            "user_id": self.user_id,
            "user_sig": self.user_sig,
        }


@app.post("/api/grid/start")
@limiter.limit(RATE_LIMITS['grid_control'])
@api_retry
async def start_grid(request: Request, config: StartConfig):
    # 使用統一的簽名驗證
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ) as auth_result:
        logger.info(
            f"用戶 {config.user_id} 簽名驗證成功",
            event_type="wallet_signature_verified",
            data={"wallet_type": auth_result["wallet_type"]}
        )

    session_id = create_session_id(config.user_id, config.ticker)
    print(session_id)
    
    with SessionContextManager(session_id):
        try:
            logger.info("啟動網格交易請求", event_type="grid_start", data={
                "session_id": session_id,
                "ticker": config.ticker,
                "direction": config.direction
            })
            metrics.increment_counter("api.grid.start.requests", tags={"ticker": config.ticker})

            success = await session_manager.create_session(session_id, config.to_internal())

            if success:
                metrics.increment_counter("api.grid.start.success", tags={"ticker": config.ticker})
                logger.info("網格交易啟動成功", event_type="grid_started", data={"session_id": session_id})
                return {"success": True, "data": {"status": "started", "session_id": session_id}}
            else:
                # 會話已存在的情況
                raise GridTradingException(
                    error_code=ErrorCode.SESSION_ALREADY_EXISTS,
                    details={"session_id": session_id}
                )
                
        except GridTradingException:
            # 重新拋出自定義異常，讓全域處理器處理
            raise
        except ValidationError as e:
            # 轉換驗證錯誤為自定義異常
            raise GridTradingException(
                error_code=ErrorCode.INVALID_GRID_CONFIG,
                details={"validation_error": str(e)},
                original_error=e
            )
        except Exception as e:
            metrics.increment_counter("api.grid.start.errors", tags={"ticker": config.ticker})
            logger.error("啟動網格交易失敗", event_type="grid_start_error", data={
                "session_id": session_id,
                "error": str(e)
            })
            raise GridTradingException(
                error_code=ErrorCode.SESSION_CREATE_FAILED,
                details={"session_id": session_id},
                original_error=e
            )


class StopConfig(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_id": "user123_PERP_ETH_USDC",
            "user_sig": "user123",
            "timestamp": 1234567890,
            "nonce": "random_nonce"
        }
    })

    session_id: str = Field(..., min_length=1)
    user_sig: str = Field(..., min_length=1)
    timestamp: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=1)

@app.post("/api/grid/stop")
@limiter.limit(RATE_LIMITS['grid_control'])
@api_retry
async def stop_grid(request: Request, config: StopConfig):
    session_id = validate_session_id(config.session_id)

    # 解析 user_id
    try:
        # 支持 ticker 中包含下劃線，僅按第一個下劃線拆分
        user_id, _ = session_id.split('_', 1)
    except ValueError:
        raise GridTradingException(
            error_code=ErrorCode.INVALID_SESSION_ID,
            details={"session_id": session_id}
        )

    # 使用統一的簽名驗證
    async with WalletAuthContext(
        user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ) as auth_result:
        logger.info(
            f"用戶 {user_id} 簽名驗證成功",
            event_type="wallet_verified",
            data={"wallet_type": auth_result["wallet_type"]}
        )

    with SessionContextManager(session_id):
        try:
            logger.info("停止網格交易請求", event_type="grid_stop", data={"session_id": session_id})
            metrics.increment_counter("api.grid.stop.requests")
            
            success = await session_manager.stop_session(session_id)
            
            if success:
                metrics.increment_counter("api.grid.stop.success")
                logger.info("網格交易停止成功", event_type="grid_stopped", data={"session_id": session_id})
                return {"success": True, "data": {"status": "stopped", "session_id": session_id}}
            else:
                # 會話不存在的情況
                raise GridTradingException(
                    error_code=ErrorCode.SESSION_NOT_FOUND,
                    details={"session_id": session_id}
                )
                
        except GridTradingException:
            # 重新拋出自定義異常，讓全域處理器處理
            raise
        except Exception as e:
            metrics.increment_counter("api.grid.stop.errors")
            logger.error("停止網格交易失敗", event_type="grid_stop_error", data={
                "session_id": session_id,
                "error": str(e)
            })
            raise GridTradingException(
                error_code=ErrorCode.SESSION_STOP_FAILED,
                details={"session_id": session_id},
                original_error=e
            )


@app.get("/api/grid/status/{session_id}")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_status(request: Request, session_id: str):
    try:
        status = await session_manager.get_session_status(session_id)
        if status is not None:
            return {"success": True, "data": status}
        else:
            raise GridTradingException(
                error_code=ErrorCode.SESSION_NOT_FOUND,
                details={"session_id": session_id}
            )
    except GridTradingException:
        raise
    except Exception as e:
        logger.error("獲取會話狀態失敗", event_type="get_status_error", data={
            "session_id": session_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"session_id": session_id},
            original_error=e
        )

@app.get("/api/grid/sessions")
@limiter.limit(RATE_LIMITS['status_check'])
async def list_sessions(request: Request):
    try:
        sessions = await session_manager.list_sessions()
        return {"success": True, "data": {"sessions": sessions}}
    except Exception as e:
        logger.error("列出會話失敗", event_type="list_sessions_error", data={"error": str(e)})
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            original_error=e
        )

@app.get("/api/user/strategies/{user_id}")
@limiter.limit(RATE_LIMITS['status_check'])
@api_retry
async def get_user_grid_strategies(request: Request, user_id: str):
    """
    獲取指定用戶的所有當前正在運行的grid策略

    Args:
        user_id: 用戶ID (路由參數)

    Returns:
        該用戶的所有活躍grid策略詳細信息
    """
    try:
        # 獲取用戶的所有會話
        user_sessions = await session_manager.get_user_sessions(user_id)

        return {
            "success": True,
            "data": {
                "user_id": user_id,
                "strategies": list[str, Any](user_sessions.values()),
                "total_strategies": len(user_sessions)
            }
        }

    except GridTradingException:
        raise
    except Exception as e:
        logger.error("獲取用戶grid策略失敗", event_type="get_user_grid_strategies_error", data={
            "user_id": user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )

@app.get("/api/grid/profit/{session_id}")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_profit_report(request: Request, session_id: str):
    """
    獲取網格交易利潤報告
    
    Args:
        session_id: 會話ID
        
    Returns:
        利潤統計報告
    """
    try:
        # 驗證會話ID
        session_id = validate_session_id(session_id)
        
        # 從會話管理器獲取機器人實例
        async with session_manager._sessions_lock:
            if session_id not in session_manager.sessions:
                raise GridTradingException(
                    error_code=ErrorCode.SESSION_NOT_FOUND,
                    details={"session_id": session_id}
                )
            
            bot = session_manager.sessions[session_id]
        
        # 獲取利潤報告
        profit_report = await bot.get_profit_report()
        
        return {"success": True, "data": profit_report}
        
    except GridTradingException:
        raise
    except Exception as e:
        logger.error("獲取利潤報告失敗", event_type="profit_report_error", data={
            "session_id": session_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"session_id": session_id},
            original_error=e
        )

@app.get("/health")
async def health_check():
    """健康檢查端點"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "version": "1.0.0"
    }

@app.get("/health/ready")
async def readiness_check():
    """就緒檢查端點"""
    try:
        # 檢查會話管理器狀態
        sessions = await session_manager.list_sessions()
        
        return {
            "status": "ready",
            "timestamp": time.time(),
            "active_sessions": len(sessions)
        }
    except Exception as e:
        logger.error("就緒檢查失敗", event_type="health_check", data={"error": str(e)})
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"check_type": "readiness"},
            original_error=e
        )
 
# 常數定義
DEFAULT_METRICS_LIMIT_COUNTERS = 10
DEFAULT_METRICS_LIMIT_GAUGES = 5
DEFAULT_METRICS_LIMIT_HISTOGRAMS = 3

@app.get("/metrics")
async def get_metrics(
    limit_counters: int = DEFAULT_METRICS_LIMIT_COUNTERS,
    limit_gauges: int = DEFAULT_METRICS_LIMIT_GAUGES,
    limit_histograms: int = DEFAULT_METRICS_LIMIT_HISTOGRAMS
):
    """獲取系統指標（可限制每類返回數量）"""
    try:
        data = metrics.get_metrics()

        def _limit_dict(d: dict, n: int) -> dict:
            try:
                if n is None or n <= 0 or len(d) <= n:
                    return d
                # 保留最近加入的 n 個鍵（Python 3.7+ dict 保序）
                items = list(d.items())[-n:]
                return {k: v for k, v in items}
            except Exception:
                return d

        data["counters"] = _limit_dict(data.get("counters", {}), limit_counters)
        data["gauges"] = _limit_dict(data.get("gauges", {}), limit_gauges)
        data["histograms"] = _limit_dict(data.get("histograms", {}), limit_histograms)
        return data
    except Exception as e:
        logger.error("獲取指標失敗", event_type="metrics", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"failed_to_get_metrics: {e}")

@app.get("/system/health")
async def system_health_check():
    """系統健康檢查端點"""
    try:
        system_monitor = get_system_monitor()
        health_status = await system_monitor.check_health()

        # 根據健康狀態返回相應的 HTTP 狀態碼
        status_code = 200
        if health_status['status'] == 'unhealthy':
            status_code = 503
        elif health_status['status'] == 'error':
            status_code = 500

        return JSONResponse(
            status_code=status_code,
            content=health_status
        )
    except Exception as e:
        logger.error("系統健康檢查失敗", event_type="health_check", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Health check failed: {e}")

@app.get("/system/metrics")
async def get_system_metrics():
    """獲取詳細的系統指標"""
    try:
        system_monitor = get_system_monitor()
        current_metrics = await system_monitor.collect_metrics()

        # 轉換為可序列化的字典
        return {
            "timestamp": current_metrics.timestamp,
            "system": {
                "cpu_percent": current_metrics.cpu_percent,
                "memory_percent": current_metrics.memory_percent,
                "memory_used_mb": current_metrics.memory_used_mb,
                "memory_available_mb": current_metrics.memory_available_mb,
                "disk_usage_percent": current_metrics.disk_usage_percent,
                "event_loop_lag_ms": current_metrics.event_loop_lag
            },
            "application": {
                "active_sessions": current_metrics.active_sessions,
                "websocket_connections": current_metrics.websocket_connections,
                "queue_sizes": current_metrics.queue_sizes
            },
            "gc": {
                "collections": list(current_metrics.gc_counts)
            }
        }
    except Exception as e:
        logger.error("獲取系統指標失敗", event_type="system_metrics", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get system metrics: {e}")

@app.post("/system/gc")
async def force_garbage_collection():
    """強制垃圾回收"""
    try:
        system_monitor = get_system_monitor()
        result = await system_monitor.force_gc()

        return {
            "success": True,
            "data": result,
            "message": "垃圾回收已完成"
        }
    except Exception as e:
        logger.error("強制垃圾回收失敗", event_type="gc_failed", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Garbage collection failed: {e}")

@app.get("/system/stats")
async def get_system_stats():
    """獲取系統統計信息"""
    try:
        # 收集各組件統計
        system_monitor = get_system_monitor()
        ws_manager = get_websocket_manager()
        error_recovery = get_error_recovery_manager()

        # 系統指標歷史
        metrics_history = await system_monitor.get_metrics_history(limit=10)

        # WebSocket 統計
        ws_stats = await ws_manager.get_stats()

        # Session 統計
        session_stats = {
            'total_attempts': session_manager.creation_metrics['total_attempts'],
            'successful': session_manager.creation_metrics['successful'],
            'failed': session_manager.creation_metrics['failed'],
            'rate_limited': session_manager.creation_metrics['rate_limited'],
            'active_sessions': len(session_manager.sessions)
        }

        # 錯誤恢復統計
        error_recovery_stats = error_recovery.get_error_statistics()

        return {
            "timestamp": time.time(),
            "system_monitor": {
                "is_monitoring": system_monitor.is_monitoring,
                "metrics_count": len(metrics_history)
            },
            "websocket": ws_stats,
            "sessions": session_stats,
            "error_recovery": error_recovery_stats,
            "metrics_history": [
                {
                    "timestamp": m.timestamp,
                    "cpu_percent": m.cpu_percent,
                    "memory_percent": m.memory_percent,
                    "active_sessions": m.active_sessions,
                    "websocket_connections": m.websocket_connections
                }
                for m in metrics_history
            ]
        }
    except Exception as e:
        logger.error("獲取系統統計失敗", event_type="system_stats", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get system stats: {e}")

@app.get("/system/recovery/stats")
async def get_error_recovery_stats():
    """獲取錯誤恢復統計信息"""
    try:
        error_recovery = get_error_recovery_manager()
        stats = error_recovery.get_error_statistics()
        return {
            "success": True,
            "data": stats
        }
    except Exception as e:
        logger.error("獲取錯誤恢復統計失敗", event_type="error_recovery_stats", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get error recovery stats: {e}")

@app.get("/api/auth/challenge")
@limiter.limit(RATE_LIMITS['auth'])
async def get_challenge(request: Request):
    """生成簽名挑戰"""
    try:
        challenge = wallet_verifier.generate_challenge()
        return {
            "success": True,
            "data": challenge
        }
    except Exception as e:
        logger.error("生成挑戰失敗", event_type="challenge_error", data={"error": str(e)})
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"reason": "challenge generation failed"},
            original_error=e
        )

class StopConfig(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_id": "user123_PERP_ETH_USDC",
        }
    })

    session_id: str = Field(..., min_length=1)

@app.post("/api/grid/teststop")
@limiter.limit(RATE_LIMITS['grid_control'])
@api_retry
async def stop_grid(request: Request, config: StopConfig):
    session_id = validate_session_id(config.session_id)

    # 解析 user_id
    try:
        # 支持 ticker 中包含下劃線，僅按第一個下劃線拆分
        user_id, _ = session_id.split('_', 1)
    except ValueError:
        raise GridTradingException(
            error_code=ErrorCode.INVALID_SESSION_ID,
            details={"session_id": session_id}
        )

    with SessionContextManager(session_id):
        try:
            logger.info("停止網格交易請求", event_type="grid_stop", data={"session_id": session_id})
            metrics.increment_counter("api.grid.stop.requests")
            
            success = await session_manager.stop_session(session_id)
            
            if success:
                metrics.increment_counter("api.grid.stop.success")
                logger.info("網格交易停止成功", event_type="grid_stopped", data={"session_id": session_id})
                return {"success": True, "data": {"status": "stopped", "session_id": session_id}}
            else:
                # 會話不存在的情況
                raise GridTradingException(
                    error_code=ErrorCode.SESSION_NOT_FOUND,
                    details={"session_id": session_id}
                )
                
        except GridTradingException:
            # 重新拋出自定義異常，讓全域處理器處理
            raise
        except Exception as e:
            metrics.increment_counter("api.grid.stop.errors")
            logger.error("停止網格交易失敗", event_type="grid_stop_error", data={
                "session_id": session_id,
                "error": str(e)
            })
            raise GridTradingException(
                error_code=ErrorCode.SESSION_STOP_FAILED,
                details={"session_id": session_id},
                original_error=e
            )

@app.post("/api/grid/cleanup/{session_id}")
@limiter.limit(RATE_LIMITS['grid_control'])
@api_retry
async def cleanup_session(request: Request, session_id: str):
    """強制清理會話的所有相關數據"""
    try:
        # 驗證會話ID
        session_id = validate_session_id(session_id)

        # 解析 user_id
        try:
            user_id, _ = session_id.split('_', 1)
        except ValueError:
            raise GridTradingException(
                error_code=ErrorCode.INVALID_SESSION_ID,
                details={"session_id": session_id}
            )

        # 強制清理會話
        cleaned = await session_manager.force_cleanup_session(session_id)

        if cleaned:
            logger.info("會話強制清理成功", event_type="session_cleanup", data={"session_id": session_id})
            return {
                "success": True,
                "data": {
                    "status": "cleaned",
                    "session_id": session_id,
                    "message": "會話已強制清理"
                }
            }
        else:
            return {
                "success": True,
                "data": {
                    "status": "no_cleanup_needed",
                    "session_id": session_id,
                    "message": "沒有需要清理的會話數據"
                }
            }

    except GridTradingException:
        raise
    except Exception as e:
        logger.error("強制清理會話失敗", event_type="session_cleanup_error", data={
            "session_id": session_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"session_id": session_id},
            original_error=e
        )

@app.get("/")
async def root():
    return {
        "message": "Dexless Bot API",
        "version": "1.0.0",
        "WHATUP": "BRO"
    }
