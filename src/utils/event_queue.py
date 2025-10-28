#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Per-session sequential event queue
確保事件按順序處理，避免競爭條件
"""

import asyncio
import logging
from typing import Callable, Any
from dataclasses import dataclass
from enum import Enum
import time



logger = logging.getLogger(__name__)

class EventType(Enum):
    SIGNAL = "signal"
    ORDER_FILLED = "order_filled"
    STOP = "stop"

@dataclass
class Event:
    """事件數據類"""
    event_type: EventType
    data: Any
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            import time
            self.timestamp = time.time()

class SessionEventQueue:
    """會話事件隊列 - 確保事件順序處理（優化版本）"""

    def __init__(self, session_id: str, event_handler: Callable, max_queue_size: int = 1000):
        self.session_id = session_id
        self.event_handler = event_handler
        self.max_queue_size = max_queue_size
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.worker_task = None
        self.is_running = False

        # 監控統計
        self.stats = {
            'total_events': 0,
            'processed_events': 0,
            'failed_events': 0,
            'dropped_events': 0,
            'peak_queue_size': 0
        }

        # 健康檢查
        self.last_activity = time.time()
        self.cleanup_interval = 60.0  # 清理間隔
        self.stale_threshold = 300.0  # 停滯閾值5分鐘
        
    async def start(self):
        """啟動事件處理器"""
        if self.is_running:
            return
            
        self.is_running = True
        self.worker_task = asyncio.create_task(self._process_events())
        logger.info(f"會話 {self.session_id} 事件隊列已啟動")
    
    async def stop(self):
        """停止事件處理器"""
        if not self.is_running:
            return
            
        self.is_running = False
        
        # 添加停止事件
        await self.add_event(Event(EventType.STOP, None))
        
        # 等待處理器完成
        if self.worker_task:
            try:
                await asyncio.wait_for(self.worker_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"會話 {self.session_id} 事件隊列停止超時")
                self.worker_task.cancel()
        
        logger.info(f"會話 {self.session_id} 事件隊列已停止")
    
    async def add_event(self, event: Event):
        """添加事件到隊列（優化版本，支持隊列滿時丟棄事件）"""
        if not self.is_running and event.event_type != EventType.STOP:
            logger.warning(f"會話 {self.session_id} 事件隊列未運行，忽略事件: {event.event_type}")
            return

        self.stats['total_events'] += 1
        self.last_activity = time.time()

        # 更新峰值隊列大小
        current_size = self.queue.qsize()
        if current_size > self.stats['peak_queue_size']:
            self.stats['peak_queue_size'] = current_size

        # 檢查隊列是否已滿
        if self.queue.full():
            self.stats['dropped_events'] += 1
            logger.warning(f"會話 {self.session_id} 事件隊列已滿，丟棄事件: {event.event_type}")

            # 如果是重要事件（如STOP），嘗試清理空間
            if event.event_type == EventType.STOP:
                # 清理一些較舊的事件為STOP事件讓路
                try:
                    # 非阻塞地獲取並丟棄一些事件
                    dropped = 0
                    while not self.queue.empty() and dropped < 5:
                        try:
                            self.queue.get_nowait()
                            dropped += 1
                        except asyncio.QueueEmpty:
                            break
                    logger.warning(f"為STOP事件清理了 {dropped} 個舊事件")
                except Exception as e:
                    logger.error(f"清理事件隊列失敗: {e}")

        # 嘗試添加事件（非阻塞）
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.stats['dropped_events'] += 1
            logger.error(f"會話 {self.session_id} 事件隊列完全滿，無法添加事件: {event.event_type}")

    async def add_event_with_timeout(self, event: Event, timeout: float = 1.0) -> bool:
        """
        帶超時的事件添加（用於重要事件）

        Args:
            event: 要添加的事件
            timeout: 超時時間

        Returns:
            是否成功添加
        """
        if not self.is_running and event.event_type != EventType.STOP:
            return False

        try:
            await asyncio.wait_for(self.queue.put(event), timeout=timeout)
            self.stats['total_events'] += 1
            self.last_activity = time.time()
            return True
        except asyncio.TimeoutError:
            self.stats['dropped_events'] += 1
            logger.warning(f"會話 {self.session_id} 添加事件超時: {event.event_type}")
            return False

    def get_queue_size(self) -> int:
        """獲取隊列大小"""
        return self.queue.qsize()

    def get_stats(self) -> dict:
        """獲取統計信息"""
        return {
            **self.stats,
            'current_queue_size': self.queue.qsize(),
            'is_running': self.is_running,
            'last_activity': self.last_activity,
            'is_stale': (time.time() - self.last_activity) > self.stale_threshold
        }

    async def cleanup_if_stale(self) -> bool:
        """如果隊列停滯則進行清理"""
        if not self.is_running:
            return False

        time_since_activity = time.time() - self.last_activity
        if time_since_activity > self.stale_threshold:
            logger.warning(f"會話 {self.session_id} 事件隊列停滯 ({time_since_activity:.1f}s)，執行清理")

            # 清理隊列中的舊事件
            try:
                dropped = 0
                while not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                        dropped += 1
                    except asyncio.QueueEmpty:
                        break

                logger.info(f"清理了 {dropped} 個停滯事件")
                self.stats['dropped_events'] += dropped
                return True

            except Exception as e:
                logger.error(f"清理停滯事件隊列失敗: {e}")
                return False

        return False
    
    async def _process_events(self):
        """事件處理循環（優化版本）"""
        logger.info(f"會話 {self.session_id} 開始處理事件")

        last_cleanup_time = time.time()

        try:
            while self.is_running:
                try:
                    # 等待事件，帶超時避免無限等待
                    event = await asyncio.wait_for(self.queue.get(), timeout=1.0)

                    # 更新活動時間
                    self.last_activity = time.time()

                    # 處理停止事件
                    if event.event_type == EventType.STOP:
                        logger.info(f"會話 {self.session_id} 收到停止事件")
                        break

                    # 處理其他事件
                    await self._handle_event(event)
                    self.stats['processed_events'] += 1

                except asyncio.TimeoutError:
                    # 超時檢查是否需要清理
                    current_time = time.time()
                    if current_time - last_cleanup_time > self.cleanup_interval:
                        await self.cleanup_if_stale()
                        last_cleanup_time = current_time
                    continue
                except Exception as e:
                    self.stats['failed_events'] += 1
                    logger.error(f"會話 {self.session_id} 處理事件失敗: {e}")

        except Exception as e:
            logger.error(f"會話 {self.session_id} 事件處理器異常: {e}")
        finally:
            logger.info(f"會話 {self.session_id} 事件處理器結束", data={
                "total_events": self.stats['total_events'],
                "processed_events": self.stats['processed_events'],
                "failed_events": self.stats['failed_events'],
                "dropped_events": self.stats['dropped_events']
            })
    
    async def _handle_event(self, event: Event):
        """處理單個事件"""
        try:
            logger.debug(f"會話 {self.session_id} 處理事件: {event.event_type}")
            await self.event_handler(event)
        except Exception as e:
            logger.error(f"會話 {self.session_id} 事件處理失敗: {event.event_type}, 錯誤: {e}")
    
    def get_queue_size(self) -> int:
        """獲取隊列大小"""
        return self.queue.qsize()
