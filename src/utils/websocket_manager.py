#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 連接管理器
統一管理所有 WebSocket 連接，避免資源競爭和連接數過多
"""

import asyncio
import time
import logging
from typing import Dict, Optional, Set, Any
from dataclasses import dataclass, field
from enum import Enum
from src.utils.logging_config import get_logger, metrics
from src.utils.error_codes import GridTradingException, ErrorCode

logger = get_logger("websocket_manager")

class WSConnectionState(Enum):
    """WebSocket 連接狀態"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"

@dataclass
class WSConnectionInfo:
    """WebSocket 連接信息"""
    session_id: str
    client: Any
    state: WSConnectionState
    created_at: float
    last_activity: float
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 5
    credentials: Dict[str, Any] = field(default_factory=dict)

class WebSocketManager:
    """WebSocket 連接管理器"""

    def __init__(self, max_connections: int = 50, connection_timeout: float = 300.0):
        """
        初始化 WebSocket 管理器

        Args:
            max_connections: 最大連接數
            connection_timeout: 連接超時時間（秒）
        """
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout

        # 連接管理
        self.connections: Dict[str, WSConnectionInfo] = {}
        self._lock = asyncio.Lock()

        # 連接統計
        self.stats = {
            'total_created': 0,
            'total_failed': 0,
            'current_connected': 0,
            'peak_connections': 0
        }

        # 清理任務
        self._cleanup_task = None
        self._cleanup_interval = 60.0  # 清理間隔60秒

        logger.info("WebSocket 管理器已初始化", data={
            "max_connections": max_connections,
            "connection_timeout": connection_timeout
        })

    async def start(self):
        """啟動 WebSocket 管理器"""
        if self._cleanup_task:
            return

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("WebSocket 管理器已啟動")

    async def stop(self):
        """停止 WebSocket 管理器"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # 關閉所有連接
        async with self._lock:
            for connection_info in self.connections.values():
                await self._close_connection(connection_info)
            self.connections.clear()

        logger.info("WebSocket 管理器已停止")

    async def create_connection(self, session_id: str, client: Any,
                              credentials: Dict[str, Any]) -> bool:
        """
        創建新的 WebSocket 連接

        Args:
            session_id: 會話ID
            client: WebSocket 客戶端
            credentials: 連接憑證

        Returns:
            是否創建成功
        """
        async with self._lock:
            # 檢查連接數限制
            if len(self.connections) >= self.max_connections:
                logger.warning(f"WebSocket 連接數已達上限: {self.max_connections}")
                metrics.increment_counter("websocket.connection_limit_reached")
                raise GridTradingException(
                    error_code=ErrorCode.WEBSOCKET_CONNECTION_LIMIT_EXCEEDED,
                    details={"current_connections": len(self.connections)}
                )

            # 檢查是否已存在連接
            if session_id in self.connections:
                logger.info(f"WebSocket 連接 {session_id} 已存在，更新連接")
                await self._close_connection(self.connections[session_id])

            # 創建連接信息
            current_time = time.time()
            connection_info = WSConnectionInfo(
                session_id=session_id,
                client=client,
                state=WSConnectionState.CONNECTING,
                created_at=current_time,
                last_activity=current_time,
                credentials=credentials
            )

            self.connections[session_id] = connection_info
            self.stats['total_created'] += 1
            self.stats['current_connected'] = len(self.connections)

            if self.stats['current_connected'] > self.stats['peak_connections']:
                self.stats['peak_connections'] = self.stats['current_connected']

            metrics.increment_counter("websocket.connections.created")
            metrics.set_gauge("websocket.connections.active", self.stats['current_connected'])

            logger.info(f"WebSocket 連接 {session_id} 已創建", event_type="ws_connection_created", data={
                "session_id": session_id,
                "total_connections": len(self.connections)
            })

            return True

    async def set_connection_state(self, session_id: str, state: WSConnectionState):
        """
        設置連接狀態

        Args:
            session_id: 會話ID
            state: 新的連接狀態
        """
        async with self._lock:
            if session_id in self.connections:
                connection_info = self.connections[session_id]
                connection_info.state = state
                connection_info.last_activity = time.time()

                logger.debug(f"WebSocket 連接 {session_id} 狀態更新為 {state.value}")

    async def get_connection(self, session_id: str) -> Optional[WSConnectionInfo]:
        """
        獲取連接信息

        Args:
            session_id: 會話ID

        Returns:
            連接信息或 None
        """
        async with self._lock:
            connection_info = self.connections.get(session_id)
            if connection_info:
                connection_info.last_activity = time.time()
            return connection_info

    async def remove_connection(self, session_id: str) -> bool:
        """
        移除 WebSocket 連接

        Args:
            session_id: 會話ID

        Returns:
            是否移除成功
        """
        async with self._lock:
            if session_id not in self.connections:
                return False

            connection_info = self.connections[session_id]
            await self._close_connection(connection_info)
            del self.connections[session_id]

            self.stats['current_connected'] = len(self.connections)
            metrics.increment_counter("websocket.connections.removed")
            metrics.set_gauge("websocket.connections.active", self.stats['current_connected'])

            logger.info(f"WebSocket 連接 {session_id} 已移除", event_type="ws_connection_removed", data={
                "session_id": session_id,
                "remaining_connections": len(self.connections)
            })

            return True

    async def get_all_connections(self) -> Dict[str, WSConnectionInfo]:
        """獲取所有連接信息"""
        async with self._lock:
            return self.connections.copy()

    async def get_stats(self) -> Dict[str, Any]:
        """獲取連接統計信息"""
        async with self._lock:
            state_counts = {}
            for connection_info in self.connections.values():
                state = connection_info.state.value
                state_counts[state] = state_counts.get(state, 0) + 1

            return {
                **self.stats,
                'state_distribution': state_counts,
                'active_connections': len(self.connections)
            }

    async def _close_connection(self, connection_info: WSConnectionInfo):
        """安全關閉 WebSocket 連接"""
        try:
            if connection_info.client:
                # 嘗試多種關閉方法
                close_methods = ['close', 'disconnect', 'close_ws', 'stop', 'shutdown']
                for method_name in close_methods:
                    if hasattr(connection_info.client, method_name):
                        method = getattr(connection_info.client, method_name)
                        if callable(method):
                            try:
                                method()
                                logger.debug(f"WebSocket 連接 {connection_info.session_id} 已通過 {method_name} 關閉")
                                break
                            except Exception as e:
                                logger.warning(f"關閉 WebSocket 連接失敗 (方法: {method_name}): {e}")
                                continue
                else:
                    logger.warning(f"WebSocket 客戶端 {connection_info.session_id} 沒有可用的關閉方法")

            connection_info.state = WSConnectionState.DISCONNECTED

        except Exception as e:
            logger.error(f"關閉 WebSocket 連接 {connection_info.session_id} 時發生錯誤: {e}")

    async def _cleanup_loop(self):
        """清理循環任務"""
        logger.info("WebSocket 清理任務已啟動")

        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired_connections()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket 清理任務發生錯誤: {e}")

        logger.info("WebSocket 清理任務已停止")

    async def _cleanup_expired_connections(self):
        """清理過期的連接"""
        current_time = time.time()
        expired_connections = []

        async with self._lock:
            for session_id, connection_info in self.connections.items():
                # 檢查是否超時
                if current_time - connection_info.last_activity > self.connection_timeout:
                    expired_connections.append(session_id)
                    continue

                # 檢查失敗的連接
                if connection_info.state == WSConnectionState.FAILED:
                    if connection_info.reconnect_attempts >= connection_info.max_reconnect_attempts:
                        expired_connections.append(session_id)
                        continue

        # 移除過期連接
        for session_id in expired_connections:
            logger.info(f"清理過期 WebSocket 連接: {session_id}")
            await self.remove_connection(session_id)

        if expired_connections:
            metrics.increment_counter("websocket.connections.cleaned",
                                   value=len(expired_connections))
            logger.info(f"已清理 {len(expired_connections)} 個過期 WebSocket 連接")

# 全局 WebSocket 管理器實例
_websocket_manager: Optional[WebSocketManager] = None

def get_websocket_manager() -> WebSocketManager:
    """獲取全局 WebSocket 管理器實例"""
    global _websocket_manager
    if _websocket_manager is None:
        _websocket_manager = WebSocketManager()
    return _websocket_manager

async def start_websocket_manager():
    """啟動全局 WebSocket 管理器"""
    manager = get_websocket_manager()
    await manager.start()

async def stop_websocket_manager():
    """停止全局 WebSocket 管理器"""
    global _websocket_manager
    if _websocket_manager:
        await _websocket_manager.stop()
        _websocket_manager = None