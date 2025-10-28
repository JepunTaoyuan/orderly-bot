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
from src.utils.websocket_manager import get_websocket_manager, WSConnectionState

logger = get_logger("grid_bot")

class GridTradingBot:
    # 常數定義
    PROCESSED_FILLS_MAX_SIZE = 1000
    PROCESSED_FILLS_TTL = 300
    ORDER_CREATION_DELAY = 0.1

    # WebSocket 重連配置
    WS_RECONNECT_MAX_RETRIES = 5
    WS_RECONNECT_BASE_DELAY = 2  # 秒
    WS_RECONNECT_MAX_DELAY = 60  # 秒

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

        self.main_loop = None  # 保存主事件循環

        self.ws_reconnect_task = None
        self.ws_reconnect_attempts = 0
        self.ws_should_reconnect = True  # 控制是否應該重連
        self.ws_credentials = None  # 保存 WebSocket 憑證
        
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
    
    async def _setup_websocket(self, account_id: str, orderly_key: str, orderly_secret: str, orderly_testnet: bool):
        """設置 WebSocket 連接監聽訂單成交（使用 WebSocket 管理器）"""
        try:
            # 保存憑證用於重連
            self.ws_credentials = {
                'account_id': account_id,
                'orderly_key': orderly_key,
                'orderly_secret': orderly_secret,
                'orderly_testnet': orderly_testnet
            }

            def on_close(_):
                logger.warning("WebSocket 連接已關閉")

                # 更新連接狀態
                if self.session_id:
                    asyncio.create_task(self._update_ws_state(WSConnectionState.DISCONNECTED))

                # 如果機器人還在運行且應該重連，則觸發重連
                if self.is_running and self.ws_should_reconnect:
                    logger.info("檢測到 WebSocket 意外關閉，準備重連")
                    # 使用 asyncio 調度重連任務
                    if self.ws_reconnect_task is None or self.ws_reconnect_task.done():
                        loop = asyncio.get_event_loop()
                        self.ws_reconnect_task = loop.create_task(self._handle_ws_reconnect())

            def on_error(_, error):
                """WebSocket 錯誤處理"""
                logger.error(f"WebSocket 錯誤: {error}", event_type="websocket_error")
                if "authentication" in str(error).lower() or "auth" in str(error).lower():
                    logger.critical("WebSocket 認證失敗，停止交易")
                    asyncio.create_task(self.stop_grid_trading())
                    return

                # 更新連接狀態為失敗
                if self.session_id:
                    asyncio.create_task(self._update_ws_state(WSConnectionState.FAILED))

                # 其他錯誤觸發重連
                if self.is_running and self.ws_should_reconnect:
                    logger.info("WebSocket 錯誤，準備重連")
                    if self.ws_reconnect_task is None or self.ws_reconnect_task.done():
                        loop = asyncio.get_event_loop()
                        self.ws_reconnect_task = loop.create_task(self._handle_ws_reconnect())

            def on_message(_, message):
                """處理 WebSocket 訊息"""
                try:
                    data = json.loads(message) if isinstance(message, str) else message

                    # 兼容不同的通知內容格式（contentRaw 或 content）
                    if data.get("topic") == "notifications":
                        payload = data.get("data", {})
                        msg_type = payload.get("messageType")
                        if msg_type == "ORDER_FILLED":
                            content = payload.get("contentRaw") or payload.get("content")
                            content_json = {}
                            if isinstance(content, str):
                                try:
                                    content_json = json.loads(content)
                                except Exception:
                                    content_json = {}
                            elif isinstance(content, dict):
                                content_json = content

                            order_id = content_json.get("orderId") or payload.get("orderId") or data.get("orderId")
                            executed_price = content_json.get("executedPrice")
                            executed_quantity = content_json.get("executedQuantity")
                            side = content_json.get("side")
                            symbol = (content_json.get("symbol") or "")
                            executed_timestamp = content_json.get("executedTimestamp", 0)

                            if order_id is None:
                                logger.warning(f"ORDER_FILLED 通知缺少 orderId，原始資料: {data}")
                                return

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
                            if executed_price is not None:
                                metrics.record_histogram("order.fill_price", float(executed_price))
                            if executed_quantity is not None:
                                metrics.record_histogram("order.fill_quantity", float(executed_quantity))

                            if self.event_queue:
                                fill_data = {
                                    "order_id": order_id,
                                    "executed_price": executed_price,
                                    "executed_quantity": executed_quantity,
                                    "side": side,
                                    "fill_id": fill_id
                                }
                                if self.event_queue and self.main_loop:
                                    event = Event(EventType.ORDER_FILLED, fill_data)
                                    # 線程安全地調度到主事件循環
                                    asyncio.run_coroutine_threadsafe(
                                        self.event_queue.add_event(event),
                                        self.main_loop
                                    )

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

            # 使用 WebSocket 管理器註冊連接
            if self.session_id:
                ws_manager = get_websocket_manager()
                await ws_manager.create_connection(
                    session_id=self.session_id,
                    client=self.wss_client,
                    credentials=self.ws_credentials
                )
                await ws_manager.set_connection_state(self.session_id, WSConnectionState.CONNECTED)

            logger.info("WebSocket 客戶端初始化成功")

        except Exception as e:
            logger.warning(f"設置 WebSocket 連接失敗: {e}")
            self.wss_client = None

    async def _update_ws_state(self, state: WSConnectionState):
        """更新 WebSocket 連接狀態"""
        if self.session_id:
            ws_manager = get_websocket_manager()
            await ws_manager.set_connection_state(self.session_id, state)

    async def _handle_ws_reconnect(self):
        """
        處理 WebSocket 重連
        這個方法會在 WebSocket 斷線時自動調用
        """
        try:
            logger.info("開始 WebSocket 重連流程")
            
            # 關閉舊連接
            if self.wss_client:
                try:
                    self._safe_close_ws()
                except Exception as e:
                    logger.warning(f"關閉舊 WebSocket 連接時發生錯誤: {e}")
            
            # 執行重連
            success = await self._reconnect_websocket()
            
            if success:
                logger.info("WebSocket 重連成功")
                metrics.increment_counter("websocket.reconnect.success")
            else:
                logger.error("WebSocket 重連失敗，已達最大重試次數")
                metrics.increment_counter("websocket.reconnect.failed")
                
                # 可選：重連失敗後的處理
                # 1. 繼續運行但不接收 WebSocket 消息
                # 2. 停止網格交易
                # 這裡選擇繼續運行（網格訂單仍然有效）
                logger.warning("WebSocket 重連失敗，機器人將繼續運行但無法接收實時成交通知")
                
        except Exception as e:
            logger.error(f"WebSocket 重連流程異常: {e}")

    async def _reconnect_websocket(self, max_retries: int = None) -> bool:
        """
        WebSocket 自動重連
        
        Args:
            max_retries: 最大重試次數（None 使用默認值）
            
        Returns:
            bool: 是否重連成功
        """
        if max_retries is None:
            max_retries = self.WS_RECONNECT_MAX_RETRIES
        
        if not self.ws_credentials:
            logger.error("缺少 WebSocket 憑證，無法重連")
            return False
        
        for attempt in range(1, max_retries + 1):
            try:
                self.ws_reconnect_attempts = attempt
                
                # 計算退避延遲（指數退避）
                delay = min(
                    self.WS_RECONNECT_BASE_DELAY * (2 ** (attempt - 1)),
                    self.WS_RECONNECT_MAX_DELAY
                )
                
                logger.info(
                    f"WebSocket 重連嘗試 {attempt}/{max_retries}",
                    data={"delay": delay}
                )
                
                # 等待後重試
                if attempt > 1:
                    await asyncio.sleep(delay)
                
                # 重新設置 WebSocket
                self._setup_websocket(
                    account_id=self.ws_credentials['account_id'],
                    orderly_key=self.ws_credentials['orderly_key'],
                    orderly_secret=self.ws_credentials['orderly_secret'],
                    orderly_testnet=self.ws_credentials['orderly_testnet']
                )
                
                if not self.wss_client:
                    raise Exception("WebSocket 客戶端創建失敗")
                
                # 啟動連線並訂閱通知
                if hasattr(self.wss_client, "run"):
                    await self.wss_client.run()
                else:
                    logger.warning("WebSocket 客戶端缺少 run()，可能無法啟動連線")
                self.wss_client.get_notifications()
                
                logger.info(f"WebSocket 重連成功（嘗試 {attempt} 次）")
                
                # 重置重連計數器
                self.ws_reconnect_attempts = 0
                
                return True
                
            except Exception as e:
                logger.warning(
                    f"WebSocket 重連失敗 ({attempt}/{max_retries}): {e}",
                    event_type="websocket_reconnect_failed"
                )
                
                if attempt == max_retries:
                    logger.error(
                        f"WebSocket 重連已達最大嘗試次數 ({max_retries})，放棄重連",
                        event_type="websocket_reconnect_exhausted"
                    )
                    return False
        
        return False
    
    def _cleanup_old_fills(self):
        """清理過期的成交記錄（優化版本）"""
        # 確保 time 模塊可用
        import time

        if not self.processed_fills:
            return

        current_time = time.time()
        original_size = len(self.processed_fills)

        # 清理過期記錄
        expired_fills = [
            fill_id for fill_id, timestamp in self.processed_fills.items()
            if current_time - timestamp > self.processed_fills_ttl
        ]

        for fill_id in expired_fills:
            del self.processed_fills[fill_id]

        # 如果仍超過最大大小，按時間戳排序，保留最近的記錄
        if len(self.processed_fills) > self.processed_fills_max_size:
            # 按時間戳排序，刪除最舊的一半記錄
            sorted_fills = sorted(self.processed_fills.items(), key=lambda x: x[1])
            to_remove = len(sorted_fills) - self.processed_fills_max_size

            for fill_id, _ in sorted_fills[:to_remove]:
                del self.processed_fills[fill_id]

        cleaned_count = original_size - len(self.processed_fills)
        if cleaned_count > 0:
            logger.debug(f"清理過期成交記錄: {cleaned_count} 個，當前大小: {len(self.processed_fills)}")

    async def cleanup_memory_if_needed(self):
        """定期清理記憶體（非阻塞版本）"""
        # 清理過期成交記錄
        self._cleanup_old_fills()

        # 清理事件隊列積壓
        if self.event_queue and self.event_queue.get_queue_size() > 1000:
            logger.warning(f"事件隊列積壓過多: {self.event_queue.get_queue_size()}，可能存在性能問題")
            metrics.increment_counter("event_queue.backlog_warning")

        # 記錄記憶體使用情況
        if len(self.processed_fills) > 1000:
            logger.warning(f"成交記錄數量過多: {len(self.processed_fills)}，可能影響性能")
            metrics.set_gauge("processed_fills.count", len(self.processed_fills))

    async def _handle_order_filled_event(self, fill_data: Dict[str, Any]):
        """處理 WebSocket 成交事件（帶去重機制）"""
        # 確保 time 模塊可用
        import time

        try:
            order_id = fill_data.get('order_id')
            executed_price = fill_data.get('executed_price')
            executed_quantity = fill_data.get('executed_quantity')
            side = fill_data.get('side')
            fill_id = fill_data.get('fill_id')
            symbol = fill_data.get('symbol', '')

            if not all([order_id, executed_price, executed_quantity, side]):
                logger.warning(f"成交事件缺少必要字段: {fill_data}")
                return

            # 🛡️ 安全檢查：確保只處理網格交易的成交
            if self.market_info and symbol != self.market_info.symbol:
                logger.debug(f"忽略非網格交易對的成交: {symbol} (網格: {self.market_info.symbol})")
                return

            # 🛡️ 安全檢查：確保是我們的訂單
            if order_id not in self.active_orders:
                logger.warning(f"收到非網格訂單的成交通知: {order_id}, symbol: {symbol}")
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
        # 確保 time 模塊可用
        import time

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

                    # 僅在完全成交時，通知訊號生成器處理下一步（取消與掛相鄰格）
                    if self.signal_generator:
                        self.signal_generator.on_order_filled(filled_signal)
                else:
                    # 部分成交時不觸發下一步，僅記錄進度
                    try:
                        if order_info:
                            progress = order_info.get_fill_percentage()
                            logger.info(
                                "部分成交，暫不觸發下一格下單",
                                event_type="order_partial",
                                data={
                                    "order_id": order_id,
                                    "filled": str(order_info.filled_quantity),
                                    "original": str(order_info.original_quantity),
                                    "remaining": str(order_info.remaining_quantity),
                                    "progress_pct": f"{progress:.2f}"
                                }
                            )
                        else:
                            logger.info(
                                "部分成交，暫不觸發下一格下單",
                                event_type="order_partial",
                                data={"order_id": order_id}
                            )
                    except Exception:
                        # 保守處理：日誌不可影響流程
                        logger.debug("記錄部分成交進度失敗，忽略")
                
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
                            "quantity": quantity,
                            "order_type": "LIMIT"  # 標記為限價單
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
                    # 🛡️ 修復：將市價開倉訂單也加入到追蹤系統
                    async with self._orders_lock:
                        self.active_orders[order_id] = {
                            "price": float(signal.price),  # 使用訊號價格作為參考
                            "side": side,
                            "quantity": float(size),
                            "order_type": "MARKET"  # 標記為市價單
                        }
                        # 市價單不需要加入 grid_orders（因為沒有固定價格）

                    # 同時添加到 order_tracker
                    self.order_tracker.add_order(
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        order_type="MARKET",
                        price=signal.price,
                        quantity=size
                    )

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
            
            # 使用浮點數價格確保一致性
            float_price = float(price)
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=float_price,
                quantity=float(size)
            )

            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    async with self._orders_lock:
                        self.active_orders[order_id] = {
                            "price": float_price,  # 統一使用浮點數
                            "side": side,
                            "quantity": float(size),
                            "order_type": "LIMIT"  # 標記為限價單
                        }
                        self.grid_orders[float_price] = order_id
                    logger.info(f"初始網格訂單創建成功: ID={order_id}, 價格={float_price}")
            
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
            
            # 使用浮點數價格確保一致性
            float_price = float(price)
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=float_price,
                quantity=float(size)
            )

            if response.get('success', True):
                order_id = response.get('data', {}).get('order_id')
                if order_id:
                    async with self._orders_lock:
                        self.active_orders[order_id] = {
                            "price": float_price,  # 統一使用浮點數
                            "side": side,
                            "quantity": float(size),
                            "order_type": "LIMIT"  # 標記為限價單
                        }
                        self.grid_orders[float_price] = order_id
                    logger.info(f"反向網格訂單創建成功: ID={order_id}, 價格={float_price}")
            
        except Exception as e:
            logger.error(f"創建反向網格訂單失敗: {e}")
    
    async def _handle_cancel_all_signal(self, symbol: str):
        """處理取消網格訂單訊號（安全版本，只取消網格訂單）"""
        try:
            logger.info(f"開始安全取消 {symbol} 的網格訂單")

            # 🛡️ 安全檢查：確保只處理網格交易對
            if self.market_info and symbol != self.market_info.symbol:
                logger.error(f"嘗試取消非網格交易對的訂單: {symbol} (網格: {self.market_info.symbol})")
                return

            async with self._orders_lock:
                backup_active_orders = self.active_orders.copy()
                backup_grid_orders = self.grid_orders.copy()
                grid_order_ids = list(backup_grid_orders.values())
                # 過濾掉 "PENDING" 狀態
                grid_order_ids = [oid for oid in grid_order_ids if oid != "PENDING"]

            if not grid_order_ids:
                logger.info(f"沒有需要取消的網格訂單: {symbol}")
                return

            try:
                # 🛡️ 安全改進：逐個取消網格訂單，而不是 cancel_all_orders
                cancelled_count = 0
                failed_orders = []

                logger.info(f"準備取消 {len(grid_order_ids)} 個網格訂單")

                for order_id in grid_order_ids:
                    time.sleep(0.101)  # 避免過快取消，增加穩定性
                    try:
                        # 檢查訂單類型，市價單通常不需要取消（已成交）
                        order_info = self.active_orders.get(order_id, {})
                        order_type = order_info.get('order_type', 'LIMIT')

                        if order_type == 'MARKET':
                            # 市價單通常已經成交，直接從追蹤中移除
                            logger.info(f"跳過取消市價單（已成交）: {order_id}")
                            cancelled_count += 1
                        else:
                            # 限價單需要取消
                            response = await self.client.cancel_order(symbol, order_id)
                            if response.get('success', True):
                                cancelled_count += 1
                                logger.info(f"成功取消網格訂單: {order_id}")
                            else:
                                failed_orders.append(order_id)
                                logger.error(f"取消網格訂單失敗: {order_id}, 原因: {response}")
                    except Exception as e:
                        failed_orders.append(order_id)
                        logger.error(f"取消網格訂單異常: {order_id}, 錯誤: {e}")

                # 清理已成功取消的訂單
                async with self._orders_lock:
                    for order_id in grid_order_ids:
                        if order_id in failed_orders:
                            continue

                        # 從 active_orders 中移除
                        if order_id in self.active_orders:
                            del self.active_orders[order_id]

                        # 從 grid_orders 中移除
                        for price, oid in self.grid_orders.items():
                            if oid == order_id:
                                del self.grid_orders[price]
                                break

                    # 清理 order_tracker
                    for order_id in grid_order_ids:
                        if order_id not in failed_orders:
                            self.order_tracker.remove_order(order_id)

                logger.info(f"網格訂單取消完成: 成功 {cancelled_count} 個, 失敗 {len(failed_orders)} 個")

                if failed_orders:
                    logger.warning(f"部分網格訂單取消失敗: {failed_orders}")

            except Exception as api_error:
                logger.error(f"取消網格訂單 API 調用異常: {api_error}")

                async with self._orders_lock:
                    self.active_orders = backup_active_orders
                    self.grid_orders = backup_grid_orders

                logger.warning("API 調用失敗，已恢復訂單狀態")
                raise

        except Exception as e:
            logger.error(f"取消網格訂單失敗: {e}")
            raise
    
    async def _handle_stop_signal(self, symbol: str):
        """處理停止訊號（安全版本，取消網格訂單並平倉）"""
        logger.info(f"收到停止訊號，安全取消 {symbol} 的網格訂單")

        try:
            self.is_running = False
            logger.info("機器人已設置為停止狀態")

            # 🛡️ 安全改進：使用安全的網格訂單取消，而不是 cancel_all_orders
            await self._handle_cancel_all_signal(symbol)

            # 🔄 新增：自動平倉邏輯 - 在取消訂單後檢查並平倉
            if self.market_info and self.market_info.symbol == symbol:
                try:
                    logger.info(f"檢查 {symbol} 的持倉狀態...")
                    positions = await self.client.get_positions()

                    if positions.get('success') and positions.get('data'):
                        for position in positions.get('data', {}).get('rows', []):
                            if position.get('symbol') == symbol:
                                position_qty = float(position.get('position_qty', 0))
                                if position_qty != 0:
                                    logger.info(f"檢測到持倉 {position_qty}，開始自動平倉...")
                                    close_result = await self.client.close_position(symbol)

                                    if close_result.get('success'):
                                        logger.info(f"持倉已成功平倉: {position_qty}")
                                    else:
                                        logger.warning(f"平倉失敗: {close_result.get('message', '未知錯誤')}")
                                    break
                        else:
                            logger.info(f"{symbol} 無持倉，無需平倉")
                    else:
                        logger.warning("無法獲取持倉信息")

                except Exception as e:
                    logger.error(f"檢查或平倉時發生錯誤: {e}")
                    # 平倉失敗不影響停止流程的其他部分

            if self.wss_client:
                self._safe_close_ws()

            logger.info("停止訊號處理完成")

        except Exception as e:
            logger.error(f"處理停止訊號失敗: {e}")

    # （已移除舊版占位符重連方法，避免覆蓋正確實作）
    
    async def start_grid_trading(self, config: Dict[str, Any]):
        """啟動網格交易（整合利潤追蹤）"""
        # 確保 time 模塊可用 (防止運行時導入問題)
        import time

        # 保存當前事件循環
        self.main_loop = asyncio.get_running_loop()
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
            # ⭐ 設置總保證金用於計算資金利用率
            self.profit_tracker.set_total_margin(Decimal(str(config['total_margin'])))
            logger.info("利潤追蹤器已初始化")
            
            # 創建並啟動事件隊列
            self.event_queue = SessionEventQueue(
                session_id=f"{config['user_id']}_{config['ticker']}",
                event_handler=self._event_handler
            )
            logger.info("事件隊列已初始化")
            await self.event_queue.start()
            
            # 設置 WebSocket 連接
            self._setup_websocket(
                account_id=config['orderly_account_id'],
                orderly_key=config['orderly_key'],
                orderly_secret=config['orderly_secret'],
                orderly_testnet=config['orderly_testnet']
            )
            logger.info("WebSocket 客戶端已初始化")

            # 啟用 WebSocket 重連
            self.ws_should_reconnect = True
            self.ws_reconnect_attempts = 0
            
            # 啟動 WebSocket 連線並監聽
            try:
                if hasattr(self.wss_client, "run"):
                    await self.wss_client.run()
                else:
                    logger.warning("WebSocket 客戶端缺少 run()，可能無法啟動連線")
                self.wss_client.get_notifications()
                logger.info("WebSocket 啟動並訂閱 notifications 成功")
            except Exception as e:
                logger.error(f"WebSocket 啟動或訂閱 notifications 失敗: {e}")
            
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

            # 確保 time 模塊可用
            import time
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

        # 禁用 WebSocket 重連
        self.ws_should_reconnect = False

        # 🛠️ 長期解決方案：安全地處理 WebSocket 重連任務
        if self.ws_reconnect_task:
            if not self.ws_reconnect_task.done():
                logger.info("正在停止 WebSocket 重連任務...")
                try:
                    # 短超時取消任務
                    self.ws_reconnect_task.cancel()
                    await asyncio.wait_for(self.ws_reconnect_task, timeout=2.0)
                    logger.info("WebSocket 重連任務已停止")
                except asyncio.TimeoutError:
                    logger.warning("WebSocket 重連任務停止超時，跳過")
                except asyncio.CancelledError:
                    logger.info("WebSocket 重連任務已取消")
                except Exception as e:
                    logger.warning(f"停止 WebSocket 重連任務時發生錯誤: {e}")

            # 清除引用
            self.ws_reconnect_task = None

        if self.signal_generator:
            await self.signal_generator.stop_by_signal()

        if self.event_queue:
            await self.event_queue.stop()
            self.event_queue = None

        if self.order_tracker:
            self.order_tracker.clear()

        if self.processed_fills:
            self.processed_fills.clear()

        # 🛡️ 安全改進：如果還有 market_info，使用安全取消方式
        if self.market_info:
            await self._handle_cancel_all_signal(self.market_info.symbol)
        else:
            # 後備方案：取消所有訂單（這種情況應該很少見）
            logger.warning("缺少 market_info，使用後備方案取消所有訂單")
            await self.client.cancel_all_orders()

        # 🔄 新增：自動平倉邏輯 - 在取消訂單後檢查並平倉
        if self.market_info:
            try:
                logger.info(f"檢查 {self.market_info.symbol} 的持倉狀態...")
                positions = await self.client.get_positions()

                if positions.get('success') and positions.get('data'):
                    for position in positions.get('data', {}).get('rows', []):
                        if position.get('symbol') == self.market_info.symbol:
                            position_qty = float(position.get('position_qty', 0))
                            if position_qty != 0:
                                logger.info(f"檢測到持倉 {position_qty}，開始自動平倉...")
                                close_result = await self.client.close_position(self.market_info.symbol)

                                if close_result.get('success'):
                                    logger.info(f"持倉已成功平倉: {position_qty}")
                                else:
                                    logger.warning(f"平倉失敗: {close_result.get('message', '未知錯誤')}")
                                break
                    else:
                        logger.info(f"{self.market_info.symbol} 無持倉，無需平倉")
                else:
                    logger.warning("無法獲取持倉信息")

            except Exception as e:
                logger.error(f"檢查或平倉時發生錯誤: {e}")
                # 平倉失敗不影響停止流程的其他部分

        if self.wss_client:
            await self._safe_close_ws()
        
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
            "event_queue_size": self.event_queue.get_queue_size() if self.event_queue else 0,

            # WebSocket 狀態
            "websocket": {
                "connected": self.wss_client is not None,
                "should_reconnect": self.ws_should_reconnect,
                "reconnect_attempts": self.ws_reconnect_attempts,
                "reconnecting": self.ws_reconnect_task is not None and not self.ws_reconnect_task.done()
            }
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

    async def get_tracked_orders_summary(self) -> Dict[str, Any]:
        """
        獲取當前追蹤的訂單摘要
        用於調試和驗證訂單追蹤的完整性
        """
        async with self._orders_lock:
            return {
                "active_orders_count": len(self.active_orders),
                "grid_orders_count": len(self.grid_orders),
                "active_order_ids": list(self.active_orders.keys()),
                "grid_order_prices": list(self.grid_orders.keys()),
                "market_orders": [
                    oid for oid, info in self.active_orders.items()
                    if info.get('order_type') == 'MARKET'
                ],
                "limit_orders": [
                    oid for oid, info in self.active_orders.items()
                    if info.get('order_type') == 'LIMIT'
                ],
                "pending_orders": [
                    price for price, oid in self.grid_orders.items()
                    if oid == "PENDING"
                ]
            }

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
