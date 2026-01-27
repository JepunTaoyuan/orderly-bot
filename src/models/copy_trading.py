"""
Copy Trading 數據模型
用於 Copy Trading 功能的所有數據結構定義
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum
from decimal import Decimal


class CopyTradingMode(str, Enum):
    """Copy Trading 模式"""
    LEADER = "leader"
    FOLLOWER = "follower"


class LeaderStatus(str, Enum):
    """Leader 申請狀態"""
    NONE = "none"           # 未申請
    PENDING = "pending"     # 待審核
    APPROVED = "approved"   # 已通過
    REJECTED = "rejected"   # 已拒絕


class CopyTradeAction(str, Enum):
    """跟單交易動作"""
    OPEN = "open"           # 開倉
    CLOSE = "close"         # 平倉
    ADD = "add"             # 加倉
    REDUCE = "reduce"       # 減倉


class CopyOrderType(str, Enum):
    """訂單類型"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class CopyOrderSide(str, Enum):
    """訂單方向"""
    BUY = "BUY"
    SELL = "SELL"


class CopyTradeStatus(str, Enum):
    """跟單狀態"""
    PENDING = "pending"         # 等待執行
    EXECUTED = "executed"       # 已執行
    FAILED = "failed"           # 執行失敗
    SKIPPED = "skipped"         # 已跳過（風控等原因）


class TradingMode(str, Enum):
    """交易模式（用於互斥檢查）"""
    GRID = "grid"
    COPY_LEADER = "copy_leader"
    COPY_FOLLOWER = "copy_follower"


# ============== Leader 相關模型 ==============

class LeaderStatistics(BaseModel):
    """Leader 統計數據"""
    follower_count: int = Field(0, description="當前跟隨者數量")
    total_followers: int = Field(0, description="歷史總跟隨者數量")
    total_trades: int = Field(0, description="總交易次數")
    win_rate: float = Field(0.0, description="勝率 (%)")
    total_profit: float = Field(0.0, description="總盈利")
    avg_profit_per_trade: float = Field(0.0, description="平均每筆盈利")


class LeaderProfile(BaseModel):
    """Leader 資料（擴展自 users collection）"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., description="用戶 ID")
    wallet_address: str = Field(..., description="錢包地址")
    is_leader: bool = Field(False, description="是否為 Leader")
    leader_status: LeaderStatus = Field(LeaderStatus.NONE, description="Leader 狀態")
    leader_approved_by: Optional[str] = Field(None, description="審核管理員 ID")
    leader_approved_at: Optional[datetime] = Field(None, description="審核時間")
    leader_is_active: bool = Field(False, description="是否開放跟隨")
    leader_statistics: Optional[LeaderStatistics] = Field(None, description="Leader 統計")


# ============== Follower 相關模型 ==============

class RiskLimits(BaseModel):
    """風控限制配置"""
    max_per_trade_amount: float = Field(1000.0, gt=0, description="單筆最大金額 (USDC)")
    daily_max_loss: float = Field(500.0, gt=0, description="每日最大虧損 (USDC)")
    max_position_count: int = Field(10, ge=1, le=50, description="最大持倉數量")
    max_position_value: float = Field(5000.0, gt=0, description="最大持倉總值 (USDC)")
    max_single_position_ratio: float = Field(0.3, gt=0, le=1.0, description="單一持倉最大佔比")


class DailyStats(BaseModel):
    """每日統計（UTC 00:00 重置）"""
    date: str = Field(..., description="日期 (YYYY-MM-DD)")
    trades_count: int = Field(0, description="當日交易次數")
    total_loss: float = Field(0.0, description="當日總虧損")
    total_profit: float = Field(0.0, description="當日總盈利")


class FollowerStatistics(BaseModel):
    """Follower 統計數據"""
    total_trades: int = Field(0, description="總交易次數")
    successful_trades: int = Field(0, description="成功交易次數")
    failed_trades: int = Field(0, description="失敗交易次數")
    skipped_trades: int = Field(0, description="跳過交易次數")
    total_profit: float = Field(0.0, description="總盈利")
    total_slippage: float = Field(0.0, description="總滑點")
    avg_latency_ms: float = Field(0.0, description="平均延遲 (ms)")

    @property
    def success_rate(self) -> float:
        """計算成功率"""
        if self.total_trades == 0:
            return 0.0
        return (self.successful_trades / self.total_trades) * 100


class FollowerConfig(BaseModel):
    """Follower 配置（存儲在 copy_followers collection）"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(None, alias="_id")
    follower_id: str = Field(..., description="Follower 用戶 ID")
    leader_id: str = Field(..., description="跟隨的 Leader ID")
    copy_ratio: float = Field(1.0, gt=0, le=10.0, description="跟單比例 (0.1-10x)")
    is_active: bool = Field(True, description="是否啟用")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="建立時間")
    updated_at: Optional[datetime] = Field(None, description="更新時間")

    risk_limits: RiskLimits = Field(default_factory=RiskLimits, description="風控設定")
    daily_stats: Optional[DailyStats] = Field(None, description="每日統計")
    statistics: FollowerStatistics = Field(default_factory=FollowerStatistics, description="總統計")

    @field_validator('copy_ratio')
    @classmethod
    def validate_copy_ratio(cls, v):
        if v < 0.1 or v > 10.0:
            raise ValueError('copy_ratio 必須在 0.1 到 10.0 之間')
        return v


