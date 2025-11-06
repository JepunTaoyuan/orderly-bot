#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
優化的會話管理器
管理多個網格交易會話，支持高並發操作
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor
from src.core.grid_bot import GridTradingBot
from src.services.database_service import MongoManager
from src.utils.logging_config import get_logger, metrics
from src.utils.error_codes import GridTradingException, ErrorCode
import os

logger = get_logger("session_manager")

class SessionCreationLimiter:
    """Session 創建速率限制器"""

    def __init__(self, max_concurrent: int = 5, max_per_second: int = 10):
        self.max_concurrent = max_concurrent
        self.max_per_second = max_per_second
        self.current_creating: Set[str] = set()
        self.creation_times = []
        self._lock = asyncio.Lock()

    async def acquire(self, session_id: str) -> bool:
        """獲取創建許可"""
        async with self._lock:
            # 檢查並發限制
            if len(self.current_creating) >= self.max_concurrent:
                logger.warning(f"並發 session 創建已達上限: {self.max_concurrent}")
                return False

            # 檢查頻率限制
            current_time = time.time()
            # 清理1秒前的記錄
            self.creation_times = [t for t in self.creation_times if current_time - t < 1.0]

            if len(self.creation_times) >= self.max_per_second:
                logger.warning(f"Session 創建頻率已達上限: {self.max_per_second}/秒")
                return False

            # 記錄此次創建
            self.current_creating.add(session_id)
            self.creation_times.append(current_time)
            return True

    async def release(self, session_id: str):
        """釋放創建許可"""
        async with self._lock:
            self.current_creating.discard(session_id)

