#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Copy Trading API 端點
提供 Copy Trading 相關的 REST API 端點
"""

import asyncio
import json
import os
import time
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from src.services.copy_trading_service import get_copy_trading_manager
from src.utils.logging_config import get_logger
from src.utils.error_codes import GridTradingException, ErrorCode
from src.utils.slowapi_limiter import limiter, RATE_LIMITS
from src.utils.resilient_handler import api_retry
from src.auth.auth_decorators import WalletAuthContext

logger = get_logger("copy_trading_routes")

async def _verify_admin(request: Request):
    """驗證管理員 API 密鑰"""
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        raise HTTPException(status_code=503, detail="管理員功能未配置")
    request_key = request.headers.get("X-Admin-API-Key")
    if not request_key or request_key != admin_key:
        raise HTTPException(status_code=403, detail="需要管理員權限")

# 創建路由器
router = APIRouter(prefix="/api/copy", tags=["Copy Trading"])


# ============== Request/Response Models ==============

class LeaderRegisterRequest(BaseModel):
    """Leader 申請請求"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user123",
            "user_sig": "signature",
            "timestamp": 1234567890,
            "nonce": "random_nonce"
        }
    })

    user_id: str = Field(..., min_length=1)
    user_sig: str = Field(..., min_length=1)
    timestamp: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=1)


class LeaderActivateRequest(BaseModel):
    """Leader 激活請求"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "user123",
            "user_sig": "signature",
            "timestamp": 1234567890,
            "nonce": "random_nonce"
        }
    })

    user_id: str = Field(..., min_length=1)
    user_sig: str = Field(..., min_length=1)
    timestamp: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=1)


class FollowStartRequest(BaseModel):
    """開始跟單請求"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "follower123",
            "leader_id": "leader456",
            "copy_ratio": 0.5,
            "max_per_trade_amount": 1000,
            "daily_max_loss": 500,
            "max_position_count": 10,
            "user_sig": "signature",
            "timestamp": 1234567890,
            "nonce": "random_nonce"
        }
    })

    user_id: str = Field(..., min_length=1, description="Follower 用戶 ID")
    leader_id: str = Field(..., min_length=1, description="要跟隨的 Leader ID")
    copy_ratio: float = Field(default=1.0, ge=0.1, le=10.0, description="跟單比例 (0.1-10.0)")
    max_per_trade_amount: float = Field(default=1000.0, gt=0, description="單筆最大金額 (USDC)")
    daily_max_loss: float = Field(default=500.0, gt=0, description="每日最大虧損 (USDC)")
    max_position_count: int = Field(default=10, ge=1, le=50, description="最大持倉數量")
    max_position_value: float = Field(default=10000.0, gt=0, description="最大持倉總值 (USDC)")
    max_single_position_ratio: float = Field(default=0.3, gt=0, le=1.0, description="單一持倉最大比例")
    user_sig: str = Field(..., min_length=1)
    timestamp: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=1)


