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
    """會話事件隊列 - 確保事件順序處理"""
    
    def __init__(self, session_id: str, event_handler: Callable):
        self.session_id = session_id
        self.event_handler = event_handler
        self.queue = asyncio.Queue()
        self.worker_task = None
        self.is_running = False
        
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
        """添加事件到隊列"""
        if not self.is_running and event.event_type != EventType.STOP:
            logger.warning(f"會話 {self.session_id} 事件隊列未運行，忽略事件: {event.event_type}")
            return
            
        await self.queue.put(event)
    
    async def _process_events(self):
        """事件處理循環"""
        logger.info(f"會話 {self.session_id} 開始處理事件")
        
        try:
            while self.is_running:
                try:
                    # 等待事件，帶超時避免無限等待
                    event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                    
                    # 處理停止事件
                    if event.event_type == EventType.STOP:
                        logger.info(f"會話 {self.session_id} 收到停止事件")
                        break
                    
                    # 處理其他事件
                    await self._handle_event(event)
                    
                except asyncio.TimeoutError:
                    # 超時繼續循環，檢查是否需要停止
                    continue
                except Exception as e:
                    logger.error(f"會話 {self.session_id} 處理事件失敗: {e}")
                    
        except Exception as e:
            logger.error(f"會話 {self.session_id} 事件處理器異常: {e}")
        finally:
            logger.info(f"會話 {self.session_id} 事件處理器結束")
    
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
