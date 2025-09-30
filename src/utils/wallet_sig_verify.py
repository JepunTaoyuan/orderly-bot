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
        初始化錢包簽名驗證器
        """
        # 存儲已使用的 nonce，防止重放攻擊
        self._used_nonces = {}  # {nonce: timestamp}
        self._max_nonces = 10000

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

    def _cleanup_old_nonces(self):
        """清理過期的 nonce 記錄"""
        current_time = int(time.time())
        expired_nonces = [
            nonce for nonce, ts in self._used_nonces.items()
            if current_time - ts > self.SIGNATURE_VALIDITY_WINDOW
        ]
        for nonce in expired_nonces:
            del self._used_nonces[nonce]

        # 如果記錄過多，清理最舊的一半
        if len(self._used_nonces) > self._max_nonces:
            sorted_nonces = sorted(self._used_nonces.items(), key=lambda x: x[1])
            for nonce, _ in sorted_nonces[:len(sorted_nonces) // 2]:
                del self._used_nonces[nonce]

    def _validate_timestamp_and_nonce(self, timestamp: int, nonce: str) -> bool:
        """
        驗證時間戳和 nonce

        Args:
            timestamp: 簽名時的時間戳
            nonce: 隨機 nonce

        Returns:
            是否有效
        """
        current_time = int(time.time())

        # 檢查時間窗口
        if abs(current_time - timestamp) > self.SIGNATURE_VALIDITY_WINDOW:
            logger.warning(f"簽名已過期: timestamp={timestamp}, current={current_time}")
            return False

        # 檢查 nonce 是否已使用
        if nonce in self._used_nonces:
            logger.warning(f"Nonce 已被使用: {nonce}")
            return False

        # 記錄 nonce
        self._used_nonces[nonce] = timestamp

        # 定期清理
        if len(self._used_nonces) % 100 == 0:
            self._cleanup_old_nonces()

        return True

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
    
    def verify_evm_signature(self, signature: str, address: str, timestamp: int, nonce: str) -> bool:
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
            # 驗證時間戳和 nonce
            if not self._validate_timestamp_and_nonce(timestamp, nonce):
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
    
    def verify_solana_signature(self, signature: str, public_key: str, timestamp: int, nonce: str) -> bool:
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
            # 驗證時間戳和 nonce
            if not self._validate_timestamp_and_nonce(timestamp, nonce):
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
    
    def verify_signature(self, signature: str, address: str, timestamp: int, nonce: str) -> dict:
        """
        驗證錢包簽名

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

        # 驗證簽名
        if wallet_type == 'evm':
            result['signature_valid'] = self.verify_evm_signature(signature, address, timestamp, nonce)
            result['message'] = 'EVM錢包驗證完成'
        elif wallet_type == 'solana':
            result['signature_valid'] = self.verify_solana_signature(signature, address, timestamp, nonce)
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