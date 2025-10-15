# 非同步 MongoDB 管理器
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import Optional, Dict, Any
from src.utils.logging_config import get_logger
from src.utils.error_codes import ErrorCode, GridTradingException

logger = get_logger("mongo_manager")

class MongoManager:
    """非同步 MongoDB 管理器"""

    _instance: Optional['MongoManager'] = None
    _initialized: bool = False

    def __new__(cls, connection_string: Optional[str] = None):
        """單例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, connection_string: Optional[str] = None) -> None:
        """初始化 MongoDB 連接（僅第一次）"""
        if not self._initialized and connection_string:
            self.client: AsyncIOMotorClient = AsyncIOMotorClient(
                connection_string,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=45000,
                serverSelectionTimeoutMS=5000
            )
            self.db: AsyncIOMotorDatabase = self.client.get_default_database()
            self.users = self.db['users']
            self._initialized = True
            logger.info("MongoDB 連接已初始化")

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
        try:
            result = await self.users.update_one(
                {"_id": user_id},
                {"$set": {
                    "api_key": api_key,
                    "api_secret": api_secret,
                }}
            )
            logger.info(f"用戶更新api key pair成功: {user_id}")
            return result
        except Exception as e:
            logger.error(f"更新用戶api key pair失敗: {e}")
            raise GridTradingException(
                error_code=ErrorCode.USER_API_KEY_PAIR_UPDATE_FAILED,
                details={"user_id": user_id},
                original_error=e
            )

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        獲取用戶

        Args:
            user_id: 用戶ID

        Returns:
            用戶數據或 None
        """
        try:
            user = await self.users.find_one({"_id": user_id})
            if user:
                logger.debug(f"獲取用戶成功: {user_id}")
            return user
        except Exception as e:
            logger.error(f"獲取用戶失敗: {e}")
            return None

    async def check_user_api_key_exist(self, user_id: str) -> bool:
        """
        檢查用戶API密鑰是否存在

        Args:
            user_id: 用戶ID

        Returns:
            是否存在API密鑰對
        """
        try:
            user = await self.users.find_one({"_id": user_id})
            return bool(user and user.get("api_key") and user.get("api_secret"))
        except Exception as e:
            logger.error(f"檢查用戶api key pair是否存在失敗: {e}")
            return False

    async def close(self):
        """關閉連接"""
        if self.client:
            self.client.close()
            logger.info("MongoDB 連接已關閉")