#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Copy Trading Bot - Follower 跟單執行引擎
接收 Leader 的交易信號並執行跟單
"""

import asyncio
import time
from datetime import datetime
from typing import Dict, Any, Optional, Callable, List
from decimal import Decimal
from src.core.client import OrderlyClient
from src.core.risk_controller import RiskController
from src.core.leader_monitor import LeaderMonitor
from src.utils.logging_config import get_logger
from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeRecord,
    CopyTradeStatus,
    CopyTradeAction,
    CopyOrderType,
    CopyOrderSide,
    RiskLimits,
    FollowerConfig,
    FollowerStatistics,
    CopyTradeResult
)

logger = get_logger("copy_trading_bot")


class CopyTradingBot:
    """
    Copy Trading Bot - 負責 Follower 的跟單執行

    功能:
    - 接收 Leader 的交易事件
    - 風控驗證
    - 執行跟單交易
    - 記錄交易結果
    - 統計追蹤
    """

    def __init__(
        self,
        follower_id: str,
        orderly_key: str,
        orderly_secret: str,
        orderly_testnet: bool = True
    ):
        """
        初始化 CopyTradingBot

        Args:
            follower_id: Follower 用戶 ID
            orderly_key: Follower 的 Orderly API Key
            orderly_secret: Follower 的 Orderly API Secret
            orderly_testnet: 是否使用測試網
        """
        self.follower_id = follower_id
        self.orderly_testnet = orderly_testnet

        # 創建 Orderly 客戶端
        self.client = OrderlyClient(
            account_id=follower_id,
            orderly_key=orderly_key,
            orderly_secret=orderly_secret,
            orderly_testnet=orderly_testnet
        )

        # 跟單配置 (將在 start 時設置)
        self.leader_id: Optional[str] = None
        self.copy_ratio: float = 1.0
        self.risk_limits: Optional[RiskLimits] = None

        # 風控控制器 (將在 start 時創建)
        self.risk_controller: Optional[RiskController] = None

        # 狀態
        self.is_running = False
        self._stop_event = asyncio.Event()

        # 統計
        self.statistics = FollowerStatistics()
        self._start_time: Optional[datetime] = None

        # 交易記錄
        self._trade_records: List[CopyTradeRecord] = []
        self._max_trade_records = 1000

        # SSE 事件回調 (用於實時推送)
        self._event_callbacks: List[Callable[[Dict[str, Any]], Any]] = []

        # 執行鎖 (避免並發執行同一筆交易)
        self._execution_lock = asyncio.Lock()

    async def start(
        self,
        leader_id: str,
        copy_ratio: float,
        risk_limits: RiskLimits
    ) -> bool:
        """
        啟動跟單

        Args:
            leader_id: 要跟隨的 Leader ID
            copy_ratio: 跟單比例
            risk_limits: 風控限制

        Returns:
            是否啟動成功
        """
        if self.is_running:
            logger.warning(f"Follower {self.follower_id}: 已在跟單中")
            return False

        try:
            self.leader_id = leader_id
            self.copy_ratio = copy_ratio
            self.risk_limits = risk_limits

            # 創建並啟動風控控制器
            self.risk_controller = RiskController(self.follower_id, risk_limits)
            await self.risk_controller.start()

            # 同步當前持倉
            await self._sync_positions()

            self.is_running = True
            self._stop_event.clear()
            self._start_time = datetime.utcnow()

            logger.info(
                f"Follower {self.follower_id}: 開始跟隨 Leader {leader_id}",
                event_type="copy_trading_started",
                data={
                    "follower_id": self.follower_id,
                    "leader_id": leader_id,
                    "copy_ratio": copy_ratio,
                    "risk_limits": {
                        "max_per_trade": risk_limits.max_per_trade_amount,
                        "daily_max_loss": risk_limits.daily_max_loss,
                        "max_positions": risk_limits.max_position_count
                    }
                }
            )

            return True

        except Exception as e:
            logger.error(f"Follower {self.follower_id}: 啟動跟單失敗: {e}")
            return False

    async def stop(self) -> bool:
        """
        停止跟單

        Returns:
            是否停止成功
        """
        if not self.is_running:
            return True

        logger.info(f"Follower {self.follower_id}: 停止跟單")

        self._stop_event.set()
        self.is_running = False

        # 停止風控控制器
        if self.risk_controller:
            await self.risk_controller.stop()

        # 發送停止事件
        await self._emit_event({
            "type": "copy_trading_stopped",
            "follower_id": self.follower_id,
            "leader_id": self.leader_id,
            "statistics": self.statistics.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        })

        logger.info(
            f"Follower {self.follower_id}: 跟單已停止",
            event_type="copy_trading_stopped",
            data={
                "follower_id": self.follower_id,
                "leader_id": self.leader_id,
                "total_trades": self.statistics.total_trades,
                "total_profit": self.statistics.total_profit
            }
        )

        return True

    async def handle_leader_trade(self, event: LeaderTradeEvent) -> CopyTradeResult:
        """
        處理 Leader 的交易事件 (被 LeaderMonitor 調用)

        Args:
            event: Leader 的交易事件

        Returns:
            跟單執行結果
        """
        if not self.is_running:
            return CopyTradeResult(
                success=False,
                follower_id=self.follower_id,
                leader_order_id=event.order_id,
                status=CopyTradeStatus.SKIPPED,
                error_message="跟單已停止"
            )

        async with self._execution_lock:
            start_time = time.time()

            try:
                # 1. 風控驗證
                validation = await self.risk_controller.validate_trade(
                    event,
                    self.copy_ratio
                )

                if not validation.is_valid:
                    self.statistics.skipped_trades += 1
                    self.statistics.total_trades += 1

                    # 記錄跳過的交易
                    trade_record = self._create_trade_record(
                        event,
                        status=CopyTradeStatus.SKIPPED,
                        failure_reason=validation.reason
                    )
                    self._add_trade_record(trade_record)

                    await self._emit_event({
                        "type": "copy_trade_skipped",
                        "follower_id": self.follower_id,
                        "leader_order_id": event.order_id,
                        "symbol": event.symbol,
                        "reason": validation.reason,
                        "risk_score": validation.risk_score,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                    return CopyTradeResult(
                        success=False,
                        follower_id=self.follower_id,
                        leader_order_id=event.order_id,
                        status=CopyTradeStatus.SKIPPED,
                        error_message=validation.reason
                    )

                # 2. 計算跟單數量
                follower_quantity = event.quantity * self.copy_ratio
                if validation.adjusted_quantity:
                    follower_quantity = validation.adjusted_quantity

                # 3. 執行跟單交易
                result = await self._execute_copy_trade(event, follower_quantity)

                # 4. 記錄結果
                latency_ms = int((time.time() - start_time) * 1000)
                result.latency_ms = latency_ms

                if result.success:
                    self.statistics.successful_trades += 1

                    # 更新風控記錄
                    await self.risk_controller.record_trade_result(
                        symbol=event.symbol,
                        quantity=follower_quantity,
                        price=result.executed_price or event.price,
                        side=event.side.value,
                        action=event.action
                    )

                    await self._emit_event({
                        "type": "copy_trade_executed",
                        "follower_id": self.follower_id,
                        "leader_order_id": event.order_id,
                        "follower_order_id": result.follower_order_id,
                        "symbol": event.symbol,
                        "side": event.side.value,
                        "quantity": follower_quantity,
                        "price": result.executed_price,
                        "latency_ms": latency_ms,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                else:
                    self.statistics.failed_trades += 1

                    await self._emit_event({
                        "type": "copy_trade_failed",
                        "follower_id": self.follower_id,
                        "leader_order_id": event.order_id,
                        "symbol": event.symbol,
                        "error": result.error_message,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                self.statistics.total_trades += 1

                # 記錄交易
                trade_record = self._create_trade_record(
                    event,
                    status=result.status,
                    failure_reason=result.error_message,
                    follower_order_id=result.follower_order_id,
                    follower_quantity=follower_quantity,
                    follower_price=result.executed_price,
                    latency_ms=latency_ms
                )
                self._add_trade_record(trade_record)

                return result

            except Exception as e:
                logger.error(f"Follower {self.follower_id}: 處理 Leader 交易失敗: {e}")
                self.statistics.failed_trades += 1
                self.statistics.total_trades += 1

                return CopyTradeResult(
                    success=False,
                    follower_id=self.follower_id,
                    leader_order_id=event.order_id,
                    status=CopyTradeStatus.FAILED,
                    error_message=str(e)
                )

    async def _execute_copy_trade(
        self,
        event: LeaderTradeEvent,
        quantity: float
    ) -> CopyTradeResult:
        """
        執行跟單交易

        Args:
            event: Leader 的交易事件
            quantity: 要執行的數量

        Returns:
            執行結果
        """
        try:
            # 根據訂單類型執行
            if event.order_type == CopyOrderType.MARKET:
                response = await self.client.create_market_order(
                    symbol=event.symbol,
                    side=event.side.value,
                    order_quantity=quantity
                )
            else:
                # 限價單：使用 Leader 的價格
                response = await self.client.create_limit_order(
                    symbol=event.symbol,
                    side=event.side.value,
                    order_price=event.price,
                    order_quantity=quantity
                )

            if response and response.get("success"):
                order_data = response.get("data", {})
                return CopyTradeResult(
                    success=True,
                    follower_id=self.follower_id,
                    leader_order_id=event.order_id,
                    follower_order_id=str(order_data.get("order_id", "")),
                    status=CopyTradeStatus.EXECUTED,
                    executed_price=float(order_data.get("price", event.price)),
                    executed_quantity=quantity
                )
            else:
                error_msg = response.get("message", "Unknown error") if response else "No response"
                return CopyTradeResult(
                    success=False,
                    follower_id=self.follower_id,
                    leader_order_id=event.order_id,
                    status=CopyTradeStatus.FAILED,
                    error_message=f"API 錯誤: {error_msg}"
                )

        except Exception as e:
            return CopyTradeResult(
                success=False,
                follower_id=self.follower_id,
                leader_order_id=event.order_id,
                status=CopyTradeStatus.FAILED,
                error_message=str(e)
            )

    def _create_trade_record(
        self,
        event: LeaderTradeEvent,
        status: CopyTradeStatus,
        failure_reason: Optional[str] = None,
        follower_order_id: Optional[str] = None,
        follower_quantity: Optional[float] = None,
        follower_price: Optional[float] = None,
        latency_ms: Optional[int] = None
    ) -> CopyTradeRecord:
        """創建交易記錄"""
        record = CopyTradeRecord(
            leader_id=event.leader_id,
            follower_id=self.follower_id,
            leader_order_id=event.order_id,
            follower_order_id=follower_order_id,
            symbol=event.symbol,
            action=event.action,
            order_type=event.order_type,
            side=event.side,
            leader_price=event.price,
            leader_quantity=event.quantity,
            follower_price=follower_price,
            follower_quantity=follower_quantity or (event.quantity * self.copy_ratio),
            copy_ratio=self.copy_ratio,
            status=status,
            failure_reason=failure_reason,
            leader_timestamp=event.timestamp,
            follower_timestamp=datetime.utcnow() if status == CopyTradeStatus.EXECUTED else None,
            latency_ms=latency_ms
        )

        # 計算滑點
        if follower_price and event.price:
            record.calculate_slippage()

        return record

    def _add_trade_record(self, record: CopyTradeRecord):
        """添加交易記錄"""
        self._trade_records.append(record)

        # 限制記錄數量
        if len(self._trade_records) > self._max_trade_records:
            self._trade_records = self._trade_records[-(self._max_trade_records // 2):]

    async def _sync_positions(self):
        """同步當前持倉到風控控制器"""
        try:
            response = await self.client.get_positions()
            if response and response.get("success"):
                positions = response.get("data", {}).get("rows", [])
                await self.risk_controller.sync_positions(positions)
        except Exception as e:
            logger.warning(f"Follower {self.follower_id}: 同步持倉失敗: {e}")

    def register_event_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        """註冊事件回調 (用於 SSE 推送)"""
        if callback not in self._event_callbacks:
            self._event_callbacks.append(callback)

    def unregister_event_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        """取消註冊事件回調"""
        if callback in self._event_callbacks:
            self._event_callbacks.remove(callback)

    async def _emit_event(self, event: Dict[str, Any]):
        """發送事件給所有回調"""
        for callback in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Follower {self.follower_id}: 發送事件回調失敗: {e}")

    async def get_status(self) -> Dict[str, Any]:
        """獲取跟單狀態"""
        risk_status = self.risk_controller.get_risk_status() if self.risk_controller else {}

        return {
            "follower_id": self.follower_id,
            "leader_id": self.leader_id,
            "is_running": self.is_running,
            "copy_ratio": self.copy_ratio,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "statistics": {
                "total_trades": self.statistics.total_trades,
                "successful_trades": self.statistics.successful_trades,
                "failed_trades": self.statistics.failed_trades,
                "skipped_trades": self.statistics.skipped_trades,
                "success_rate": self.statistics.success_rate,
                "total_profit": self.statistics.total_profit,
                "total_slippage": self.statistics.total_slippage
            },
            "risk_status": risk_status,
            "recent_trades": [
                {
                    "symbol": r.symbol,
                    "side": r.side.value,
                    "status": r.status.value,
                    "leader_quantity": r.leader_quantity,
                    "follower_quantity": r.follower_quantity,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at.isoformat()
                }
                for r in self._trade_records[-10:]  # 最近10筆
            ]
        }

    def get_trade_history(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """
        獲取交易歷史

        Args:
            limit: 返回數量限制
            offset: 偏移量

        Returns:
            交易記錄列表
        """
        records = self._trade_records[-(offset + limit):len(self._trade_records) - offset if offset else None]
        return [r.model_dump() for r in reversed(records[:limit])]
