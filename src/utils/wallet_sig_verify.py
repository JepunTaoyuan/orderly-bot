import re
import base58
import time
import json
from datetime import datetime, timedelta
from eth_account import Account
from eth_account.messages import encode_defunct
from solana.publickey import PublicKey
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import base64


class WalletSignatureVerifier:
    def __init__(sel):
        """
        初始化錢包簽名驗證器
        """
    message = "Please sign this message to confirm your identity."

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
    
    def verify_evm_signature(self, signature: str, address: str) -> bool:
        """
        驗證 EVM (Ethereum) 錢包簽名
        
        Args:
            message: 原始訊息
            signature: 簽名 (hex格式，以0x開頭)
            address: EVM地址
            
        Returns:
            bool: 簽名是否有效
        """
        try:
            # 將訊息編碼為以太坊標準格式
            encoded_message = encode_defunct(text=self.message)
            
            # 恢復簽名者地址
            recovered_address = Account.recover_message(encoded_message, signature=signature)
            
            # 比較地址（不區分大小寫）
            return recovered_address.lower() == address.lower()
            
        except Exception as e:
            print(f"EVM簽名驗證錯誤: {e}")
            return False
    
    def verify_solana_signature(self, signature: str, public_key: str) -> bool:
        """
        驗證 Solana 錢包簽名
        
        Args:
            signature: 簽名 (base64或base58編碼)
            public_key: Solana公鑰地址
            
        Returns:
            bool: 簽名是否有效
        """
        try:
            # 將訊息轉換為字節
            message_bytes = self.message.encode('utf-8')
            
            # 處理公鑰
            pubkey_bytes = base58.b58decode(public_key)
            verify_key = VerifyKey(pubkey_bytes)
            
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
                        print("無法解碼簽名")
                        return False
            message_bytes = self.message.encode('utf-8')

            # 驗證簽名
            verify_key.verify(message_bytes, signature_bytes)
            return True
            
        except BadSignatureError:
            print("Solana簽名驗證失敗")
            return False
        except Exception as e:
            print(f"Solana簽名驗證錯誤: {e}")
            return False
    
    def verify_signature(self, signature: str, address: str) -> dict:
        """
        驗證錢包簽名
        
        Args:
            signature: 簽名
            address: 錢包地址
            
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
            return result
        
        # 驗證簽名
        if wallet_type == 'evm':
            result['signature_valid'] = self.verify_evm_signature(signature, address)
            result['message'] = 'EVM錢包驗證完成'
        elif wallet_type == 'solana':
            result['signature_valid'] = self.verify_solana_signature(signature, address)
            result['message'] = 'Solana錢包驗證完成'
        
        return result