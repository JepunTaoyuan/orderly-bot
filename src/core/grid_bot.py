#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易主程式
整合訊號生成器和交易客戶端，實現完整的網格交易系統
"""

import asyncio
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

# 使用結構化日誌
logger = get_logger("grid_bot")

class GridTradingBot:
    # 常數定義
    PROCESSED_FILLS_MAX_SIZE = 1000  # WebSocket 去重記錄最大數量
    PROCESSED_FILLS_TTL = 300  # WebSocket 去重記錄 TTL（秒）
    ORDER_CREATION_DELAY = 0.1  # 訂單創建之間的延遲（秒）

    def __init__(self, account_id: str, orderly_key: str, orderly_secret: str, orderly_testnet: bool):
        """初始化網格交易機器人"""
        self.client = OrderlyClient(account_id = account_id, orderly_key = orderly_key, orderly_secret = orderly_secret, orderly_testnet = orderly_testnet)
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
        self.session_id = None  # 會話ID，用於生成唯一的 WebSocket ID

        # WebSocket 事件去重（使用時間戳進行 TTL 管理）
        self.processed_fills = {}  # {fill_id: timestamp}
        self.processed_fills_max_size = self.PROCESSED_FILLS_MAX_SIZE
        self.processed_fills_ttl = self.PROCESSED_FILLS_TTL
        
    def _convert_symbol(self, symbol: str) -> str:
        """
        將訊號生成器的符號轉換為 Orderly 格式
        例如: BTCUSDT -> PERP_BTC_USDC
        """
        # 標準化符號格式（移除可能的 USDC 後綴）
        symbol_upper = symbol.upper().replace("USDC", "").replace("USDT", "")

        # 符號映射表（與 market_validator.py 保持一致）
        symbol_map = {
            "BTC": "PERP_BTC_USDC",
            "ETH": "PERP_ETH_USDC",
            "SOL": "PERP_SOL_USDC",
            "NEAR": "PERP_NEAR_USDC",
            "ARB": "PERP_ARB_USDC",
            "OP": "PERP_OP_USDC",
        }

        # 如果已經是 Orderly 格式（以 PERP_ 開頭），直接返回
        if symbol.startswith("PERP_"):
            return symbol

        # 嘗試映射
        if symbol_upper in symbol_map:
            return symbol_map[symbol_upper]

        # 如果找不到映射，記錄警告並返回原始值
        logger.warning(f"無法轉換交易對符號: {symbol}，使用原始值")
        return symbol
    
    def _convert_side(self, side: OrderSide) -> str:
        """將訊號生成器的方向轉換為 Orderly 格式"""
        return "BUY" if side == OrderSide.BUY else "SELL"
    
    def _safe_close_ws(self):
        """安全地關閉 WebSocket 連接，兼容不同客戶端實作。"""
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
        # 若無任何已知關閉方法，忽略但記錄
        logger.warning("WebSocket 客戶端不支援顯式關閉方法，已略過")
    
    def _setup_websocket(self, account_id: str, orderly_key: str, orderly_secret: str, orderly_testnet: bool):
        """設置 WebSocket 連接監聽訂單成交"""
        try:
            
            def on_close(_):
                logger.warning("WebSocket 連接已關閉")

            def on_error(_, error):
                """WebSocket 錯誤處理"""
                logger.error(f"WebSocket 錯誤: {error}", event_type="websocket_error")
                # 如果是認證錯誤，停止交易
                if "authentication" in str(error).lower() or "auth" in str(error).lower():
                    logger.critical("WebSocket 認證失敗，停止交易")
                    asyncio.create_task(self.stop_grid_trading())

            def on_message(_, message):
                """處理 WebSocket 訊息"""
                try:
                    data = json.loads(message) if isinstance(message, str) else message

                    # 檢查是否為訂單成交通知
                    if (data.get("topic") == "notifications" and
                        data.get("data", {}).get("messageType") == "ORDER_FILLED"):

                        # 解析成交信息
                        content_raw = data["data"]["contentRaw"]

                        order_id = content_raw["orderId"]
                        executed_price = content_raw["executedPrice"]
                        executed_quantity = content_raw["executedQuantity"]
                        side = content_raw["side"]
                        symbol = content_raw.get("symbol", "")
                        executed_timestamp = content_raw.get("executedTimestamp", 0)

                        # 生成唯一的 fill_id（防止重複處理）
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
                                "side": side,
                                "fill_id": fill_id
                            }
                            event = Event(EventType.ORDER_FILLED, fill_data)
                            asyncio.create_task(self.event_queue.add_event(event))
                        
                except Exception as e:
                    logger.error(f"處理 WebSocket 訊息失敗: {e}")

            # 創建 WebSocket 客戶端
            # 使用 session_id 作為 wss_id 確保每個會話都有唯一的 WebSocket 連接
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
        """清理過期的成交記錄（TTL 機制）"""
        current_time = time.time()
        expired_fills = [
            fill_id for fill_id, timestamp in self.processed_fills.items()
            if current_time - timestamp > self.processed_fills_ttl
        ]

        for fill_id in expired_fills:
            del self.processed_fills[fill_id]

        if expired_fills:
            logger.debug(f"清理過期成交記錄: {len(expired_fills)} 個")

        # 如果記錄仍然過多，清理最舊的一半
        if len(self.processed_fills) > self.processed_fills_max_size:
            sorted_fills = sorted(self.processed_fills.items(), key=lambda x: x[1])
            for fill_id, _ in sorted_fills[:len(sorted_fills) // 2]:
                del self.processed_fills[fill_id]
            logger.warning(f"強制清理舊記錄，保留 {len(self.processed_fills)} 個")

    async def _handle_order_filled_event(self, fill_data: Dict[str, Any]):
        """
        處理 WebSocket 成交事件（帶去重機制）

        Args:
            fill_data: 成交數據
        """
        try:
            # 提取成交信息
            order_id = fill_data.get('order_id')
            executed_price = fill_data.get('executed_price')
            executed_quantity = fill_data.get('executed_quantity')
            side = fill_data.get('side')
            fill_id = fill_data.get('fill_id')  # 成交唯一ID

            # 檢查必要字段
            if not all([order_id, executed_price, executed_quantity, side]):
                logger.warning(f"成交事件缺少必要字段: {fill_data}")
                return

            # WebSocket 事件去重檢查
            if fill_id:
                if fill_id in self.processed_fills:
                    logger.debug(f"重複成交事件，跳過: fill_id={fill_id}")
                    return

                # 添加到已處理集合，記錄時間戳
                current_time = time.time()
                self.processed_fills[fill_id] = current_time

                # 定期清理過期記錄
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
        創建網格訂單（帶重複檢查和事務性處理）
        
        Args:
            price: 訂單價格
            side: 交易方向
        """
        try:
            # 檢查重複掛單
            async with self._orders_lock:
                if price in self.grid_orders:
                    existing_order_id = self.grid_orders[price]
                    if existing_order_id != "PENDING":
                        logger.warning(f"價格 {price} 已有掛單 {existing_order_id}，跳過重複掛單")
                        return
                    else:
                        logger.warning(f"價格 {price} 正在處理中，跳過")
                        return
                
                # 標記為處理中，防止併發重複
                self.grid_orders[price] = "PENDING"
            
            # 計算訂單數量
            quantity = self.signal_generator.total_amount / self.signal_generator.grid_levels / price
            
            # 驗證並標準化訂單
            if self.market_info:
                try:
                    norm_price, norm_quantity = self.validator.validate_order(
                        self.market_info.symbol, Decimal(str(price)), Decimal(str(quantity))
                    )
                    price, quantity = float(norm_price), float(norm_quantity)
                except ValidationError as e:
                    logger.error(f"訂單驗證失敗: {e}")
                    # 清理 PENDING 標記
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
            
            # 事務性更新狀態
            async with self._orders_lock:
                if response.get('success', True):
                    order_id = response.get('data', {}).get('order_id')
                    if order_id:
                        # 更新訂單記錄
                        self.active_orders[order_id] = {
                            "price": price,
                            "side": side,
                            "quantity": quantity
                        }
                        self.grid_orders[price] = order_id
                        
                        # 添加到訂單追踪器
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
                        # 清理 PENDING 標記
                        self.grid_orders.pop(price, None)
                else:
                    logger.error(f"創建訂單失敗: {response}")
                    # 清理 PENDING 標記
                    self.grid_orders.pop(price, None)
            
        except Exception as e:
            logger.error(f"創建網格訂單失敗: {e}")
            # 異常時清理 PENDING 標記
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
                
            elif signal.signal_type == "MARKET_OPEN":
                # 處理市價開倉訊號
                await self._handle_market_open_signal(signal, orderly_symbol, orderly_side)
                
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
    
    async def _handle_market_open_signal(self, signal: TradingSignal, symbol: str, side: str):
        """處理市價開倉訊號（做多/做空初始倉位）"""
        try:
            logger.info(f"執行市價開倉: {side} @ 市價, 數量={signal.size}")
            
            # 驗證訂單大小
            size = signal.size
            if self.market_info:
                try:
                    # 市價單只需要驗證數量
                    _, norm_size = self.validator.validate_order(
                        self.market_info.symbol, 
                        signal.price,  # 市價單價格僅用於參考
                        signal.size
                    )
                    size = norm_size
                except ValidationError as e:
                    logger.error(f"市價開倉訂單驗證失敗: {e}")
                    return
            
            # 創建市價訂單
            response = await self.client.create_market_order(
                symbol=symbol,
                side=side,
                quantity=float(size)
            )
            
            # 記錄訂單（市價單不需要加入網格追踪）
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
        """處理取消所有訊號（帶狀態一致性保護）"""
        try:
            logger.info(f"開始取消 {symbol} 的所有訂單")
            
            # 先記錄當前狀態，用於回滾
            async with self._orders_lock:
                backup_active_orders = self.active_orders.copy()
                backup_grid_orders = self.grid_orders.copy()
            
            try:
                # 取消所有相關訂單
                response = await self.client.cancel_all_orders(symbol)
                
                # 檢查取消是否成功
                if not response.get('success', True):
                    logger.error(f"取消訂單 API 調用失敗: {response}")
                    return
                
                # 成功後清空狀態
                async with self._orders_lock:
                    self.active_orders.clear()
                    self.grid_orders.clear()
                
                # 清空訂單追踪器
                self.order_tracker.clear()
                
                logger.info(f"已成功取消 {symbol} 的所有訂單")
                
            except Exception as api_error:
                logger.error(f"取消訂單 API 調用異常: {api_error}")
                
                # API 調用失敗，恢復狀態（保守策略）
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
            # 先停止機器人，防止新訂單被創建
            self.is_running = False
            logger.info("機器人已設置為停止狀態")

            # 取消所有相關訂單
            await self.client.cancel_all_orders(symbol)

            # 清空活躍訂單記錄
            async with self._orders_lock:
                self.active_orders.clear()
                self.grid_orders.clear()

            # 清空訂單追踪器
            self.order_tracker.clear()

            # 關閉 WebSocket 連接
            if self.wss_client:
                self._safe_close_ws()

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
            self.session_id = session_id  # 保存 session_id 供 WebSocket 使用
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
            self._setup_websocket(
                account_id=config['orderly_account_id'],
                orderly_key=config['orderly_key'],
                orderly_secret=config['orderly_secret'],
                orderly_testnet=config['orderly_testnet']
            )
            
            # 啟動 WebSocket 監聽 - 訂閱通知（包含訂單成交通知）
            try:
                self.wss_client.get_notifications()
                logger.info("WebSocket 訂閱 notifications 成功")
            except Exception as e:
                # WebSocket 訂閱失敗是嚴重問題，應該記錄錯誤
                logger.error(f"WebSocket 訂閱 notifications 失敗: {e}")
                # 在測試環境中可能沒有實現，所以只記錄不中斷
            
            # 創建訊號生成器（僅用於初始網格設置）
            self.signal_generator = GridSignalGenerator(
                ticker=config['ticker'],
                current_price=config['current_price'],
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
        """停止網格交易（帶狀態清理）"""
        logger.info("停止網格交易機器人")
        
        if self.signal_generator:
            self.signal_generator.stop_by_signal()
        
        # 停止事件隊列
        if self.event_queue:
            await self.event_queue.stop()
            self.event_queue = None
        
        # 清空訂單追踪器
        self.order_tracker.clear()
        
        # 清空 WebSocket 事件去重記錄
        self.processed_fills.clear()
        
        # 關閉 WebSocket 連接
        if self.wss_client:
            self._safe_close_ws()
        
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