class SessionManager:
    def __init__(self):
        """初始化會話管理器"""
        self.sessions: Dict[str, GridTradingBot] = {}
        self._creating_sessions: set = set()  # 追踪正在創建的會話
        self._sessions_lock = asyncio.Lock()
        self._creation_limiter = SessionCreationLimiter()
        self.mongo_manager = MongoManager(os.getenv("MONGODB_URI"))

        # 性能統計
        self.creation_metrics = {
            'total_attempts': 0,
            'successful': 0,
            'failed': 0,
            'rate_limited': 0
        }
    
    async def create_session(self, session_id: str, config: Dict[str, Any]) -> bool:
        """
        創建新的交易會話（優化版本，支持高並發）

        Args:
            session_id: 會話ID
            config: 網格配置

        Returns:
            是否創建成功
        """
        start_time = time.time()
        self.creation_metrics['total_attempts'] += 1
        metrics.increment_counter("session.create.attempts")

        # 使用速率限制器
        if not await self._creation_limiter.acquire(session_id):
            self.creation_metrics['rate_limited'] += 1
            metrics.increment_counter("session.create.rate_limited")
            logger.warning(f"Session {session_id} 創建被速率限制器阻擋")
            raise GridTradingException(
                error_code=ErrorCode.SESSION_CREATE_RATE_LIMITED,
                details={"session_id": session_id}
            )

        try:
            # 使用細粒度鎖：先檢查是否已存在
            async with self._sessions_lock:
                if session_id in self.sessions:
                    logger.warning(f"會話 {session_id} 已存在")
                    await self._creation_limiter.release(session_id)
                    return False

                if session_id in self._creating_sessions:
                    logger.warning(f"會話 {session_id} 正在創建中")
                    await self._creation_limiter.release(session_id)
                    return False

                # 標記為創建中
                self._creating_sessions.add(session_id)

            # 釋放鎖，執行耗時的創建操作
            try:
                # 從數據庫獲取用戶憑證
                user_id = config.get('user_id')
                if not user_id:
                    raise ValueError("配置中缺少 user_id")

                user_data = await self.mongo_manager.get_user(user_id)
                if not user_data:
                    raise ValueError(f"用戶 {user_id} 不存在")

                # 創建 GridTradingBot 實例，傳入用戶憑證
                wallet_address = user_data.get('wallet_address') or user_data.get('evm_wallet_address')
                bot = GridTradingBot(
                    account_id=user_id,
                    orderly_key=user_data.get('api_key'),
                    orderly_secret=user_data.get('api_secret'),
                    orderly_testnet=True  # 可以從配置或環境變數獲取
                )

                # 將用戶憑證添加到配置中，供 GridTradingBot 使用
                enhanced_config = config.copy()
                enhanced_config.update({
                    'orderly_account_id': user_id,
                    'orderly_key': user_data.get('api_key'),
                    'orderly_secret': user_data.get('api_secret'),
                    'orderly_testnet': True
                })

                # 啟動網格交易
                await bot.start_grid_trading(enhanced_config)

                # 再次獲取鎖來更新 sessions
                async with self._sessions_lock:
                    self.sessions[session_id] = bot
                    self._creating_sessions.discard(session_id)

                # 記錄成功指標
                self.creation_metrics['successful'] += 1
                elapsed_time = time.time() - start_time
                metrics.record_histogram("session.create.duration", elapsed_time)
                metrics.increment_counter("session.create.success")

                logger.info(f"會話 {session_id} 創建成功", event_type="session_created", data={
                    "session_id": session_id,
                    "creation_time": elapsed_time,
                    "active_sessions": len(self.sessions)
                })
                return True

            except Exception as e:
                # 清理創建中標記
                async with self._sessions_lock:
                    self._creating_sessions.discard(session_id)

                # 記錄失敗指標
                self.creation_metrics['failed'] += 1
                metrics.increment_counter("session.create.failed", tags={"error": type(e).__name__})

                logger.error(f"創建會話 {session_id} 失敗", event_type="session_create_failed", data={
                    "session_id": session_id,
                    "error": str(e),
                    "creation_time": time.time() - start_time
                })
                raise

        finally:
            # 確保釋放速率限制器
            await self._creation_limiter.release(session_id)

    async def create_session_batch(self, session_configs: list[tuple[str, dict]]) -> dict[str, bool]:
        """
        批量創建會話（支持高並發）

        Args:
            session_configs: [(session_id, config), ...] 的列表

        Returns:
            {session_id: success_bool} 的字典
        """
        logger.info(f"開始批量創建 {len(session_configs)} 個會話")

        # 使用 asyncio.gather 並發創建
        tasks = []
        for session_id, config in session_configs:
            task = self.create_session(session_id, config)
            tasks.append((session_id, task))

        results = {}

        # 使用並發限制來避免過多同時創建
        semaphore = asyncio.Semaphore(3)  # 最多同時3個創建操作

        async def limited_create(session_id: str, config: dict) -> tuple[str, bool]:
            async with semaphore:
                try:
                    return session_id, await self.create_session(session_id, config)
                except Exception as e:
                    logger.error(f"批量創建會話 {session_id} 失敗: {e}")
                    return session_id, False

        # 執行並發創建
        completed_tasks = await asyncio.gather(
            *[limited_create(sid, cfg) for sid, cfg in session_configs],
            return_exceptions=True
        )

        for result in completed_tasks:
            if isinstance(result, Exception):
                logger.error(f"批量創建過程中發生異常: {result}")
                continue
            session_id, success = result
            results[session_id] = success

        successful_count = sum(results.values())
        logger.info(f"批量創建完成: {successful_count}/{len(session_configs)} 成功")

        return results
    
    async def stop_session(self, session_id: str) -> bool:
        """
        停止交易會話

        Args:
            session_id: 會話ID

        Returns:
            是否停止成功
        """
        async with self._sessions_lock:
            if session_id not in self.sessions:
                logger.warning(f"會話 {session_id} 不存在")
                # 即使會話不存在，也要清理 _creating_sessions
                self._creating_sessions.discard(session_id)
                return False

            bot = self.sessions[session_id]
            stop_successful = False
            cleanup_errors = []

            try:
                # 嘗試正常停止網格交易
                await bot.stop_grid_trading()
                stop_successful = True
                logger.info(f"會話 {session_id} 正常停止")
            except Exception as e:
                # 記錄停止錯誤但不重新拋出，因為會話需要被清理
                cleanup_errors.append(f"停止錯誤: {str(e)}")
                logger.warning(f"停止會話 {session_id} 時發生錯誤: {e}")
                # 即使停止失敗，也要繼續清理流程
            finally:
                # 無論停止是否成功，都要清理會話數據
                try:
                    del self.sessions[session_id]
                    self._creating_sessions.discard(session_id)

                    if cleanup_errors:
                        logger.warning(f"會話 {session_id} 已清理，但有 {len(cleanup_errors)} 個警告: {'; '.join(cleanup_errors)}")
                    else:
                        logger.info(f"會話 {session_id} 已成功停止並清理")

                    # 如果核心停止功能成功，或即使有警告但會話已清理，都返回 True
                    return True

                except Exception as cleanup_error:
                    # 清理本身的錯誤
                    logger.error(f"清理會話 {session_id} 數據時發生錯誤: {cleanup_error}")
                    raise GridTradingException(
                        error_code=ErrorCode.SESSION_STOP_FAILED,
                        details={"session_id": session_id, "cleanup_error": str(cleanup_error)},
                        original_error=cleanup_error
                    )

    async def force_cleanup_session(self, session_id: str) -> bool:
        """
        強制清理會話的所有相關數據

        Args:
            session_id: 會話ID

        Returns:
            是否清理成功
        """
        async with self._sessions_lock:
            # 清理所有可能的殘留
            was_in_sessions = session_id in self.sessions
            was_in_creating = session_id in self._creating_sessions

            if was_in_sessions:
                try:
                    bot = self.sessions[session_id]
                    if bot.is_running:
                        # 設置超時以避免卡死
                        try:
                            await asyncio.wait_for(bot.stop_grid_trading(), timeout=10.0)
                        except asyncio.TimeoutError:
                            logger.warning(f"停止會話 {session_id} 超時，強制移除")
                        except Exception as e:
                            logger.error(f"強制停止會話 {session_id} 失敗: {e}")
                    del self.sessions[session_id]
                except Exception as e:
                    logger.error(f"強制清理會話 {session_id} 時發生錯誤: {e}")
                    # 強制刪除，即使停止失敗
                    del self.sessions[session_id]

            # 清理創建中標記
            self._creating_sessions.discard(session_id)

            cleaned = was_in_sessions or was_in_creating
            if cleaned:
                logger.info(f"強制清理會話 {session_id}")

            return cleaned

    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取會話狀態
        
        Args:
            session_id: 會話ID
            
        Returns:
            會話狀態或None
        """
        async with self._sessions_lock:
            if session_id not in self.sessions:
                return None
            
            try:
                bot = self.sessions[session_id]
                status = await bot.get_status()
                return status
            except Exception as e:
                logger.error(f"獲取會話 {session_id} 狀態失敗: {e}")
                return None
    
    async def list_sessions(self) -> Dict[str, bool]:
        """
        列出所有會話

        Returns:
            會話ID和運行狀態的字典
        """
        async with self._sessions_lock:
            return {sid: bot.is_running for sid, bot in self.sessions.items()}

    async def get_user_sessions(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """
        獲取指定用戶的所有活躍網格策略會話

        Args:
            user_id: 用戶ID

        Returns:
            該用戶的所有會話詳細信息字典
        """
        async with self._sessions_lock:
            user_sessions = {}

            for session_id, bot in self.sessions.items():
                # 解析session_id中的user_id (格式: user_id_ticker)
                session_user_id = session_id.split('_', 1)[0] if '_' in session_id else session_id

                if session_user_id == user_id and bot.is_running:
                    try:
                        # 獲取會話狀態
                        status = await bot.get_status()

                        # 從session_id提取ticker
                        ticker = session_id.split('_', 1)[1] if '_' in session_id else 'unknown'

                        user_sessions[session_id] = {
                            'session_id': session_id,
                            'user_id': user_id,
                            'ticker': ticker,
                            'is_running': bot.is_running,
                            'status': status,
                        }
                    except Exception as e:
                        logger.error(f"獲取會話 {session_id} 狀態失敗: {e}")
                        # 即使獲取狀態失敗，也返回基本資訊
                        user_sessions[session_id] = {
                            'session_id': session_id,
                            'user_id': user_id,
                            'ticker': session_id.split('_', 1)[1] if '_' in session_id else 'unknown',
                            'is_running': bot.is_running,
                            'status': None,
                            'error': str(e)
                        }

            return user_sessions
    
    async def stop_all_sessions(self):
        """停止所有會話"""
        async with self._sessions_lock:
            session_ids = list(self.sessions.keys())
            
        for session_id in session_ids:
            await self.stop_session(session_id)
        
        logger.info("所有會話已停止")
