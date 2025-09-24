#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orderly 交易客戶端
負責處理實際的帳戶操作，包括開倉、平倉等
"""

from orderly_evm_connector.rest import RestAsync 
from typing import Dict, Any, Optional
import asyncio
import logging
import os
from src.utils.retry_handler import RetryHandler, RetryConfig
import asyncio

# 設置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OrderlyClient:
    def __init__(self):
        """初始化 Orderly 客戶端"""
        self.client = RestAsync(
            orderly_key=os.getenv("ORDERLY_KEY", "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T"),
            orderly_secret=os.getenv("ORDERLY_SECRET", "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs"),
            orderly_testnet=True,
            orderly_account_id=os.getenv("ORDERLY_ACCOUNT_ID", "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0"),
        )
        
        # 重試處理器
        self.retry_handler = RetryHandler(RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            max_delay=30.0
        ))
        
    async def create_limit_order(self, symbol: str, side: str, price: float, quantity: float) -> Dict[str, Any]:
        """
        創建限價訂單
        
        Args:
            symbol: 交易對符號 (如 'PERP_BTC_USDC')
            side: 訂單方向 ('BUY' 或 'SELL')
            price: 限價價格
            quantity: 訂單數量
            
        Returns:
            訂單響應
        """
        logger.info(f"創建限價訂單: {symbol} {side} @ {price} 數量: {quantity}")
        
        async def _create_order():
            response = await self.client.create_order(
                symbol=symbol,
                order_type="LIMIT",
                side=side,
                order_price=price,
                order_quantity=quantity,
            )
            await asyncio.sleep(0.1)  # 避免過快請求
            return response
        
        try:
            response = await self.retry_handler.retry_async(_create_order)
            logger.info(f"訂單創建成功: {response}")
            return response
        except Exception as e:
            logger.error(f"創建訂單失敗: {e}")
            raise
    
    async def create_market_order(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        """
        創建市價訂單
        
        Args:
            symbol: 交易對符號
            side: 訂單方向 ('BUY' 或 'SELL')
            quantity: 訂單數量
            
        Returns:
            訂單響應
        """
        logger.info(f"創建市價訂單: {symbol} {side} 數量: {quantity}")
        
        async def _create_market_order():
            response = await self.client.create_order(
                symbol=symbol,
                order_type="MARKET",
                side=side,
                order_quantity=quantity,
            )
            await asyncio.sleep(0.1)
            return response
        
        try:
            response = await self.retry_handler.retry_async(_create_market_order)
            logger.info(f"市價訂單創建成功: {response}")
            return response
        except Exception as e:
            logger.error(f"創建市價訂單失敗: {e}")
            raise
    
    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """
        取消訂單
        
        Args:
            symbol: 交易對符號
            order_id: 訂單ID
            
        Returns:
            取消響應
        """
        logger.info(f"取消訂單: {symbol} {order_id}")
        
        async def _cancel_order():
            return await self.client.cancel_order(
                symbol=symbol,
                order_id=order_id
            )
        
        try:
            response = await self.retry_handler.retry_async(_cancel_order)
            logger.info(f"訂單取消成功: {response}")
            return response
        except Exception as e:
            logger.error(f"取消訂單失敗: {e}")
            raise
    
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        取消所有訂單
        
        Args:
            symbol: 可選，指定交易對。如果不指定則取消所有交易對的訂單
            
        Returns:
            取消響應
        """
        try:
            logger.info(f"取消所有訂單: {symbol if symbol else '所有交易對'}")
            
            if symbol:
                response = await self.client.cancel_orders(symbol=symbol)
            else:
                response = await self.client.cancel_orders()
            
            logger.info(f"批量取消訂單成功: {response}")
            return response
            
        except Exception as e:
            logger.error(f"批量取消訂單失敗: {e}")
            raise
    
    async def get_account_info(self) -> Dict[str, Any]:
        """
        獲取帳戶信息
        
        Returns:
            帳戶信息
        """
        try:
            response = await self.client.get_account()
            logger.info("獲取帳戶信息成功")
            return response
            
        except Exception as e:
            logger.error(f"獲取帳戶信息失敗: {e}")
            raise
    
    async def get_positions(self) -> Dict[str, Any]:
        """
        獲取持倉信息
        
        Returns:
            持倉信息
        """
        try:
            response = await self.client.get_positions()
            logger.info("獲取持倉信息成功")
            return response
            
        except Exception as e:
            logger.error(f"獲取持倉信息失敗: {e}")
            raise
    
    async def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
        """
        獲取訂單列表
        
        Args:
            symbol: 可選，指定交易對
            status: 可選，訂單狀態篩選
            
        Returns:
            訂單列表
        """
        try:
            params = {}
            if symbol:
                params['symbol'] = symbol
            if status:
                params['status'] = status
                
            response = await self.client.get_orders(**params)
            logger.info(f"獲取訂單列表成功: {len(response.get('data', {}).get('rows', []))} 個訂單")
            return response
            
        except Exception as e:
            logger.error(f"獲取訂單列表失敗: {e}")
            raise
    
    async def close_position(self, symbol: str, quantity: Optional[float] = None) -> Dict[str, Any]:
        """
        平倉操作
        
        Args:
            symbol: 交易對符號
            quantity: 可選，平倉數量。如果不指定則全部平倉
            
        Returns:
            平倉響應
        """
        try:
            # 先獲取當前持倉
            positions = await self.get_positions()
            
            # 找到對應的持倉
            target_position = None
            for position in positions.get('data', {}).get('rows', []):
                if position.get('symbol') == symbol:
                    target_position = position
                    break
            
            if not target_position:
                logger.warning(f"未找到 {symbol} 的持倉")
                return {"success": False, "message": "未找到持倉"}
            
            position_qty = float(target_position.get('position_qty', 0))
            if position_qty == 0:
                logger.info(f"{symbol} 持倉為0，無需平倉")
                return {"success": True, "message": "持倉為0"}
            
            # 確定平倉數量和方向
            close_qty = abs(quantity) if quantity else abs(position_qty)
            close_side = "SELL" if position_qty > 0 else "BUY"
            
            logger.info(f"平倉: {symbol} {close_side} 數量: {close_qty}")
            
            # 使用市價單平倉
            response = await self.create_market_order(symbol, close_side, close_qty)
            
            return response
            
        except Exception as e:
            logger.error(f"平倉失敗: {e}")
            raise


async def main():
    """測試函數"""
    client = OrderlyClient()
    
    try:
        # 測試獲取帳戶信息
        account_info = await client.get_account_info()
        print("帳戶信息:", account_info)
        
        # 測試獲取持倉
        positions = await client.get_positions()
        print("持倉信息:", positions)
        
        # 測試創建限價訂單
        # response = await client.create_limit_order(
        #     symbol="PERP_BTC_USDC",
        #     side="BUY",
        #     price=100000,
        #     quantity=0.001
        # )
        # print("訂單響應:", response)
        
    except Exception as e:
        print(f"測試失敗: {e}")


if __name__ == "__main__":
    asyncio.run(main())
