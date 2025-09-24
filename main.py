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
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from grid_signal import Direction
from grid_bot import GridTradingBot


app = FastAPI(title="Grid Trading Server (MVP)")

# 全域單例 Bot（簡化示範）
bot = GridTradingBot()


class StartConfig(BaseModel):
    ticker: str = Field(..., example="BTCUSDT")
    direction: str = Field(..., pattern="^(LONG|SHORT|BOTH)$", example="BOTH")
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
    if bot.is_running:
        return {"status": "already_running"}

    try:
        await bot.start_grid_trading(config.to_internal())
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed_to_start: {e}")


@app.post("/api/grid/stop")
async def stop_grid():
    try:
        if bot.is_running:
            await bot.stop_grid_trading()
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed_to_stop: {e}")


@app.get("/api/grid/status")
async def get_status():
    try:
        status = await bot.get_status()
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed_to_get_status: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
