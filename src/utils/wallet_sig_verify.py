import os
import re
import base58
import time
import json
from datetime import datetime, timedelta
from eth_account import Account
from eth_account.messages import encode_defunct
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import base64
from src.utils.logging_config import get_logger

logger = get_logger("wallet_verifier")

class WalletSignatureVerifier:
    # 簽名有效時間窗口（秒）
    SIGNATURE_VALIDITY_WINDOW = 300  # 5分鐘

    def __init__(self):
        """
        初始化錢包簽名驗證器 - 使用 MongoDB 持久化存儲防止重放攻擊
        """
        # 將在初始化時設置 MongoDB 連接
        self.nonces_collection = None

    def initialize_with_database(self, database):
        """
        使用數據庫連接初始化驗證器

        Args:
            database: MongoDB 數據庫對象
        """
        self.nonces_collection = database.get_collection("used_nonces")
        logger.info("錢包驗證器已初始化 MongoDB 連接")

    async def ensure_indexes(self):
        """
        確保創建必要的索引
        - nonce 唯一索引
        - expires_at 索引用於查詢優化
        """
        if self.nonces_collection is None:
            logger.warning("MongoDB 連接未初始化，跳過索引創建")
            return

        try:
            # nonce 唯一索引（防止重複）
            await self.nonces_collection.create_index(
                "nonce",
                unique=True,
                background=True
            )

            # expires_at 索引用於自動清理查詢
            await self.nonces_collection.create_index(
                "expires_at",
                background=True
            )

            logger.info("Nonce 索引創建成功")

        except Exception as e:
            logger.error(f"創建索引失敗: {e}")
            # 索引創建失敗不應該阻止應用啟動

    def _generate_message(self, timestamp: int, nonce: str) -> str:
        """
        生成帶時間戳和 nonce 的驗證訊息

        Args:
            timestamp: Unix 時間戳（秒）
            nonce: 隨機 nonce

        Returns:
            格式化的驗證訊息
        """
        return f"Please sign this message to confirm your identity.\nTimestamp: {timestamp}\nNonce: {nonce}"

    async def cleanup_expired_nonces(self):
        """清理過期的 nonce 記錄（持久化清理）"""
        if self.nonces_collection is None:
            logger.warning("MongoDB 連接未初始化，跳過清理操作")
            return

        try:
            current_time = int(time.time())
            result = await self.nonces_collection.delete_many({
                "expires_at": {"$lt": current_time}
            })

            if result.deleted_count > 0:
                logger.debug(f"清理了 {result.deleted_count} 個過期 nonce")

        except Exception as e:
            logger.error(f"清理過期 nonce 失敗: {e}")

    async def validate_timestamp_and_nonce(self, timestamp: int, nonce: str) -> bool:
        """
        驗證時間戳和 nonce - 使用 MongoDB 持久化存儲防止重放攻擊

        Args:
            timestamp: 簽名時的時間戳
            nonce: 隨機 nonce

        Returns:
            是否有效
        """
        if self.nonces_collection is None:
            logger.warning("MongoDB 連接未初始化，為安全起見拒絕請求")
            return False

        current_time = int(time.time())

        # 檢查時間窗口
        if abs(current_time - timestamp) > self.SIGNATURE_VALIDITY_WINDOW:
            logger.warning(f"簽名已過期: timestamp={timestamp}, current={current_time}")
            return False

        try:
            # 檢查 nonce 是否已使用（持久化檢查）
            existing = await self.nonces_collection.find_one({"nonce": nonce})
            if existing:
                logger.warning(
                    f"Nonce 重複使用檢測: {nonce}",
                    event_type="security.replay_attempt",
                    data={
                        "nonce": nonce[:10] + "...",  # 只記錄部分 nonce 保護隱私
                        "existing_timestamp": existing.get("timestamp"),
                        "attempt_timestamp": timestamp
                    }
                )
                return False

            # 記錄 nonce 使用（持久化存儲）
            expires_at = timestamp + self.SIGNATURE_VALIDITY_WINDOW
            await self.nonces_collection.insert_one({
                "nonce": nonce,
                "timestamp": timestamp,
                "expires_at": expires_at,
                "created_at": current_time
            })

            logger.debug(
                f"Nonce 記錄成功: {nonce[:10]}...",
                event_type="security.nonce_recorded",
                data={
                    "nonce": nonce[:10] + "...",
                    "expires_at": expires_at
                }
            )

            return True

        except Exception as e:
            logger.error(f"Nonce 驗證失敗: {e}")
            # 如果數據庫操作失敗，為安全起見拒絕請求
            return False

    def detect_wallet_type(self, address: str) -> str:
        """
        根據地址格式檢測錢包類型
        
        Args:
            address: 錢包地址
            
        Returns:
            'evm' 或 'solana'
        """
        # EVM 地址檢測：只檢查是否以0x開頭
        if address.startswith('0x'):
            return 'evm'
        else:
        # 其他都是solana
            return 'solana'
    
    async def verify_evm_signature(self, signature: str, address: str, timestamp: int, nonce: str) -> bool:
        """
        驗證 EVM (Ethereum) 錢包簽名

        Args:
            signature: 簽名 (hex格式，以0x開頭)
            address: EVM地址
            timestamp: 簽名時的時間戳
            nonce: 隨機 nonce

        Returns:
            bool: 簽名是否有效
        """
        try:
            # 驗證時間戳和 nonce（異步持久化檢查）
            if not await self.validate_timestamp_and_nonce(timestamp, nonce):
                return False

            # 生成驗證訊息
            message = self._generate_message(timestamp, nonce)

            # 將訊息編碼為以太坊標準格式
            encoded_message = encode_defunct(text=message)

            # 恢復簽名者地址
            recovered_address = Account.recover_message(encoded_message, signature=signature)

            # 比較地址（不區分大小寫）
            is_valid = recovered_address.lower() == address.lower()

            if is_valid:
                logger.info(f"EVM簽名驗證成功: {address[:10]}...")
            else:
                logger.warning(f"EVM簽名驗證失敗: 地址不匹配")

            return is_valid

        except ValueError as e:
            logger.error(f"EVM簽名格式錯誤: {e}")
            return False
        except Exception as e:
            logger.error(f"EVM簽名驗證異常: {e}")
            return False
    
    async def verify_solana_signature(self, signature: str, public_key: str, timestamp: int, nonce: str) -> bool:
        """
        驗證 Solana 錢包簽名

        Args:
            signature: 簽名 (base64或base58編碼)
            public_key: Solana公鑰地址
            timestamp: 簽名時的時間戳
            nonce: 隨機 nonce

        Returns:
            bool: 簽名是否有效
        """
        try:
            # 驗證時間戳和 nonce（異步持久化檢查）
            if not await self.validate_timestamp_and_nonce(timestamp, nonce):
                return False

            # 生成驗證訊息
            message = self._generate_message(timestamp, nonce)
            message_bytes = message.encode('utf-8')

            # 處理公鑰
            try:
                pubkey_bytes = base58.b58decode(public_key)
                verify_key = VerifyKey(pubkey_bytes)
            except Exception as e:
                logger.error(f"Solana 公鑰解碼失敗: {e}")
                return False

            # 處理簽名 - 嘗試不同的編碼格式
            signature_bytes = None

            # 嘗試base58解碼
            try:
                signature_bytes = base58.b58decode(signature)
            except:
                # 嘗試base64解碼
                try:
                    signature_bytes = base64.b64decode(signature)
                except:
                    # 嘗試hex解碼
                    try:
                        signature_bytes = bytes.fromhex(signature.replace('0x', ''))
                    except:
                        logger.error("無法解碼 Solana 簽名")
                        return False

            # 驗證簽名
            verify_key.verify(message_bytes, signature_bytes)
            logger.info(f"Solana簽名驗證成功: {public_key[:10]}...")
            return True

        except BadSignatureError:
            logger.warning("Solana簽名驗證失敗: 簽名不匹配")
            return False
        except Exception as e:
            logger.error(f"Solana簽名驗證異常: {e}")
            return False
    
    async def verify_signature(self, signature: str, address: str, timestamp: int, nonce: str) -> dict:
        """
        驗證錢包簽名 - 使用安全的持久化 nonce 驗證

        Args:
            signature: 簽名
            address: 錢包地址
            timestamp: 簽名時的時間戳
            nonce: 隨機 nonce

        Returns:
            dict: 完整的驗證結果
        """
        # 檢測錢包類型
        wallet_type = self.detect_wallet_type(address)

        result = {
            'wallet_type': wallet_type,
            'address': address,
            'signature_valid': False,
            'message': ''
        }

        if wallet_type == 'unknown':
            result['message'] = '無法識別的錢包地址格式'
            logger.warning(f"無法識別的錢包地址: {address}")
            return result

        # 驗證簽名（異步）
        if wallet_type == 'evm':
            result['signature_valid'] = await self.verify_evm_signature(signature, address, timestamp, nonce)
            result['message'] = 'EVM錢包驗證完成'
        elif wallet_type == 'solana':
            result['signature_valid'] = await self.verify_solana_signature(signature, address, timestamp, nonce)
            result['message'] = 'Solana錢包驗證完成'

        return result

    def generate_challenge(self) -> dict:
        """
        生成驗證挑戰（給前端使用）

        Returns:
            dict: 包含時間戳、nonce 和待簽名訊息
        """
        timestamp = int(time.time())
        nonce = base64.b64encode(os.urandom(32)).decode('utf-8')
        message = self._generate_message(timestamp, nonce)

        return {
            'timestamp': timestamp,
            'nonce': nonce,
            'message': message
        }