"""
網格交易總結數據模型
用於存儲每個網格交易會話結束時的總統計數據
"""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum
from bson import ObjectId


class StopReason(str, Enum):
    """網格停止原因枚舉"""
    MANUAL = "manual"           # 手動停止
    STOP_LOSS = "stop_loss"     # 觸及止損
    SYSTEM_ERROR = "system_error"  # 系統錯誤
    TIMEOUT = "timeout"         # 超時
    INSUFFICIENT_MARGIN = "insufficient_margin"  # 保證金不足


class GridSummary(BaseModel):
    """網格交易總結數據模型"""

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={ObjectId: str},
        json_schema_extra={
            "example": {
                "_id": "64a1b2c3d4e5f6789012345",
                "session_id": "user123_PERP_ETH_USDC",
                "user_id": "user123",
                "start_time": "2024-01-01T10:00:00Z",
                "end_time": "2024-01-01T15:30:00Z",
                "duration_seconds": 19800,
                "total_profit": 125.50,
                "grid_profit": 98.75,
                "unpaired_profit": 26.75,
                "arbitrage_times": 15,
                "stop_reason": "manual",
                "grid_config": {
                    "ticker": "PERP_ETH_USDC",
                    "direction": "BOTH",
                    "grid_type": "ARITHMETIC",
                    "grid_levels": 10,
                    "upper_bound": 45000,
                    "lower_bound": 40000,
                    "total_margin": 1000
                }
            }
        }
    )

    id: Optional[str] = Field(None, alias="_id")
    session_id: str = Field(..., description="網格交易會話ID")
    user_id: str = Field(..., description="用戶ID")
    start_time: datetime = Field(..., description="網格開始時間")
    end_time: datetime = Field(..., description="網格結束時間")
    duration_seconds: int = Field(..., description="運行時長（秒）")
    sub_account_id: Optional[str] = Field(None, description="關聯的子帳戶ID")

    # 盈虧相關
    total_profit: float = Field(..., description="總盈虧")
    grid_profit: float = Field(..., description="網格盈虧（已完成套利）")
    unpaired_profit: float = Field(..., description="未配對盈虧")

    # 交易統計
    arbitrage_times: int = Field(..., description="總套利次數")

    # 停止信息
    stop_reason: StopReason = Field(..., description="停止原因")

    # 網格配置快照
    grid_config: Dict[str, Any] = Field(..., description="網格配置快照")

    # 可選的額外統計
    max_drawdown: Optional[float] = Field(None, description="最大回撤")
    capital_utilization: Optional[float] = Field(None, description="資本利用率")

    @classmethod
    def create_from_bot_data(
        cls,
        session_id: str,
        user_id: str,
        start_time: datetime,
        end_time: datetime,
        profit_data: Dict[str, Any],
        grid_config: Dict[str, Any],
        stop_reason: StopReason,
        **kwargs
    ) -> "GridSummary":
        """
        從機器人數據創建網格總結對象

        Args:
            session_id: 會話ID
            user_id: 用戶ID
            start_time: 開始時間
            end_time: 結束時間
            profit_data: 盈虧數據
            grid_config: 網格配置
            stop_reason: 停止原因
            **kwargs: 其他可選參數

        Returns:
            GridSummary 實例
        """
        duration_seconds = int((end_time - start_time).total_seconds())

        return cls(
            session_id=session_id,
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            total_profit=profit_data.get("total_profit", 0.0),
            grid_profit=profit_data.get("grid_profit", 0.0),
            unpaired_profit=profit_data.get("unpaired_profit", 0.0),
            arbitrage_times=profit_data.get("arbitrage_times", 0),
            stop_reason=stop_reason,
            stop_reason=stop_reason,
            grid_config=grid_config,
            sub_account_id=grid_config.get("sub_account_id") or grid_config.get("orderly_account_id"),
            max_drawdown=kwargs.get("max_drawdown"),
            capital_utilization=kwargs.get("capital_utilization")
        )


class GridSummaryFilter(BaseModel):
    """網格總結查詢過濾器"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "user123",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-31T23:59:59Z",
                "stop_reason": "manual",
                "limit": 20,
                "offset": 0
            }
        }
    )

    user_id: Optional[str] = Field(None, description="用戶ID")
    start_date: Optional[datetime] = Field(None, description="開始日期")
    end_date: Optional[datetime] = Field(None, description="結束日期")
    stop_reason: Optional[StopReason] = Field(None, description="停止原因")
    limit: int = Field(20, ge=1, le=100, description="返回數量限制")
    offset: int = Field(0, ge=0, description="偏移量")


class GridSummaryResponse(BaseModel):
    """網格總結查詢響應"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "data": {
                    "summaries": [
                        {
                            "_id": "64a1b2c3d4e5f6789012345",
                            "session_id": "user123_PERP_ETH_USDC",
                            "total_profit": 125.50,
                            "arbitrage_times": 15,
                            "duration_seconds": 19800,
                            "stop_reason": "manual"
                        }
                    ],
                    "total_count": 1,
                    "has_more": False
                }
            }
        }
    )

    success: bool
    data: Dict[str, Any]