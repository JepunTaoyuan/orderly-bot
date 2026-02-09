#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MongoDB 管理器
提供統一的 MongoDB 操作接口
"""

import asyncio
import time
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from motor.core import AgnosticCursor
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from src.utils.logging_config import get_logger

logger = get_logger("mongo_manager")


class MongoManager:
    """MongoDB 管理器類"""

    def __init__(self, uri: str = None, db_name: str = "grid_bot", existing_client: AsyncIOMotorClient = None):
        """
        初始化 MongoManager

        Args:
            uri: MongoDB 連接字符串
            db_name: 數據庫名稱，默認為 grid_bot
            existing_client: 現有的 AsyncIOMotorClient 實例（可選）
        """
        self.uri = uri
        self.db_name = db_name
        self.client = existing_client
        self.db: Optional[AsyncIOMotorDatabase] = None

        if not existing_client and uri:
            # 只有在有 URI 且沒有現有客戶端時才創建新客戶端
            self.client = AsyncIOMotorClient(uri)

        if self.client:
            # 如果有現有客戶端
            if existing_client:
                # 如果指定了具體的 db_name (不是默認值)，則使用該名稱
                if db_name and db_name != "grid_bot":
                    self.db = self.client[db_name]
                    self.db_name = db_name
                else:
                    # 否則嘗試使用連接字符串中的默認數據庫
                    try:
                        self.db = self.client.get_default_database()
                        self.db_name = self.db.name
                    except Exception:
                        # 如果無法獲取默認數據庫，回退到 grid_bot
                        self.db = self.client[db_name]
                        self.db_name = db_name
            else:
                self.db = self.client.get_database(db_name)

        logger.info(f"MongoManager 初始化完成，數據庫: {self.db_name}")

    async def get_database(self) -> AsyncIOMotorDatabase:
        """獲取數據庫實例"""
        if self.db is None:
            raise RuntimeError("數據庫未初始化")
        return self.db

    async def get_collection(self, collection_name: str) -> AsyncIOMotorCollection:
        """獲取集合實例"""
        db = await self.get_database()
        return db.get_collection(collection_name)

    async def create_user(self, user_id: str, api_key: str, api_secret: str,
                         wallet_address: str, **kwargs) -> Any:
        """
        創建用戶

        Args:
            user_id: 用戶ID
            api_key: API Key
            api_secret: API Secret
            wallet_address: 錢包地址
            **kwargs: 額外的字段

        Returns:
            插入結果
        """
        collection = await self.get_collection("users")

        user_data = {
            "user_id": user_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "wallet_address": wallet_address,
            "created_at": kwargs.get("created_at", time.time()),
            "updated_at": time.time(),
            **{k: v for k, v in kwargs.items() if k not in ["created_at", "updated_at"]}
        }

        try:
            # 確保有必要的索引
            await self._ensure_user_indexes(collection)

            result = await collection.insert_one(user_data)
            logger.info(f"用戶創建成功: {user_id}")
            return result
        except DuplicateKeyError:
            logger.error(f"用戶已存在: {user_id}")
            raise
        except Exception as e:
            logger.error(f"創建用戶失敗: {e}")
            raise

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取用戶信息

        Args:
            user_id: 用戶ID

        Returns:
            用戶信息或 None
        """
        collection = await self.get_collection("users")

        # First try to find by user_id field
        user_data = await collection.find_one({"user_id": user_id})

        # If not found, try by _id field (MongoDB ObjectID or user identifier)
        if not user_data:
            user_data = await collection.find_one({"_id": user_id})

        # If still not found, try by wallet_address field
        if not user_data:
            user_data = await collection.find_one({"wallet_address": user_id})

        return user_data

    async def update_user(self, user_id: str, update_data: Dict[str, Any]) -> Any:
        """
        更新用戶信息

        Args:
            user_id: 用戶ID
            update_data: 更新的數據

        Returns:
            更新結果
        """
        collection = await self.get_collection("users")

        update_data["updated_at"] = time.time()

        # 使用更靈活的過濾條件，因為文檔可能通過 user_id, _id 或 wallet_address 識別
        filter_query = {
            "$or": [
                {"user_id": user_id},
                {"_id": user_id},
                {"wallet_address": user_id}
            ]
        }

        result = await collection.update_one(
            filter_query,
            {"$set": update_data}
        )

        if result.matched_count > 0:
            logger.info(f"用戶更新成功: {user_id}")
        else:
            logger.warning(f"用戶不存在: {user_id}")

        return result

    async def delete_user(self, user_id: str) -> Any:
        """
        刪除用戶

        Args:
            user_id: 用戶ID

        Returns:
            刪除結果
        """
        collection = await self.get_collection("users")
        result = await collection.delete_one({"user_id": user_id})

        if result.deleted_count > 0:
            logger.info(f"用戶刪除成功: {user_id}")
        else:
            logger.warning(f"用戶不存在: {user_id}")

        return result

    async def user_exists(self, user_id: str) -> bool:
        """
        檢查用戶是否存在

        Args:
            user_id: 用戶ID

        Returns:
            是否存在
        """
        user_data = await self.get_user(user_id)
        return user_data is not None

    async def list_users(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        列出所有用戶

        Args:
            limit: 限制返回的用戶數量

        Returns:
            用戶列表
        """
        collection = await self.get_collection("users")
        cursor = collection.find({})

        if limit:
            users = await cursor.to_list(length=limit)
        else:
            users = await cursor.to_list(length=None)

        return users

    async def find_user_by_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        根據 API Key 查找用戶

        Args:
            api_key: API Key

        Returns:
            用戶信息或 None
        """
        collection = await self.get_collection("users")
        return await collection.find_one({"api_key": api_key})

    async def find_user_by_wallet_address(self, wallet_address: str) -> Optional[Dict[str, Any]]:
        """
        根據錢包地址查找用戶

        Args:
            wallet_address: 錢包地址

        Returns:
            用戶信息或 None
        """
        collection = await self.get_collection("users")
        return await collection.find_one({"wallet_address": wallet_address})

    async def health_check(self) -> bool:
        """
        健康檢查

        Returns:
            是否健康
        """
        try:
            if self.client is None or self.db is None:
                return False

            # 執行 ping 命令
            collection = await self.get_collection("health_check")
            result = await collection.command("ping")

            return result.get("ok") == 1
        except Exception as e:
            logger.error(f"MongoDB 健康檢查失敗: {e}")
            return False

    async def _ensure_user_indexes(self, collection: AsyncIOMotorCollection):
        """確保用戶集合有必要的索引"""
        try:
            index_info = await collection.index_information()

            # 確保 user_id 有唯一索引
            if "user_id_1" not in index_info:
                await collection.create_index("user_id", unique=True)
                logger.info("創建 user_id 唯一索引")

            # 確保 api_key 有唯一索引
            if "api_key_1" not in index_info:
                await collection.create_index("api_key", unique=True)
                logger.info("創建 api_key 唯一索引")

            # 確保 wallet_address 有唯一索引
            if "wallet_address_1" not in index_info:
                await collection.create_index("wallet_address", unique=True)
                logger.info("創建 wallet_address 唯一索引")

        except Exception as e:
            logger.error(f"創建用戶索引失敗: {e}")

    async def close(self):
        """關閉連接"""
        if self.client:
            self.client.close()
            logger.info("MongoDB 連接已關閉")

    async def __aenter__(self):
        """異步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """異步上下文管理器出口"""
        await self.close()

    # 額外的實用方法

    async def create_session(self, session_data: Dict[str, Any]) -> Any:
        """
        創建交易會話

        Args:
            session_data: 會話數據

        Returns:
            插入結果
        """
        collection = await self.get_collection("sessions")

        session_data.update({
            "created_at": time.time(),
            "updated_at": time.time()
        })

        try:
            await self._ensure_session_indexes(collection)
            result = await collection.insert_one(session_data)
            logger.info(f"會話創建成功: {session_data.get('session_id')}")
            return result
        except DuplicateKeyError:
            logger.error(f"會話已存在: {session_data.get('session_id')}")
            raise
        except Exception as e:
            logger.error(f"創建會話失敗: {e}")
            raise

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取會話信息

        Args:
            session_id: 會話ID

        Returns:
            會話信息或 None
        """
        collection = await self.get_collection("sessions")
        return await collection.find_one({"session_id": session_id})

    async def update_session(self, session_id: str, update_data: Dict[str, Any]) -> Any:
        """
        更新會話信息

        Args:
            session_id: 會話ID
            update_data: 更新的數據

        Returns:
            更新結果
        """
        collection = await self.get_collection("sessions")

        update_data["updated_at"] = time.time()

        result = await collection.update_one(
            {"session_id": session_id},
            {"$set": update_data}
        )

        if result.matched_count > 0:
            logger.info(f"會話更新成功: {session_id}")
        else:
            logger.warning(f"會話不存在: {session_id}")

        return result

    async def _ensure_session_indexes(self, collection: AsyncIOMotorCollection):
        """確保會話集合有必要的索引"""
        try:
            index_info = await collection.index_information()

            # 確保 session_id 有唯一索引
            if "session_id_1" not in index_info:
                await collection.create_index("session_id", unique=True)
                logger.info("創建 session_id 唯一索引")

            # 確保 user_id + ticker + status 組合索引
            if "user_id_1_ticker_1_status_1" not in index_info:
                await collection.create_index(
                    [("user_id", 1), ("ticker", 1), ("status", 1)],
                    unique=True,
                    partialFilterExpression={"status": "active"}
                )
                logger.info("創建 (user_id, ticker, status) 組合索引")

        except Exception as e:
            logger.error(f"創建會話索引失敗: {e}")

    async def create_nonce(self, nonce: str, expires_at: float) -> Any:
        """
        創建 nonce 記錄

        Args:
            nonce: nonce 值
            expires_at: 過期時間

        Returns:
            插入結果
        """
        collection = await self.get_collection("used_nonces")

        nonce_data = {
            "nonce": nonce,
            "expires_at": expires_at,
            "created_at": time.time()
        }

        try:
            result = await collection.insert_one(nonce_data)
            return result
        except DuplicateKeyError:
            # Nonce 已存在，這是正常的
            pass
        except Exception as e:
            logger.error(f"創建 nonce 失敗: {e}")
            raise

    async def nonce_exists(self, nonce: str) -> bool:
        """
        檢查 nonce 是否存在且未過期

        Args:
            nonce: nonce 值

        Returns:
            是否存在且未過期
        """
        collection = await self.get_collection("used_nonces")

        # 查找未過期的 nonce
        current_time = time.time()
        result = await collection.find_one({
            "nonce": nonce,
            "expires_at": {"$gt": current_time}
        })

        return result is not None

    async def cleanup_expired_nonces(self) -> int:
        """
        清理過期的 nonce 記錄

        Returns:
            清理的記錄數
        """
        collection = await self.get_collection("used_nonces")

        current_time = time.time()
        result = await collection.delete_many({
            "expires_at": {"$lte": current_time}
        })

        deleted_count = result.deleted_count
        if deleted_count > 0:
            logger.info(f"清理了 {deleted_count} 條過期的 nonce 記錄")

        return deleted_count

    async def check_user_api_key_exist(self, user_id: str) -> bool:
        """
        檢查用戶API密鑰是否存在

        Args:
            user_id: 用戶ID

        Returns:
            是否存在API密鑰對
        """
        try:
            user = await self.get_user(user_id)
            return bool(user and user.get("api_key") and user.get("api_secret"))
        except Exception as e:
            logger.error(f"檢查用戶API密鑰是否存在失敗: {e}")
            return False

    async def update_user_api_key_pair(self, user_id: str, api_key: str, api_secret: str) -> Any:
        """
        更新用戶API密鑰對

        Args:
            user_id: 用戶ID
            api_key: API密鑰
            api_secret: API密碼

        Returns:
            更新結果
        """
        update_data = {
            "api_key": api_key,
            "api_secret": api_secret,
        }
        return await self.update_user(user_id, update_data)
