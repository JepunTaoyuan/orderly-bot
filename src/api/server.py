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
from typing import Optional

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
from src.services.database_service import MongoManager
from fastapi.middleware.cors import CORSMiddleware
from src.auth.wallet_signature import WalletSignatureVerifier
from src.auth.auth_decorators import init_auth_dependencies, WalletAuthContext
from src.utils.resilient_handler import api_retry
from src.utils.slowapi_limiter import get_slowapi_rate_limiter, limiter, RATE_LIMITS
from slowapi.errors import RateLimitExceeded
from src.utils.slowapi_dependencies import auto_rate_limit


load_dotenv()

# 配置日誌
configure_logging(level="INFO", format_json=True)
logger = get_logger("main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用啟動時的初始化"""
    try:
        # 初始化錢包驗證器的數據庫連接
        if hasattr(mongo_manager, 'db'):
            wallet_verifier.initialize_with_database(mongo_manager.db)
            await wallet_verifier.ensure_indexes()

            # 初始化認證依賴
            init_auth_dependencies(mongo_manager, wallet_verifier)
            logger.info("錢包驗證器初始化完成")
        else:
            logger.warning("MongoDB 連接未正確初始化，錢包驗證器將使用內存模式")

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

    except Exception as e:
        logger.error(f"安全組件初始化失敗: {e}")
        # 初始化失敗不應該阻止應用啟動

app = FastAPI(title="Grid Trading Server", version="1.0.0", lifespan=lifespan)

# 錢包簽名驗證器
wallet_verifier = WalletSignatureVerifier()

#For test
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://myapp.com", "http://localhost:5174", "http://localhost:5173", "http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全域會話管理器
session_manager = SessionManager()

# 全域MongoDB管理器
mongo_manager = MongoManager(os.getenv("MONGODB_URI"))

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
            "user_wallet_address": "user123"
        }
    })
    
    user_id: str
    user_api_key: str
    user_api_secret: str
    user_wallet_address: str

@app.post("/api/user/enable")
@limiter.limit(RATE_LIMITS['auth'])
@api_retry
async def enable_bot_trading(request: Request, config: RegisterConfig):
    """啟用機器人交易 儲存用戶資料進database"""
    try:
        # 檢查用戶是否已存在
        if await mongo_manager.get_user(config.user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_ALREADY_EXISTS,
                details={"user_id": config.user_id}
            )

        # 創建用戶
        result = await mongo_manager.create_user(
            config.user_id,
            config.user_api_key,
            config.user_api_secret,
            config.user_wallet_address
        )

        if not result.inserted_id:
            raise GridTradingException(
                error_code=ErrorCode.USER_CREATION_FAILED,
                details={"user_id": config.user_id}
            )

        return {"success": True, "data": {"user_id": config.user_id}}
        
    except GridTradingException:
        raise
    except Exception as e:
        logger.error("創建用戶失敗", event_type="user_creation_error", data={
            "user_id": config.user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.USER_CREATION_FAILED,
            details={"user_id": config.user_id},
            original_error=e
        )

class UpdateConfig(BaseModel):
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

@app.get("/api/user/check/{user_id}")
async def check_user_exists(user_id: str):
    """檢查用戶是否存在"""
    try:
        logger.info("檢查用戶是否存在", event_type="user_check", data={"user_id": user_id})

        user = await mongo_manager.get_user(user_id)

        if user:
            return {
                "success": True,
                "data": {
                    "exists": True,
                    "user_id": user_id,
                    "wallet_address": user.get("wallet_address")
                }
            }
        else:
            return {
                "success": True,
                "data": {
                    "exists": False,
                    "user_id": user_id
                }
            }

    except Exception as e:
        logger.error("檢查用戶失敗", event_type="user_check_error", data={
            "user_id": user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )

@app.put("/api/user/update")
@api_retry
async def update_user_data(config: UpdateConfig):
    """更新機器人交易 儲存用戶資料進database"""
    try:
        # 檢查用戶是否存在
        if not await mongo_manager.get_user(config.user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_NOT_FOUND,
                details={"user_id": config.user_id}
            )

        # 更新用戶
        result = await mongo_manager.update_user(config.user_id, {
            "api_key": config.user_api_key,
            "api_secret": config.user_api_secret,
        })

        if result.modified_count == 0 and result.matched_count == 0:
            raise GridTradingException(
                error_code=ErrorCode.USER_UPDATE_FAILED,
                details={"user_id": config.user_id}
            )

        return {"success": True, "data": {"user_id": config.user_id}}
        
    except GridTradingException:
        raise
    except Exception as e:
        logger.error("更新用戶失敗", event_type="user_update_error", data={
            "user_id": config.user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.USER_UPDATE_FAILED,
            details={"user_id": config.user_id},
            original_error=e
        )

class StartConfig(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "ticker": "BTCUSDT",
            "direction": "BOTH",
            "current_price": 42500,
            "upper_bound": 45000,
            "lower_bound": 40000,
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

    ticker: str = Field(..., pattern=r"^[A-Z]+USDT$")
    direction: str = Field(..., pattern="^(LONG|SHORT|BOTH)$")
    current_price: float = Field(..., gt=0)
    upper_bound: float = Field(..., gt=0)
    lower_bound: float = Field(..., gt=0)
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

    def to_internal(self) -> dict:
        # 轉 Direction 枚舉
        dir_map = {
            "LONG": Direction.LONG,
            "SHORT": Direction.SHORT,
            "BOTH": Direction.BOTH,
        }
        direction_enum = dir_map[self.direction]
        return {
            "ticker": self.ticker,
            "direction": direction_enum,
            "current_price": self.current_price,
            "upper_bound": self.upper_bound,
            "lower_bound": self.lower_bound,
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
        logger.info(f"用戶 {config.user_id} 簽名驗證成功",
                   extra={"wallet_type": auth_result["wallet_type"]})

    session_id = create_session_id(config.user_id, config.ticker)
    
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
            "session_id": "user123_BTCUSDT",
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
    parts = session_id.split('_')
    if len(parts) < 2:
        raise GridTradingException(
            error_code=ErrorCode.INVALID_SESSION_ID,
            details={"session_id": session_id}
        )
    user_id = parts[-2]

    # 使用統一的簽名驗證
    async with WalletAuthContext(
        user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ) as auth_result:
        logger.info(f"用戶 {user_id} 簽名驗證成功",
                   extra={"wallet_type": auth_result["wallet_type"]})

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

@app.get("/")
async def root():
    return {
        "message": "Dexless Bot API",
        "version": "1.0.0",
        "WHATUP": "BRO"
    }
