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
import hashlib
from typing import Any, Optional
from datetime import datetime

from dotenv import load_dotenv
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from pydantic import model_validator
from contextlib import asynccontextmanager
import json

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
from src.utils.cors_config import configure_cors
from src.utils.slowapi_limiter import get_slowapi_rate_limiter, limiter, RATE_LIMITS
from slowapi.errors import RateLimitExceeded
from src.utils.slowapi_dependencies import auto_rate_limit
from src.core.grid_signal import GridType
from src.utils.websocket_manager import start_websocket_manager, stop_websocket_manager
from src.utils.system_monitor import start_system_monitor, stop_system_monitor, get_system_monitor
from src.utils.error_recovery import start_error_recovery, stop_error_recovery, get_error_recovery_manager, ErrorSeverity
from src.utils.mongodb_health import start_mongodb_health_monitoring, stop_mongodb_health_monitoring
from src.models.grid_summary import GridSummaryFilter
from src.services.grid_summary_service import GridSummaryService
from src.services.copy_trading_service import get_copy_trading_manager
from src.api.copy_trading_routes import router as copy_trading_router


load_dotenv()

# 配置日誌
configure_logging(level="INFO", format_json=True)
logger = get_logger("main")

# 全域統一數據庫管理器
db_manager = DatabaseManager()

mongo_manager = None  # 聲明全域變數，僅用於 init_auth_dependencies

