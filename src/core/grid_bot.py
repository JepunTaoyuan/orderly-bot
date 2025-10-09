#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易主程式（整合利潤追蹤版本）
整合訊號生成器、交易客戶端和利潤追蹤，實現完整的網格交易系統
"""

import asyncio
import json
import time
from decimal import Decimal
from typing import Dict, Any
from .grid_signal import GridSignalGenerator, TradingSignal, Direction, OrderSide
from .client import OrderlyClient
from .profit_tracker import ProfitTracker  # ⭐ 新增利潤追蹤
from src.utils.event_queue import SessionEventQueue, Event, EventType
from src.utils.market_validator import MarketValidator, ValidationError
from src.utils.order_tracker import OrderTracker, OrderStatus
from src.utils.logging_config import get_logger, metrics, set_session_context
from orderly_evm_connector.websocket.websocket_api import WebsocketPrivateAPIClient

logger = get_logger("grid_bot")

class GridTradingBot:
    # 常數定義
    PROCESSED_FILLS_MAX_SIZE = 1000
    PROCESSED_FILLS_TTL = 300
    ORDER_CREATION_DELAY = 0.1

    def __init__(self, account_id: str, orderly_key: str, orderly_secret: str, orderly_testnet: bool):
        """初始化網格交易機器人"""
        self.client = OrderlyClient(account_id = account_id, orderly_key = orderly_key, orderly_secret = orderly_secret, orderly_testnet = orderly_testnet)
        self.signal_generator = None
        self.active_orders = {}
        self.grid_orders = {}
        self.is_running = False
        self.wss_client = None
        self._orders_lock = asyncio.Lock()
        self.event_queue = None
        self.validator = MarketValidator()
        self.market_info = None
        self.order_tracker = OrderTracker()
        self.session_id = None
        
        # ⭐ 新增：利潤追蹤器
        self.profit_tracker: ProfitTracker = None

        # WebSocket 事件去重
        self.processed_fills = {}
        self.processed_fills_max_size = self.PROCESSED_FILLS_MAX_SIZE
        self.processed_fills_ttl = self.PROCESSED_FILLS_TTL
        
    
    def _convert_side(self, side: OrderSide) -> str:
        """將訊號生成器的方向轉換為 Orderly 格式"""
        return "BUY" if side == OrderSide.BUY else "SELL"
    
    def _safe_close_ws(self):
        """安全地關閉 WebSocket 連接"""
        if not self.wss_client:
            return
        for attr in ("close", "disconnect", "close_ws", "stop", "shutdown"):
            try:
                fn = getattr(self.wss_client, attr, None)
                if callable(fn):
                    fn()
                    logger.info(f"WebSocket 已關閉（方法: {attr}）")
                    return
            except Exception as e:
                logger.warning(f"嘗試關閉 WebSocket 失敗（方法: {attr}）: {e}")
        logger.warning("WebSocket 客戶端不支援顯式關閉方法，已略過")
    
    def _setup_websocket(self, account_id: str, orderly_key: str, orderly_secret: str, orderly_testnet: bool):
        """設置 WebSocket 連接監聽訂單成交"""
        try:
            
            def on_close(_):
                logger.warning("WebSocket 連接已關閉")

            def on_error(_, error):
                """WebSocket 錯誤處理"""
                logger.error(f"WebSocket 錯誤: {error}", event_type="websocket_error")
                if "authentication" in str(error).lower() or "auth" in str(error).lower():
                    logger.critical("WebSocket 認證失敗，停止交易")
                    asyncio.create_task(self.stop_grid_trading())

            def on_message(_, message):
                """處理 WebSocket 訊息"""
                try:
                    data = json.loads(message) if isinstance(message, str) else message

                    if (data.get("topic") == "notifications" and
                        data.get("data", {}).get("messageType") == "ORDER_FILLED"):

                        content_raw = data["data"]["contentRaw"]

                        order_id = content_raw["orderId"]
                        executed_price = content_raw["executedPrice"]
                        executed_quantity = content_raw["executedQuantity"]
                        side = content_raw["side"]
                        symbol = content_raw.get("symbol", "")
                        executed_timestamp = content_raw.get("executedTimestamp", 0)

                        fill_id = f"{order_id}_{executed_price}_{executed_quantity}_{executed_timestamp}"

                        logger.info("訂單成交", event_type="order_filled", data={
                            "order_id": order_id,
                            "symbol": symbol,
                            "price": executed_price,
                            "quantity": executed_quantity,
                            "side": side,
                            "timestamp": executed_timestamp,
                            "fill_id": fill_id
                        })

                        metrics.increment_counter("orders.filled", tags={"side": side})
                        metrics.record_histogram("order.fill_price", float(executed_price))
                        metrics.record_histogram("order.fill_quantity", float(executed_quantity))

                        if self.event_queue:
                            fill_data = {
                                "order_id": order_id,
                                "executed_price": executed_price,
                                "executed_quantity": executed_quantity,
                                "side": side,
                                "fill_id": fill_id
                            }
                            event = Event(EventType.ORDER_FILLED, fill_data)
                            asyncio.create_task(self.event_queue.add_event(event))
                        
                except Exception as e:
                    logger.error(f"處理 WebSocket 訊息失敗: {e}")

            wss_id = self.session_id or "grid_bot_default"
            self.wss_client = WebsocketPrivateAPIClient(
                orderly_testnet=orderly_testnet,
                orderly_account_id=account_id,
                wss_id=wss_id,
                orderly_key=orderly_key,
                orderly_secret=orderly_secret,
                on_message=on_message,
                on_close=on_close,
                on_error=on_error,
            )
            
        except Exception as e:
            logger.warning(f"設置 WebSocket 連接失敗: {e}")
            self.wss_client = None
    
    def _cleanup_old_fills(self):
        """清理過期的成交記錄"""
        current_time = time.time()
        expired_fills = [
            fill_id for fill_id, timestamp in self.processed_fills.items()
            if current_time - timestamp > self.processed_fills_ttl
        ]

        for fill_id in expired_fills:
            del self.processed_fills[fill_id]

        if expired_fills:
            logger.debug(f"清理過期成交記錄: {len(expired_fills)} 個")

        if len(self.processed_fills) > self.processed_fills_max_size:
            sorted_fills = sorted(self.processed_fills.items(), key=lambda x: x[1])
            for fill_id, _ in sorted_fills[:len(sorted_fills) // 2]:
                del self.processed_fills[fill_id]
            logger.warning(f"強制清理舊記錄，保留 {len(self.processed_fills)} 個")

    async def _handle_order_filled_event(self, fill_data: Dict[str, Any]):
        """處理 WebSocket 成交事件（帶去重機制）"""
        try:
            order_id = fill_data.get('order_id')
            executed_price = fill_data.get('executed_price')
            executed_quantity = fill_data.get('executed_quantity')
            side = fill_data.get('side')
            fill_id = fill_data.get('fill_id')

            if not all([order_id, executed_price, executed_quantity, side]):
                logger.warning(f"成交事件缺少必要字段: {fill_data}")
                return

            # WebSocket 事件去重檢查
            if fill_id:
                if fill_id in self.processed_fills:
                    logger.debug(f"重複成交事件，跳過: fill_id={fill_id}")
                    return

                current_time = time.time()
                self.processed_fills[fill_id] = current_time

                if len(self.processed_fills) % 100 == 0:
                    self._cleanup_old_fills()
            
            # 處理成交事件
            await self._handle_order_filled(
                order_id=int(order_id),
                executed_price=float(executed_price),
                executed_quantity=float(executed_quantity),
                side=side
            )
            
        except Exception as e:
            logger.error(f"處理成交事件失敗: {e}, 數據: {fill_data}")
    
    async def _handle_order_filled(self, order_id: int, executed_price: float, executed_quantity: float, side: str):
        """
        處理訂單成交事件（整合利潤追蹤）
        """
        try:
            if not self.is_running:
                return
            
            # ⭐ 新增：記錄到利潤追蹤器
            if self.profit_tracker:
                profit_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
                self.profit_tracker.add_trade(
                    side=profit_side,
                    price=Decimal(str(executed_price)),
                    quantity=Decimal(str(executed_quantity)),
                    timestamp=time.time()
                )
                logger.info(f"成交記錄已添加到利潤追蹤器: {side} {executed_quantity} @ {executed_price}")
            
            # 檢查是否為我們的網格訂單
            if order_id in self.active_orders:
                order_info = self.active_orders[order_id]
                grid_price = order_info["price"]
                
                logger.info(f"網格訂單成交: 價格={grid_price}, 成交價={executed_price}")
                
                # 創建成交訊號對象
                filled_signal = TradingSignal(
                    symbol=self.signal_generator.ticker if self.signal_generator else "UNKNOWN",
                    side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                    price=Decimal(str(executed_price)),
                    size=Decimal(str(executed_quantity)),
                    signal_type="FILLED"
                )
                
                # 添加成交記錄到追踪器
                fill_id = f"{order_id}_{int(time.time() * 1000000)}"
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
        """創建網格訂單"""
        try:
            async with self._orders_lock:
                if price in self.grid_orders:
                    existing_order_id = self.grid_orders[price]
                    if existing_order_id != "PENDING":
                        logger.warning(f"價格 {price} 已有掛單 {existing_order_id}，跳過重複掛單")
                        return
                    else:
                        logger.warning(f"價格 {price} 正在處理中，跳過")
                        return
                
                self.grid_orders[price] = "PENDING"
            
            # ⭐ 使用固定數量
            quantity = float(self.signal_generator.quantity_per_grid)
            
            # 驗證並標準化訂單
            if self.market_info:
                try:
                    norm_price, norm_quantity = self.validator.validate_order(
                        self.market_info.symbol, Decimal(str(price)), Decimal(str(quantity))
                    )
                    price, quantity = float(norm_price), float(norm_quantity)
                except ValidationError as e:
                    logger.error(f"訂單驗證失敗: {e}")
                    async with self._orders_lock:
                        self.grid_orders.pop(price, None)
                    return
            
            # 創建限價訂單
            symbol = self.market_info.symbol 
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=price,
                quantity=quantity
            )
            
            async with self._orders_lock:
                if response.get('success', True):
                    order_id = response.get('data', {}).get('order_id')
                    if order_id:
                        self.active_orders[order_id] = {
                            "price": price,
                            "side": side,
                            "quantity": quantity
                        }
                        self.grid_orders[price] = order_id
                        
                        self.order_tracker.add_order(
                            order_id=order_id,
                            symbol=symbol,
                            side=side,
                            order_type="LIMIT",
                            price=Decimal(str(price)),
                            quantity=Decimal(str(quantity))
                        )
                        
                        logger.info(f"網格訂單創建成功: ID={order_id}, 價格={price}, 方向={side}")
                    else:
                        logger.error(f"API 響應中缺少 order_id: {response}")
                        self.grid_orders.pop(price, None)
                else:
                    logger.error(f"創建訂單失敗: {response}")
                    self.grid_orders.pop(price, None)
            
        except Exception as e:
            logger.error(f"創建網格訂單失敗: {e}")
            async with self._orders_lock:
                self.grid_orders.pop(price, None)
    
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
        """處理交易訊號的回調函數"""
        if self.event_queue:
            event = Event(EventType.SIGNAL, signal)
            await self.event_queue.add_event(event)
        else:
            await self._handle_signal_event(signal)
    
    async def _handle_signal_event(self, signal: TradingSignal):
        """實際處理交易訊號"""
        try:
            logger.info(f"處理訊號: {signal.symbol} {signal.side.value} @ {signal.price} 數量:{signal.size}")
            
            if not self.is_running:
                logger.warning("機器人未運行，忽略訊號")
                return
            
            orderly_symbol = signal.symbol
            orderly_side = self._convert_side(signal.side)
            
            if signal.signal_type == "STOP":
                await self._handle_stop_signal(orderly_symbol)
                
            elif signal.signal_type == "MARKET_OPEN":
                await self._handle_market_open_signal(signal, orderly_symbol, orderly_side)
                
            elif signal.signal_type == "INITIAL":
                await self._handle_initial_signal(signal, orderly_symbol, orderly_side)
                
            elif signal.signal_type == "COUNTER":
                await self._handle_counter_signal(signal, orderly_symbol, orderly_side)
                
            elif signal.signal_type == "CANCEL_ALL":
                await self._handle_cancel_all_signal(orderly_symbol)
                
        except Exception as e:
            logger.error(f"處理訊號失敗: {e}")
    
    async def _handle_market_open_signal(self, signal: TradingSignal, symbol: str, side: str):
        """處理市價開倉訊號"""
        try:
            logger.info(f"執行市價開倉: {side} @ 市價, 數量={signal.size}")
            
            size = signal.size
            if self.market_info:
                try:
                    _, norm_size = self.validator.validate_order(
                        self.market_info.symbol, 
                        signal.price,
                        signal.size
                    )
                    size = norm_size
                except ValidationError as e:
                    logger.error(f"市價開倉訂單驗證失敗: {e}")
                    return
            
            response = await self.client.create_market_order(
                symbol=symbol,
                side=side,
                quantity=float(size)
            )
            
            # ⭐ 新增：記錄市價開倉到利潤追蹤器
            if response.get('success', True) and self.profit_tracker:
                # 使用當前價格作為市價開倉的價格
                profit_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
                self.profit_tracker.add_trade(
                    side=profit_side,
                    price=signal.price,
                    quantity=size,
                    timestamp=time.time()
                )
                logger.info(f"市價開倉已記錄到利潤追蹤器: {side} {size} @ {signal.price}")
            
            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    logger.info(f"市價開倉成功: ID={order_id}, 方向={side}, 數量={size}")
                    metrics.increment_counter("orders.market_open", tags={"side": side})
                else:
                    logger.error(f"市價開倉響應中缺少 order_id: {response}")
            else:
                logger.error(f"市價開倉失敗: {response}")
                metrics.increment_counter("orders.market_open.errors", tags={"side": side})
            
        except Exception as e:
            logger.error(f"執行市價開倉失敗: {e}")
            metrics.increment_counter("orders.market_open.errors", tags={"side": side})
    
    async def _handle_initial_signal(self, signal: TradingSignal, symbol: str, side: str):
        """處理初始網格訊號"""
        try:
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
            
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=float(price),
                quantity=float(size)
            )
            
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
            
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=float(price),
                quantity=float(size)
            )
            
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
            logger.info(f"開始取消 {symbol} 的所有訂單")
            
            async with self._orders_lock:
                backup_active_orders = self.active_orders.copy()
                backup_grid_orders = self.grid_orders.copy()
            
            try:
                response = await self.client.cancel_all_orders(symbol)
                
                if not response.get('success', True):
                    logger.error(f"取消訂單 API 調用失敗: {response}")
                    return
                
                async with self._orders_lock:
                    self.active_orders.clear()
                    self.grid_orders.clear()
                
                self.order_tracker.clear()
                
                logger.info(f"已成功取消 {symbol} 的所有訂單")
                
            except Exception as api_error:
                logger.error(f"取消訂單 API 調用異常: {api_error}")
                
                async with self._orders_lock:
                    self.active_orders = backup_active_orders
                    self.grid_orders = backup_grid_orders
                
                logger.warning("API 調用失敗，已恢復訂單狀態")
                raise
            
        except Exception as e:
            logger.error(f"取消所有訂單失敗: {e}")
            raise
    
    async def _handle_stop_signal(self, symbol: str):
        """處理停止訊號"""
        logger.info(f"收到停止訊號，取消 {symbol} 的所有訂單")

        try:
            self.is_running = False
            logger.info("機器人已設置為停止狀態")

            await self.client.cancel_all_orders(symbol)

            async with self._orders_lock:
                self.active_orders.clear()
                self.grid_orders.clear()

            self.order_tracker.clear()

            if self.wss_client:
                self._safe_close_ws()

            logger.info("停止訊號處理完成")

        except Exception as e:
            logger.error(f"處理停止訊號失敗: {e}")
    
    async def start_grid_trading(self, config: Dict[str, Any]):
        """啟動網格交易（整合利潤追蹤）"""
        try:
            session_id = f"{config['user_id']}_{config['ticker']}"
            self.session_id = session_id
            set_session_context(session_id)
            
            logger.info("啟動網格交易機器人", event_type="bot_start", data={
                "ticker": config['ticker'],
                "direction": config['direction'].value if hasattr(config['direction'], 'value') else str(config['direction']),
                "grid_levels": config['grid_levels'],
                "total_margin": config['total_margin']  # ⭐ 改名
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
            
            # ⭐ 新增：初始化利潤追蹤器
            self.profit_tracker = ProfitTracker(
                symbol=config['_orderly_symbol'],
                fee_rate=Decimal('0.001')  # 0.1% 手續費
            )
            logger.info("利潤追蹤器已初始化")
            
            # 創建並啟動事件隊列
            self.event_queue = SessionEventQueue(
                session_id=f"{config['user_id']}_{config['ticker']}",
                event_handler=self._event_handler
            )
            await self.event_queue.start()
            
            # 設置 WebSocket 連接
            self._setup_websocket(
                account_id=config['orderly_account_id'],
                orderly_key=config['orderly_key'],
                orderly_secret=config['orderly_secret'],
                orderly_testnet=config['orderly_testnet']
            )
            
            # 啟動 WebSocket 監聽
            try:
                self.wss_client.get_notifications()
                logger.info("WebSocket 訂閱 notifications 成功")
            except Exception as e:
                logger.error(f"WebSocket 訂閱 notifications 失敗: {e}")
            
            # 創建訊號生成器（⭐ 使用新的固定數量版本）
            self.signal_generator = GridSignalGenerator(
                ticker=config['ticker'],
                current_price=config['current_price'],
                direction=config['direction'],
                upper_bound=config['upper_bound'],
                lower_bound=config['lower_bound'],
                grid_levels=config['grid_levels'],
                total_margin=config['total_margin'],  # ⭐ 改名
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
        
        if self.event_queue:
            await self.event_queue.stop()
            self.event_queue = None
        
        self.order_tracker.clear()
        
        self.processed_fills.clear()
        
        if self.wss_client:
            self._safe_close_ws()
        
        self.is_running = False
        logger.info("網格交易機器人已停止", event_type="bot_stopped")
    
    async def get_status(self):
        """獲取機器人狀態（包含利潤統計）"""
        status = {
            "is_running": self.is_running,
            "active_orders_count": len(self.active_orders),
            "active_orders": self.active_orders,
            "grid_orders": self.grid_orders,
            "order_statistics": self.order_tracker.get_statistics(),
            "event_queue_size": self.event_queue.get_queue_size() if self.event_queue else 0
        }
        
        # ⭐ 新增：包含利潤統計
        if self.profit_tracker:
            try:
                # 獲取當前市場價格
                positions = await self.client.get_positions()
                current_price = None
                
                # 嘗試從持倉信息中獲取當前價格
                for position in positions.get('data', {}).get('rows', []):
                    if position.get('symbol') == self.profit_tracker.symbol:
                        current_price = Decimal(str(position.get('mark_price', 0)))
                        break
                
                # 獲取利潤統計摘要
                profit_summary = self.profit_tracker.get_summary(current_price)
                status["profit_statistics"] = profit_summary
                
            except Exception as e:
                logger.error(f"獲取利潤統計失敗: {e}")
                status["profit_statistics"] = {"error": str(e)}
        
        if self.signal_generator:
            self.signal_generator.get_status()
        
        try:
            account_info = await self.client.get_account_info()
            status["account_info"] = account_info
            
            positions = await self.client.get_positions()
            status["positions"] = positions
            
        except Exception as e:
            logger.error(f"獲取狀態失敗: {e}")
        
        return status
    
    async def get_profit_report(self) -> Dict[str, Any]:
        """
        ⭐ 新增：獲取利潤報告
        
        Returns:
            利潤報告字典
        """
        if not self.profit_tracker:
            return {"error": "利潤追蹤器未初始化"}
        
        try:
            # 獲取當前價格
            positions = await self.client.get_positions()
            current_price = None
            
            for position in positions.get('data', {}).get('rows', []):
                if position.get('symbol') == self.profit_tracker.symbol:
                    current_price = Decimal(str(position.get('mark_price', 0)))
                    break
            
            # 獲取完整報告
            return {
                "summary": self.profit_tracker.get_summary(current_price),
                "trade_history": self.profit_tracker.get_trade_history(limit=20),
                "closed_positions": self.profit_tracker.get_closed_positions(limit=10),
                "open_positions": self.profit_tracker.get_open_positions()
            }
            
        except Exception as e:
            logger.error(f"獲取利潤報告失敗: {e}")
            return {"error": str(e)}
