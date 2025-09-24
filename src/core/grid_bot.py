#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易主程式
整合訊號生成器和交易客戶端，實現完整的網格交易系統
"""

import asyncio
import logging
import json
import time
from decimal import Decimal
from typing import Dict, Any
from .grid_signal import GridSignalGenerator, TradingSignal, Direction, OrderSide
from .client import OrderlyClient
from src.utils.event_queue import SessionEventQueue, Event, EventType
from src.utils.market_validator import MarketValidator, ValidationError
from src.utils.order_tracker import OrderTracker, OrderStatus
from src.utils.logging_config import get_logger, metrics, set_session_context
from orderly_evm_connector.websocket.websocket_api import WebsocketPrivateAPIClient
import asyncio

# 使用結構化日誌
logger = get_logger("grid_bot")

class GridTradingBot:
    def __init__(self):
        """初始化網格交易機器人"""
        self.client = OrderlyClient()
        self.signal_generator = None
        self.active_orders = {}  # 記錄活躍訂單 {order_id: {"price": price, "side": side, "quantity": quantity}}
        self.grid_orders = {}    # 記錄網格訂單 {price: order_id}
        self.is_running = False
        self.wss_client = None
        self._orders_lock = asyncio.Lock()  # 保護共享狀態的鎖
        self.event_queue = None  # 事件隊列
        self.validator = MarketValidator()  # 市場驗證器
        self.market_info = None  # 當前市場信息
        self.order_tracker = OrderTracker()  # 訂單追踪器
        
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
                    
                    logger.info("訂單成交", event_type="order_filled", data={
                        "order_id": order_id,
                        "price": executed_price,
                        "quantity": executed_quantity,
                        "side": side
                    })
                    
                    # 記錄指標
                    metrics.increment_counter("orders.filled", tags={"side": side})
                    metrics.record_histogram("order.fill_price", float(executed_price))
                    metrics.record_histogram("order.fill_quantity", float(executed_quantity))
                    
                    # 將訂單成交事件添加到隊列
                    if self.event_queue:
                        fill_data = {
                            "order_id": order_id,
                            "executed_price": executed_price,
                            "executed_quantity": executed_quantity,
                            "side": side
                        }
                        event = Event(EventType.ORDER_FILLED, fill_data)
                        asyncio.create_task(self.event_queue.add_event(event))
                    
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
    
    async def _handle_order_filled_event(self, fill_data: Dict[str, Any]):
        """
        通過事件隊列處理訂單成交
        """
        order_id = fill_data["order_id"]
        executed_price = fill_data["executed_price"]
        executed_quantity = fill_data["executed_quantity"]
        side = fill_data["side"]
        
        await self._handle_order_filled(order_id, executed_price, executed_quantity, side)
    
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
                    price=Decimal(str(executed_price)),
                    size=Decimal(str(executed_quantity)),
                    signal_type="FILLED"
                )
                
                # 添加成交記錄到追踪器
                fill_id = f"{order_id}_{int(time.time() * 1000000)}"  # 生成唯一成交ID
                self.order_tracker.add_fill(
                    order_id=order_id,
                    fill_id=fill_id,
                    price=Decimal(str(executed_price)),
                    quantity=Decimal(str(executed_quantity)),
                    side=side
                )
                
                # 檢查訂單是否完全成交
                order_info = self.order_tracker.get_order(order_id)
                if order_info and order_info.is_fully_filled():
                    # 從活躍訂單中移除
                    async with self._orders_lock:
                        if order_id in self.active_orders:
                            del self.active_orders[order_id]
                        if grid_price in self.grid_orders:
                            del self.grid_orders[grid_price]
                
                # 通知訊號生成器處理成交
                if self.signal_generator:
                    self.signal_generator.on_order_filled(filled_signal)
                
        except Exception as e:
            logger.error(f"處理訂單成交失敗: {e}")
    

    
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
            
            # 驗證並標準化訂單
            if self.market_info:
                try:
                    norm_price, norm_quantity = self.validator.validate_order(
                        self.market_info.symbol, price, quantity
                    )
                    price, quantity = norm_price, norm_quantity
                except ValidationError as e:
                    logger.error(f"訂單驗證失敗: {e}")
                    return
            
            # 創建限價訂單
            response = await self.client.create_limit_order(
                symbol=self.market_info.symbol if self.market_info else "PERP_BTC_USDC",
                side=side,
                price=float(price),
                quantity=float(quantity)
            )
            await asyncio.sleep(0.1)
            
            # 記錄訂單
            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    async with self._orders_lock:
                        self.active_orders[order_id] = {
                            "price": price,
                            "side": side,
                            "quantity": quantity
                        }
                        self.grid_orders[price] = order_id
                    
                    # 添加到訂單追踪器
                    symbol = self.market_info.symbol if self.market_info else "PERP_BTC_USDC"
                    self.order_tracker.add_order(
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        order_type="LIMIT",
                        price=price,
                        quantity=quantity
                    )
                    
                    logger.info(f"網格訂單創建成功: ID={order_id}, 價格={price}, 方向={side}")
            
        except Exception as e:
            logger.error(f"創建網格訂單失敗: {e}")
    
    async def _event_handler(self, event: Event):
        """統一事件處理器"""
        try:
            if event.event_type == EventType.SIGNAL:
                await self._handle_signal_event(event.data)
            elif event.event_type == EventType.ORDER_FILLED:
                await self._handle_order_filled_event(event.data)
        except Exception as e:
            logger.error(f"事件處理失敗: {e}")
    
    async def signal_handler(self, signal: TradingSignal):
        """
        處理交易訊號的回調函數（通過事件隊列）
        
        Args:
            signal: 交易訊號
        """
        if self.event_queue:
            event = Event(EventType.SIGNAL, signal)
            await self.event_queue.add_event(event)
        else:
            # 直接處理（向後兼容）
            await self._handle_signal_event(signal)
    
    async def _handle_signal_event(self, signal: TradingSignal):
        """
        實際處理交易訊號
        
        Args:
            signal: 交易訊號
        """
        try:
            logger.info(f"處理訊號: {signal.symbol} {signal.side.value} @ {signal.price} 數量:{signal.size}")
            
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
                
            elif signal.signal_type == "COUNTER":
                # 處理反向網格訊號
                await self._handle_counter_signal(signal, orderly_symbol, orderly_side)
                
            elif signal.signal_type == "CANCEL_ALL":
                # 處理取消所有訊號
                await self._handle_cancel_all_signal(orderly_symbol)
                
        except Exception as e:
            logger.error(f"處理訊號失敗: {e}")
    
    async def _handle_initial_signal(self, signal: TradingSignal, symbol: str, side: str):
        """處理初始網格訊號"""
        try:
            # 驗證並標準化訂單
            price, size = signal.price, signal.size
            if self.market_info:
                try:
                    norm_price, norm_size = self.validator.validate_order(
                        self.market_info.symbol, signal.price, signal.size
                    )
                    price, size = norm_price, norm_size
                except ValidationError as e:
                    logger.error(f"初始訂單驗證失敗: {e}")
                    return
            
            # 創建初始網格訂單
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=float(price),
                quantity=float(size)
            )
            
            # 記錄訂單
            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    async with self._orders_lock:
                        self.active_orders[order_id] = {
                            "price": signal.price,
                            "side": side,
                            "quantity": signal.size
                        }
                        self.grid_orders[signal.price] = order_id
                    logger.info(f"初始網格訂單創建成功: ID={order_id}, 價格={signal.price}")
            
        except Exception as e:
            logger.error(f"創建初始網格訂單失敗: {e}")
    
    async def _handle_counter_signal(self, signal: TradingSignal, symbol: str, side: str):
        """處理反向網格訊號"""
        try:
            # 驗證並標準化訂單
            price, size = signal.price, signal.size
            if self.market_info:
                try:
                    norm_price, norm_size = self.validator.validate_order(
                        self.market_info.symbol, signal.price, signal.size
                    )
                    price, size = norm_price, norm_size
                except ValidationError as e:
                    logger.error(f"反向訂單驗證失敗: {e}")
                    return
            
            # 創建反向網格訂單
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=float(price),
                quantity=float(size)
            )
            
            # 記錄訂單
            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    async with self._orders_lock:
                        self.active_orders[order_id] = {
                            "price": signal.price,
                            "side": side,
                            "quantity": signal.size
                        }
                        self.grid_orders[signal.price] = order_id
                    logger.info(f"反向網格訂單創建成功: ID={order_id}, 價格={signal.price}")
            
        except Exception as e:
            logger.error(f"創建反向網格訂單失敗: {e}")
    
    async def _handle_cancel_all_signal(self, symbol: str):
        """處理取消所有訊號"""
        try:
            # 取消所有相關訂單
            await self.client.cancel_all_orders(symbol)
            
            # 清空活躍訂單記錄
            async with self._orders_lock:
                self.active_orders.clear()
                self.grid_orders.clear()
            
            # 清空訂單追踪器
            self.order_tracker.clear()
            
            logger.info(f"已取消 {symbol} 的所有訂單")
            
        except Exception as e:
            logger.error(f"取消所有訂單失敗: {e}")
    
    async def _handle_stop_signal(self, symbol: str):
        """處理停止訊號"""
        logger.info(f"收到停止訊號，取消 {symbol} 的所有訂單")
        
        try:
            # 取消所有相關訂單
            await self.client.cancel_all_orders(symbol)
            
            # 清空活躍訂單記錄
            async with self._orders_lock:
                self.active_orders.clear()
                self.grid_orders.clear()
            
            # 清空訂單追踪器
            self.order_tracker.clear()
            
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
            # 設置會話上下文
            session_id = f"{config['user_id']}_{config['ticker']}"
            set_session_context(session_id)
            
            logger.info("啟動網格交易機器人", event_type="bot_start", data={
                "ticker": config['ticker'],
                "direction": config['direction'].value if hasattr(config['direction'], 'value') else str(config['direction']),
                "grid_levels": config['grid_levels'],
                "total_amount": config['total_amount']
            })
            
            metrics.increment_counter("bot.start", tags={"ticker": config['ticker']})
            start_time = time.time()
            
            # 驗證配置
            try:
                config = self.validator.validate_config(config)
                self.market_info = config.get("_market_info")
                logger.info("配置驗證通過", event_type="config_validated", data={"symbol": config['_orderly_symbol']})
            except ValidationError as e:
                logger.error("配置驗證失敗", event_type="config_validation_error", data={"error": str(e)})
                raise
            
            # 創建並啟動事件隊列
            self.event_queue = SessionEventQueue(
                session_id=f"{config['user_id']}_{config['ticker']}",
                event_handler=self._event_handler
            )
            await self.event_queue.start()
            
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
            
            elapsed_time = time.time() - start_time
            metrics.record_histogram("bot.start_time", elapsed_time)
            metrics.increment_counter("bot.start.success", tags={"ticker": config['ticker']})
            
            logger.info("網格交易機器人啟動成功", event_type="bot_started", data={
                "session_id": session_id,
                "start_time": elapsed_time
            })
            
        except Exception as e:
            metrics.increment_counter("bot.start.errors", tags={"ticker": config.get('ticker', 'unknown')})
            logger.error("啟動網格交易失敗", event_type="bot_start_error", data={
                "error": str(e),
                "ticker": config.get('ticker', 'unknown')
            })
            raise
    
    async def stop_grid_trading(self):
        """停止網格交易"""
        logger.info("停止網格交易機器人")
        
        if self.signal_generator:
            self.signal_generator.stop_by_signal()
        
        # 停止事件隊列
        if self.event_queue:
            await self.event_queue.stop()
            self.event_queue = None
        
        # 清空訂單追踪器
        self.order_tracker.clear()
        
        # 關閉 WebSocket 連接
        if self.wss_client:
            self.wss_client.close()
        
        self.is_running = False
        logger.info("網格交易機器人已停止", event_type="bot_stopped")
    
    async def get_status(self):
        """獲取機器人狀態"""
        status = {
            "is_running": self.is_running,
            "active_orders_count": len(self.active_orders),
            "active_orders": self.active_orders,
            "grid_orders": self.grid_orders,
            "order_statistics": self.order_tracker.get_statistics(),
            "event_queue_size": self.event_queue.get_queue_size() if self.event_queue else 0
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