#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易主程式
整合訊號生成器和交易客戶端，實現完整的網格交易系統
"""

import asyncio
import logging
import json
from typing import Dict, Any
from grid_signal import GridSignalGenerator, TradingSignal, Direction, OrderSide
from client import OrderlyClient
from orderly_evm_connector.websocket.websocket_api import WebsocketPrivateAPIClient
import time

# 設置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GridTradingBot:
    def __init__(self):
        """初始化網格交易機器人"""
        self.client = OrderlyClient()
        self.signal_generator = None
        self.active_orders = {}  # 記錄活躍訂單 {order_id: {"price": price, "side": side, "quantity": quantity}}
        self.grid_orders = {}    # 記錄網格訂單 {price: order_id}
        self.is_running = False
        self.wss_client = None
        
    def _convert_symbol(self, symbol: str) -> str:
        """
        將訊號生成器的符號轉換為 Orderly 格式
        例如: BTCUSDT -> PERP_BTC_USDC
        """
        if symbol == "BTCUSDT":
            return "PERP_BTC_USDC"
        # 可以根據需要添加更多轉換規則
        return symbol
    
    def _convert_side(self, side: OrderSide) -> str:
        """將訊號生成器的方向轉換為 Orderly 格式"""
        return "BUY" if side == OrderSide.BUY else "SELL"
    
    def _setup_websocket(self):
        """設置 WebSocket 連接監聽訂單成交"""
        def on_close(_):
            logger.info("WebSocket 連接已關閉")

        def on_message(_, message):
            """處理 WebSocket 訊息"""
            try:
                data = json.loads(message) if isinstance(message, str) else message
                
                # 檢查是否為訂單成交通知
                if (data.get("topic") == "notifications" and 
                    data.get("data", {}).get("messageType") == "ORDER_FILLED_PUSH"):
                    
                    # 解析成交信息
                    content_raw = data["data"]["contentRaw"]
                    fill_data = json.loads(content_raw)
                    
                    order_id = fill_data["orderId"]
                    executed_price = fill_data["executedPrice"]
                    executed_quantity = fill_data["executedQuantity"]
                    side = fill_data["side"]
                    
                    logger.info(f"訂單成交: ID={order_id}, 價格={executed_price}, 數量={executed_quantity}, 方向={side}")
                    
                    # 處理訂單成交
                    asyncio.create_task(self._handle_order_filled(order_id, executed_price, executed_quantity, side))
                    
            except Exception as e:
                logger.error(f"處理 WebSocket 訊息失敗: {e}")

        self.wss_client = WebsocketPrivateAPIClient(
            orderly_testnet=True,
            orderly_account_id="0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0",
            orderly_key="ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
            orderly_secret="ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
            on_message=on_message,
            on_close=on_close,
            debug=True,
        )
    
    async def _handle_order_filled(self, order_id: int, executed_price: float, executed_quantity: float, side: str):
        """
        處理訂單成交事件
        
        Args:
            order_id: 訂單 ID
            executed_price: 成交價格
            executed_quantity: 成交數量
            side: 交易方向
        """
        try:
            if not self.is_running:
                return
            
            # 檢查是否為我們的網格訂單
            if order_id in self.active_orders:
                order_info = self.active_orders[order_id]
                grid_price = order_info["price"]
                
                logger.info(f"網格訂單成交: 價格={grid_price}, 成交價={executed_price}")
                
                # 創建成交訊號對象
                from grid_signal import TradingSignal, OrderSide
                filled_signal = TradingSignal(
                    symbol="BTCUSDT",
                    side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                    price=executed_price,
                    size=executed_quantity,
                    signal_type="FILLED"
                )
                
                # 從活躍訂單中移除
                del self.active_orders[order_id]
                if grid_price in self.grid_orders:
                    del self.grid_orders[grid_price]
                
                # 通知訊號生成器處理成交
                if self.signal_generator:
                    self.signal_generator.on_order_filled(filled_signal)
                
                # 生成反向網格訂單
                await self._place_counter_grid_order(executed_price, side)
                
        except Exception as e:
            logger.error(f"處理訂單成交失敗: {e}")
    
    async def _place_counter_grid_order(self, filled_price: float, filled_side: str):
        """
        基於成交訂單放置反向網格訂單
        
        Args:
            filled_price: 成交價格
            filled_side: 成交方向
        """
        try:
            if not self.signal_generator:
                return
            
            # 計算網格間距
            grid_spacing = (self.signal_generator.upper_bound - self.signal_generator.lower_bound) / self.signal_generator.grid_levels
            
            # 根據成交方向決定反向訂單
            if filled_side == "BUY":
                # 買單成交，放置賣單
                counter_price = filled_price + grid_spacing
                if counter_price <= self.signal_generator.upper_bound:
                    counter_side = "SELL"
                    await self._create_grid_order(counter_price, counter_side)
            else:
                # 賣單成交，放置買單
                counter_price = filled_price - grid_spacing
                if counter_price >= self.signal_generator.lower_bound:
                    counter_side = "BUY"
                    await self._create_grid_order(counter_price, counter_side)
                    
        except Exception as e:
            logger.error(f"放置反向網格訂單失敗: {e}")
    
    async def _create_grid_order(self, price: float, side: str):
        """
        創建網格訂單
        
        Args:
            price: 訂單價格
            side: 交易方向
        """
        try:
            # 計算訂單數量
            quantity = self.signal_generator.total_amount / self.signal_generator.grid_levels / price
            
            # 創建限價訂單
            response = await self.client.create_limit_order(
                symbol="PERP_BTC_USDC",
                side=side,
                price=price,
                quantity=quantity
            )
            time.sleep(0.1)
            
            # 記錄訂單
            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    self.active_orders[order_id] = {
                        "price": price,
                        "side": side,
                        "quantity": quantity
                    }
                    self.grid_orders[price] = order_id
                    logger.info(f"網格訂單創建成功: ID={order_id}, 價格={price}, 方向={side}")
            
        except Exception as e:
            logger.error(f"創建網格訂單失敗: {e}")
    
    async def signal_handler(self, signal: TradingSignal):
        """
        處理交易訊號的回調函數（用於初始網格設置）
        
        Args:
            signal: 交易訊號
        """
        try:
            logger.info(f"收到初始網格訊號: {signal.symbol} {signal.side.value} @ {signal.price} 數量:{signal.size}")
            
            if not self.is_running:
                logger.warning("機器人未運行，忽略訊號")
                return
            
            orderly_symbol = self._convert_symbol(signal.symbol)
            orderly_side = self._convert_side(signal.side)
            
            if signal.signal_type == "STOP":
                # 處理停止訊號
                await self._handle_stop_signal(orderly_symbol)
                
            elif signal.signal_type == "INITIAL":
                # 處理初始網格訊號
                await self._handle_initial_signal(signal, orderly_symbol, orderly_side)
                
        except Exception as e:
            logger.error(f"處理訊號失敗: {e}")
    
    async def _handle_initial_signal(self, signal: TradingSignal, symbol: str, side: str):
        """處理初始網格訊號"""
        try:
            # 創建初始網格訂單
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=signal.price,
                quantity=signal.size
            )
            
            # 記錄訂單
            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    self.active_orders[order_id] = {
                        "price": signal.price,
                        "side": side,
                        "quantity": signal.size
                    }
                    self.grid_orders[signal.price] = order_id
                    logger.info(f"初始網格訂單創建成功: ID={order_id}, 價格={signal.price}")
            
        except Exception as e:
            logger.error(f"創建初始網格訂單失敗: {e}")
    
    async def _handle_stop_signal(self, symbol: str):
        """處理停止訊號"""
        logger.info(f"收到停止訊號，取消 {symbol} 的所有訂單")
        
        try:
            # 取消所有相關訂單
            await self.client.cancel_all_orders(symbol)
            
            # 清空活躍訂單記錄
            self.active_orders.clear()
            self.grid_orders.clear()
            
            # 停止機器人
            self.is_running = False
            
            # 關閉 WebSocket 連接
            if self.wss_client:
                self.wss_client.close()
            
            logger.info("停止訊號處理完成")
            
        except Exception as e:
            logger.error(f"處理停止訊號失敗: {e}")
    
    async def start_grid_trading(self, config: Dict[str, Any]):
        """
        啟動網格交易
        
        Args:
            config: 網格配置參數
        """
        try:
            logger.info("啟動網格交易機器人")
            
            # 設置 WebSocket 連接
            self._setup_websocket()
            
            # 啟動 WebSocket 監聽
            self.wss_client.get_notifications()
            
            # 創建訊號生成器（僅用於初始網格設置）
            self.signal_generator = GridSignalGenerator(
                ticker=config['ticker'],
                direction=config['direction'],
                upper_bound=config['upper_bound'],
                lower_bound=config['lower_bound'],
                grid_levels=config['grid_levels'],
                total_amount=config['total_amount'],
                stop_bot_price=config.get('stop_bot_price'),
                stop_top_price=config.get('stop_top_price'),
                signal_callback=self.signal_handler
            )
            
            # 啟動機器人
            self.is_running = True
            
            # 設置初始網格
            self.signal_generator.setup_initial_grid()
            
            logger.info("網格交易機器人啟動成功")
            
        except Exception as e:
            logger.error(f"啟動網格交易失敗: {e}")
            raise
    
    async def stop_grid_trading(self):
        """停止網格交易"""
        logger.info("停止網格交易機器人")
        
        if self.signal_generator:
            self.signal_generator.stop_by_signal()
        
        # 關閉 WebSocket 連接
        if self.wss_client:
            self.wss_client.close()
        
        self.is_running = False
        logger.info("網格交易機器人已停止")
    
    async def get_status(self):
        """獲取機器人狀態"""
        status = {
            "is_running": self.is_running,
            "active_orders_count": len(self.active_orders),
            "active_orders": self.active_orders,
            "grid_orders": self.grid_orders
        }
        
        if self.signal_generator:
            self.signal_generator.get_status()
        
        try:
            # 獲取帳戶信息
            account_info = await self.client.get_account_info()
            status["account_info"] = account_info
            
            # 獲取持倉信息
            positions = await self.client.get_positions()
            status["positions"] = positions
            
        except Exception as e:
            logger.error(f"獲取狀態失敗: {e}")
        
        return status