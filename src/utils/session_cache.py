#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
會話狀態緩存管理器
提供高效的會話狀態緩存，減少重複數據庫查詢
"""

import asyncio
import time
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass
from src.utils.logging_config import get_logger

logger = get_logger("session_cache")

@dataclass
class SessionCacheEntry:
    """會話緩存條目"""
    session_id: str
    data: Dict[str, Any]
    timestamp: float
    ttl: float = 30.0  # 默認30秒過期

    def is_expired(self) -> bool:
        """檢查是否過期"""
        return time.time() - self.timestamp > self.ttl

    def refresh(self, new_data: Dict[str, Any], new_ttl: float = None):
        """刷新緩存條目"""
        self.data = new_data
        self.timestamp = time.time()
        if new_ttl:
            self.ttl = new_ttl

class SessionStateCache:
    """會話狀態緩存管理器"""

    def __init__(self,
                 max_size: int = 1000,
                 cleanup_interval: float = 60.0,
                 default_ttl: float = 30.0):
        """
        初始化緩存管理器

        Args:
            max_size: 最大緩存條目數
            cleanup_interval: 清理過期條目的間隔（秒）
            default_ttl: 默認過期時間（秒）
        """
        self.cache: Dict[str, SessionCacheEntry] = {}
        self.max_size = max_size
        self.cleanup_interval = cleanup_interval
        self.default_ttl = default_ttl
        self._lock = asyncio.Lock()  # 使用普通鎖（Python 標準庫沒有 RWLock）
        self._cleanup_task: Optional[asyncio.Task] = None
        self._access_count: Dict[str, int] = {}  # LRU 計數器

        # 緩存統計
        self.stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'cleanups': 0,
            'size': 0
        }

    async def start(self):
        """啟動緩存管理器"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_entries())
            logger.info("會話狀態緩存已啟動")

    async def stop(self):
        """停止緩存管理器"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("會話狀態緩存已停止")

    async def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取會話狀態

        Args:
            session_id: 會話ID

        Returns:
            會話狀態數據，如果不存在或已過期則返回None
        """
        async with self._lock:
            entry = self.cache.get(session_id)
            if entry is None:
                self.stats['misses'] += 1
                return None

            if entry.is_expired():
                # 刪除過期條目
                del self.cache[session_id]
                self._access_count.pop(session_id, None)
                self.stats['size'] = len(self.cache)

                self.stats['misses'] += 1
                return None

            # 更新 LRU 計數器
            self._access_count[session_id] = self._access_count.get(session_id, 0) + 1
            self.stats['hits'] += 1
            return entry.data.copy()

    async def set(self, session_id: str, data: Dict[str, Any], ttl: float = None) -> None:
        """
        設置會話狀態

        Args:
            session_id: 會話ID
            data: 會話狀態數據
            ttl: 過期時間（秒），如果為None則使用默認值
        """
        current_time = time.time()
        cache_ttl = ttl or self.default_ttl

        async with self._lock:
            # 檢查是否需要驅逐條目
            if session_id not in self.cache and len(self.cache) >= self.max_size:
                await self._evict_lru()

            # 創建或更新緩存條目
            if session_id in self.cache:
                self.cache[session_id].refresh(data, cache_ttl)
            else:
                self.cache[session_id] = SessionCacheEntry(
                    session_id=session_id,
                    data=data,
                    timestamp=current_time,
                    ttl=cache_ttl
                )

            self._access_count[session_id] = 1
            self.stats['size'] = len(self.cache)

    async def invalidate(self, session_id: str) -> bool:
        """
        使指定會話緩存失效

        Args:
            session_id: 會話ID

        Returns:
            是否成功刪除
        """
        async with self._lock:
            if session_id in self.cache:
                del self.cache[session_id]
                self._access_count.pop(session_id, None)
                self.stats['size'] = len(self.cache)
                return True
            return False

    async def invalidate_batch(self, session_ids: List[str]) -> int:
        """
        批量使會話緩存失效

        Args:
            session_ids: 會話ID列表

        Returns:
            成功刪除的數量
        """
        async with self._lock:
            deleted_count = 0
            for session_id in session_ids:
                if session_id in self.cache:
                    del self.cache[session_id]
                    self._access_count.pop(session_id, None)
                    deleted_count += 1

            self.stats['size'] = len(self.cache)
            return deleted_count

    async def clear(self) -> int:
        """
        清空所有緩存

        Returns:
            清空的條目數量
        """
        async with self._lock:
            count = len(self.cache)
            self.cache.clear()
            self._access_count.clear()
            self.stats['size'] = 0
            return count

    async def get_stats(self) -> Dict[str, Any]:
        """獲取緩存統計信息"""
        total_requests = self.stats['hits'] + self.stats['misses']
        hit_rate = self.stats['hits'] / total_requests if total_requests > 0 else 0

        return {
            **self.stats,
            'hit_rate': hit_rate,
            'max_size': self.max_size,
            'cleanup_interval': self.cleanup_interval,
            'default_ttl': self.default_ttl
        }

    async def _evict_lru(self):
        """驅逐最近最少使用的條目"""
        if not self._access_count:
            return

        # 找到訪問次數最少的條目
        lru_session_id = min(self._access_count.items(), key=lambda x: x[1])[0]

        if lru_session_id in self.cache:
            del self.cache[lru_session_id]
            self._access_count.pop(lru_session_id, None)
            self.stats['evictions'] += 1

    async def _cleanup_expired_entries(self):
        """定期清理過期條目"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)

                async with self._lock:
                    expired_sessions = [
                        session_id
                        for session_id, entry in self.cache.items()
                        if entry.is_expired()
                    ]

                    for session_id in expired_sessions:
                        del self.cache[session_id]
                        self._access_count.pop(session_id, None)

                    if expired_sessions:
                        self.stats['cleanups'] += 1
                        self.stats['size'] = len(self.cache)
                        logger.debug(f"清理了 {len(expired_sessions)} 個過期緩存條目")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理過期緩存條目時發生錯誤: {e}")

# 全局緩存實例
session_cache = SessionStateCache()

async def get_session_cache() -> SessionStateCache:
    """獲取全局會話緩存實例"""
    return session_cache