# ============== 交易記錄模型 ==============

class CopyTradeRecord(BaseModel):
    """跟單交易記錄（存儲在 copy_trades collection）"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(None, alias="_id")
    leader_id: str = Field(..., description="Leader 用戶 ID")
    follower_id: str = Field(..., description="Follower 用戶 ID")
    leader_order_id: str = Field(..., description="Leader 訂單 ID")
    follower_order_id: Optional[str] = Field(None, description="Follower 訂單 ID")

    symbol: str = Field(..., description="交易對 (如 PERP_BTC_USDC)")
    action: CopyTradeAction = Field(..., description="交易動作")
    order_type: CopyOrderType = Field(..., description="訂單類型")
    side: CopyOrderSide = Field(..., description="買/賣方向")

    leader_price: float = Field(..., description="Leader 成交價格")
    leader_quantity: float = Field(..., description="Leader 成交數量")
    follower_price: Optional[float] = Field(None, description="Follower 成交價格")
    follower_quantity: Optional[float] = Field(None, description="Follower 成交數量")

    copy_ratio: float = Field(..., description="跟單比例")
    status: CopyTradeStatus = Field(CopyTradeStatus.PENDING, description="跟單狀態")
    failure_reason: Optional[str] = Field(None, description="失敗原因")

    slippage: Optional[float] = Field(None, description="滑點 (USDC)")
    slippage_pct: Optional[float] = Field(None, description="滑點百分比")
    latency_ms: Optional[int] = Field(None, description="延遲 (ms)")

    leader_timestamp: datetime = Field(..., description="Leader 交易時間")
    follower_timestamp: Optional[datetime] = Field(None, description="Follower 交易時間")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="記錄建立時間")

    pnl: Optional[float] = Field(None, description="盈虧 (平倉時計算)")

    def calculate_slippage(self):
        """計算滑點"""
        if self.follower_price and self.leader_price:
            self.slippage = abs(self.follower_price - self.leader_price)
            self.slippage_pct = (self.slippage / self.leader_price) * 100

    def calculate_latency(self):
        """計算延遲"""
        if self.follower_timestamp and self.leader_timestamp:
            delta = self.follower_timestamp - self.leader_timestamp
            self.latency_ms = int(delta.total_seconds() * 1000)


# ============== API 請求/響應模型 ==============

class RegisterLeaderRequest(BaseModel):
    """申請成為 Leader 請求"""
    user_id: str = Field(..., description="用戶 ID")
    user_sig: str = Field(..., description="錢包簽名")
    timestamp: int = Field(..., description="時間戳")
    nonce: str = Field(..., description="Nonce")


class StartFollowingRequest(BaseModel):
    """開始跟隨請求"""
    user_id: str = Field(..., description="Follower 用戶 ID")
    leader_id: str = Field(..., description="要跟隨的 Leader ID")
    copy_ratio: float = Field(1.0, gt=0, le=10.0, description="跟單比例")
    max_per_trade_amount: float = Field(1000.0, gt=0, description="單筆最大金額")
    daily_max_loss: float = Field(500.0, gt=0, description="每日最大虧損")
    max_position_count: int = Field(10, ge=1, le=50, description="最大持倉數量")
    user_sig: str = Field(..., description="錢包簽名")
    timestamp: int = Field(..., description="時間戳")
    nonce: str = Field(..., description="Nonce")


class StopFollowingRequest(BaseModel):
    """停止跟隨請求"""
    user_id: str = Field(..., description="Follower 用戶 ID")
    user_sig: str = Field(..., description="錢包簽名")
    timestamp: int = Field(..., description="時間戳")
    nonce: str = Field(..., description="Nonce")


class ApproveLeaderRequest(BaseModel):
    """審核通過 Leader 請求（管理員用）"""
    admin_id: str = Field(..., description="管理員 ID")
    leader_id: str = Field(..., description="Leader 用戶 ID")


class RejectLeaderRequest(BaseModel):
    """拒絕 Leader 請求（管理員用）"""
    admin_id: str = Field(..., description="管理員 ID")
    leader_id: str = Field(..., description="Leader 用戶 ID")
    reason: Optional[str] = Field(None, description="拒絕原因")


class CopyTradingStatusResponse(BaseModel):
    """跟單狀態響應"""
    success: bool
    data: Dict[str, Any]


class LeaderListResponse(BaseModel):
    """Leader 列表響應"""
    success: bool
    data: Dict[str, Any]


# ============== 內部事件模型 ==============

class LeaderTradeEvent(BaseModel):
    """Leader 交易事件（用於內部廣播）"""
    leader_id: str
    order_id: str
    symbol: str
    side: CopyOrderSide
    order_type: CopyOrderType
    price: float
    quantity: float
    action: CopyTradeAction
    timestamp: datetime

    # 從 execution_report 解析的原始數據
    raw_data: Optional[Dict[str, Any]] = None


class CopyTradeResult(BaseModel):
    """跟單執行結果"""
    success: bool
    follower_id: str
    leader_order_id: str
    follower_order_id: Optional[str] = None
    status: CopyTradeStatus
    error_message: Optional[str] = None
    executed_price: Optional[float] = None
    executed_quantity: Optional[float] = None
    latency_ms: Optional[int] = None
