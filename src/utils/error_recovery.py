#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
錯誤處理和恢復機制
提供自動錯誤恢復、故障轉移和系統自愈功能
"""

import asyncio
import time
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from enum import Enum
from src.utils.logging_config import get_logger, metrics
from src.utils.error_codes import GridTradingException, ErrorCode

logger = get_logger("error_recovery")

class ErrorSeverity(Enum):
    """錯誤嚴重程度"""
    LOW = "low"         # 輕微錯誤，記錄即可
    MEDIUM = "medium"   # 中等錯誤，需要重試
    HIGH = "high"       # 嚴重錯誤，需要恢復操作
    CRITICAL = "critical" # 致命錯誤，需要立即處理

@dataclass
class ErrorEvent:
    """錯誤事件"""
    error: Exception
    context: Dict[str, Any]
    severity: ErrorSeverity
    timestamp: float
    component: str
    session_id: Optional[str] = None
    retry_count: int = 0
    recovered: bool = False

class RecoveryAction:
    """恢復操作基類"""

    def __init__(self, name: str, severity_threshold: ErrorSeverity = ErrorSeverity.MEDIUM):
        self.name = name
        self.severity_threshold = severity_threshold
        self.last_execution = 0.0
        self.execution_count = 0

    async def can_execute(self, error_event: ErrorEvent) -> bool:
        """檢查是否可以執行恢復操作"""
        if error_event.severity.value < self.severity_threshold.value:
            return False

        # 避免頻繁執行（冷卻時間 60 秒）
        if time.time() - self.last_execution < 60:
            return False

        return True

    async def execute(self, error_event: ErrorEvent) -> bool:
        """執行恢復操作"""
        try:
            self.last_execution = time.time()
            self.execution_count += 1
            return await self._recover(error_event)
        except Exception as e:
            logger.error(f"恢復操作 {self.name} 執行失敗: {e}")
            return False

    async def _recover(self, error_event: ErrorEvent) -> bool:
        """子類需要實現的恢復邏輯"""
        raise NotImplementedError

class SessionRestartAction(RecoveryAction):
    """Session 重啟恢復操作"""

    def __init__(self):
        super().__init__("session_restart", ErrorSeverity.HIGH)

    async def _recover(self, error_event: ErrorEvent) -> bool:
        """重啟失敗的 session"""
        session_id = error_event.session_id
        if not session_id:
            return False

        try:
            from src.services.session_service import SessionManager
            session_manager = SessionManager()

            # 強制清理 session
            success = await session_manager.force_cleanup_session(session_id)
            if success:
                logger.info(f"Session {session_id} 已通過恢復機制清理", event_type="session_recovery")
                metrics.increment_counter("session.recovery.success")
                return True

        except Exception as e:
            logger.error(f"Session 恢復失敗: {e}")
            metrics.increment_counter("session.recovery.failed")

        return False

class WebSocketReconnectAction(RecoveryAction):
    """WebSocket 重連恢復操作"""

    def __init__(self):
        super().__init__("websocket_reconnect", ErrorSeverity.MEDIUM)

    async def _recover(self, error_event: ErrorEvent) -> bool:
        """重新連接 WebSocket"""
        session_id = error_event.session_id
        if not session_id:
            return False

        try:
            from src.utils.websocket_manager import get_websocket_manager
            ws_manager = get_websocket_manager()

            # 獲取連接信息
            connection_info = await ws_manager.get_connection(session_id)
            if connection_info and connection_info.state.value in ["disconnected", "failed"]:
                logger.info(f"WebSocket {session_id} 開始恢復重連", event_type="websocket_recovery")
                metrics.increment_counter("websocket.recovery.attempt")

                # 這裡可以實現具體的重連邏輯
                # 實際重連邏輯在 GridTradingBot 中處理
                return True

        except Exception as e:
            logger.error(f"WebSocket 恢復失敗: {e}")
            metrics.increment_counter("websocket.recovery.failed")

        return False

class MemoryCleanupAction(RecoveryAction):
    """記憶體清理恢復操作"""

    def __init__(self):
        super().__init__("memory_cleanup", ErrorSeverity.MEDIUM)

    async def _recover(self, error_event: ErrorEvent) -> bool:
        """清理記憶體"""
        try:
            # 強制垃圾回收
            import gc
            before_memory = self._get_memory_usage()
            collected = gc.collect()
            after_memory = self._get_memory_usage()

            memory_freed = before_memory - after_memory
            logger.info(f"記憶體清理完成", data={
                "objects_collected": collected,
                "memory_freed_mb": memory_freed
            })

            metrics.increment_counter("memory.recovery.success")
            metrics.record_histogram("memory.recovery.freed_mb", memory_freed)

            return memory_freed > 0

        except Exception as e:
            logger.error(f"記憶體清理失敗: {e}")
            metrics.increment_counter("memory.recovery.failed")
            return False

    def _get_memory_usage(self) -> float:
        """獲取當前記憶體使用量（MB）"""
        try:
            import psutil
            return psutil.virtual_memory().used / 1024 / 1024
        except:
            return 0.0

class ErrorRecoveryManager:
    """錯誤恢復管理器"""

    def __init__(self):
        self.recovery_actions: List[RecoveryAction] = []
        self.error_history: List[ErrorEvent] = []
        self.max_history_size = 1000
        self.is_running = False

        # 註冊默認恢復操作
        self._register_default_actions()

    def _register_default_actions(self):
        """註冊默認恢復操作"""
        self.recovery_actions.extend([
            SessionRestartAction(),
            WebSocketReconnectAction(),
            MemoryCleanupAction(),
        ])

    def register_action(self, action: RecoveryAction):
        """註冊自定義恢復操作"""
        self.recovery_actions.append(action)
        logger.info(f"註冊恢復操作: {action.name}")

    async def handle_error(self, error: Exception, context: Dict[str, Any],
                          severity: ErrorSeverity = ErrorSeverity.MEDIUM,
                          component: str = "unknown", session_id: str = None) -> bool:
        """
        處理錯誤並嘗試恢復

        Args:
            error: 發生的錯誤
            context: 錯誤上下文信息
            severity: 錯誤嚴重程度
            component: 組件名稱
            session_id: 可選的會話ID

        Returns:
            是否成功恢復
        """
        # 創建錯誤事件
        error_event = ErrorEvent(
            error=error,
            context=context,
            severity=severity,
            timestamp=time.time(),
            component=component,
            session_id=session_id
        )

        # 記錄錯誤
        await self._record_error(error_event)

        # 記錄指標
        metrics.increment_counter("error.recovery.handled", tags={
            "component": component,
            "severity": severity.value
        })

        # 嘗試恢復
        recovered = await self._attempt_recovery(error_event)
        error_event.recovered = recovered

        if recovered:
            metrics.increment_counter("error.recovery.success", tags={
                "component": component,
                "severity": severity.value
            })
            logger.info(f"錯誤恢復成功", event_type="error_recovery_success", data={
                "component": component,
                "error_type": type(error).__name__,
                "severity": severity.value
            })
        else:
            metrics.increment_counter("error.recovery.failed", tags={
                "component": component,
                "severity": severity.value
            })
            logger.warning(f"錯誤恢復失敗", event_type="error_recovery_failed", data={
                "component": component,
                "error_type": type(error).__name__,
                "severity": severity.value
            })

        return recovered

    async def _record_error(self, error_event: ErrorEvent):
        """記錄錯誤事件"""
        self.error_history.append(error_event)

        # 限制歷史記錄大小
        if len(self.error_history) > self.max_history_size:
            self.error_history.pop(0)

        # 記錄詳細錯誤日誌
        log_level = {
            ErrorSeverity.LOW: "debug",
            ErrorSeverity.MEDIUM: "warning",
            ErrorSeverity.HIGH: "error",
            ErrorSeverity.CRITICAL: "critical"
        }.get(error_event.severity, "info")

        log_message = f"組件 {error_event.component} 發生 {error_event.severity.value} 錯誤"
        getattr(logger, log_level)(log_message, event_type="error_recorded", data={
            "component": error_event.component,
            "error_type": type(error_event.error).__name__,
            "error_message": str(error_event.error),
            "severity": error_event.severity.value,
            "context": error_event.context,
            "session_id": error_event.session_id
        })

    async def _attempt_recovery(self, error_event: ErrorEvent) -> bool:
        """嘗試恢復"""
        recovery_success = False

        for action in self.recovery_actions:
            try:
                if await action.can_execute(error_event):
                    logger.info(f"執行恢復操作: {action.name}", event_type="recovery_attempt", data={
                        "component": error_event.component,
                        "action": action.name
                    })

                    success = await action.execute(error_event)
                    if success:
                        recovery_success = True
                        logger.info(f"恢復操作 {action.name} 成功", event_type="recovery_success", data={
                            "component": error_event.component,
                            "action": action.name
                        })
                        break

            except Exception as e:
                logger.error(f"恢復操作 {action.name} 執行異常: {e}")

        return recovery_success

    async def start_monitoring(self):
        """啟動錯誤恢復監控"""
        if self.is_running:
            return

        self.is_running = True
        logger.info("錯誤恢復監控已啟動")

    async def stop_monitoring(self):
        """停止錯誤恢復監控"""
        self.is_running = False
        logger.info("錯誤恢復監控已停止")

    def get_error_statistics(self) -> Dict[str, Any]:
        """獲取錯誤統計信息"""
        if not self.error_history:
            return {"total_errors": 0}

        # 按組件統計
        component_stats = {}
        severity_stats = {}

        for error_event in self.error_history:
            # 組件統計
            component = error_event.component
            if component not in component_stats:
                component_stats[component] = {"total": 0, "recovered": 0}
            component_stats[component]["total"] += 1
            if error_event.recovered:
                component_stats[component]["recovered"] += 1

            # 嚴重程度統計
            severity = error_event.severity.value
            if severity not in severity_stats:
                severity_stats[severity] = {"total": 0, "recovered": 0}
            severity_stats[severity]["total"] += 1
            if error_event.recovered:
                severity_stats[severity]["recovered"] += 1

        # 計算恢復率
        for stats in [component_stats, severity_stats]:
            for key, value in stats.items():
                total = value["total"]
                recovered = value["recovered"]
                value["recovery_rate"] = (recovered / total * 100) if total > 0 else 0

        return {
            "total_errors": len(self.error_history),
            "component_stats": component_stats,
            "severity_stats": severity_stats,
            "recovery_actions": [
                {
                    "name": action.name,
                    "execution_count": action.execution_count,
                    "last_execution": action.last_execution
                }
                for action in self.recovery_actions
            ]
        }

# 全局錯誤恢復管理器
_error_recovery_manager: Optional[ErrorRecoveryManager] = None

def get_error_recovery_manager() -> ErrorRecoveryManager:
    """獲取全局錯誤恢復管理器"""
    global _error_recovery_manager
    if _error_recovery_manager is None:
        _error_recovery_manager = ErrorRecoveryManager()
    return _error_recovery_manager

async def start_error_recovery():
    """啟動全局錯誤恢復"""
    manager = get_error_recovery_manager()
    await manager.start_monitoring()

async def stop_error_recovery():
    """停止全局錯誤恢復"""
    global _error_recovery_manager
    if _error_recovery_manager:
        await _error_recovery_manager.stop_monitoring()

# 裝飾器：自動錯誤恢復
def auto_recover(severity: ErrorSeverity = ErrorSeverity.MEDIUM, component: str = "unknown"):
    """自動錯誤恢復裝飾器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # 嘗試自動恢復
                manager = get_error_recovery_manager()
                recovered = await manager.handle_error(
                    error=e,
                    context={"function": func.__name__, "args": str(args)[:200]},
                    severity=severity,
                    component=component
                )

                if recovered:
                    # 恢復成功，重試一次
                    try:
                        return await func(*args, **kwargs)
                    except Exception as retry_error:
                        logger.error(f"恢復後重試仍然失敗: {retry_error}")
                        raise

                # 恢復失敗，重新拋出原始錯誤
                raise

        return wrapper
    return decorator