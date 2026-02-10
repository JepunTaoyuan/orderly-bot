#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
優化的會話管理器
管理多個網格交易會話，支持高並發操作
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, Set, List
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from src.core.grid_bot import GridTradingBot
from src.utils.mongo_manager import MongoManager
from src.services.database_connection import db_manager
from src.utils.logging_config import get_logger, metrics
from src.utils.error_codes import GridTradingException, ErrorCode
from src.models.copy_trading import TradingMode
from src.utils.session_cache import get_session_cache, SessionStateCache
from src.utils.bot_pool import get_bot_pool, GridTradingBotPool
from src.utils.api_batch_optimizer import get_api_optimizer, APIBatchOptimizer
from src.utils.session_recovery_manager import SessionRecoveryManager
from src.interfaces.session_manager_interface import SessionManagerInterface
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

class SessionManager(SessionManagerInterface):
    def __init__(self):
        """初始化會話管理器"""
        self.sessions: Dict[str, GridTradingBot] = {}
        self._creating_sessions: set = set()  # 追踪正在創建的會話

        # 🚀 優化：使用更細粒度的鎖機制
        self._sessions_lock = asyncio.Lock()  # 主要會話操作鎖
        self._creation_lock = asyncio.Lock()  # 創建操作專用鎖
        self._user_session_locks = defaultdict(asyncio.Lock)  # 用戶級別的鎖，避免用戶間互相阻塞

        self._creation_limiter = SessionCreationLimiter()
        # 🚀 優化：將在初始化後設置，以避免創建重複連接池
        self.mongo_manager = None

        # 🆕 Copy Trading 互斥機制：追蹤每個用戶的交易模式
        self._user_trading_modes: Dict[str, TradingMode] = {}
        self._trading_mode_lock = asyncio.Lock()

        # 性能統計
        self.creation_metrics = {
            'total_attempts': 0,
            'successful': 0,
            'failed': 0,
            'rate_limited': 0
        }

    async def initialize(self):
        """初始化 SessionManager，設置 MongoManager、緩存和對象池"""
        if self.mongo_manager is None:
            # 🚀 優化：使用統一的數據庫管理器獲取 MongoManager
            self.mongo_manager = await db_manager.get_mongo_manager()
            logger.info("SessionManager 已使用統一數據庫連接池初始化")

        # 🚀 優化：初始化會話狀態緩存
        self.session_cache = await get_session_cache()
        await self.session_cache.start()
        logger.info("SessionManager 緩存系統已啟動")

        # 🚀 優化：初始化 GridTradingBot 對象池
        self.bot_pool = await get_bot_pool()
        await self.bot_pool.start()
        logger.info("SessionManager 對象池已啟動")

        # 🚀 優化：初始化 API 批量調用優化器
        self.api_optimizer = await get_api_optimizer()
        await self.api_optimizer.start()
        logger.info("SessionManager API 優化器已啟動")

        # 🚀 新增：初始化會話恢復管理器
        self.recovery_manager = SessionRecoveryManager(self)
        await self.recovery_manager.start_monitoring()
        logger.info("SessionManager 恢復管理器已啟動")
    
    async def _validate_session_uniqueness(self, session_id: str, config: Dict[str, Any]) -> None:
        """
        驗證會話唯一性：確保同一個 ticker-account 組合只能有一個活躍會話

        Args:
            session_id: 會話ID
            config: 網格配置

        Raises:
            GridTradingException: 如果發現重複的網格會話
        """
        # 從配置中獲取 user_id 和 ticker（最可靠的來源）
        user_id = config.get('user_id')
        ticker = config.get('ticker')

        if not user_id:
            logger.warning(f"配置中缺少 user_id")
            return

        if not ticker:
            logger.warning(f"配置中缺少 ticker")
            return

        # 也嘗試從 session_id 解析作為備份
        user_id_from_id = None
        ticker_from_id = None
        if '_' in session_id:
            parts = session_id.split('_', 1)
            if len(parts) == 2:
                user_id_from_id = parts[0]
                ticker_from_id = parts[1]

        # 驗證一致性（可選，用於調試）
        if user_id_from_id and user_id_from_id != user_id:
            logger.warning(f"Session ID 和配置中的 user_id 不一致: {user_id_from_id} vs {user_id}")

        if ticker_from_id and ticker_from_id != ticker:
            logger.warning(f"Session ID 和配置中的 ticker 不一致: {ticker_from_id} vs {ticker}")

        # 檢查是否有相同的 ticker-account 組合
        async with self._sessions_lock:
            for existing_session_id, bot in self.sessions.items():
                if not bot.is_running:
                    continue

                # 對於現有會話，我們需要獲取它們的配置信息
                # 由於我們在創建時保存了配置，可以通過其他方式獲取
                # 但為了簡化，我們使用基於模式匹配的方法

                # 使用更智能的解析：尋找 PERP_ 模式來分離 user_id 和 ticker
                if '_PERP_' in existing_session_id:
                    # 格式：user_id_PERP_[SYMBOL]_USDC
                    perp_index = existing_session_id.find('_PERP_')
                    existing_user_id = existing_session_id[:perp_index]
                    existing_ticker = existing_session_id[perp_index + 1:]  # 從 PERP_ 開始
                else:
                    # 後備方案：簡單分割
                    if '_' in existing_session_id:
                        existing_user_id = existing_session_id.split('_', 1)[0]
                        existing_ticker = existing_session_id.split('_', 1)[1]
                    else:
                        existing_user_id = existing_session_id
                        existing_ticker = 'unknown'

                # 檢查是否為相同組合
                if existing_user_id == user_id and existing_ticker == ticker:
                    logger.warning(f"發現重複的網格會話: 現有會話 {existing_session_id}，新會話 {session_id}")
                    raise GridTradingException(
                        error_code=ErrorCode.DUPLICATE_GRID_SESSION,
                        details={
                            "existing_session_id": existing_session_id,
                            "new_session_id": session_id,
                            "user_id": user_id,
                            "ticker": ticker,
                            "message": f"用戶 {user_id} 在交易對 {ticker} 上已有活躍的網格會話 {existing_session_id}"
                        }
                    )

        # 同時檢查數據庫中是否有重複記錄
        try:
            # 查詢數據庫中相同的 ticker-account 組合
            existing_sessions = await self.mongo_manager.get_user_sessions(user_id)
            for existing_session in existing_sessions:
                if (existing_session.get('ticker') == ticker and
                    existing_session.get('status') == 'active' and
                    existing_session.get('session_id') != session_id):
                    logger.warning(f"數據庫中發現重複的網格會話: {existing_session.get('session_id')}")
                    raise GridTradingException(
                        error_code=ErrorCode.DUPLICATE_GRID_SESSION,
                        details={
                            "existing_session_id": existing_session.get('session_id'),
                            "new_session_id": session_id,
                            "user_id": user_id,
                            "ticker": ticker,
                            "message": f"數據庫中發現用戶 {user_id} 在交易對 {ticker} 上有其他活躍會話"
                        }
                    )
        except Exception as e:
            # 如果數據庫查詢失敗，記錄警告但不阻止會話創建
            if isinstance(e, GridTradingException):
                raise
            logger.error(f"查詢數據庫檢查會話唯一性失敗: {e}")

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
            # 🆕 檢查交易模式衝突（Grid Trading vs Copy Trading 互斥）
            user_id = config.get('user_id')
            if user_id:
                if await self.check_trading_mode_conflict(user_id, TradingMode.GRID):
                    await self._creation_limiter.release(session_id)
                    raise GridTradingException(
                        error_code=ErrorCode.TRADING_MODE_CONFLICT,
                        details={
                            "user_id": user_id,
                            "current_mode": (await self.get_user_trading_mode(user_id)).value if await self.get_user_trading_mode(user_id) else "unknown",
                            "requested_mode": "grid"
                        }
                    )

            # 驗證會話唯一性
            await self._validate_session_uniqueness(session_id, config)

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

                # 🚀 優化：從對象池獲取 GridTradingBot 實例
                wallet_address = user_data.get('wallet_address') or user_data.get('evm_wallet_address')
                
                # -------------------------------------------------------------
                # 🆕 原生子帳戶集成 (Native Sub-Account Integration)
                # -------------------------------------------------------------
                # 當前策略：為每個網格會話創建一個獨立的子帳戶以隔離資金
                
                # 1. 初始化臨時客戶端（使用主帳戶身份）
                from src.core.client import OrderlyClient
                main_account_id = user_id
                temp_client = OrderlyClient(
                    account_id=main_account_id,
                    orderly_key=user_data.get('api_key'),
                    orderly_secret=user_data.get('api_secret'),
                    orderly_testnet=True # 假設默認使用測試網，需確認環境配置
                )
                
                sub_account_id = config.get('sub_account_id')
                initial_investment = float(config.get('initial_investment', 0))
                
                # 2. 如果沒有提供子帳戶，則創建一個新的
                if not sub_account_id:
                    try:
                        logger.info(f"為會話 {session_id} 創建新的子帳戶...")
                        sub_acc_desc = f"Grid_{session_id}"[:30] # 描述長度可能有限制
                        sub_acc_res = await temp_client.add_sub_account(description=sub_acc_desc)
                        
                        if sub_acc_res and sub_acc_res.get('success'):
                            sub_account_id = sub_acc_res['data']['sub_account_id']
                            logger.info(f"子帳戶創建成功: {sub_account_id}")
                            
                            # 保存子帳戶ID到配置，以便後續使用和恢復
                            config['sub_account_id'] = sub_account_id
                        else:
                            raise GridTradingException(
                                error_code=ErrorCode.API_ERROR,
                                details={"message": "無法創建子帳戶", "response": sub_acc_res}
                            )
                    except Exception as e:
                        logger.error(f"創建子帳戶失敗: {e}")
                        raise
                
                # 3. 資金劃轉：從主帳戶 -> 子帳戶
                if initial_investment > 0:
                    try:
                        logger.info(f"正在將 {initial_investment} USDC 劃轉至子帳戶 {sub_account_id}...")
                        transfer_res = await temp_client.internal_transfer(
                            token="USDC",
                            receiver_list=[{
                                "account_id": sub_account_id,
                                "amount": initial_investment
                            }]
                        )
                        
                        if not transfer_res or not transfer_res.get('success'):
                            raise GridTradingException(
                                error_code=ErrorCode.INSUFFICIENT_BALANCE,
                                details={"message": "資金劃轉失敗", "response": transfer_res}
                            )
                            
                        logger.info("資金劃轉成功，等待餘額更新...")
                        # 稍微等待餘額更新
                        await asyncio.sleep(2.0)
                        
                    except Exception as e:
                        logger.error(f"資金劃轉失敗: {e}")
                        raise
                
                # -------------------------------------------------------------
                
                # 這裡我們初始化 Bot 時，傳入的是主帳戶的 Key，但在增強配置中指定 sub_account_id
                # Bot 內部的 Client 初始化會使用 config 中的 orderly_account_id
                bot = await self.bot_pool.get_bot(
                    account_id=main_account_id, # Bot Pool 緩存鍵仍使用主帳戶 ID
                    orderly_key=user_data.get('api_key'),
                    orderly_secret=user_data.get('api_secret')
                )

                # 將用戶憑證添加到配置中，供 GridTradingBot 使用
                # 關鍵修改：將 orderly_account_id 設置為 sub_account_id
                enhanced_config = config.copy()
                enhanced_config.update({
                    'orderly_account_id': sub_account_id,  # ⭐ 使用子帳戶 ID
                    'main_account_id': main_account_id,    # 保留主帳戶 ID 備查
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

                # 🆕 註冊交易模式
                await self.register_trading_mode(user_id, TradingMode.GRID)

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
        # 鎖內僅做讀取與存在性檢查，避免長時間持鎖
        async with self._sessions_lock:
            if session_id not in self.sessions:
                logger.warning(f"會話 {session_id} 不存在")
                self._creating_sessions.discard(session_id)
                return False
            bot = self.sessions[session_id]

        stop_successful = False
        cleanup_errors = []
        stop_error = None

        try:
            await bot.stop_grid_trading()
            stop_successful = True
            logger.info(f"會話 {session_id} 正常停止")
        except Exception as e:
            stop_error = e
            cleanup_errors.append(f"停止錯誤: {str(e)}")
            logger.warning(f"停止會話 {session_id} 時發生錯誤: {e}")

        # 釋放鎖後再獲鎖進行最終清理與狀態更新
        async with self._sessions_lock:
            try:
                if session_id in self.sessions:
                    del self.sessions[session_id]
                self._creating_sessions.discard(session_id)

                if cleanup_errors:
                    logger.warning(f"會話 {session_id} 已清理，但有 {len(cleanup_errors)} 個警告: {'; '.join(cleanup_errors)}")
                else:
                    logger.info(f"會話 {session_id} 已成功停止並清理")

                # 🚀 優化：清理相關緩存
                await self._clear_session_cache(session_id)

                # 🆕 檢查用戶是否還有其他 Grid Trading 會話，若無則取消交易模式註冊
                user_id = session_id.split('_', 1)[0] if '_' in session_id else session_id
                has_other_grid_sessions = False
                for other_session_id in self.sessions:
                    other_user_id = other_session_id.split('_', 1)[0] if '_' in other_session_id else other_session_id
                    if other_user_id == user_id:
                        has_other_grid_sessions = True
                        break

                if not has_other_grid_sessions:
                    await self.unregister_trading_mode(user_id, TradingMode.GRID)

                # 🚀 優化：將 bot 歸還到對象池
                if hasattr(self, 'bot_pool') and stop_successful:
                    try:
                        await self.bot_pool.return_bot(bot)
                        logger.debug(f"已將 bot 歸還到對象池: {session_id}")
                    except Exception as e:
                        logger.warning(f"歸還 bot 到對象池失敗: {e}")

                if stop_error is not None:
                    raise stop_error
                return True
            except Exception as cleanup_error:
                logger.error(f"清理會話 {session_id} 數據時發生錯誤: {cleanup_error}")
                raise GridTradingException(
                    error_code=ErrorCode.SESSION_STOP_FAILED,
                    details={"session_id": session_id, "cleanup_error": str(cleanup_error)},
                    original_error=cleanup_error
                )

    async def restart_session(self, session_id: str) -> bool:
        """
        重啟交易會話

        Args:
            session_id: 會話ID

        Returns:
            是否重啟成功
        """
        # 先獲取會話配置以便重啟
        async with self._sessions_lock:
            if session_id not in self.sessions:
                logger.warning(f"會話 {session_id} 不存在，無法重啟")
                return False

            bot = self.sessions[session_id]

        try:
            # 獲取會話配置
            status = await bot.get_status()
            config = status.get('config', {}) if status else {}

            # 停止現有會話
            await self.stop_session(session_id)

            # 短暫等待確保完全停止
            await asyncio.sleep(1)

            # 重新創建會話
            return await self.create_session(session_id, config)

        except Exception as e:
            logger.error(f"重啟會話 {session_id} 失敗: {e}")
            return False

    async def _clear_session_cache(self, session_id: str):
        """
        清理會話相關的緩存條目

        Args:
            session_id: 會話ID
        """
        if not hasattr(self, 'session_cache'):
            return

        try:
            # 解析用戶ID
            user_id = session_id.split('_', 1)[0] if '_' in session_id else session_id

            # 清理用戶會話緩存
            cache_key = f"user_sessions_{user_id}"
            await self.session_cache.invalidate(cache_key)

            # 清理個別會話緩存（如果有）
            await self.session_cache.invalidate(session_id)

            logger.debug(f"已清理會話 {session_id} 的相關緩存")

        except Exception as e:
            logger.warning(f"清理會話 {session_id} 緩存時發生錯誤: {e}")

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

    async def get_user_sessions(self, user_id: str, use_cache: bool = True) -> Dict[str, Dict[str, Any]]:
        """
        獲取指定用戶的所有活躍網格策略會話

        Args:
            user_id: 用戶ID
            use_cache: 是否使用緩存（默認True）

        Returns:
            該用戶的所有會話詳細信息字典
        """
        # 🚀 優化：嘗試從緩存獲取
        cache_key = f"user_sessions_{user_id}"
        if use_cache and hasattr(self, 'session_cache'):
            cached_data = await self.session_cache.get(cache_key)
            if cached_data:
                logger.debug(f"從緩存獲取用戶 {user_id} 的會話數據")
                return cached_data

        # 🚀 優化：使用用戶級別的鎖，避免用戶間互相阻塞
        user_lock = self._user_session_locks[user_id]
        async with user_lock:
            # 🚀 優化：讀取操作使用最小鎖定時間
            async with self._sessions_lock:
                # 快速複製相關會話信息，然後釋放鎖
                user_session_items = []
                for session_id, bot in self.sessions.items():
                    # 解析session_id中的user_id (格式: user_id_ticker)
                    session_user_id = session_id.split('_', 1)[0] if '_' in session_id else session_id

                    if session_user_id == user_id and bot.is_running:
                        user_session_items.append((session_id, bot))

            # 🚀 優化：在鎖外執行並行狀態獲取
            user_sessions = {}

            if user_session_items:
                # 並行獲取所有會話狀態
                session_tasks = []
                session_ids = []

                for session_id, bot in user_session_items:
                    session_ids.append(session_id)
                    session_tasks.append(self._get_session_status_safe(session_id, bot, user_id))

                try:
                    results = await asyncio.gather(*session_tasks, return_exceptions=True)

                    for i, result in enumerate(results):
                        session_id = session_ids[i]

                        if isinstance(result, Exception):
                            # 處理異常
                            logger.error(f"獲取會話 {session_id} 狀態失敗: {result}")
                            user_sessions[session_id] = {
                                'session_id': session_id,
                                'user_id': user_id,
                                'ticker': session_id.split('_', 1)[1] if '_' in session_id else 'unknown',
                                'is_running': False,
                                'status': None,
                                'error': str(result)
                            }
                        else:
                            user_sessions[session_id] = result

                except Exception as e:
                    logger.error(f"批量獲取會話狀態時發生錯誤: {e}")
                    # 如果批量獲取失敗，回退到串行處理
                    for session_id, _ in user_session_items:
                        user_sessions[session_id] = {
                            'session_id': session_id,
                            'user_id': user_id,
                            'ticker': session_id.split('_', 1)[1] if '_' in session_id else 'unknown',
                            'is_running': False,
                            'status': None,
                            'error': '批量獲取失敗'
                        }

            # 🚀 優化：緩存結果（較短的TTL，因為會話狀態變化頻繁）
            if use_cache and hasattr(self, 'session_cache') and user_sessions:
                await self.session_cache.set(cache_key, user_sessions, ttl=5.0)  # 5秒緩存
                logger.debug(f"已緩存用戶 {user_id} 的 {len(user_sessions)} 個會話")

            return user_sessions

    async def _get_session_status_safe(self, session_id: str, bot, user_id: str) -> Dict[str, Any]:
        """
        安全獲取單個會話狀態，包含錯誤處理

        Args:
            session_id: 會話ID
            bot: GridTradingBot實例
            user_id: 用戶ID

        Returns:
            會話狀態字典
        """
        try:
            # 獲取會話狀態
            status = await bot.get_status()

            # 從session_id提取ticker
            ticker = session_id.split('_', 1)[1] if '_' in session_id else 'unknown'

            return {
                'session_id': session_id,
                'user_id': user_id,
                'ticker': ticker,
                'is_running': bot.is_running,
                'status': status,
                'last_updated': time.time()
            }
        except Exception as e:
            logger.error(f"獲取會話 {session_id} 狀態失敗: {e}")
            # 即使獲取狀態失敗，也返回基本資訊
            return {
                'session_id': session_id,
                'user_id': user_id,
                'ticker': session_id.split('_', 1)[1] if '_' in session_id else 'unknown',
                'is_running': bot.is_running,
                'status': None,
                'error': str(e),
                'last_updated': time.time()
            }
    
    async def stop_all_sessions(self):
        """🚀 優化：並行停止所有會話"""
        async with self._sessions_lock:
            session_ids = list(self.sessions.keys())

        if not session_ids:
            logger.info("沒有活動的會話需要停止")
            return

        logger.info(f"開始並行停止 {len(session_ids)} 個會話")

        # 🚀 優化：使用信號量控制並發數，避免系統過載
        semaphore = asyncio.Semaphore(5)  # 最多同時停止5個會話

        async def limited_stop(session_id: str) -> tuple[str, bool]:
            async with semaphore:
                try:
                    success = await self.stop_session(session_id)
                    return session_id, success
                except Exception as e:
                    logger.error(f"停止會話 {session_id} 失敗: {e}")
                    return session_id, False

        # 🚀 優化：並行執行所有停止操作
        stop_tasks = [limited_stop(session_id) for session_id in session_ids]
        results = await asyncio.gather(*stop_tasks, return_exceptions=True)

        # 統計結果
        successful = sum(1 for _, success in results if success)
        failed = len(session_ids) - successful

        logger.info(f"批量停止會話完成: {successful} 成功, {failed} 失敗")

        # 🚀 優化：批量清理相關緩存
        if hasattr(self, 'session_cache'):
            user_ids = set()
            for session_id in session_ids:
                user_id = session_id.split('_', 1)[0] if '_' in session_id else session_id
                user_ids.add(user_id)

            cache_keys = [f"user_sessions_{user_id}" for user_id in user_ids]
            await self.session_cache.invalidate_batch(cache_keys)
            logger.debug(f"已清理 {len(cache_keys)} 個用戶的會話緩存")

    async def stop_sessions_batch(self, session_ids: List[str]) -> Dict[str, bool]:
        """
        🚀 優化：批量停止指定的會話

        Args:
            session_ids: 要停止的會話ID列表

        Returns:
            {session_id: success_bool} 的字典
        """
        logger.info(f"開始批量停止 {len(session_ids)} 個指定會話")

        # 過濾存在的會話
        async with self._sessions_lock:
            existing_sessions = [sid for sid in session_ids if sid in self.sessions]

        if not existing_sessions:
            logger.warning("沒有找到要停止的活動會話")
            return {sid: False for sid in session_ids}

        # 使用信號量控制並發數
        semaphore = asyncio.Semaphore(5)

        async def limited_stop(session_id: str) -> tuple[str, bool]:
            async with semaphore:
                try:
                    success = await self.stop_session(session_id)
                    return session_id, success
                except Exception as e:
                    logger.error(f"批量停止會話 {session_id} 失敗: {e}")
                    return session_id, False

        # 並行執行停止操作
        stop_tasks = [limited_stop(session_id) for session_id in existing_sessions]
        results = await asyncio.gather(*stop_tasks, return_exceptions=True)

        # 構建結果字典
        result_dict = {}
        for session_id in session_ids:
            result_dict[session_id] = False  # 默認失敗

        for session_id, success in results:
            result_dict[session_id] = success

        successful = sum(result_dict.values())
        logger.info(f"批量停止指定會話完成: {successful}/{len(session_ids)} 成功")

        return result_dict

    # ============== Copy Trading 互斥機制方法 ==============

    async def check_trading_mode_conflict(self, user_id: str, requested_mode: TradingMode) -> bool:
        """
        檢查用戶是否有交易模式衝突

        Args:
            user_id: 用戶ID
            requested_mode: 請求的交易模式

        Returns:
            True 如果存在衝突，False 如果無衝突
        """
        async with self._trading_mode_lock:
            if user_id not in self._user_trading_modes:
                return False

            current_mode = self._user_trading_modes[user_id]
            has_conflict = current_mode != requested_mode

            if has_conflict:
                logger.warning(
                    f"用戶 {user_id} 交易模式衝突: 當前模式 {current_mode.value}, 請求模式 {requested_mode.value}"
                )

            return has_conflict

    async def register_trading_mode(self, user_id: str, mode: TradingMode) -> bool:
        """
        註冊用戶的交易模式

        Args:
            user_id: 用戶ID
            mode: 交易模式

        Returns:
            True 如果註冊成功，False 如果存在衝突
        """
        async with self._trading_mode_lock:
            # 檢查是否已有其他模式
            if user_id in self._user_trading_modes:
                current_mode = self._user_trading_modes[user_id]
                if current_mode != mode:
                    logger.warning(
                        f"用戶 {user_id} 已在 {current_mode.value} 模式，無法切換到 {mode.value}"
                    )
                    return False
                # 相同模式，視為成功
                return True

            self._user_trading_modes[user_id] = mode
            logger.info(f"用戶 {user_id} 已註冊交易模式: {mode.value}")
            return True

    async def unregister_trading_mode(self, user_id: str, mode: Optional[TradingMode] = None) -> bool:
        """
        取消註冊用戶的交易模式

        Args:
            user_id: 用戶ID
            mode: 可選，只有當前模式匹配時才取消註冊

        Returns:
            True 如果取消成功或用戶本來就沒有註冊
        """
        async with self._trading_mode_lock:
            if user_id not in self._user_trading_modes:
                return True

            current_mode = self._user_trading_modes[user_id]

            # 如果指定了模式，檢查是否匹配
            if mode is not None and current_mode != mode:
                logger.warning(
                    f"用戶 {user_id} 當前模式 {current_mode.value} 與請求取消的模式 {mode.value} 不匹配"
                )
                return False

            del self._user_trading_modes[user_id]
            logger.info(f"用戶 {user_id} 已取消交易模式註冊: {current_mode.value}")
            return True

    async def get_user_trading_mode(self, user_id: str) -> Optional[TradingMode]:
        """
        獲取用戶當前的交易模式

        Args:
            user_id: 用戶ID

        Returns:
            用戶的交易模式，如果沒有則返回 None
        """
        async with self._trading_mode_lock:
            return self._user_trading_modes.get(user_id)

    async def get_all_trading_modes(self) -> Dict[str, TradingMode]:
        """
        獲取所有用戶的交易模式

        Returns:
            用戶ID到交易模式的映射
        """
        async with self._trading_mode_lock:
            return dict(self._user_trading_modes)