class FollowStopRequest(BaseModel):
    """停止跟單請求"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "follower123",
            "user_sig": "signature",
            "timestamp": 1234567890,
            "nonce": "random_nonce"
        }
    })

    user_id: str = Field(..., min_length=1)
    user_sig: str = Field(..., min_length=1)
    timestamp: int = Field(..., gt=0)
    nonce: str = Field(..., min_length=1)


class AdminApproveRequest(BaseModel):
    """管理員審核請求"""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "user_id": "leader123",
            "admin_id": "admin001",
            "reason": "Optional rejection reason"
        }
    })

    user_id: str = Field(..., min_length=1, description="申請者 ID")
    admin_id: str = Field(..., min_length=1, description="管理員 ID")
    reason: Optional[str] = Field(default="", description="拒絕原因 (僅拒絕時使用)")


# ============== Leader API 端點 ==============

@router.post("/leader/register")
@limiter.limit(RATE_LIMITS['auth'])
@api_retry
async def register_leader(request: Request, config: LeaderRegisterRequest):
    """
    申請成為 Leader (需要管理員審核)

    申請後狀態為 pending，需要管理員審核通過後才能成為正式 Leader
    """
    # 驗證簽名
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ) as auth_result:
        logger.info(
            f"用戶 {config.user_id} 申請成為 Leader",
            event_type="leader_register_request",
            data={"wallet_type": auth_result["wallet_type"]}
        )

    try:
        manager = await get_copy_trading_manager()
        result = await manager.register_leader(config.user_id)

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"申請成為 Leader 失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"user_id": config.user_id, "error": str(e)},
            original_error=e
        )


@router.post("/leader/unregister")
@limiter.limit(RATE_LIMITS['auth'])
@api_retry
async def unregister_leader(request: Request, config: LeaderRegisterRequest):
    """
    取消 Leader 身份

    取消後將停止所有 Follower 的跟單
    """
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ):
        pass

    try:
        manager = await get_copy_trading_manager()

        # 先停用 Leader
        await manager.deactivate_leader(config.user_id)

        # 更新數據庫狀態
        await manager.mongo_manager.users.update_one(
            {"_id": config.user_id},
            {
                "$set": {
                    "is_leader": False,
                    "leader_is_active": False
                }
            }
        )

        return {
            "success": True,
            "data": {
                "user_id": config.user_id,
                "message": "已取消 Leader 身份"
            }
        }

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"取消 Leader 身份失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"user_id": config.user_id},
            original_error=e
        )


@router.post("/leader/activate")
@limiter.limit(RATE_LIMITS['trading'])
@api_retry
async def activate_leader(request: Request, config: LeaderActivateRequest):
    """
    激活 Leader (開始接受 Followers 跟單)

    僅限已審核通過的 Leader 使用
    """
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ):
        pass

    try:
        manager = await get_copy_trading_manager()
        result = await manager.activate_leader(config.user_id)

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"激活 Leader 失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"user_id": config.user_id},
            original_error=e
        )


@router.post("/leader/deactivate")
@limiter.limit(RATE_LIMITS['trading'])
@api_retry
async def deactivate_leader(request: Request, config: LeaderActivateRequest):
    """
    停用 Leader (停止接受新 Followers)

    現有 Followers 將繼續跟單直到主動停止
    """
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ):
        pass

    try:
        manager = await get_copy_trading_manager()
        result = await manager.deactivate_leader(config.user_id)

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"停用 Leader 失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"user_id": config.user_id},
            original_error=e
        )


@router.get("/leaders")
@limiter.limit(RATE_LIMITS['status_check'])
async def list_available_leaders(request: Request):
    """
    獲取可跟隨的 Leaders 列表

    只返回已審核通過且已激活的 Leaders
    """
    try:
        manager = await get_copy_trading_manager()
        leaders = await manager.get_available_leaders()

        return {
            "success": True,
            "data": {
                "leaders": leaders,
                "total": len(leaders)
            }
        }

    except Exception as e:
        logger.error(f"獲取 Leaders 列表失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            original_error=e
        )


@router.get("/leader/{leader_id}")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_leader_detail(request: Request, leader_id: str):
    """
    獲取 Leader 詳細資訊

    包括統計數據、跟隨者數量等
    """
    try:
        manager = await get_copy_trading_manager()
        detail = await manager.get_leader_detail(leader_id)

        if not detail:
            raise GridTradingException(
                error_code=ErrorCode.LEADER_NOT_FOUND,
                details={"leader_id": leader_id}
            )

        return {"success": True, "data": detail}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"獲取 Leader 詳情失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"leader_id": leader_id},
            original_error=e
        )


# ============== Follower API 端點 ==============

@router.post("/follow/start")
@limiter.limit(RATE_LIMITS['trading'])
@api_retry
async def start_following(request: Request, config: FollowStartRequest):
    """
    開始跟隨某個 Leader

    需要設置跟單比例和風控參數
    """
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ):
        pass

    try:
        manager = await get_copy_trading_manager()

        risk_limits = {
            "max_per_trade_amount": config.max_per_trade_amount,
            "daily_max_loss": config.daily_max_loss,
            "max_position_count": config.max_position_count,
            "max_position_value": config.max_position_value,
            "max_single_position_ratio": config.max_single_position_ratio
        }

        result = await manager.start_following(
            follower_id=config.user_id,
            leader_id=config.leader_id,
            copy_ratio=config.copy_ratio,
            risk_limits=risk_limits
        )

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"開始跟單失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={
                "follower_id": config.user_id,
                "leader_id": config.leader_id
            },
            original_error=e
        )


@router.post("/follow/stop")
@limiter.limit(RATE_LIMITS['trading'])
@api_retry
async def stop_following(request: Request, config: FollowStopRequest):
    """
    停止跟單
    """
    async with WalletAuthContext(
        config.user_id,
        config.user_sig,
        config.timestamp,
        config.nonce
    ):
        pass

    try:
        manager = await get_copy_trading_manager()
        result = await manager.stop_following(config.user_id)

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"停止跟單失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"follower_id": config.user_id},
            original_error=e
        )


@router.get("/status/{user_id}")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_copy_status(request: Request, user_id: str):
    """
    獲取用戶的 Copy Trading 狀態

    適用於 Leader 和 Follower
    """
    try:
        manager = await get_copy_trading_manager()

        # 檢查是否為 Follower
        follower_status = await manager.get_follower_status(user_id)
        if follower_status:
            return {
                "success": True,
                "data": {
                    "role": "follower",
                    "status": follower_status
                }
            }

        # 檢查是否為 Leader
        leader_detail = await manager.get_leader_detail(user_id)
        if leader_detail:
            return {
                "success": True,
                "data": {
                    "role": "leader",
                    "status": leader_detail
                }
            }

        # 都不是
        return {
            "success": True,
            "data": {
                "role": "none",
                "status": None,
                "message": "用戶未參與 Copy Trading"
            }
        }

    except Exception as e:
        logger.error(f"獲取 Copy Trading 狀態失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )


@router.get("/statistics/{user_id}")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_copy_statistics(request: Request, user_id: str):
    """
    獲取用戶的 Copy Trading 統計數據
    """
    try:
        manager = await get_copy_trading_manager()

        follower_status = await manager.get_follower_status(user_id)
        if follower_status:
            return {
                "success": True,
                "data": {
                    "role": "follower",
                    "statistics": follower_status.get("statistics", {})
                }
            }

        leader_detail = await manager.get_leader_detail(user_id)
        if leader_detail:
            return {
                "success": True,
                "data": {
                    "role": "leader",
                    "statistics": leader_detail.get("statistics", {})
                }
            }

        raise GridTradingException(
            error_code=ErrorCode.USER_NOT_FOUND,
            details={"user_id": user_id, "message": "用戶未參與 Copy Trading"}
        )

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"獲取統計數據失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )


@router.get("/trades/{user_id}")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_copy_trades(
    request: Request,
    user_id: str,
    limit: int = 50,
    offset: int = 0
):
    """
    獲取用戶的跟單交易歷史
    """
    try:
        manager = await get_copy_trading_manager()
        trades = await manager.get_follower_trade_history(
            follower_id=user_id,
            limit=min(limit, 100),
            offset=max(offset, 0)
        )

        return {
            "success": True,
            "data": {
                "trades": trades,
                "total": len(trades),
                "limit": limit,
                "offset": offset
            }
        }

    except Exception as e:
        logger.error(f"獲取交易歷史失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            details={"user_id": user_id},
            original_error=e
        )


@router.get("/stream/{user_id}")
async def stream_copy_events(
    request: Request,
    user_id: str,
    user_sig: str = "",
    timestamp: int = 0,
    nonce: str = ""
):
    """
    SSE 即時跟單更新推送

    推送跟單執行結果、風控警告等事件
    """
    if not user_sig or not timestamp or not nonce:
        raise HTTPException(status_code=401, detail="需要認證參數")
    try:
        from src.auth.auth_decorators import verify_wallet_signature_db
        await verify_wallet_signature_db(user_id, user_sig, timestamp, nonce)
    except Exception as e:
        raise HTTPException(status_code=403, detail="認證失敗")

    async def event_generator():
        manager = await get_copy_trading_manager()
        event_queue = asyncio.Queue()

        async def event_callback(event: dict):
            await event_queue.put(event)

        try:
            # 註冊事件回調
            manager.register_event_callback(user_id, event_callback)

            # 發送連接確認
            yield f"event: connected\ndata: {json.dumps({'message': 'connected', 'user_id': user_id})}\n\n"

            heartbeat_interval = 30
            last_heartbeat = time.time()

            while True:
                if await request.is_disconnected():
                    break

                try:
                    # 使用超時等待事件
                    event = await asyncio.wait_for(
                        event_queue.get(),
                        timeout=1.0
                    )
                    yield f"data: {json.dumps(event)}\n\n"

                except asyncio.TimeoutError:
                    # 發送心跳
                    if time.time() - last_heartbeat > heartbeat_interval:
                        heartbeat = {
                            "type": "heartbeat",
                            "user_id": user_id,
                            "timestamp": time.time()
                        }
                        yield f"data: {json.dumps(heartbeat)}\n\n"
                        last_heartbeat = time.time()

        except Exception as e:
            logger.error(f"SSE 流錯誤: {e}")
            yield f"event: error\ndata: {json.dumps({'error': 'stream_error'})}\n\n"

        finally:
            # 取消註冊
            manager.unregister_event_callback(user_id, event_callback)

    headers = {
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
    }

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers
    )


# ============== 管理員 API 端點 ==============

@router.get("/admin/leaders/pending")
@limiter.limit(RATE_LIMITS['status_check'])
async def list_pending_leaders(request: Request):
    """
    獲取待審核的 Leader 申請列表 (管理員用)
    """
    try:
        await _verify_admin(request)
        manager = await get_copy_trading_manager()
        applications = await manager.get_pending_leader_applications()

        return {
            "success": True,
            "data": {
                "applications": applications,
                "total": len(applications)
            }
        }

    except Exception as e:
        logger.error(f"獲取待審核申請失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            original_error=e
        )


@router.post("/admin/leader/approve")
@limiter.limit(RATE_LIMITS['auth'])
async def approve_leader(request: Request, config: AdminApproveRequest):
    """
    審核通過 Leader 申請 (管理員用)
    """
    try:
        await _verify_admin(request)
        manager = await get_copy_trading_manager()
        result = await manager.approve_leader(config.user_id, config.admin_id)

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"審核 Leader 失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"user_id": config.user_id},
            original_error=e
        )


@router.post("/admin/leader/reject")
@limiter.limit(RATE_LIMITS['auth'])
async def reject_leader(request: Request, config: AdminApproveRequest):
    """
    拒絕 Leader 申請 (管理員用)
    """
    try:
        await _verify_admin(request)
        manager = await get_copy_trading_manager()
        result = await manager.reject_leader(
            config.user_id,
            config.admin_id,
            config.reason or ""
        )

        return {"success": True, "data": result}

    except GridTradingException:
        raise
    except Exception as e:
        logger.error(f"拒絕 Leader 申請失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.COPY_TRADING_ERROR,
            details={"user_id": config.user_id},
            original_error=e
        )


@router.get("/admin/stats")
@limiter.limit(RATE_LIMITS['status_check'])
async def get_copy_trading_stats(request: Request):
    """
    獲取 Copy Trading 系統統計 (管理員用)
    """
    try:
        await _verify_admin(request)
        manager = await get_copy_trading_manager()
        stats = manager.get_stats()

        return {"success": True, "data": stats}

    except Exception as e:
        logger.error(f"獲取系統統計失敗: {e}")
        raise GridTradingException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            original_error=e
        )
