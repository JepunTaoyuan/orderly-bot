#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Session Manager Interface
定義SessionManager的抽象接口，用於解決循環依賴問題
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class SessionManagerInterface(ABC):
    """SessionManager的抽象接口"""

    @abstractmethod
    async def list_sessions(self) -> Dict[str, bool]:
        """
        列出所有會話及其運行狀態

        Returns:
            Dict[str, bool]: 會話ID到運行狀態的映射
        """
        pass

    @abstractmethod
    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取指定會話的詳細狀態

        Args:
            session_id (str): 會話ID

        Returns:
            Optional[Dict[str, Any]]: 會話狀態信息，如果會話不存在則返回None
        """
        pass

    @abstractmethod
    async def create_session(self, session_id: str, config: Dict[str, Any]) -> bool:
        """
        創建新的會話

        Args:
            session_id (str): 會話ID
            config (Dict[str, Any]): 會話配置

        Returns:
            bool: 創建是否成功
        """
        pass

    @abstractmethod
    async def stop_session(self, session_id: str) -> bool:
        """
        停止指定會話

        Args:
            session_id (str): 會話ID

        Returns:
            bool: 停止是否成功
        """
        pass

    @abstractmethod
    async def restart_session(self, session_id: str) -> bool:
        """
        重啟指定會話

        Args:
            session_id (str): 會話ID

        Returns:
            bool: 重啟是否成功
        """
        pass