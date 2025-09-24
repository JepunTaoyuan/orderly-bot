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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.core.grid_signal import Direction
from src.utils.session_manager import SessionManager
from src.utils.logging_config import configure_logging, get_logger, metrics, set_session_context


# 配置日誌
configure_logging(level="INFO", format_json=True)
logger = get_logger("main")

app = FastAPI(title="Grid Trading Server (MVP)")

# 全域會話管理器
session_manager = SessionManager()


class StartConfig(BaseModel):
    ticker: str = Field(..., example="BTCUSDT")
    direction: str = Field(..., pattern="^(LONG|SHORT|BOTH)$", example="BOTH")
    current_price: float = Field(..., example=42500)
    upper_bound: float = Field(..., example=45000)
    lower_bound: float = Field(..., example=40000)
    grid_levels: int = Field(..., ge=2, example=6)
    total_amount: float = Field(..., gt=0, example=100)
    stop_bot_price: Optional[float] = Field(None, example=38000)
    stop_top_price: Optional[float] = Field(None, example=47000)
    user_id: str = Field(..., example="user123")
    user_sig: str = Field(..., example="user123sig")

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
            "total_amount": self.total_amount,
            "stop_bot_price": self.stop_bot_price,
            "stop_top_price": self.stop_top_price,
            "user_id": self.user_id,
            "user_sig": self.user_sig,
        }


@app.post("/api/grid/start")
async def start_grid(config: StartConfig):
    session_id = f"{config.user_id}_{config.ticker}"
    set_session_context(session_id)
    
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
            return {"status": "started", "session_id": session_id}
        else:
            metrics.increment_counter("api.grid.start.already_running", tags={"ticker": config.ticker})
            logger.warning("網格交易已在運行", event_type="grid_already_running", data={"session_id": session_id})
            return {"status": "already_running", "session_id": session_id}
            
    except Exception as e:
        metrics.increment_counter("api.grid.start.errors", tags={"ticker": config.ticker})
        logger.error("啟動網格交易失敗", event_type="grid_start_error", data={
            "session_id": session_id,
            "error": str(e)
        })
        raise HTTPException(status_code=500, detail=f"failed_to_start: {e}")


class StopConfig(BaseModel):
    session_id: str = Field(..., example="user123_BTCUSDT")

@app.post("/api/grid/stop")
async def stop_grid(config: StopConfig):
    set_session_context(config.session_id)
    
    try:
        logger.info("停止網格交易請求", event_type="grid_stop", data={"session_id": config.session_id})
        metrics.increment_counter("api.grid.stop.requests")
        
        success = await session_manager.stop_session(config.session_id)
        
        if success:
            metrics.increment_counter("api.grid.stop.success")
            logger.info("網格交易停止成功", event_type="grid_stopped", data={"session_id": config.session_id})
            return {"status": "stopped", "session_id": config.session_id}
        else:
            metrics.increment_counter("api.grid.stop.not_found")
            logger.warning("網格交易會話不存在", event_type="grid_not_found", data={"session_id": config.session_id})
            return {"status": "not_found", "session_id": config.session_id}
            
    except Exception as e:
        metrics.increment_counter("api.grid.stop.errors")
        logger.error("停止網格交易失敗", event_type="grid_stop_error", data={
            "session_id": config.session_id,
            "error": str(e)
        })
        raise HTTPException(status_code=500, detail=f"failed_to_stop: {e}")


@app.get("/api/grid/status/{session_id}")
async def get_status(session_id: str):
    try:
        status = await session_manager.get_session_status(session_id)
        if status is not None:
            return {"session_id": session_id, "status": status}
        else:
            raise HTTPException(status_code=404, detail="session_not_found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed_to_get_status: {e}")

@app.get("/api/grid/sessions")
async def list_sessions():
    try:
        sessions = await session_manager.list_sessions()
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed_to_list_sessions: {e}")

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
        raise HTTPException(status_code=503, detail="Service not ready")

@app.get("/metrics")
async def get_metrics():
    """獲取系統指標"""
    try:
        return metrics.get_metrics()
    except Exception as e:
        logger.error("獲取指標失敗", event_type="metrics", data={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"failed_to_get_metrics: {e}")

@app.get("/")
async def root():
    return {
        "message": "Dexless Bot API",
        "version": "1.0.0",
        "WHATUP": "BRO"
    }
# Entry point moved to root main.py
