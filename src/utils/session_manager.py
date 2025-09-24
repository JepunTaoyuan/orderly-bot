#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
簡單的會話管理器
管理多個網格交易會話
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from src.core.grid_bot import GridTradingBot

logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(self):
        """初始化會話管理器"""
        self.sessions: Dict[str, GridTradingBot] = {}
        self._sessions_lock = asyncio.Lock()
    
    async def create_session(self, session_id: str, config: Dict[str, Any]) -> bool:
        """
        創建新的交易會話
        
        Args:
            session_id: 會話ID
            config: 網格配置
            
        Returns:
            是否創建成功
        """
        async with self._sessions_lock:
            if session_id in self.sessions:
                logger.warning(f"會話 {session_id} 已存在")
                return False
            
            try:
                bot = GridTradingBot()
                await bot.start_grid_trading(config)
                self.sessions[session_id] = bot
                logger.info(f"會話 {session_id} 創建成功")
                return True
            except Exception as e:
                logger.error(f"創建會話 {session_id} 失敗: {e}")
                return False
    
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
                return False
            
            try:
                bot = self.sessions[session_id]
                await bot.stop_grid_trading()
                del self.sessions[session_id]
                logger.info(f"會話 {session_id} 已停止")
                return True
            except Exception as e:
                logger.error(f"停止會話 {session_id} 失敗: {e}")
                return False
    
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
    
    async def stop_all_sessions(self):
        """停止所有會話"""
        async with self._sessions_lock:
            session_ids = list(self.sessions.keys())
            
        for session_id in session_ids:
            await self.stop_session(session_id)
        
        logger.info("所有會話已停止")
