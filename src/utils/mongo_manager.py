# 簡單的工具類別
from pymongo import MongoClient
from pymongo.results import InsertOneResult, UpdateResult
from typing import Optional, Dict, Any

class MongoManager:
    def __init__(self, connection_string: str) -> None:
        self.client = MongoClient(connection_string)
        self.db = self.client.get_default_database()
        self.users = self.db['users']
    
    def create_user(self, user_id: str, api_key: str, api_secret: str, 
                   user_wallet_address: str) -> InsertOneResult:
        """創建用戶，加密敏感信息"""
        return self.users.insert_one({
            "_id": user_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "evm_wallet_address": user_wallet_address,  # EVM 錢包地址通常是公開的
        })
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """獲取用戶，自動解密敏感信息"""
        user = self.users.find_one({"_id": user_id})
        if user:
            # 解密敏感字段
            user["api_key"] = user["api_key"]
            user["api_secret"] = user["api_secret"]
        return user
    
    def update_user(self, user_id: str, updates: Dict[str, Any]) -> UpdateResult:
        """更新用戶信息，自動加密敏感字段"""
        # 加密敏感字段
        encrypted_updates = {}
        for key, value in updates.items():
            if key in ["api_key", "api_secret"]:
                encrypted_updates[key] = value
            else:
                encrypted_updates[key] = value
        
        result = self.users.update_one({"_id": user_id}, {"$set": encrypted_updates})
        return result