async def get_current_mongo_manager():
    """
    安全獲取當前的 mongo_manager 實例

    始終從 db_manager 獲取最新的 mongo_manager，確保在 MongoDB 健康監控
    重建連接後仍能獲取有效的連接實例。

    Returns:
        MongoManager: 當前有效的 MongoManager 實例

    Raises:
        HTTPException: 如果數據庫未初始化
    """
    try:
        return await db_manager.get_mongo_manager()
    except RuntimeError as e:
        logger.error(f"獲取 mongo_manager 失敗: {e}")
        raise HTTPException(
            status_code=503,
            detail="數據庫服務不可用 - mongo_manager 未初始化"
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mongo_manager
    """應用啟動時的初始化"""
    try:
        # 初始化統一數據庫連接
        await db_manager.initialize(os.getenv("MONGODB_URI"), db_name=os.getenv("DB_NAME"))
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

        # 🚀 優化：初始化 SessionManager 使用統一數據庫連接池
        await session_manager.initialize()
        logger.info("SessionManager 已使用統一數據庫連接池初始化")

        # 🆕 初始化 CopyTradingSessionManager
        # copy_trading_manager = await get_copy_trading_manager()
        # await copy_trading_manager.initialize(session_manager)
        # logger.info("CopyTradingSessionManager 已初始化")

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
        logger.error(f"關鍵組件初始化失敗，應用無法安全運行: {e}")
        raise

    # 應用運行期間
    yield

    # 應用關閉時的清理
    logger.info("應用正在關閉，執行清理操作...")

    # 🆕 停止 CopyTradingSessionManager
    try:
        copy_trading_manager = await get_copy_trading_manager()
        await copy_trading_manager.shutdown()
        logger.info("CopyTradingSessionManager 已停止")
    except Exception as e:
        logger.error(f"停止 CopyTradingSessionManager 失敗: {e}")

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

    # 🚀 優化：停止會話緩存系統
    try:
        if hasattr(session_manager, 'session_cache') and session_manager.session_cache:
            await session_manager.session_cache.stop()
            logger.info("會話緩存系統已停止")
    except Exception as e:
        logger.error(f"停止會話緩存系統失敗: {e}")

    # 🚀 優化：停止 GridTradingBot 對象池
    try:
        if hasattr(session_manager, 'bot_pool') and session_manager.bot_pool:
            await session_manager.bot_pool.stop()
            logger.info("GridTradingBot 對象池已停止")
    except Exception as e:
        logger.error(f"停止對象池失敗: {e}")

    # 🚀 優化：停止 API 批量調用優化器
    try:
        if hasattr(session_manager, 'api_optimizer') and session_manager.api_optimizer:
            await session_manager.api_optimizer.stop()
            logger.info("API 批量調用優化器已停止")
    except Exception as e:
        logger.error(f"停止 API 優化器失敗: {e}")

    # 關閉數據庫連接
    try:
        await db_manager.close()
        logger.info("數據庫連接已關閉")
    except Exception as e:
        logger.error(f"關閉數據庫連接失敗: {e}")

app = FastAPI(title="Grid Trading Server", version="1.0.0", lifespan=lifespan)

# 錢包簽名驗證器
wallet_verifier = WalletSignatureVerifier()

configure_cors(app)

# 全域會話管理器
session_manager = SessionManager()

# 🆕 註冊 Copy Trading 路由
app.include_router(copy_trading_router)

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
        # 獲取當前有效的 mongo_manager
        current_mongo_manager = await get_current_mongo_manager()

        # 檢查用戶是否已存在
        config.user_api_key = "ed25519:" + config.user_api_key
        config.user_api_secret = "ed25519:" + config.user_api_secret

        # 檢查用戶是否已存在
        user = await current_mongo_manager.get_user(config.user_id)
        
        if not user:
            # 用戶不存在，創建新用戶
            logger.info(f"用戶 {config.user_id} 不存在，正在創建新用戶")
            await current_mongo_manager.create_user(
                user_id=config.user_id,
                api_key=config.user_api_key,
                api_secret=config.user_api_secret,
                wallet_address=config.user_id  # 假設 user_id 即為 wallet_address
            )
            return {"success": True, "data": {"user_id": config.user_id, "action": "created"}}
        else:
            # 用戶已存在
            found_user_id = user.get("user_id")
            logger.info(f"用戶已存在: {config.user_id}, 數據庫 user_id: {found_user_id}")
            
            # 使用數據庫中的 user_id 進行更新 (確保能匹配到)
            target_id = found_user_id if found_user_id else config.user_id
            
            # 更新用戶API密鑰對
            result = await current_mongo_manager.update_user_api_key_pair(
                target_id,
                config.user_api_key,
                config.user_api_secret,
            )

            if not result.modified_count:
                # 如果沒有修改，可能是因為值相同，這不一定是錯誤，但如果連匹配都沒匹配到則是錯誤
                if result.matched_count == 0:
                    raise GridTradingException(
                        error_code=ErrorCode.USER_NOT_FOUND,
                        details={"user_id": config.user_id}
                    )
                logger.info(f"用戶 {config.user_id} API Key 未變更")
            
            return {"success": True, "data": {"user_id": config.user_id, "action": "updated"}}

        
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
        # 獲取當前有效的 mongo_manager
        current_mongo_manager = await get_current_mongo_manager()

        # 檢查用戶是否已存在
        if not await current_mongo_manager.get_user(user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_NOT_FOUND,
                details={"user_id": user_id}
            )

        # 檢查用戶API密鑰是否存在
        api_key_exist = await current_mongo_manager.check_user_api_key_exist(user_id)
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
    grid_levels: int = Field(..., ge=2, le=200)
    total_margin: float = Field(..., gt=0, le=1_000_000)
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


async def _pre_validate_grid_session(user_id: str, ticker: str) -> None:
    """
    預驗證網格會話的唯一性，在進行複雜操作前快速檢查

    Args:
        user_id: 用戶ID
        ticker: 交易對

    Raises:
        GridTradingException: 如果發現重複會話
    """
    try:
        # 快速內存檢查
        user_sessions = await session_manager.get_user_sessions(user_id)
        for session_data in user_sessions.values():
            if (session_data.get('ticker') == ticker and
                session_data.get('is_running', False)):
                raise GridTradingException(
                    error_code=ErrorCode.DUPLICATE_GRID_SESSION,
                    details={
                        "user_id": user_id,
                        "ticker": ticker,
                        "existing_session_id": session_data.get('session_id'),
                        "message": f"用戶 {user_id} 在交易對 {ticker} 上已有活躍的網格會話"
                    }
                )

        # 數據庫層面檢查
        duplicate_session = await db_manager.check_duplicate_grid_session(user_id, ticker)
        if duplicate_session:
            raise GridTradingException(
                error_code=ErrorCode.DUPLICATE_GRID_SESSION,
                details={
                    "user_id": user_id,
                    "ticker": ticker,
                    "existing_session_id": duplicate_session.get('session_id'),
                    "message": f"數據庫中發現用戶 {user_id} 在交易對 {ticker} 上有其他活躍會話"
                }
            )

    except GridTradingException:
        raise
    except Exception as e:
        # 預驗證失敗不應該阻止請求，記錄警告但繼續處理
        logger.warning(f"預驗證網格會話失敗，將繼續處理請求: {e}")

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

    # 預驗證：快速檢查重複會話
    await _pre_validate_grid_session(config.user_id, config.ticker)

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
                "strategies": list(user_sessions.values()),
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
        bot = await session_manager.get_bot(session_id)
        
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

class TestStopConfig(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_id": "user123_PERP_ETH_USDC",
        }
    })

    session_id: str = Field(..., min_length=1)

