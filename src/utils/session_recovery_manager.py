#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格會話恢復管理器
處理grid session意外中止的檢測、恢復和預防機制
"""

import asyncio
import time
from typing import Dict, Any, Optional, Set, List
from dataclasses import dataclass
from enum import Enum
from src.utils.logging_config import get_logger
from src.interfaces.session_manager_interface import SessionManagerInterface
from src.services.database_connection import db_manager

logger = get_logger("session_recovery_manager")


class SessionStatus(Enum):
    """會話狀態枚舉"""
    RUNNING = "running"
    STOPPED = "stopped"
    RECOVERING = "recovering"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RecoveryTrigger(Enum):
    """恢復觸發原因"""
    WEBSOCKET_DISCONNECTED = "websocket_disconnected"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    API_ERROR = "api_error"
    HEALTH_CHECK_FAILED = "health_check_failed"
    MANUAL_REQUEST = "manual_request"
    UNKNOWN = "unknown"


@dataclass
class SessionRecoveryConfig:
    """會話恢復配置"""

    # 檢測設置
    health_check_interval: int = 60  # 健康檢查間隔（秒）
    session_timeout_threshold: int = 300  # 會話超時閾值（5分鐘）
    max_consecutive_failures: int = 3  # 最大連續失敗次數

    # 恢復設置
    enable_auto_recovery: bool = True  # 啟用自動恢復
    max_recovery_attempts: int = 5  # 最大恢復嘗試次數
    recovery_backoff_base: int = 30  # 恢復退避基礎時間（秒）
    recovery_backoff_multiplier: float = 2.0  # 退避倍數

    # 保護設置
    recovery_cooldown: int = 600  # 恢復冷卻時間（10分鐘）
    min_stable_time: int = 120  # 最小穩定運行時間（2分鐘）

    def __post_init__(self):
        """初始化後處理"""
        self.last_recovery_time: Dict[str, float] = {}
        self.recovery_attempts: Dict[str, int] = {}


@dataclass
class SessionHealthStatus:
    """會話健康狀態"""
    session_id: str
    status: SessionStatus
    last_activity: float
    consecutive_failures: int
    error_count: int
    last_error: Optional[str] = None
    recovery_attempts: int = 0
    is_stable: bool = False


class SessionRecoveryManager:
    """會話恢復管理器"""

    def __init__(self, session_manager: SessionManagerInterface):
        self.session_manager = session_manager
        self.config = SessionRecoveryConfig()

        # 狀態追蹤
        self.session_statuses: Dict[str, SessionHealthStatus] = {}
        self.active_recoveries: Set[str] = set()
        self.recovery_history: List[Dict[str, Any]] = []

        # 監控任務
        self.health_monitor_task: Optional[asyncio.Task] = None
        self.is_monitoring = False

        # 統計信息
        self.stats = {
            'total_recoveries': 0,
            'successful_recoveries': 0,
            'failed_recoveries': 0,
            'prevented_recoveries': 0,
            'last_recovery_time': 0
        }

    async def start_monitoring(self):
        """開始會話健康監控"""
        if self.is_monitoring:
            logger.warning("會話恢復監控已在運行")
            return

        self.is_monitoring = True
        self.health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        logger.info("會話恢復監控已啟動", event_type="recovery_monitoring_started")

    async def stop_monitoring(self):
        """停止會話健康監控"""
        self.is_monitoring = False
        if self.health_monitor_task:
            self.health_monitor_task.cancel()
            try:
                await self.health_monitor_task
            except asyncio.CancelledError:
                pass
            self.health_monitor_task = None

        logger.info("會話恢復監控已停止", event_type="recovery_monitoring_stopped")

    async def _health_monitor_loop(self):
        """健康監控循環"""
        logger.info("會話健康監控循環已啟動")

        while self.is_monitoring:
            try:
                await self._check_all_sessions_health()
                await asyncio.sleep(self.config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康監控循環錯誤: {e}", exc_info=True)
                await asyncio.sleep(10)  # 短暫等待後重試

        logger.info("會話健康監控循環已停止")

    async def _check_all_sessions_health(self):
        """檢查所有會話的健康狀態"""
        try:
            # 獲取當前所有會話
            sessions = await self.session_manager.list_sessions()
            current_time = time.time()

            for session_id, is_running in sessions.items():
                await self._check_single_session_health(session_id, is_running, current_time)

            # 清理不存在的會話狀態
            await self._cleanup_stale_sessions(sessions.keys())

        except Exception as e:
            logger.error(f"檢查所有會話健康狀態失敗: {e}")

    async def _check_single_session_health(self, session_id: str, is_running: bool, current_time: float):
        """檢查單個會話的健康狀態"""
        try:
            # 獲取或創建會話狀態
            if session_id not in self.session_statuses:
                self.session_statuses[session_id] = SessionHealthStatus(
                    session_id=session_id,
                    status=SessionStatus.RUNNING if is_running else SessionStatus.STOPPED,
                    last_activity=current_time,
                    consecutive_failures=0,
                    error_count=0
                )

            status = self.session_statuses[session_id]

            # 更新狀態
            if is_running:
                status.status = SessionStatus.RUNNING
                status.last_activity = current_time

                # 檢查是否穩定
                time_since_creation = current_time - self.config.last_recovery_time.get(session_id, 0)
                status.is_stable = time_since_creation >= self.config.min_stable_time

                # 重置失敗計數
                if status.consecutive_failures > 0:
                    logger.info(f"會話 {session_id} 已恢復正常", event_type="session_recovered",
                               data={"session_id": session_id, "previous_failures": status.consecutive_failures})
                    status.consecutive_failures = 0
            else:
                status.status = SessionStatus.STOPPED
                status.consecutive_failures += 1

                # 檢查是否需要恢復
                if (self.config.enable_auto_recovery and
                    session_id not in self.active_recoveries and
                    await self._should_attempt_recovery(session_id, status, current_time)):

                    logger.warning(f"檢測到會話 {session_id} 需要恢復",
                                  event_type="session_recovery_needed",
                                  data={
                                      "session_id": session_id,
                                      "consecutive_failures": status.consecutive_failures,
                                      "last_activity": current_time - status.last_activity
                                  })

                    # 啟動恢復任務
                    recovery_task = asyncio.create_task(
                        self._attempt_session_recovery(session_id, RecoveryTrigger.HEALTH_CHECK_FAILED)
                    )

        except Exception as e:
            logger.error(f"檢查會話 {session_id} 健康狀態失敗: {e}")
            if session_id in self.session_statuses:
                self.session_statuses[session_id].error_count += 1

    async def _should_attempt_recovery(self, session_id: str, status: SessionHealthStatus, current_time: float) -> bool:
        """判斷是否應該嘗試恢復"""

        # 檢查連續失敗次數
        if status.consecutive_failures < self.config.max_consecutive_failures:
            return False

        # 檢查恢復嘗試次數
        recovery_attempts = self.config.recovery_attempts.get(session_id, 0)
        if recovery_attempts >= self.config.max_recovery_attempts:
            logger.warning(f"會話 {session_id} 恢復嘗試次數已達上限",
                         event_type="recovery_attempts_exceeded",
                         data={"session_id": session_id, "attempts": recovery_attempts})
            return False

        # 檢查恢復冷卻時間
        last_recovery = self.config.last_recovery_time.get(session_id, 0)
        if current_time - last_recovery < self.config.recovery_cooldown:
            logger.debug(f"會話 {session_id} 仍在恢復冷卻期內",
                        data={"session_id": session_id, "cooldown_remaining": self.config.recovery_cooldown - (current_time - last_recovery)})
            return False

        return True

    async def _attempt_session_recovery(self, session_id: str, trigger: RecoveryTrigger):
        """嘗試恢復會話"""
        if session_id in self.active_recoveries:
            logger.warning(f"會話 {session_id} 已在恢復中", event_type="recovery_already_in_progress")
            return

        self.active_recoveries.add(session_id)
        current_time = time.time()

        try:
            logger.info(f"開始恢復會話 {session_id}",
                       event_type="recovery_attempt_started",
                       data={
                           "session_id": session_id,
                           "trigger": trigger.value,
                           "attempt": self.config.recovery_attempts.get(session_id, 0) + 1
                       })

            # 更新恢復統計
            self.config.recovery_attempts[session_id] = self.config.recovery_attempts.get(session_id, 0) + 1
            self.config.last_recovery_time[session_id] = current_time
            self.stats['total_recoveries'] += 1

            # 執行恢復邏輯
            success = await self._execute_recovery(session_id)

            if success:
                self.stats['successful_recoveries'] += 1
                logger.info(f"會話 {session_id} 恢復成功",
                           event_type="recovery_successful",
                           data={
                               "session_id": session_id,
                               "attempt": self.config.recovery_attempts[session_id],
                               "trigger": trigger.value
                           })

                # 重置失敗計數
                if session_id in self.session_statuses:
                    self.session_statuses[session_id].consecutive_failures = 0
                    self.session_statuses[session_id].recovery_attempts += 1

            else:
                self.stats['failed_recoveries'] += 1
                logger.error(f"會話 {session_id} 恢復失敗",
                            event_type="recovery_failed",
                            data={
                                "session_id": session_id,
                                "attempt": self.config.recovery_attempts[session_id],
                                "trigger": trigger.value
                            })

            # 記錄恢復歷史
            self._record_recovery_history(session_id, trigger, success)

        except Exception as e:
            self.stats['failed_recoveries'] += 1
            logger.error(f"會話 {session_id} 恢復過程發生錯誤: {e}",
                        event_type="recovery_error",
                        data={"session_id": session_id, "error": str(e)})

        finally:
            self.active_recoveries.discard(session_id)

    async def _execute_recovery(self, session_id: str) -> bool:
        """執行具體的恢復邏輯"""
        try:
            # 這裡可以實現多種恢復策略：
            # 1. 重新創建會話
            # 2. 重啟現有會話
            # 3. 恢復中斷的訂單

            # 暫時返回 False，需要根據具體需求實現
            logger.info(f"執行會話 {session_id} 的恢復邏輯")

            # TODO: 實現具體的恢復邏輯
            # 可能需要從數據庫恢復會話配置，然後重新啟動

            return False

        except Exception as e:
            logger.error(f"執行恢復邏輯失敗: {e}")
            return False

    def _record_recovery_history(self, session_id: str, trigger: RecoveryTrigger, success: bool):
        """記錄恢復歷史"""
        history_entry = {
            "session_id": session_id,
            "trigger": trigger.value,
            "success": success,
            "timestamp": time.time(),
            "attempt": self.config.recovery_attempts.get(session_id, 0)
        }

        self.recovery_history.append(history_entry)

        # 保持歷史記錄在合理範圍內
        if len(self.recovery_history) > 100:
            self.recovery_history = self.recovery_history[-50:]  # 保留最近50條

    async def _cleanup_stale_sessions(self, active_session_ids: Set[str]):
        """清理過期的會話狀態"""
        stale_sessions = [
            session_id for session_id in self.session_statuses.keys()
            if session_id not in active_session_ids
        ]

        for session_id in stale_sessions:
            status = self.session_statuses[session_id]

            # 如果會話已停止超過1小時，清理其狀態
            if time.time() - status.last_activity > 3600:
                logger.debug(f"清理過期會話狀態: {session_id}")
                del self.session_statuses[session_id]

                # 清理相關的恢復配置
                self.config.recovery_attempts.pop(session_id, None)
                self.config.last_recovery_time.pop(session_id, None)

    async def trigger_manual_recovery(self, session_id: str) -> bool:
        """手動觸發會話恢復"""
        if session_id not in self.session_statuses:
            logger.warning(f"未知會話: {session_id}")
            return False

        if session_id in self.active_recoveries:
            logger.warning(f"會話 {session_id} 已在恢復中")
            return False

        # 立即觸發恢復
        await self._attempt_session_recovery(session_id, RecoveryTrigger.MANUAL_REQUEST)
        return True

    def get_session_status(self, session_id: str) -> Optional[SessionHealthStatus]:
        """獲取會話健康狀態"""
        return self.session_statuses.get(session_id)

    def get_all_session_status(self) -> Dict[str, SessionHealthStatus]:
        """獲取所有會話健康狀態"""
        return self.session_statuses.copy()

    def get_statistics(self) -> Dict[str, Any]:
        """獲取恢復統計信息"""
        current_time = time.time()

        return {
            "is_monitoring": self.is_monitoring,
            "tracked_sessions": len(self.session_statuses),
            "active_recoveries": len(self.active_recoveries),
            "stats": self.stats.copy(),
            "config": {
                "enable_auto_recovery": self.config.enable_auto_recovery,
                "max_recovery_attempts": self.config.max_recovery_attempts,
                "health_check_interval": self.config.health_check_interval
            },
            "recent_history": [
                entry for entry in self.recovery_history[-10:]
                if current_time - entry["timestamp"] < 3600  # 最近1小時
            ]
        }


# 全局恢復管理器實例
_recovery_manager: Optional[SessionRecoveryManager] = None


async def get_recovery_manager() -> SessionRecoveryManager:
    """獲取全局恢復管理器"""
    global _recovery_manager
    if _recovery_manager is None:
        from src.services.session_service import session_manager
        _recovery_manager = SessionRecoveryManager(session_manager)
        await _recovery_manager.start_monitoring()
    return _recovery_manager


async def stop_recovery_manager():
    """停止全局恢復管理器"""
    global _recovery_manager
    if _recovery_manager:
        await _recovery_manager.stop_monitoring()
        _recovery_manager = None