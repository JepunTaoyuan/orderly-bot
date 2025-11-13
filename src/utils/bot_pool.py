#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GridTradingBot 對象池
實現對象池模式來減少頻繁創建和銷毀 GridTradingBot 實例的成本
"""

import asyncio
import time
from typing import Dict, Any, Optional, Set
from dataclasses import dataclass
from src.utils.logging_config import get_logger
from src.core.grid_bot import GridTradingBot

logger = get_logger("bot_pool")

@dataclass
class PooledBot:
    """池化的 GridTradingBot 實例"""
    bot: GridTradingBot
    account_id: str
    orderly_key: str
    orderly_secret: str
    created_at: float
    last_used: float
    use_count: int = 0
    is_active: bool = True

    def mark_used(self):
        """標記為已使用"""
        self.last_used = time.time()
        self.use_count += 1

    def is_expired(self, max_idle_time: float = 300.0) -> bool:
        """檢查是否過期（空閒時間過長）"""
        return (time.time() - self.last_used) > max_idle_time

class GridTradingBotPool:
    """
    GridTradingBot 對象池
    管理預創建的 bot 實例，支持重用以減少創建開銷
    """

    def __init__(self,
                 max_pool_size: int = 10,
                 max_idle_time: float = 300.0,  # 5分鐘
                 cleanup_interval: float = 60.0):  # 1分鐘清理一次
        """
        初始化對象池

        Args:
            max_pool_size: 最大池大小
            max_idle_time: 最大空閒時間（秒）
            cleanup_interval: 清理過期對象的間隔（秒）
        """
        self.pool: Dict[str, PooledBot] = {}  # key: f"{account_id}_{orderly_key}"
        self.max_pool_size = max_pool_size
        self.max_idle_time = max_idle_time
        self.cleanup_interval = cleanup_interval
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

        # 統計信息
        self.stats = {
            'pool_hits': 0,
            'pool_misses': 0,
            'bot_creations': 0,
            'bot_reuses': 0,
            'pool_evictions': 0,
            'current_size': 0
        }

    async def start(self):
        """啟動對象池"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_bots())
            logger.info("GridTradingBot 對象池已啟動")

    async def stop(self):
        """停止對象池"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        # 清理所有池中的 bot
        async with self._lock:
            for pooled_bot in self.pool.values():
                try:
                    if pooled_bot.bot.is_running:
                        await pooled_bot.bot.stop_grid_trading()
                    logger.debug(f"已清理池中 bot: {pooled_bot.account_id}")
                except Exception as e:
                    logger.warning(f"清理池中 bot 失敗: {e}")

            self.pool.clear()
            self.stats['current_size'] = 0

        logger.info("GridTradingBot 對象池已停止")

    async def get_bot(self,
                      account_id: str,
                      orderly_key: str,
                      orderly_secret: str,
                      force_create: bool = False) -> GridTradingBot:
        """
        獲取 GridTradingBot 實例

        Args:
            account_id: 賬戶ID
            orderly_key: Orderly API Key
            orderly_secret: Orderly API Secret
            force_create: 強制創建新實例

        Returns:
            GridTradingBot 實例
        """
        pool_key = f"{account_id}_{orderly_key}"

        async with self._lock:
            # 嘗試從池中獲取
            if not force_create and pool_key in self.pool:
                pooled_bot = self.pool[pool_key]
                if pooled_bot.is_active and not pooled_bot.bot.is_running:
                    # 重新啟用已停止的 bot
                    pooled_bot.mark_used()
                    self.stats['pool_hits'] += 1
                    self.stats['bot_reuses'] += 1
                    logger.debug(f"從對象池重用 bot: {account_id}")
                    return pooled_bot.bot
                else:
                    # bot 正在運行或無效，從池中移除
                    del self.pool[pool_key]
                    self.stats['current_size'] = len(self.pool)

        # 🚀 創建新的 bot 實例
        bot = GridTradingBot(
            account_id=account_id,
            orderly_key=orderly_key,
            orderly_secret=orderly_secret,
            orderly_testnet=True
        )

        async with self._lock:
            # 添加到池中
            pooled_bot = PooledBot(
                bot=bot,
                account_id=account_id,
                orderly_key=orderly_key,
                orderly_secret=orderly_secret,
                created_at=time.time(),
                last_used=time.time()
            )

            # 檢查池大小限制
            if len(self.pool) >= self.max_pool_size:
                await self._evict_least_recently_used()
                self.stats['pool_evictions'] += 1

            self.pool[pool_key] = pooled_bot
            self.stats['current_size'] = len(self.pool)
            self.stats['pool_misses'] += 1
            self.stats['bot_creations'] += 1

            logger.debug(f"創建新 bot 並加入池: {account_id}")

        return bot

    async def return_bot(self, bot: GridTradingBot) -> None:
        """
        將 bot 實例歸還到池中

        Args:
            bot: 要歸還的 GridTradingBot 實例
        """
        if not hasattr(bot, 'account_id'):
            logger.warning("嘗試歸還沒有 account_id 的 bot")
            return

        pool_key = f"{bot.account_id}_{bot.orderly_key}"

        async with self._lock:
            if pool_key in self.pool:
                pooled_bot = self.pool[pool_key]
                pooled_bot.mark_used()
                logger.debug(f"Bot {bot.account_id} 已歸還到池中")
            else:
                # 池中不存在，可能是被清理了，嘗試重新加入
                if len(self.pool) < self.max_pool_size:
                    pooled_bot = PooledBot(
                        bot=bot,
                        account_id=bot.account_id,
                        orderly_key=getattr(bot, 'orderly_key', ''),
                        orderly_secret=getattr(bot, 'orderly_secret', ''),
                        created_at=time.time(),
                        last_used=time.time()
                    )
                    self.pool[pool_key] = pooled_bot
                    self.stats['current_size'] = len(self.pool)
                    logger.debug(f"Bot {bot.account_id} 已重新加入池中")

    async def remove_bot(self, account_id: str, orderly_key: str) -> bool:
        """
        從池中移除指定的 bot

        Args:
            account_id: 賬戶ID
            orderly_key: Orderly API Key

        Returns:
            是否成功移除
        """
        pool_key = f"{account_id}_{orderly_key}"

        async with self._lock:
            if pool_key in self.pool:
                pooled_bot = self.pool[pool_key]
                try:
                    if pooled_bot.bot.is_running:
                        await pooled_bot.bot.stop_grid_trading()
                except Exception as e:
                    logger.warning(f"停止池中 bot 失敗: {e}")

                del self.pool[pool_key]
                self.stats['current_size'] = len(self.pool)
                logger.debug(f"已從池中移除 bot: {account_id}")
                return True

            return False

    async def get_stats(self) -> Dict[str, Any]:
        """獲取池統計信息"""
        total_requests = self.stats['pool_hits'] + self.stats['pool_misses']
        hit_rate = self.stats['pool_hits'] / total_requests if total_requests > 0 else 0

        return {
            **self.stats,
            'hit_rate': hit_rate,
            'max_pool_size': self.max_pool_size,
            'max_idle_time': self.max_idle_time
        }

    async def _evict_least_recently_used(self):
        """驅逐最近最少使用的 bot"""
        if not self.pool:
            return

        # 找到最久未使用的 bot
        lru_bot = min(self.pool.values(), key=lambda b: b.last_used)

        try:
            if lru_bot.bot.is_running:
                await lru_bot.bot.stop_grid_trading()
        except Exception as e:
            logger.warning(f"停止 LRU bot 失敗: {e}")

        pool_key = f"{lru_bot.account_id}_{lru_bot.orderly_key}"
        del self.pool[pool_key]
        logger.debug(f"已驅逐 LRU bot: {lru_bot.account_id}")

    async def _cleanup_expired_bots(self):
        """定期清理過期的 bot"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)

                async with self._lock:
                    expired_bots = [
                        (pool_key, pooled_bot)
                        for pool_key, pooled_bot in self.pool.items()
                        if pooled_bot.is_expired(self.max_idle_time)
                    ]

                    for pool_key, pooled_bot in expired_bots:
                        try:
                            if pooled_bot.bot.is_running:
                                await pooled_bot.bot.stop_grid_trading()
                        except Exception as e:
                            logger.warning(f"清理過期 bot 失敗: {e}")

                        del self.pool[pool_key]

                    if expired_bots:
                        self.stats['current_size'] = len(self.pool)
                        logger.debug(f"清理了 {len(expired_bots)} 個過期 bot")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理過期 bot 時發生錯誤: {e}")

# 全局對象池實例
bot_pool = GridTradingBotPool()

async def get_bot_pool() -> GridTradingBotPool:
    """獲取全局對象池實例"""
    return bot_pool