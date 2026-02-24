import os
import base58
import time
import asyncio
import logging
import base64
from eth_account import Account
from eth_account.messages import encode_defunct
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError


class WalletSignatureVerifier:
    SIGNATURE_VALIDITY_WINDOW = 300  # 5分鐘

    def __init__(self, logger=None):
        """
        初始化錢包簽名驗證器 - 使用 MongoDB 持久化存儲防止重放攻擊
        添加內存緩存作為降級方案
        """
        self.logger = logger or logging.getLogger(__name__)
        self.nonces_collection = None
        self.memory_nonces = {}
        self.memory_cleanup_interval = 600  # 10分鐘清理一次
        self.last_cleanup_time = time.time()

    def initialize_with_database(self, database):
        """
        使用數據庫連接初始化驗證器

        Args:
            database: MongoDB 數據庫對象
        """
        self.nonces_collection = database.get_collection("used_nonces")
        self.logger.info("錢包驗證器已初始化 MongoDB 連接")

    async def ensure_indexes(self):
        """
        確保創建必要的索引
        - nonce 唯一索引
        - expires_at 索引用於查詢優化
        """
        if self.nonces_collection is None:
            self.logger.warning("MongoDB 連接未初始化，跳過索引創建")
            return

        try:
            await self.nonces_collection.create_index(
                "nonce",
                unique=True,
                background=True
            )

            await self.nonces_collection.create_index(
                "expires_at",
                background=True
            )

            self.logger.info("Nonce 索引創建成功")

        except Exception as e:
            self.logger.error(f"創建索引失敗: {e}")

    def _cleanup_memory_nonces(self, force=False):
        """清理過期的內存 nonce"""
        current_time = time.time()

        if not force and current_time - self.last_cleanup_time < self.memory_cleanup_interval:
            return

        expired_nonces = []
        for nonce, data in self.memory_nonces.items():
            if current_time > data.get('expires_at', 0):
                expired_nonces.append(nonce)

        for nonce in expired_nonces:
            del self.memory_nonces[nonce]

        self.last_cleanup_time = current_time

        if expired_nonces:
            self.logger.debug(f"清理了 {len(expired_nonces)} 個過期的內存 nonce")

    def _memory_nonce_exists(self, nonce: str) -> bool:
        """檢查內存中是否存在 nonce"""
        self._cleanup_memory_nonces()
        return nonce in self.memory_nonces

    def _add_memory_nonce(self, nonce: str, timestamp: int, expires_at: int):
        """添加 nonce 到內存緩存"""
        self.memory_nonces[nonce] = {
            'timestamp': timestamp,
            'expires_at': expires_at
        }

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
            self.logger.warning("MongoDB 連接未初始化，跳過清理操作")
            return

        try:
            current_time = int(time.time())
            result = await self.nonces_collection.delete_many({
                "expires_at": {"$lt": current_time}
            })

            if result.deleted_count > 0:
                self.logger.debug(f"清理了 {result.deleted_count} 個過期 nonce")

        except Exception as e:
            self.logger.error(f"清理過期 nonce 失敗: {e}")

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
            self.logger.warning("MongoDB 連接未初始化，為安全起見拒絕請求")
            return False

        current_time = int(time.time())

        if abs(current_time - timestamp) > self.SIGNATURE_VALIDITY_WINDOW:
            self.logger.warning(f"簽名已過期: timestamp={timestamp}, current={current_time}")
            return False

        max_retries = 3
        for attempt in range(max_retries):
            try:
                existing = await self.nonces_collection.find_one({"nonce": nonce})
                if existing:
                    self.logger.warning(
                        f"Nonce 重複使用檢測: {nonce}",
                        event_type="security.replay_attempt",
                        data={
                            "nonce": nonce[:10] + "...",
                            "existing_timestamp": existing.get("timestamp"),
                            "attempt_timestamp": timestamp
                        }
                    )
                    return False

                expires_at = timestamp + self.SIGNATURE_VALIDITY_WINDOW

                try:
                    await self.nonces_collection.insert_one({
                        "nonce": nonce,
                        "timestamp": timestamp,
                        "expires_at": expires_at,
                        "created_at": current_time
                    })
                except Exception as insert_error:
                    if "duplicate key" in str(insert_error).lower() or "11000" in str(insert_error):
                        self.logger.warning(f"Nonce 重複使用 (競爭條件): {nonce[:10]}...")
                        return False
                    elif "transaction" in str(insert_error).lower() or "unknown" in str(insert_error).lower():
                        self.logger.warning(f"事務錯誤，嘗試重新插入 (嘗試 {attempt + 1}/{max_retries}): {insert_error}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.1 * (attempt + 1))
                            continue
                        else:
                            self.logger.error(f"事務錯誤達到最大重試次數，使用降級策略: {insert_error}")
                            return await self._fallback_nonce_validation(nonce, timestamp, current_time, expires_at)
                    else:
                        raise insert_error

                self.logger.debug(
                    f"Nonce 記錄成功: {nonce[:10]}...",
                    event_type="security.nonce_recorded",
                    data={
                        "nonce": nonce[:10] + "...",
                        "expires_at": expires_at
                    }
                )

                return True

            except Exception as e:
                self.logger.error(f"Nonce 驗證失敗 (嘗試 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                else:
                    self.logger.error(f"Nonce 驗證達到最大重試次數，拒絕請求: {e}")
                    return False

        return False

    async def _fallback_nonce_validation(self, nonce: str, timestamp: int, current_time: int, expires_at: int) -> bool:
        """
        降級 nonce 驗證策略 - 當 MongoDB 事務失敗時使用
        首先嘗試 upsert，如果還是失敗則使用內存緩存
        """
        try:
            result = await self.nonces_collection.update_one(
                {"nonce": nonce},
                {
                    "$setOnInsert": {
                        "nonce": nonce,
                        "timestamp": timestamp,
                        "expires_at": expires_at,
                        "created_at": current_time
                    }
                },
                upsert=True
            )

            if result.upserted_id:
                self.logger.info(
                    f"降級策略成功記錄 nonce: {nonce[:10]}...",
                    event_type="security.nonce_fallback_success"
                )
                return True
            else:
                self.logger.warning(
                    f"降級策略檢測到重複 nonce: {nonce[:10]}...",
                    event_type="security.nonce_fallback_duplicate"
                )
                return False

        except Exception as e:
            self.logger.warning(f"MongoDB 降級策略失敗，使用內存緩存: {e}")

        try:
            if self._memory_nonce_exists(nonce):
                self.logger.warning(
                    f"內存緩存檢測到重複 nonce: {nonce[:10]}...",
                    event_type="security.memory_duplicate"
                )
                return False

            self._add_memory_nonce(nonce, timestamp, expires_at)
            self.logger.info(
                f"內存緩存成功記錄 nonce: {nonce[:10]}...",
                event_type="security.memory_fallback_success"
            )
            return True

        except Exception as e:
            self.logger.error(f"內存緩存策略也失敗: {e}")
            return False

    def detect_wallet_type(self, address: str) -> str:
        """
        根據地址格式檢測錢包類型

        Args:
            address: 錢包地址

        Returns:
            'evm' 或 'solana'
        """
        if address.startswith('0x'):
            return 'evm'
        else:
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
            if not await self.validate_timestamp_and_nonce(timestamp, nonce):
                return False

            message = self._generate_message(timestamp, nonce)
            encoded_message = encode_defunct(text=message)
            recovered_address = Account.recover_message(encoded_message, signature=signature)

            is_valid = recovered_address.lower() == address.lower()

            if is_valid:
                self.logger.info(f"EVM簽名驗證成功: {address[:10]}...")
            else:
                self.logger.warning(f"EVM簽名驗證失敗: 地址不匹配")

            return is_valid

        except ValueError as e:
            self.logger.error(f"EVM簽名格式錯誤: {e}")
            return False
        except Exception as e:
            self.logger.error(f"EVM簽名驗證異常: {e}")
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
            if not await self.validate_timestamp_and_nonce(timestamp, nonce):
                return False

            message = self._generate_message(timestamp, nonce)
            message_bytes = message.encode('utf-8')

            try:
                pubkey_bytes = base58.b58decode(public_key)
                verify_key = VerifyKey(pubkey_bytes)
            except Exception as e:
                self.logger.error(f"Solana 公鑰解碼失敗: {e}")
                return False

            signature_bytes = None

            try:
                signature_bytes = base58.b58decode(signature)
            except:
                try:
                    signature_bytes = base64.b64decode(signature)
                except:
                    try:
                        signature_bytes = bytes.fromhex(signature.replace('0x', ''))
                    except:
                        self.logger.error("無法解碼 Solana 簽名")
                        return False

            verify_key.verify(message_bytes, signature_bytes)
            self.logger.info(f"Solana簽名驗證成功: {public_key[:10]}...")
            return True

        except BadSignatureError:
            self.logger.warning("Solana簽名驗證失敗: 簽名不匹配")
            return False
        except Exception as e:
            self.logger.error(f"Solana簽名驗證異常: {e}")
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
        wallet_type = self.detect_wallet_type(address)

        result = {
            'wallet_type': wallet_type,
            'address': address,
            'signature_valid': False,
            'message': ''
        }

        if wallet_type == 'unknown':
            result['message'] = '無法識別的錢包地址格式'
            self.logger.warning(f"無法識別的錢包地址: {address}")
            return result

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
