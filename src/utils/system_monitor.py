#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系統監控組件
監控系統資源使用情況、性能指標和健康狀態
"""

import asyncio
import time
import psutil
import gc
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from src.utils.logging_config import get_logger, metrics
from src.utils.error_codes import GridTradingException, ErrorCode

logger = get_logger("system_monitor")

@dataclass
class SystemMetrics:
    """系統指標數據類"""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_usage_percent: float
    active_sessions: int = 0
    websocket_connections: int = 0
    queue_sizes: Dict[str, int] = field(default_factory=dict)
    gc_counts: tuple = field(default_factory=tuple)
    event_loop_lag: float = 0.0

class CircuitBreaker:
    """熔斷器實現"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0,
                 expected_exception: type = Exception):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def __call__(self, func):
        """裝飾器實現"""
        async def wrapper(*args, **kwargs):
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    logger.info("熔斷器進入半開狀態")
                else:
                    raise GridTradingException(
                        error_code=ErrorCode.CIRCUIT_BREAKER_OPEN,
                        details={"service": func.__name__}
                    )

            try:
                result = await func(*args, **kwargs)
                # 成功執行，重置失敗計數
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                    self.failure_count = 0
                    logger.info("熔斷器已關閉")
                return result
            except self.expected_exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()

                if self.failure_count >= self.failure_threshold:
                    self.state = "OPEN"
                    logger.error(f"熔斷器開啟，失敗次數: {self.failure_count}")
                    metrics.increment_counter("circuit_breaker.opened", tags={"service": func.__name__})

                raise

        return wrapper

