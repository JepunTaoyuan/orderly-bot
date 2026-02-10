#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Copy Trading Session Manager
管理 Copy Trading 的所有會話，包括 Leader 監控和 Follower 跟單
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from src.core.leader_monitor import LeaderMonitor
from src.core.copy_trading_bot import CopyTradingBot
from src.core.risk_controller import RiskController
from src.utils.mongo_manager import MongoManager
from src.services.database_connection import db_manager
from src.utils.logging_config import get_logger
from src.utils.error_codes import ErrorCode, GridTradingException
from src.models.copy_trading import (
    TradingMode,
    LeaderStatus,
    LeaderProfile,
    FollowerConfig,
    RiskLimits,
    LeaderTradeEvent,
    CopyTradeRecord,
    CopyTradeStatus
)

logger = get_logger("copy_trading_service")


class CopyTradingSessionManager:
    """
    Copy Trading 會話管理器

    功能:
    - 管理 Leader 申請、審核、激活
    - 管理 LeaderMonitor 實例 (一個 Leader 一個 Monitor)
    - 管理 CopyTradingBot 實例 (每個 Follower 一個 Bot)
    - 處理 Leader-Follower 關係映射
    - 協調與 SessionManager 的互斥機制
    """

    def __init__(self):
        """初始化 CopyTradingSessionManager"""
        # MongoDB 管理器 (將在 initialize 時設置)
        self.mongo_manager: Optional[MongoManager] = None

        # Leader Monitors: leader_id -> LeaderMonitor
        self._leader_monitors: Dict[str, LeaderMonitor] = {}
        self._leader_monitors_lock = asyncio.Lock()

        # Follower Bots: follower_id -> CopyTradingBot
        self._follower_bots: Dict[str, CopyTradingBot] = {}
        self._follower_bots_lock = asyncio.Lock()

        # Leader -> Followers 映射: leader_id -> Set[follower_id]
        self._leader_followers: Dict[str, set] = {}

        # SSE 事件回調 (用於推送狀態更新)
        self._event_callbacks: Dict[str, List[Callable[[Dict[str, Any]], Any]]] = {}

        # 統計
        self._stats = {
            "total_leaders": 0,
            "active_leaders": 0,
            "total_followers": 0,
            "active_followers": 0,
            "total_trades_processed": 0
        }

        # SessionManager 引用 (用於交易模式互斥)
        self._session_manager = None

        logger.info("CopyTradingSessionManager 已初始化")

    async def initialize(self, session_manager=None):
        """
        初始化 CopyTradingSessionManager

        Args:
            session_manager: SessionManager 實例，用於交易模式互斥檢查
        """
        if self.mongo_manager is None:
            self.mongo_manager = await db_manager.get_mongo_manager()

        self._session_manager = session_manager

        # 確保 MongoDB collections 存在
        await self._ensure_collections()

        logger.info("CopyTradingSessionManager 初始化完成")

    async def _ensure_collections(self):
        """確保必要的 MongoDB collections 存在"""
        try:
            db = self.mongo_manager.db

            # 確保 copy_followers collection 存在並創建索引
            copy_followers = db['copy_followers']
            await copy_followers.create_index("follower_id", unique=True)
            await copy_followers.create_index("leader_id")
            await copy_followers.create_index("is_active")

            # 確保 copy_trades collection 存在並創建索引
            copy_trades = db['copy_trades']
            await copy_trades.create_index("follower_id")
            await copy_trades.create_index("leader_id")
            await copy_trades.create_index([("created_at", -1)])
            await copy_trades.create_index([("follower_id", 1), ("created_at", -1)])

            logger.info("Copy Trading collections 已確認")

        except Exception as e:
            logger.error(f"確保 collections 時發生錯誤: {e}")

    # ============== Leader 管理 ==============

    async def register_leader(self, user_id: str) -> Dict[str, Any]:
        """
        申請成為 Leader

        Args:
            user_id: 用戶 ID

        Returns:
            申請結果
        """
        try:
            # 檢查用戶是否存在
            user = await self.mongo_manager.get_user(user_id)
            if not user:
                raise GridTradingException(
                    error_code=ErrorCode.USER_NOT_FOUND,
                    details={"user_id": user_id}
                )

            # 檢查是否已經是 Leader 或正在申請中
            current_status = user.get("leader_status", LeaderStatus.NONE.value)
            if current_status == LeaderStatus.APPROVED.value:
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_ALREADY_REGISTERED,
                    details={"user_id": user_id}
                )

            if current_status == LeaderStatus.PENDING.value:
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_ALREADY_PENDING,
                    details={"user_id": user_id}
                )

            # 檢查是否有 API Key
            if not user.get("api_key") or not user.get("api_secret"):
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_MISSING_API_KEY,
                    details={"user_id": user_id}
                )

            # 更新用戶狀態為待審核
            await self.mongo_manager.users.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "leader_status": LeaderStatus.PENDING.value,
                        "leader_applied_at": datetime.utcnow()
                    }
                }
            )

            logger.info(
                f"用戶 {user_id} 申請成為 Leader",
                event_type="leader_registration",
                data={"user_id": user_id}
            )

            return {
                "success": True,
                "user_id": user_id,
                "status": LeaderStatus.PENDING.value,
                "message": "申請已提交，等待審核"
            }

        except GridTradingException:
            raise
        except Exception as e:
            logger.error(f"申請成為 Leader 失敗: {e}")
            raise GridTradingException(
                error_code=ErrorCode.COPY_TRADING_ERROR,
                details={"user_id": user_id, "error": str(e)},
                original_error=e
            )

    async def approve_leader(self, user_id: str, admin_id: str) -> Dict[str, Any]:
        """
        審核通過 Leader 申請 (管理員操作)

        Args:
            user_id: 申請者 ID
            admin_id: 審核管理員 ID

        Returns:
            審核結果
        """
        try:
            user = await self.mongo_manager.get_user(user_id)
            if not user:
                raise GridTradingException(
                    error_code=ErrorCode.USER_NOT_FOUND,
                    details={"user_id": user_id}
                )

            current_status = user.get("leader_status", LeaderStatus.NONE.value)
            if current_status != LeaderStatus.PENDING.value:
                raise GridTradingException(
                    error_code=ErrorCode.COPY_TRADING_ERROR,
                    details={
                        "user_id": user_id,
                        "current_status": current_status,
                        "message": "用戶不在待審核狀態"
                    }
                )

            # 更新為已審核通過
            await self.mongo_manager.users.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "is_leader": True,
                        "leader_status": LeaderStatus.APPROVED.value,
                        "leader_approved_by": admin_id,
                        "leader_approved_at": datetime.utcnow(),
                        "leader_is_active": False,  # 需要 Leader 手動開啟
                        "leader_statistics": {
                            "follower_count": 0,
                            "total_trades": 0,
                            "win_rate": 0.0
                        }
                    }
                }
            )

            self._stats["total_leaders"] += 1

            logger.info(
                f"Leader 申請已通過: {user_id}",
                event_type="leader_approved",
                data={"user_id": user_id, "admin_id": admin_id}
            )

            return {
                "success": True,
                "user_id": user_id,
                "status": LeaderStatus.APPROVED.value,
                "message": "審核通過"
            }

        except GridTradingException:
            raise
        except Exception as e:
            logger.error(f"審核 Leader 失敗: {e}")
            raise

    async def reject_leader(self, user_id: str, admin_id: str, reason: str = "") -> Dict[str, Any]:
        """
        拒絕 Leader 申請 (管理員操作)

        Args:
            user_id: 申請者 ID
            admin_id: 審核管理員 ID
            reason: 拒絕原因

        Returns:
            審核結果
        """
        try:
            user = await self.mongo_manager.get_user(user_id)
            if not user:
                raise GridTradingException(
                    error_code=ErrorCode.USER_NOT_FOUND,
                    details={"user_id": user_id}
                )

            await self.mongo_manager.users.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "is_leader": False,
                        "leader_status": LeaderStatus.REJECTED.value,
                        "leader_rejected_by": admin_id,
                        "leader_rejected_at": datetime.utcnow(),
                        "leader_rejection_reason": reason
                    }
                }
            )

            logger.info(
                f"Leader 申請被拒絕: {user_id}",
                event_type="leader_rejected",
                data={"user_id": user_id, "admin_id": admin_id, "reason": reason}
            )

            return {
                "success": True,
                "user_id": user_id,
                "status": LeaderStatus.REJECTED.value,
                "message": f"申請已拒絕: {reason}" if reason else "申請已拒絕"
            }

        except GridTradingException:
            raise
        except Exception as e:
            logger.error(f"拒絕 Leader 失敗: {e}")
            raise

    async def activate_leader(self, user_id: str) -> Dict[str, Any]:
        """
        激活 Leader (開放接受 Followers)

        Args:
            user_id: Leader ID

        Returns:
            激活結果
        """
        try:
            user = await self.mongo_manager.get_user(user_id)
            if not user:
                raise GridTradingException(
                    error_code=ErrorCode.USER_NOT_FOUND,
                    details={"user_id": user_id}
                )

            if not user.get("is_leader"):
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_NOT_FOUND,
                    details={"user_id": user_id}
                )

            # 檢查交易模式衝突
            if self._session_manager:
                if await self._session_manager.check_trading_mode_conflict(user_id, TradingMode.COPY_LEADER):
                    raise GridTradingException(
                        error_code=ErrorCode.TRADING_MODE_CONFLICT,
                        details={
                            "user_id": user_id,
                            "requested_mode": TradingMode.COPY_LEADER.value
                        }
                    )

            # 啟動 LeaderMonitor
            await self._start_leader_monitor(user_id, user)

            # 更新狀態
            await self.mongo_manager.users.update_one(
                {"_id": user_id},
                {"$set": {"leader_is_active": True}}
            )

            # 註冊交易模式
            if self._session_manager:
                await self._session_manager.register_trading_mode(user_id, TradingMode.COPY_LEADER)

            self._stats["active_leaders"] += 1

            logger.info(
                f"Leader 已激活: {user_id}",
                event_type="leader_activated",
                data={"user_id": user_id}
            )

            return {
                "success": True,
                "user_id": user_id,
                "is_active": True,
                "message": "Leader 已開始接受跟隨"
            }

        except GridTradingException:
            raise
        except Exception as e:
            logger.error(f"激活 Leader 失敗: {e}")
            raise

    async def deactivate_leader(self, user_id: str) -> Dict[str, Any]:
        """
        停用 Leader (停止接受新 Followers，現有 Followers 繼續)

        Args:
            user_id: Leader ID

        Returns:
            停用結果
        """
        try:
            # 停止 LeaderMonitor
            await self._stop_leader_monitor(user_id)

            # 更新狀態
            await self.mongo_manager.users.update_one(
                {"_id": user_id},
                {"$set": {"leader_is_active": False}}
            )

            # 取消交易模式註冊
            if self._session_manager:
                await self._session_manager.unregister_trading_mode(user_id, TradingMode.COPY_LEADER)

            self._stats["active_leaders"] = max(0, self._stats["active_leaders"] - 1)

            logger.info(
                f"Leader 已停用: {user_id}",
                event_type="leader_deactivated",
                data={"user_id": user_id}
            )

            return {
                "success": True,
                "user_id": user_id,
                "is_active": False,
                "message": "Leader 已停止接受跟隨"
            }

        except Exception as e:
            logger.error(f"停用 Leader 失敗: {e}")
            raise

    async def _start_leader_monitor(self, leader_id: str, user_data: Dict[str, Any]):
        """
        啟動 Leader 的交易監控

        Args:
            leader_id: Leader ID
            user_data: 用戶數據 (包含 API credentials)
        """
        async with self._leader_monitors_lock:
            if leader_id in self._leader_monitors:
                logger.warning(f"Leader {leader_id} 的 Monitor 已存在")
                return

            monitor = LeaderMonitor(leader_id)

            # 註冊交易回調
            monitor.register_trade_callback(
                lambda event: asyncio.create_task(self._on_leader_trade(event))
            )

            # 啟動監控
            success = await monitor.start_monitoring(
                orderly_key=user_data.get("api_key"),
                orderly_secret=user_data.get("api_secret"),
                orderly_testnet=os.getenv("ORDERLY_TESTNET", "true").lower() == "true"
            )

            if success:
                self._leader_monitors[leader_id] = monitor
                self._leader_followers[leader_id] = set()
                logger.info(f"Leader {leader_id} 的 Monitor 已啟動")
            else:
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_MONITOR_START_FAILED,
                    details={"leader_id": leader_id}
                )

    async def _stop_leader_monitor(self, leader_id: str):
        """
        停止 Leader 的交易監控

        Args:
            leader_id: Leader ID
        """
        async with self._leader_monitors_lock:
            if leader_id not in self._leader_monitors:
                return

            monitor = self._leader_monitors[leader_id]
            await monitor.stop_monitoring()
            del self._leader_monitors[leader_id]
            logger.info(f"Leader {leader_id} 的 Monitor 已停止")

    async def _on_leader_trade(self, event: LeaderTradeEvent):
        """
        處理 Leader 的交易事件

        Args:
            event: Leader 交易事件
        """
        leader_id = event.leader_id

        logger.info(
            f"收到 Leader {leader_id} 的交易事件",
            event_type="leader_trade_received",
            data={
                "leader_id": leader_id,
                "order_id": event.order_id,
                "symbol": event.symbol,
                "side": event.side.value,
                "quantity": event.quantity
            }
        )

        # 獲取該 Leader 的所有 Followers
        follower_ids = self._leader_followers.get(leader_id, set())

        if not follower_ids:
            logger.debug(f"Leader {leader_id} 沒有 Followers")
            return

        # 並發廣播給所有 Followers
        tasks = []
        async with self._follower_bots_lock:
            for follower_id in follower_ids:
                if follower_id in self._follower_bots:
                    bot = self._follower_bots[follower_id]
                    tasks.append(bot.handle_leader_trade(event))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 記錄交易結果
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Follower 處理交易失敗: {result}")
                else:
                    # 保存交易記錄到數據庫
                    await self._save_trade_record(result)

            self._stats["total_trades_processed"] += len(tasks)

    async def _save_trade_record(self, result):
        """保存交易記錄到數據庫"""
        try:
            if not hasattr(result, 'follower_id'):
                return

            db = self.mongo_manager.db
            copy_trades = db['copy_trades']

            record = {
                "follower_id": result.follower_id,
                "leader_order_id": result.leader_order_id,
                "follower_order_id": result.follower_order_id,
                "status": result.status.value if hasattr(result.status, 'value') else str(result.status),
                "error_message": result.error_message,
                "executed_price": result.executed_price,
                "executed_quantity": result.executed_quantity,
                "latency_ms": result.latency_ms,
                "created_at": datetime.utcnow()
            }

            await copy_trades.insert_one(record)

        except Exception as e:
            logger.error(f"保存交易記錄失敗: {e}")

    # ============== Follower 管理 ==============

    async def start_following(
        self,
        follower_id: str,
        leader_id: str,
        copy_ratio: float = 1.0,
        risk_limits: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        開始跟隨某個 Leader

        Args:
            follower_id: Follower 用戶 ID
            leader_id: 要跟隨的 Leader ID
            copy_ratio: 跟單比例 (0.1 - 10.0)
            risk_limits: 風控限制配置

        Returns:
            開始跟單結果
        """
        try:
            # 驗證 Follower
            follower_user = await self.mongo_manager.get_user(follower_id)
            if not follower_user:
                raise GridTradingException(
                    error_code=ErrorCode.USER_NOT_FOUND,
                    details={"user_id": follower_id}
                )

            # 檢查 Follower 是否有 API credentials
            if not follower_user.get("api_key") or not follower_user.get("api_secret"):
                raise GridTradingException(
                    error_code=ErrorCode.FOLLOWER_MISSING_API_KEY,
                    details={"follower_id": follower_id}
                )

            # 驗證 Leader
            leader_user = await self.mongo_manager.get_user(leader_id)
            if not leader_user or not leader_user.get("is_leader"):
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_NOT_FOUND,
                    details={"leader_id": leader_id}
                )

            if not leader_user.get("leader_is_active"):
                raise GridTradingException(
                    error_code=ErrorCode.LEADER_NOT_ACTIVE,
                    details={"leader_id": leader_id}
                )

            # 檢查是否已在跟單
            async with self._follower_bots_lock:
                if follower_id in self._follower_bots:
                    raise GridTradingException(
                        error_code=ErrorCode.FOLLOWER_ALREADY_FOLLOWING,
                        details={"follower_id": follower_id}
                    )

            # 檢查交易模式衝突
            if self._session_manager:
                if await self._session_manager.check_trading_mode_conflict(follower_id, TradingMode.COPY_FOLLOWER):
                    raise GridTradingException(
                        error_code=ErrorCode.TRADING_MODE_CONFLICT,
                        details={
                            "user_id": follower_id,
                            "requested_mode": TradingMode.COPY_FOLLOWER.value
                        }
                    )

            # 驗證 copy_ratio
            if not 0.1 <= copy_ratio <= 10.0:
                raise GridTradingException(
                    error_code=ErrorCode.INVALID_COPY_RATIO,
                    details={"copy_ratio": copy_ratio}
                )

            # 構建風控限制
            limits = RiskLimits(
                max_per_trade_amount=risk_limits.get("max_per_trade_amount", 1000.0) if risk_limits else 1000.0,
                daily_max_loss=risk_limits.get("daily_max_loss", 500.0) if risk_limits else 500.0,
                max_position_count=risk_limits.get("max_position_count", 10) if risk_limits else 10,
                max_position_value=risk_limits.get("max_position_value", 10000.0) if risk_limits else 10000.0,
                max_single_position_ratio=risk_limits.get("max_single_position_ratio", 0.3) if risk_limits else 0.3
            )

            # 創建 CopyTradingBot
            bot = CopyTradingBot(
                follower_id=follower_id,
                orderly_key=follower_user.get("api_key"),
                orderly_secret=follower_user.get("api_secret"),
                orderly_testnet=os.getenv("ORDERLY_TESTNET", "true").lower() == "true"
            )

            # 啟動跟單
            success = await bot.start(leader_id, copy_ratio, limits)
            if not success:
                raise GridTradingException(
                    error_code=ErrorCode.FOLLOWER_START_FAILED,
                    details={"follower_id": follower_id}
                )

            # 保存到內存和數據庫
            async with self._follower_bots_lock:
                self._follower_bots[follower_id] = bot

            # 添加到 Leader 的 Followers 列表
            if leader_id in self._leader_followers:
                self._leader_followers[leader_id].add(follower_id)

            # 保存到數據庫
            await self._save_follower_config(
                follower_id, leader_id, copy_ratio, limits
            )

            # 註冊交易模式
            if self._session_manager:
                await self._session_manager.register_trading_mode(follower_id, TradingMode.COPY_FOLLOWER)

            # 更新 Leader 的 follower_count
            await self.mongo_manager.users.update_one(
                {"_id": leader_id},
                {"$inc": {"leader_statistics.follower_count": 1}}
            )

            self._stats["total_followers"] += 1
            self._stats["active_followers"] += 1

            logger.info(
                f"Follower {follower_id} 開始跟隨 Leader {leader_id}",
                event_type="follower_started",
                data={
                    "follower_id": follower_id,
                    "leader_id": leader_id,
                    "copy_ratio": copy_ratio
                }
            )

            return {
                "success": True,
                "follower_id": follower_id,
                "leader_id": leader_id,
                "copy_ratio": copy_ratio,
                "risk_limits": limits.model_dump(),
                "message": "開始跟單成功"
            }

        except GridTradingException:
            raise
        except Exception as e:
            logger.error(f"開始跟單失敗: {e}")
            raise GridTradingException(
                error_code=ErrorCode.COPY_TRADING_ERROR,
                details={"error": str(e)},
                original_error=e
            )

    async def stop_following(self, follower_id: str) -> Dict[str, Any]:
        """
        停止跟單

        Args:
            follower_id: Follower 用戶 ID

        Returns:
            停止結果
        """
        try:
            async with self._follower_bots_lock:
                if follower_id not in self._follower_bots:
                    raise GridTradingException(
                        error_code=ErrorCode.FOLLOWER_NOT_FOLLOWING,
                        details={"follower_id": follower_id}
                    )

                bot = self._follower_bots[follower_id]
                leader_id = bot.leader_id

                # 停止跟單
                await bot.stop()

                # 從內存移除
                del self._follower_bots[follower_id]

            # 從 Leader 的 Followers 列表移除
            if leader_id and leader_id in self._leader_followers:
                self._leader_followers[leader_id].discard(follower_id)

            # 更新數據庫
            db = self.mongo_manager.db
            await db['copy_followers'].update_one(
                {"follower_id": follower_id},
                {
                    "$set": {
                        "is_active": False,
                        "stopped_at": datetime.utcnow()
                    }
                }
            )

            # 取消交易模式註冊
            if self._session_manager:
                await self._session_manager.unregister_trading_mode(follower_id, TradingMode.COPY_FOLLOWER)

            # 更新 Leader 的 follower_count
            if leader_id:
                await self.mongo_manager.users.update_one(
                    {"_id": leader_id},
                    {"$inc": {"leader_statistics.follower_count": -1}}
                )

            self._stats["active_followers"] = max(0, self._stats["active_followers"] - 1)

            logger.info(
                f"Follower {follower_id} 已停止跟單",
                event_type="follower_stopped",
                data={"follower_id": follower_id, "leader_id": leader_id}
            )

            return {
                "success": True,
                "follower_id": follower_id,
                "message": "已停止跟單"
            }

        except GridTradingException:
            raise
        except Exception as e:
            logger.error(f"停止跟單失敗: {e}")
            raise

    async def _save_follower_config(
        self,
        follower_id: str,
        leader_id: str,
        copy_ratio: float,
        limits: RiskLimits
    ):
        """保存 Follower 配置到數據庫"""
        db = self.mongo_manager.db
        copy_followers = db['copy_followers']

        doc = {
            "follower_id": follower_id,
            "leader_id": leader_id,
            "copy_ratio": copy_ratio,
            "is_active": True,
            "created_at": datetime.utcnow(),
            "risk_limits": {
                "max_per_trade_amount": limits.max_per_trade_amount,
                "daily_max_loss": limits.daily_max_loss,
                "max_position_count": limits.max_position_count,
                "max_position_value": limits.max_position_value,
                "max_single_position_ratio": limits.max_single_position_ratio
            },
            "daily_stats": {
                "date": datetime.utcnow().strftime("%Y-%m-%d"),
                "trades_count": 0,
                "total_loss": 0.0
            },
            "statistics": {
                "total_trades": 0,
                "successful_trades": 0,
                "total_profit": 0.0
            }
        }

        await copy_followers.update_one(
            {"follower_id": follower_id},
            {"$set": doc},
            upsert=True
        )

    # ============== 查詢方法 ==============

    async def get_available_leaders(self) -> List[Dict[str, Any]]:
        """
        獲取可跟隨的 Leaders 列表

        Returns:
            Leaders 列表
        """
        try:
            cursor = self.mongo_manager.users.find({
                "is_leader": True,
                "leader_status": LeaderStatus.APPROVED.value,
                "leader_is_active": True
            })

            leaders = []
            async for user in cursor:
                leaders.append({
                    "leader_id": user["_id"],
                    "statistics": user.get("leader_statistics", {}),
                    "approved_at": user.get("leader_approved_at")
                })

            return leaders

        except Exception as e:
            logger.error(f"獲取可用 Leaders 失敗: {e}")
            return []

    async def get_leader_detail(self, leader_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取 Leader 詳細資訊

        Args:
            leader_id: Leader ID

        Returns:
            Leader 詳細資訊
        """
        try:
            user = await self.mongo_manager.get_user(leader_id)
            if not user or not user.get("is_leader"):
                return None

            # 獲取健康狀態
            health_status = None
            async with self._leader_monitors_lock:
                if leader_id in self._leader_monitors:
                    health_status = self._leader_monitors[leader_id].get_health_status()

            return {
                "leader_id": leader_id,
                "status": user.get("leader_status"),
                "is_active": user.get("leader_is_active", False),
                "statistics": user.get("leader_statistics", {}),
                "approved_at": user.get("leader_approved_at"),
                "follower_count": len(self._leader_followers.get(leader_id, set())),
                "health_status": health_status
            }

        except Exception as e:
            logger.error(f"獲取 Leader 詳情失敗: {e}")
            return None

    async def get_follower_status(self, follower_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取 Follower 的跟單狀態

        Args:
            follower_id: Follower ID

        Returns:
            跟單狀態
        """
        try:
            async with self._follower_bots_lock:
                if follower_id not in self._follower_bots:
                    # 從數據庫查詢
                    db = self.mongo_manager.db
                    doc = await db['copy_followers'].find_one({"follower_id": follower_id})
                    if doc:
                        return {
                            "follower_id": follower_id,
                            "leader_id": doc.get("leader_id"),
                            "is_active": doc.get("is_active", False),
                            "copy_ratio": doc.get("copy_ratio"),
                            "statistics": doc.get("statistics", {}),
                            "risk_limits": doc.get("risk_limits", {})
                        }
                    return None

                bot = self._follower_bots[follower_id]
                return await bot.get_status()

        except Exception as e:
            logger.error(f"獲取 Follower 狀態失敗: {e}")
            return None

    async def get_follower_trade_history(
        self,
        follower_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        獲取 Follower 的交易歷史

        Args:
            follower_id: Follower ID
            limit: 返回數量限制
            offset: 偏移量

        Returns:
            交易記錄列表
        """
        try:
            db = self.mongo_manager.db
            cursor = db['copy_trades'].find(
                {"follower_id": follower_id}
            ).sort("created_at", -1).skip(offset).limit(limit)

            records = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                records.append(doc)

            return records

        except Exception as e:
            logger.error(f"獲取交易歷史失敗: {e}")
            return []

    async def get_pending_leader_applications(self) -> List[Dict[str, Any]]:
        """
        獲取待審核的 Leader 申請列表 (管理員用)

        Returns:
            待審核申請列表
        """
        try:
            cursor = self.mongo_manager.users.find({
                "leader_status": LeaderStatus.PENDING.value
            })

            applications = []
            async for user in cursor:
                applications.append({
                    "user_id": user["_id"],
                    "applied_at": user.get("leader_applied_at"),
                    "wallet_address": user.get("wallet_address") or user.get("evm_wallet_address")
                })

            return applications

        except Exception as e:
            logger.error(f"獲取待審核申請失敗: {e}")
            return []

    # ============== SSE 事件回調 ==============

    def register_event_callback(self, user_id: str, callback: Callable[[Dict[str, Any]], Any]):
        """
        註冊用戶的事件回調 (用於 SSE 推送)

        Args:
            user_id: 用戶 ID
            callback: 回調函數
        """
        if user_id not in self._event_callbacks:
            self._event_callbacks[user_id] = []
        if callback not in self._event_callbacks[user_id]:
            self._event_callbacks[user_id].append(callback)

        # 如果是 Follower，也註冊到 Bot
        if user_id in self._follower_bots:
            self._follower_bots[user_id].register_event_callback(callback)

    def unregister_event_callback(self, user_id: str, callback: Callable[[Dict[str, Any]], Any]):
        """
        取消註冊用戶的事件回調

        Args:
            user_id: 用戶 ID
            callback: 回調函數
        """
        if user_id in self._event_callbacks:
            if callback in self._event_callbacks[user_id]:
                self._event_callbacks[user_id].remove(callback)

        if user_id in self._follower_bots:
            self._follower_bots[user_id].unregister_event_callback(callback)

    # ============== 生命週期管理 ==============

    async def shutdown(self):
        """關閉所有 Copy Trading 會話"""
        logger.info("開始關閉 CopyTradingSessionManager...")

        # 停止所有 Follower Bots
        async with self._follower_bots_lock:
            for follower_id, bot in list(self._follower_bots.items()):
                try:
                    await bot.stop()
                    logger.info(f"Follower {follower_id} 已停止")
                except Exception as e:
                    logger.error(f"停止 Follower {follower_id} 失敗: {e}")

            self._follower_bots.clear()

        # 停止所有 Leader Monitors
        async with self._leader_monitors_lock:
            for leader_id, monitor in list(self._leader_monitors.items()):
                try:
                    await monitor.stop_monitoring()
                    logger.info(f"Leader {leader_id} 的 Monitor 已停止")
                except Exception as e:
                    logger.error(f"停止 Leader {leader_id} Monitor 失敗: {e}")

            self._leader_monitors.clear()

        self._leader_followers.clear()
        self._event_callbacks.clear()

        logger.info("CopyTradingSessionManager 已關閉")

    def get_stats(self) -> Dict[str, Any]:
        """獲取統計信息"""
        return {
            **self._stats,
            "active_leader_monitors": len(self._leader_monitors),
            "active_follower_bots": len(self._follower_bots)
        }


# 單例實例
_copy_trading_manager: Optional[CopyTradingSessionManager] = None


async def get_copy_trading_manager() -> CopyTradingSessionManager:
    """獲取 CopyTradingSessionManager 單例"""
    global _copy_trading_manager
    if _copy_trading_manager is None:
        _copy_trading_manager = CopyTradingSessionManager()
    return _copy_trading_manager