@app.post("/api/grid/teststop")
@limiter.limit(RATE_LIMITS['grid_control'])
@api_retry
async def test_stop_grid(request: Request, config: TestStopConfig):
    # 僅在 DEBUG 模式下可用
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"
    if not debug_mode:
        raise HTTPException(status_code=404, detail="Not Found")

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
            logger.info("停止網格交易請求 (test)", event_type="grid_test_stop", data={"session_id": session_id})
            metrics.increment_counter("api.grid.stop.requests")
            
            success = await session_manager.stop_session(session_id)
            
            if success:
                metrics.increment_counter("api.grid.stop.success")
                logger.info("網格交易停止成功 (test)", event_type="grid_test_stopped", data={"session_id": session_id})
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
            logger.error("停止網格交易失敗 (test)", event_type="grid_test_stop_error", data={
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

@app.get("/api/grid/summaries/{user_id}")
@limiter.limit(RATE_LIMITS['status_check'])
@api_retry
async def get_grid_summaries(request: Request, user_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None,
                            stop_reason: Optional[str] = None, limit: int = 20, offset: int = 0):
    """
    獲取用戶的網格交易總結列表

    Args:
        user_id: 用戶ID
        start_date: 開始日期 (ISO 8601 格式)
        end_date: 結束日期 (ISO 8601 格式)
        stop_reason: 停止原因過濾
        limit: 返回數量限制 (1-100)
        offset: 偏移量

    Returns:
        網格總結列表和統計信息
    """
    try:
        # 獲取當前有效的 mongo_manager
        current_mongo_manager = await get_current_mongo_manager()

        # 檢查用戶是否存在
        if not await current_mongo_manager.get_user(user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_NOT_FOUND,
                details={"user_id": user_id}
            )

        # 創建過濾器
        filter_data = GridSummaryFilter(
            user_id=user_id,
            start_date=datetime.fromisoformat(start_date) if start_date else None,
            end_date=datetime.fromisoformat(end_date) if end_date else None,
            stop_reason=stop_reason,
            limit=min(max(limit, 1), 100),  # 限制在 1-100 之間
            offset=max(offset, 0)  # 確保不為負數
        )

        # 獲取數據庫連接
        database = await db_manager.get_database()
        grid_summary_service = GridSummaryService(database)

        # 查詢網格總結
        result = await grid_summary_service.get_grid_summaries_by_user(user_id, filter_data)

        return {
            "success": True,
            "data": result
        }

    except GridTradingException:
        raise
    except ValueError as e:
        # 處理日期格式錯誤
        raise GridTradingException(
            error_code=ErrorCode.INVALID_GRID_CONFIG,
            details={"validation_error": f"日期格式錯誤: {str(e)}"}
        )
    except Exception as e:
        logger.error("獲取網格總結列表失敗", event_type="get_grid_summaries_error", data={
            "user_id": user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )


@app.get("/api/grid/summary/{session_id}")
@limiter.limit(RATE_LIMITS['status_check'])
@api_retry
async def get_grid_summary(request: Request, session_id: str):
    """
    獲取特定網格會話的詳細總結

    Args:
        session_id: 會話ID

    Returns:
        網格總結詳細信息
    """
    try:
        # 驗證會話ID格式
        session_id = validate_session_id(session_id)

        # 獲取數據庫連接
        database = await db_manager.get_database()
        grid_summary_service = GridSummaryService(database)

        # 查詢網格總結
        summary = await grid_summary_service.get_grid_summary_by_session(session_id)

        if not summary:
            raise GridTradingException(
                error_code=ErrorCode.SESSION_NOT_FOUND,
                details={"session_id": session_id, "message": "找不到該會話的總結數據"}
            )

        return {
            "success": True,
            "data": summary
        }

    except GridTradingException:
        raise
    except Exception as e:
        logger.error("獲取網格總結失敗", event_type="get_grid_summary_error", data={
            "session_id": session_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"session_id": session_id},
            original_error=e
        )


@app.get("/api/grid/statistics/{user_id}")
@limiter.limit(RATE_LIMITS['status_check'])
@api_retry
async def get_user_grid_statistics(request: Request, user_id: str):
    """
    獲取用戶的網格交易統計信息

    Args:
        user_id: 用戶ID

    Returns:
        用戶網格交易統計信息
    """
    try:
        # 獲取當前有效的 mongo_manager
        current_mongo_manager = await get_current_mongo_manager()

        # 檢查用戶是否存在
        if not await current_mongo_manager.get_user(user_id):
            raise GridTradingException(
                error_code=ErrorCode.USER_NOT_FOUND,
                details={"user_id": user_id}
            )

        # 獲取數據庫連接
        database = await db_manager.get_database()
        grid_summary_service = GridSummaryService(database)

        # 獲取統計信息
        statistics = await grid_summary_service.get_user_statistics(user_id)

        return {
            "success": True,
            "data": statistics
        }

    except GridTradingException:
        raise
    except Exception as e:
        logger.error("獲取用戶統計信息失敗", event_type="get_user_statistics_error", data={
            "user_id": user_id,
            "error": str(e)
        })
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )

@app.get("/api/grid/stream/{user_id}")
async def stream_user_strategies(
    request: Request,
    user_id: str,
    user_sig: str = "",
    timestamp: int = 0,
    nonce: str = ""
):
    """
    🚀 優化版本：智能 SSE 流，支持緩存、變化檢測和動態頻率調整
    """
    if not user_sig or not timestamp or not nonce:
        raise HTTPException(status_code=401, detail="需要認證參數")
    try:
        from src.auth.auth_decorators import verify_wallet_signature_db
        await verify_wallet_signature_db(user_id, user_sig, timestamp, nonce)
    except Exception as e:
        raise HTTPException(status_code=403, detail="認證失敗")

    async def event_generator():
        try:
            # SSE 連接狀態
            last_payload_hash = None
            no_change_count = 0
            base_interval = 1.0
            current_interval = base_interval

            # 🚀 優化：使用更智能的頻率調整策略
            def calculate_interval(strategy_count: int, no_change_streak: int) -> float:
                """
                根據策略數量和無變化持續時間智能調整更新頻率

                Args:
                    strategy_count: 當前策略數量
                    no_change_streak: 連續無變化次數

                Returns:
                    調整後的間隔時間（秒）
                """
                # 基礎間隔根據策略數量調整
                if strategy_count > 50:
                    base = 2.0  # 大量策略時降低頻率
                elif strategy_count > 20:
                    base = 1.5  # 中等數量策略
                else:
                    base = 1.0  # 少量策略時保持高頻率

                # 如果長時間無變化，逐步增加間隔（最高到10秒）
                if no_change_streak > 30:  # 30次無變化（約30秒）
                    return min(base * 4, 10.0)
                elif no_change_streak > 10:  # 10次無變化（約10秒）
                    return min(base * 2, 5.0)
                else:
                    return base

            # 發送初始連接確認
            yield "event: connected\n" + f"data: {json.dumps({'message': 'connected', 'user_id': user_id})}\n\n"

            while True:
                if await request.is_disconnected():
                    break

                try:
                    # 🚀 優化：使用緩存但允許手動刷新
                    sessions = await session_manager.get_user_sessions(user_id, use_cache=no_change_count < 5)

                    # 構建載荷
                    payload = {
                        "user_id": user_id,
                        "strategies": list(sessions.values()),
                        "total_strategies": len(sessions),
                        "timestamp": time.time(),
                        "update_interval": current_interval,
                        "cache_used": no_change_count < 5
                    }

                    # 🚀 優化：計算載荷哈希檢測變化
                    payload_str = json.dumps(payload, sort_keys=True)
                    current_hash = hashlib.sha256(payload_str.encode()).hexdigest()

                    # 只有在數據變化時才發送完整載荷
                    if current_hash != last_payload_hash:
                        data = json.dumps(payload)
                        yield f"data: {data}\n\n"
                        last_payload_hash = current_hash
                        no_change_count = 0
                    else:
                        # 無變化時只發送心跳
                        no_change_count += 1
                        if no_change_count % 10 == 0:  # 每10次無變化發送一次心跳
                            heartbeat = {
                                "user_id": user_id,
                                "heartbeat": True,
                                "no_change_count": no_change_count,
                                "timestamp": time.time()
                            }
                            yield f"data: {json.dumps(heartbeat)}\n\n"

                    # 🚀 優化：智能調整更新頻率
                    current_interval = calculate_interval(len(sessions), no_change_count)

                    # 動態休眠
                    await asyncio.sleep(current_interval)

                except Exception as e:
                    logger.error(f"SSE 流處理錯誤: {e}")
                    yield "event: error\n" + f"data: {json.dumps({'message': 'stream_error'})}\n\n"
                    await asyncio.sleep(5.0)  # 錯誤時等待更長時間

        except Exception as e:
            logger.error(f"SSE 生成器錯誤: {e}")
            yield "event: error\n" + f"data: {json.dumps({'message': 'generator_error'})}\n\n"

    # 🚀 優化：添加響應頭優化客戶端體驗
    headers = {
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
    }

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers
    )


@app.get("/")
async def root():
    return {
        "message": "Dexless Bot API",
        "version": "1.0.0",
        "WHATUP": "BRO"
    }
