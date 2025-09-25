# 簡單的工具類別
from pymongo import MongoClient

class MongoManager:
    def __init__(self, connection_string):
        self.client = MongoClient(connection_string)
        self.db = self.client['your_app']
        self.users = self.db['users']
    
    def create_user(self, user_id, api_key, api_secret, wallet_address):
        return self.users.insert_one({
            "_id": user_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "wallet_address": wallet_address
        })
    
    def get_user(self, user_id):
        return self.users.find_one({"_id": user_id})
    
    def update_user(self, user_id, updates):
        """更新用戶信息"""
        try:
            result = self.users.update_one({"_id": user_id}, {"$set": updates})
            return True
        except Exception as e:
            return False