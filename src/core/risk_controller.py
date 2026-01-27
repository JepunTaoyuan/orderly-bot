#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Risk Controller - 跟單風險控制
在執行跟單之前驗證是否符合風控限制
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from decimal import Decimal
from dataclasses import dataclass, field
from src.utils.logging_config import get_logger
from src.models.copy_trading import (
    RiskLimits,
    CopyTradeAction,
    LeaderTradeEvent,
    CopyTradeStatus
)

logger = get_logger("risk_controller")


@dataclass
class RiskValidationResult:
    """風控驗證結果"""
    is_valid: bool
    reason: str
    adjusted_quantity: Optional[float] = None  # 調整後的數量 (可能因風控降低)
    risk_score: float = 0.0  # 風險評分 0-100


@dataclass
class PositionInfo:
    """持倉資訊"""
    symbol: str
    quantity: float
    value: float  # USDC 價值
    side: str  # LONG or SHORT
    entry_price: float
    unrealized_pnl: float = 0.0


class RiskController:
    """
    Copy Trading 風險控制器

    功能:
    - 單筆交易金額限制
    - 每日最大虧損限制
    - 最大持倉數量限制
    - 最大持倉總值限制
    - 單一持倉集中度限制
    """

    def __init__(self, follower_id: str, limits: RiskLimits):
        """
        初始化風控控制器

        Args:
            follower_id: Follower 用戶 ID
            limits: 風控限制配置
        """
        self.follower_id = follower_id
        self.limits = limits

        # 每日統計 (UTC 時間)
        self._daily_stats = {
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "total_loss": Decimal("0"),
            "total_profit": Decimal("0"),
            "trades_count": 0,
            "trades": []  # 當日交易記錄
        }

        # 當前持倉追蹤
        self._positions: Dict[str, PositionInfo] = {}
        self._positions_lock = asyncio.Lock()

        # 每日重置任務
        self._reset_task: Optional[asyncio.Task] = None

    async def start(self):
        """啟動風控控制器 (包括每日重置排程)"""
        self._reset_task = asyncio.create_task(self._daily_reset_loop())
        logger.info(f"Follower {self.follower_id}: 風控控制器已啟動")

    async def stop(self):
        """停止風控控制器"""
        if self._reset_task:
            self._reset_task.cancel()
            try:
                await self._reset_task
            except asyncio.CancelledError:
                pass
        logger.info(f"Follower {self.follower_id}: 風控控制器已停止")

    async def validate_trade(
        self,
        trade_event: LeaderTradeEvent,
        copy_ratio: float,
        current_price: Optional[float] = None
    ) -> RiskValidationResult:
        """
        驗證跟單交易是否符合風控限制

        Args:
            trade_event: Leader 的交易事件
            copy_ratio: 跟單比例
            current_price: 當前價格 (用於計算價值)

        Returns:
            RiskValidationResult 驗證結果
        """
        # 檢查是否需要重置每日統計
        await self._check_daily_reset()

        # 計算跟單數量和價值
        follower_quantity = trade_event.quantity * copy_ratio
        price = current_price or trade_event.price
        trade_value = follower_quantity * price

        risk_score = 0.0
        adjusted_quantity = follower_quantity

        # 1. 檢查單筆金額限制
        if trade_value > self.limits.max_per_trade_amount:
            # 可以選擇拒絕或調整數量
            adjusted_quantity = self.limits.max_per_trade_amount / price
            adjusted_value = adjusted_quantity * price

            if adjusted_quantity < follower_quantity * 0.1:
                # 如果調整後數量太小 (< 10% 原本)，拒絕交易
                return RiskValidationResult(
                    is_valid=False,
                    reason=f"交易金額 {trade_value:.2f} USDC 超過單筆限制 {self.limits.max_per_trade_amount:.2f} USDC",
                    risk_score=100.0
                )

            logger.warning(
                f"Follower {self.follower_id}: 交易金額超限，已調整數量 {follower_quantity:.6f} -> {adjusted_quantity:.6f}"
            )
            risk_score += 30

        # 2. 檢查每日虧損限制
        daily_loss_check = await self._check_daily_loss_limit()
        if not daily_loss_check:
            return RiskValidationResult(
                is_valid=False,
                reason=f"已達到每日最大虧損限制 {self.limits.daily_max_loss:.2f} USDC",
                risk_score=100.0
            )

        # 計算距離每日虧損限制的餘量
        remaining_loss_allowance = self.limits.daily_max_loss - float(self._daily_stats["total_loss"])
        if remaining_loss_allowance < self.limits.daily_max_loss * 0.2:
            risk_score += 40  # 接近每日虧損限制

        # 3. 檢查持倉數量限制 (僅開倉/加倉時檢查)
        if trade_event.action in [CopyTradeAction.OPEN, CopyTradeAction.ADD]:
            async with self._positions_lock:
                current_position_count = len(self._positions)

            if current_position_count >= self.limits.max_position_count:
                # 如果是對現有持倉加倉，允許
                if trade_event.symbol not in self._positions:
                    return RiskValidationResult(
                        is_valid=False,
                        reason=f"已達到最大持倉數量限制 {self.limits.max_position_count}",
                        risk_score=100.0
                    )
            elif current_position_count >= self.limits.max_position_count * 0.8:
                risk_score += 20  # 接近持倉數量限制

        # 4. 檢查持倉總值限制
        async with self._positions_lock:
            current_total_value = sum(p.value for p in self._positions.values())

        if trade_event.action in [CopyTradeAction.OPEN, CopyTradeAction.ADD]:
            new_total_value = current_total_value + (adjusted_quantity * price)

            if new_total_value > self.limits.max_position_value:
                # 調整數量使其不超過限制
                available_value = self.limits.max_position_value - current_total_value
                if available_value <= 0:
                    return RiskValidationResult(
                        is_valid=False,
                        reason=f"已達到最大持倉總值限制 {self.limits.max_position_value:.2f} USDC",
                        risk_score=100.0
                    )

                adjusted_quantity = min(adjusted_quantity, available_value / price)
                risk_score += 25

        # 5. 檢查單一持倉集中度
        if trade_event.action in [CopyTradeAction.OPEN, CopyTradeAction.ADD]:
            async with self._positions_lock:
                symbol_value = self._positions.get(trade_event.symbol, PositionInfo(
                    symbol=trade_event.symbol, quantity=0, value=0, side="", entry_price=0
                )).value

            new_symbol_value = symbol_value + (adjusted_quantity * price)
            new_total_value = current_total_value + (adjusted_quantity * price)

            if new_total_value > 0:
                concentration = new_symbol_value / new_total_value
                if concentration > self.limits.max_single_position_ratio:
                    # 調整數量以符合集中度限制
                    max_symbol_value = new_total_value * self.limits.max_single_position_ratio
                    max_additional_value = max_symbol_value - symbol_value

                    if max_additional_value <= 0:
                        return RiskValidationResult(
                            is_valid=False,
                            reason=f"持倉集中度 {concentration:.1%} 超過限制 {self.limits.max_single_position_ratio:.1%}",
                            risk_score=100.0
                        )

                    adjusted_quantity = min(adjusted_quantity, max_additional_value / price)
                    risk_score += 15

        # 驗證通過
        return RiskValidationResult(
            is_valid=True,
            reason="風控驗證通過",
            adjusted_quantity=adjusted_quantity if adjusted_quantity != follower_quantity else None,
            risk_score=min(risk_score, 99.0)
        )

    async def _check_daily_loss_limit(self) -> bool:
        """
        檢查是否已達到每日虧損限制

        Returns:
            True 如果未達到限制，False 如果已達到
        """
        return float(self._daily_stats["total_loss"]) < self.limits.daily_max_loss

    async def _check_daily_reset(self):
        """檢查是否需要重置每日統計"""
        current_date = datetime.utcnow().strftime("%Y-%m-%d")
        if self._daily_stats["date"] != current_date:
            await self.reset_daily_limits()

    async def reset_daily_limits(self):
        """重置每日統計"""
        current_date = datetime.utcnow().strftime("%Y-%m-%d")

        logger.info(
            f"Follower {self.follower_id}: 重置每日統計",
            event_type="risk_daily_reset",
            data={
                "follower_id": self.follower_id,
                "previous_date": self._daily_stats["date"],
                "previous_loss": float(self._daily_stats["total_loss"]),
                "previous_profit": float(self._daily_stats["total_profit"]),
                "trades_count": self._daily_stats["trades_count"]
            }
        )

        self._daily_stats = {
            "date": current_date,
            "total_loss": Decimal("0"),
            "total_profit": Decimal("0"),
            "trades_count": 0,
            "trades": []
        }

    async def _daily_reset_loop(self):
        """每日重置循環"""
        while True:
            try:
                # 計算到下一個 UTC 00:00 的秒數
                now = datetime.utcnow()
                tomorrow = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                seconds_until_reset = (tomorrow - now).total_seconds()

                await asyncio.sleep(seconds_until_reset)
                await self.reset_daily_limits()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Follower {self.follower_id}: 每日重置失敗: {e}")
                await asyncio.sleep(60)  # 1分鐘後重試

    async def record_trade_result(
        self,
        symbol: str,
        quantity: float,
        price: float,
        side: str,
        action: CopyTradeAction,
        pnl: Optional[float] = None
    ):
        """
        記錄交易結果

        Args:
            symbol: 交易對
            quantity: 數量
            price: 價格
            side: 方向
            action: 交易動作
            pnl: 盈虧 (平倉時)
        """
        trade_value = quantity * price

        # 更新每日統計
        self._daily_stats["trades_count"] += 1
        self._daily_stats["trades"].append({
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "side": side,
            "action": action.value,
            "value": trade_value,
            "pnl": pnl,
            "timestamp": datetime.utcnow().isoformat()
        })

        if pnl is not None:
            if pnl < 0:
                self._daily_stats["total_loss"] += Decimal(str(abs(pnl)))
            else:
                self._daily_stats["total_profit"] += Decimal(str(pnl))

        # 更新持倉
        async with self._positions_lock:
            if action == CopyTradeAction.OPEN:
                self._positions[symbol] = PositionInfo(
                    symbol=symbol,
                    quantity=quantity,
                    value=trade_value,
                    side="LONG" if side == "BUY" else "SHORT",
                    entry_price=price
                )

            elif action == CopyTradeAction.ADD:
                if symbol in self._positions:
                    pos = self._positions[symbol]
                    # 計算加權平均入場價
                    total_qty = pos.quantity + quantity
                    avg_price = (pos.entry_price * pos.quantity + price * quantity) / total_qty
                    self._positions[symbol] = PositionInfo(
                        symbol=symbol,
                        quantity=total_qty,
                        value=total_qty * avg_price,
                        side=pos.side,
                        entry_price=avg_price
                    )
                else:
                    # 如果不存在，視為開倉
                    self._positions[symbol] = PositionInfo(
                        symbol=symbol,
                        quantity=quantity,
                        value=trade_value,
                        side="LONG" if side == "BUY" else "SHORT",
                        entry_price=price
                    )

            elif action == CopyTradeAction.REDUCE:
                if symbol in self._positions:
                    pos = self._positions[symbol]
                    new_qty = max(0, pos.quantity - quantity)
                    if new_qty > 0:
                        self._positions[symbol] = PositionInfo(
                            symbol=symbol,
                            quantity=new_qty,
                            value=new_qty * pos.entry_price,
                            side=pos.side,
                            entry_price=pos.entry_price,
                            unrealized_pnl=pos.unrealized_pnl
                        )
                    else:
                        del self._positions[symbol]

            elif action == CopyTradeAction.CLOSE:
                if symbol in self._positions:
                    del self._positions[symbol]

        logger.debug(
            f"Follower {self.follower_id}: 記錄交易結果",
            event_type="risk_trade_recorded",
            data={
                "symbol": symbol,
                "action": action.value,
                "pnl": pnl,
                "daily_loss": float(self._daily_stats["total_loss"]),
                "position_count": len(self._positions)
            }
        )

    async def update_position_pnl(self, symbol: str, current_price: float):
        """
        更新持倉的未實現盈虧

        Args:
            symbol: 交易對
            current_price: 當前價格
        """
        async with self._positions_lock:
            if symbol in self._positions:
                pos = self._positions[symbol]
                if pos.side == "LONG":
                    pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity
                pos.value = pos.quantity * current_price

    async def sync_positions(self, positions: List[Dict[str, Any]]):
        """
        從 API 同步持倉數據

        Args:
            positions: 持倉列表
        """
        async with self._positions_lock:
            self._positions.clear()
            for pos_data in positions:
                symbol = pos_data.get("symbol", "")
                quantity = float(pos_data.get("position_qty", 0) or 0)
                if quantity == 0:
                    continue

                entry_price = float(pos_data.get("average_open_price", 0) or 0)
                unrealized_pnl = float(pos_data.get("unsettled_pnl", 0) or 0)

                self._positions[symbol] = PositionInfo(
                    symbol=symbol,
                    quantity=abs(quantity),
                    value=abs(quantity) * entry_price,
                    side="LONG" if quantity > 0 else "SHORT",
                    entry_price=entry_price,
                    unrealized_pnl=unrealized_pnl
                )

        logger.info(f"Follower {self.follower_id}: 已同步 {len(self._positions)} 個持倉")

    def get_risk_status(self) -> Dict[str, Any]:
        """
        獲取風控狀態

        Returns:
            風控狀態字典
        """
        total_position_value = sum(p.value for p in self._positions.values())

        return {
            "follower_id": self.follower_id,
            "limits": {
                "max_per_trade_amount": self.limits.max_per_trade_amount,
                "daily_max_loss": self.limits.daily_max_loss,
                "max_position_count": self.limits.max_position_count,
                "max_position_value": self.limits.max_position_value,
                "max_single_position_ratio": self.limits.max_single_position_ratio
            },
            "current_status": {
                "daily_loss": float(self._daily_stats["total_loss"]),
                "daily_profit": float(self._daily_stats["total_profit"]),
                "daily_trades_count": self._daily_stats["trades_count"],
                "position_count": len(self._positions),
                "total_position_value": total_position_value,
                "daily_loss_remaining": self.limits.daily_max_loss - float(self._daily_stats["total_loss"])
            },
            "utilization": {
                "daily_loss_pct": float(self._daily_stats["total_loss"]) / self.limits.daily_max_loss * 100 if self.limits.daily_max_loss > 0 else 0,
                "position_count_pct": len(self._positions) / self.limits.max_position_count * 100 if self.limits.max_position_count > 0 else 0,
                "position_value_pct": total_position_value / self.limits.max_position_value * 100 if self.limits.max_position_value > 0 else 0
            },
            "positions": {
                symbol: {
                    "quantity": pos.quantity,
                    "value": pos.value,
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "unrealized_pnl": pos.unrealized_pnl
                }
                for symbol, pos in self._positions.items()
            }
        }
