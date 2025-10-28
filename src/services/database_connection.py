#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一數據庫管理器
使用單例模式管理所有數據庫連接
"""

import os
import asyncio
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from src.utils.logging_config import get_logger
from src.services.database_service import MongoManager

logger = get_logger("database_manager")

class DatabaseManager:
    """統一數據庫管理器 - 單例模式"""

    _instance: Optional['DatabaseManager'] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.client: Optional[AsyncIOMotorClient] = None
            self.db: Optional[AsyncIOMotorDatabase] = None
            self.mongo_manager: Optional[MongoManager] = None
            self.connection_string: Optional[str] = None
            self._lock = asyncio.Lock()
            DatabaseManager._initialized = True
            logger.info("DatabaseManager 初始化")

    async def initialize(self, connection_string: Optional[str] = None):
        """初始化數據庫連接"""
        async with self._lock:
            if self.client is not None:
                logger.warning("數據庫已經初始化，跳過重複初始化")
                return

            self.connection_string = connection_string or os.getenv("MONGODB_URI")

            if not self.connection_string:
                raise ValueError("MongoDB 連接字符串未提供")

            try:
                # 創建客戶端 - 禁用不必要的事務機制
                self.client = AsyncIOMotorClient(
                    self.connection_string,
                    maxPoolSize=50,
                    minPoolSize=10,
                    maxIdleTimeMS=45000,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=10000,
                    # 禁用重試寫入避免事務混亂
                    retryWrites=False,
                    retryReads=True,
                    # 使用簡單的寫入確認
                    w=1,
                    # 簡化讀取配置
                    readPreference="primary",
                    # 禁用讀取關注級別避免事務
                    # readConcern="majority",  # 註釋掉避免事務
                    # writeConcern={"w": "majority", "j": True}  # 註釋掉避免事務
                )

                # 獲取數據庫
                self.db = self.client.get_default_database()

                # 創建 MongoManager 實例
                self.mongo_manager = MongoManager(self.connection_string)

                # 測試連接
                await self.client.admin.command('ping')

                logger.info("MongoDB 連接初始化成功")

                # 初始化索引
                await self._ensure_indexes()

            except Exception as e:
                logger.error(f"MongoDB 連接初始化失敗: {e}")
                await self.close()
                raise

    async def _ensure_indexes(self):
        """確保必要的索引存在"""
        try:
            # 用戶集合索引
            await self.db.users.create_index("user_id", unique=True)

            # nonce 集合索引（用於簽名驗證）
            await self.db.used_nonces.create_index("nonce", unique=True, background=True)
            await self.db.used_nonces.create_index("expires_at", background=True)

            # 會話集合索引（如果存在）
            if "sessions" in await self.db.list_collection_names():
                await self.db.sessions.create_index("session_id", unique=True)
                await self.db.sessions.create_index("created_at", background=True)

            logger.info("數據庫索引初始化完成")

        except Exception as e:
            logger.error(f"創建索引失敗: {e}")

    async def get_database(self) -> AsyncIOMotorDatabase:
        """獲取數據庫實例"""
        if self.db is None:
            raise RuntimeError("數據庫未初始化，請先調用 initialize()")
        return self.db

    async def get_mongo_manager(self) -> MongoManager:
        """獲取 MongoManager 實例"""
        if self.mongo_manager is None:
            raise RuntimeError("數據庫未初始化，請先調用 initialize()")
        return self.mongo_manager

    async def close(self):
        """關閉所有連接"""
        if self.client:
            self.client.close()
            self.client = None
            self.db = None
            self.mongo_manager = None
            logger.info("MongoDB 連接已關閉")

    async def health_check(self) -> Dict[str, Any]:
        """健康檢查"""
        try:
            if not self.client:
                return {"status": "unhealthy", "reason": "客戶端未初始化"}

            # 執行 ping 命令
            result = await self.client.admin.command('ping')

            # 獲取服務器信息
            server_info = await self.client.admin.command('serverStatus')

            # 獲取數據庫統計
            db_stats = await self.db.command('dbStats')

            return {
                "status": "healthy",
                "ping": result,
                "server_version": server_info.get("version"),
                "uptime": server_info.get("uptime"),
                "connections": server_info.get("connections", {}),
                "database_stats": {
                    "collections": db_stats.get("collections"),
                    "data_size": db_stats.get("dataSize"),
                    "index_size": db_stats.get("indexSize"),
                    "objects": db_stats.get("objects")
                }
            }

        except Exception as e:
            logger.error(f"數據庫健康檢查失敗: {e}")
            return {
                "status": "unhealthy",
                "reason": str(e)
            }

    async def create_indexes_for_collection(self, collection_name: str, indexes: list):
        """為指定集合創建索引"""
        try:
            collection = self.db[collection_name]
            for index_spec in indexes:
                await collection.create_index(
                    index_spec["keys"],
                    **index_spec.get("options", {})
                )
            logger.info(f"集合 {collection_name} 的索引創建完成")
        except Exception as e:
            logger.error(f"創建集合 {collection_name} 索引失敗: {e}")

    async def backup_database(self, backup_path: str):
        """數據庫備份（需要 mongodump 工具）"""
        # 這裡可以實現備份邏輯
        # 例如調用 mongodump 命令或使用 MongoDB 的備份功能
        logger.info(f"數據庫備份功能待實現: {backup_path}")

    async def get_connection_stats(self) -> Dict[str, Any]:
        """獲取連接統計信息"""
        try:
            if not self.client:
                return {"error": "客戶端未初始化"}

            server_status = await self.client.admin.command('serverStatus')
            connections = server_status.get('connections', {})

            return {
                "current": connections.get('current'),
                "available": connections.get('available'),
                "total_created": connections.get('totalCreated'),
                "active": connections.get('active'),
                "threaded": connections.get('threaded')
            }

        except Exception as e:
            logger.error(f"獲取連接統計失敗: {e}")
            return {"error": str(e)}

# 全局實例
db_manager = DatabaseManager()

# 便捷函數
async def init_database(connection_string: Optional[str] = None) -> DatabaseManager:
    """初始化數據庫連接"""
    await db_manager.initialize(connection_string)
    return db_manager

async def get_db() -> AsyncIOMotorDatabase:
    """獲取數據庫實例"""
    return await db_manager.get_database()

async def get_mongo_mgr() -> MongoManager:
    """獲取 MongoManager 實例"""
    return await db_manager.get_mongo_manager()

# 裝飾器
def with_database(func):
    """自動提供數據庫連接的裝飾器"""
    async def wrapper(*args, **kwargs):
        db = await get_db()
        kwargs['db'] = db
        return await func(*args, **kwargs)
    return wrapper

def with_mongo_manager(func):
    """自動提供 MongoManager 的裝飾器"""
    async def wrapper(*args, **kwargs):
        mongo_mgr = await get_mongo_mgr()
        kwargs['mongo_manager'] = mongo_mgr
        return await func(*args, **kwargs)
    return wrapper