class SystemMonitor:
    """系統監控器"""

    def __init__(self, monitoring_interval: float = 30.0, alert_thresholds: Optional[Dict] = None):
        self.monitoring_interval = monitoring_interval
        self.alert_thresholds = alert_thresholds or {
            'cpu_percent': 80.0,
            'memory_percent': 85.0,
            'disk_usage_percent': 90.0,
            'active_sessions': 100,
            'gc_pressure': 1000  # GC 次數閾值
        }

        self.is_monitoring = False
        self.monitor_task = None
        self.metrics_history: List[SystemMetrics] = []
        self.max_history_size = 100

        # 資源限制
        self.resource_limits = {
            'max_sessions': 200,
            'max_websocket_connections': 150,
            'max_queue_size': 5000
        }

        # 健康檢查回調
        self.health_check_callbacks: List[callable] = []

        logger.info("系統監控器已初始化", data={
            "monitoring_interval": monitoring_interval,
            "alert_thresholds": self.alert_thresholds
        })

    async def start(self):
        """啟動系統監控"""
        if self.is_monitoring:
            return

        self.is_monitoring = True
        self.monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info("系統監控已啟動")

    async def stop(self):
        """停止系統監控"""
        if not self.is_monitoring:
            return

        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None

        logger.info("系統監控已停止")

    def add_health_check_callback(self, callback: callable):
        """添加健康檢查回調"""
        self.health_check_callbacks.append(callback)

    async def collect_metrics(self) -> SystemMetrics:
        """收集系統指標"""
        try:
            # 基本系統指標
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')

            # GC 統計
            gc_stats = gc.get_stats() if hasattr(gc, 'get_stats') else ()
            gc_counts = tuple([stat.get('collections', 0) for stat in gc_stats]) if gc_stats else (0, 0, 0)

            # 事件循環延遲
            start_time = time.time()
            await asyncio.sleep(0.001)  # 極短延遲測量
            event_loop_lag = (time.time() - start_time) * 1000  # 轉換為毫秒

            metrics = SystemMetrics(
                timestamp=time.time(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / 1024 / 1024,
                memory_available_mb=memory.available / 1024 / 1024,
                disk_usage_percent=disk.percent,
                gc_counts=gc_counts,
                event_loop_lag=event_loop_lag
            )

            # 收集應用層指標
            await self._collect_app_metrics(metrics)

            return metrics

        except Exception as e:
            logger.error(f"收集系統指標失敗: {e}")
            # 返回默認指標
            return SystemMetrics(
                timestamp=time.time(),
                cpu_percent=0.0,
                memory_percent=0.0,
                memory_used_mb=0.0,
                memory_available_mb=0.0,
                disk_usage_percent=0.0
            )

    async def _collect_app_metrics(self, metrics: SystemMetrics):
        """收集應用層指標"""
        try:
            # 獲取 session 統計
            from src.services.session_service import SessionManager
            session_manager = SessionManager()
            metrics.active_sessions = len(session_manager.sessions)

            # 獲取 WebSocket 統計
            from src.utils.websocket_manager import get_websocket_manager
            ws_manager = get_websocket_manager()
            ws_stats = await ws_manager.get_stats()
            metrics.websocket_connections = ws_stats.get('active_connections', 0)

            # 獲取隊列統計
            metrics.queue_sizes = {
                'active_sessions': metrics.active_sessions,
                'websocket_connections': metrics.websocket_connections
            }

        except Exception as e:
            logger.warning(f"收集應用指標失敗: {e}")

    async def check_health(self) -> Dict[str, Any]:
        """檢查系統健康狀態"""
        health_status = {
            'status': 'healthy',
            'checks': {},
            'metrics': {},
            'timestamp': time.time()
        }

        try:
            # 系統資源檢查
            current_metrics = await self.collect_metrics()
            health_status['metrics'] = {
                'cpu_percent': current_metrics.cpu_percent,
                'memory_percent': current_metrics.memory_percent,
                'disk_usage_percent': current_metrics.disk_usage_percent,
                'active_sessions': current_metrics.active_sessions,
                'websocket_connections': current_metrics.websocket_connections,
                'event_loop_lag_ms': current_metrics.event_loop_lag
            }

            # 檢查各項指標
            checks = [
                ('cpu', current_metrics.cpu_percent < self.alert_thresholds['cpu_percent']),
                ('memory', current_metrics.memory_percent < self.alert_thresholds['memory_percent']),
                ('disk', current_metrics.disk_usage_percent < self.alert_thresholds['disk_usage_percent']),
                ('sessions', current_metrics.active_sessions < self.alert_thresholds['active_sessions']),
                ('event_loop', current_metrics.event_loop_lag < 100.0)  # 100ms 延遲閾值
            ]

            health_status['checks'] = {name: 'pass' if passed else 'fail' for name, passed in checks}

            # 檢查是否有失敗項
            if any(not passed for _, passed in checks):
                health_status['status'] = 'unhealthy'

            # 執行自定義健康檢查
            for callback in self.health_check_callbacks:
                try:
                    custom_health = await callback()
                    if isinstance(custom_health, dict):
                        health_status['checks'].update(custom_health.get('checks', {}))
                        if custom_health.get('status') == 'unhealthy':
                            health_status['status'] = 'unhealthy'
                except Exception as e:
                    logger.error(f"自定義健康檢查失敗: {e}")
                    health_status['status'] = 'unhealthy'

        except Exception as e:
            logger.error(f"健康檢查失敗: {e}")
            health_status['status'] = 'error'
            health_status['error'] = str(e)

        return health_status

    async def check_resource_limits(self) -> Dict[str, bool]:
        """檢查資源限制"""
        try:
            current_metrics = await self.collect_metrics()

            limits_status = {
                'sessions_limit': current_metrics.active_sessions < self.resource_limits['max_sessions'],
                'websocket_limit': current_metrics.websocket_connections < self.resource_limits['max_websocket_connections'],
                'queue_size_limit': all(size < self.resource_limits['max_queue_size']
                                      for size in current_metrics.queue_sizes.values()),
            }

            # 檢查是否有限制觸發
            if not all(limits_status.values()):
                logger.warning("資源限制警告", data=limits_status)
                metrics.increment_counter("resource_limits.warning")

            return limits_status

        except Exception as e:
            logger.error(f"檢查資源限制失敗: {e}")
            return {}

    async def force_gc(self):
        """強制垃圾回收"""
        try:
            before_memory = psutil.virtual_memory().used
            collected = gc.collect()
            after_memory = psutil.virtual_memory().used

            memory_freed = (before_memory - after_memory) / 1024 / 1024  # MB
            logger.info(f"強制垃圾回收完成", data={
                "objects_collected": collected,
                "memory_freed_mb": memory_freed
            })

            metrics.increment_counter("gc.forced")
            metrics.record_histogram("gc.memory_freed_mb", memory_freed)

            return {
                'objects_collected': collected,
                'memory_freed_mb': memory_freed
            }

        except Exception as e:
            logger.error(f"強制垃圾回收失敗: {e}")
            return {}

    async def get_metrics_history(self, limit: int = 50) -> List[SystemMetrics]:
        """獲取指標歷史"""
        return self.metrics_history[-limit:]

    async def _monitoring_loop(self):
        """監控循環"""
        logger.info("系統監控循環已啟動")

        while self.is_monitoring:
            try:
                # 收集指標
                current_metrics = await self.collect_metrics()

                # 保存到歷史記錄
                self.metrics_history.append(current_metrics)
                if len(self.metrics_history) > self.max_history_size:
                    self.metrics_history.pop(0)

                # 記錄指標到監控系統
                metrics.set_gauge("system.cpu_percent", current_metrics.cpu_percent)
                metrics.set_gauge("system.memory_percent", current_metrics.memory_percent)
                metrics.set_gauge("system.memory_used_mb", current_metrics.memory_used_mb)
                metrics.set_gauge("system.active_sessions", current_metrics.active_sessions)
                metrics.set_gauge("system.websocket_connections", current_metrics.websocket_connections)
                metrics.set_gauge("system.event_loop_lag_ms", current_metrics.event_loop_lag)

                # 檢查資源限制
                await self.check_resource_limits()

                # 檢查是否需要警告
                await self._check_alerts(current_metrics)

                # 定期垃圾回收
                if current_metrics.memory_percent > 80:
                    await self.force_gc()

            except Exception as e:
                logger.error(f"監控循環錯誤: {e}")

            # 等待下次監控
            await asyncio.sleep(self.monitoring_interval)

        logger.info("系統監控循環已停止")

    async def _check_alerts(self, metrics: SystemMetrics):
        """檢查並發送警告"""
        alerts = []

        if metrics.cpu_percent > self.alert_thresholds['cpu_percent']:
            alerts.append(f"CPU 使用率過高: {metrics.cpu_percent:.1f}%")

        if metrics.memory_percent > self.alert_thresholds['memory_percent']:
            alerts.append(f"記憶體使用率過高: {metrics.memory_percent:.1f}%")

        if metrics.disk_usage_percent > self.alert_thresholds['disk_usage_percent']:
            alerts.append(f"磁盤使用率過高: {metrics.disk_usage_percent:.1f}%")

        if metrics.active_sessions > self.alert_thresholds['active_sessions']:
            alerts.append(f"活躍會話數過多: {metrics.active_sessions}")

        if metrics.event_loop_lag > 100:  # 100ms
            alerts.append(f"事件循環延遲過高: {metrics.event_loop_lag:.1f}ms")

        if alerts:
            logger.warning("系統資源警告", event_type="system_alert", data={
                "alerts": alerts,
                "metrics": {
                    "cpu_percent": metrics.cpu_percent,
                    "memory_percent": metrics.memory_percent,
                    "active_sessions": metrics.active_sessions,
                    "event_loop_lag": metrics.event_loop_lag
                }
            })
            metrics.increment_counter("system.alerts", value=len(alerts))

# 全局系統監控器實例
_system_monitor: Optional[SystemMonitor] = None

def get_system_monitor() -> SystemMonitor:
    """獲取全局系統監控器實例"""
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor()
    return _system_monitor

async def start_system_monitor():
    """啟動全局系統監控器"""
    monitor = get_system_monitor()
    await monitor.start()

async def stop_system_monitor():
    """停止全局系統監控器"""
    global _system_monitor
    if _system_monitor:
        await _system_monitor.stop()