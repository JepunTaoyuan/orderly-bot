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
from typing import Dict, Any, Optional
from datetime import datetime
from .grid_signal import GridSignalGenerator, TradingSignal, Direction, OrderSide
from .client import OrderlyClient
from .profit_tracker import ProfitTracker  # ⭐ 新增利潤追蹤
from src.utils.event_queue import SessionEventQueue, Event, EventType
from src.utils.market_validator import MarketValidator, ValidationError
from src.utils.order_tracker import OrderTracker, OrderStatus
from src.utils.logging_config import get_logger, metrics, set_session_context
from src.models.grid_summary import GridSummary, StopReason
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

        # ⭐ 新增：網格總結服務
        self.grid_summary_service = None

        # 記錄開始時間用於總結
        self.start_time: datetime = None

        # 訂單恢復配置
        from src.config.order_restoration_config import OrderRestorationConfig
        self.restoration_config = OrderRestorationConfig()

        # 恢復頻率追蹤
        self.restoration_attempts = {}  # 時間 -> 恢復次數
        self.last_restoration_cleanup = time.time()

        # WebSocket 事件去重
        self.processed_fills = {}
        self.processed_fills_max_size = self.PROCESSED_FILLS_MAX_SIZE
        self.processed_fills_ttl = self.PROCESSED_FILLS_TTL

        # ⭐ 新增：訂單統計追蹤
        self.order_statistics = {
            "signals_received": 0,
            "signals_processed": 0,
            "orders_attempted": 0,
            "orders_created": 0,
            "orders_failed": 0,
            "duplicate_prevented": 0,
            "validation_failed": 0,
            "api_failed": 0,
            "last_signal_time": None,
            "last_order_time": None,
            "failure_reasons": {}
        }

        # ⭐ 新增：並發處情況追蹤
        self.concurrency_stats = {
            "concurrent_signals": 0,
            "max_concurrent_signals": 0,
            "concurrent_orders": 0,
            "max_concurrent_orders": 0,
            "lock_contentions": 0,
            "lock_wait_time": 0,
            "concurrent_events": 0,
            "processing_collisions": 0,
            "signal_queue_overflows": 0
        }

        # 追蹤當前正在處理的信號和訂單
        self._processing_signals = set()
        self._processing_orders = set()
        self._lock_acquisition_times = {}

        # ⭐ 新增：精確的訂單去重追蹤
        self._order_dedup_tracker = {
            "price_to_order": {},        # 價格到訂單ID的映射
            "order_timestamps": {},      # 訂單創建時間戳
            "pending_orders": {},        # 處理中訂單的詳細信息
            "expired_orders": set(),     # 已過期訂單ID集合
            "order_age_limit": 300,      # 訂單追蹤時間限制（秒）
            "price_tolerance": 1e-8      # 價格匹配容差
        }

    @staticmethod
    def _track_concurrency(operation_type: str):
        """並發處理追蹤裝飾器"""
        def decorator(func):
            async def wrapper(*args, **kwargs):
                # 獲取self實例
                if args:
                    instance = args[0]
                else:
                    raise ValueError("Missing self argument in decorated method")

                operation_id = f"{operation_type}_{time.time()}_{id(args)}"
                start_time = time.time()

                if operation_type == "signal":
                    instance._processing_signals.add(operation_id)
                    current_concurrent = len(instance._processing_signals)
                    instance.concurrency_stats["concurrent_signals"] = current_concurrent
                    if current_concurrent > instance.concurrency_stats["max_concurrent_signals"]:
                        instance.concurrency_stats["max_concurrent_signals"] = current_concurrent

                    # 檢測信號處理碰撞
                    if current_concurrent > 1:
                        instance.concurrency_stats["processing_collisions"] += 1
                        logger.warning(f"檢測到並發信號處理: {current_concurrent} 個信號同時處理",
                                     event_type="concurrent_signals_detected", data={
                                         "concurrent_count": current_concurrent,
                                         "operation_id": operation_id
                                     })

                elif operation_type == "order":
                    instance._processing_orders.add(operation_id)
                    current_concurrent = len(instance._processing_orders)
                    instance.concurrency_stats["concurrent_orders"] = current_concurrent
                    if current_concurrent > instance.concurrency_stats["max_concurrent_orders"]:
                        instance.concurrency_stats["max_concurrent_orders"] = current_concurrent

                try:
                    result = await func(*args, **kwargs)
                    return result

                finally:
                    # 清理處理記錄
                    if operation_type == "signal" and operation_id in instance._processing_signals:
                        instance._processing_signals.remove(operation_id)
                    elif operation_type == "order" and operation_id in instance._processing_orders:
                        instance._processing_orders.remove(operation_id)

                    processing_time = time.time() - start_time

                    # 記錄並發處理統計
                    if operation_type == "signal":
                        logger.debug(f"信號處理完成: {operation_id}, 處理時間: {processing_time:.3f}s",
                                   event_type="signal_processing_completed", data={
                                       "operation_id": operation_id,
                                       "processing_time": processing_time,
                                       "concurrent_signals": len(instance._processing_signals)
                                   })
                    elif operation_type == "order":
                        logger.debug(f"訂單處理完成: {operation_id}, 處理時間: {processing_time:.3f}s",
                                   event_type="order_processing_completed", data={
                                       "operation_id": operation_id,
                                       "processing_time": processing_time,
                                       "concurrent_orders": len(instance._processing_orders)
                                   })

            return wrapper
        return decorator

    async def _track_lock_contention(self, lock_name: str):
        """追蹤鎖競爭情況"""
        start_time = time.time()
        try:
            # 這裡我們模擬鎖獲取，實際的鎖操作在具體方法中
            self._lock_acquisition_times[lock_name] = start_time
        except Exception as e:
            wait_time = time.time() - start_time
            self.concurrency_stats["lock_contentions"] += 1
            self.concurrency_stats["lock_wait_time"] += wait_time

            logger.warning(f"檢測到鎖競爭: {lock_name}, 等待時間: {wait_time:.3f}s",
                         event_type="lock_contention", data={
                             "lock_name": lock_name,
                             "wait_time": wait_time,
                             "total_contentions": self.concurrency_stats["lock_contentions"]
                         })

    def get_concurrency_statistics(self) -> Dict[str, Any]:
        """獲取並發處理統計"""
        stats = self.concurrency_stats.copy()

        # 計算平均鎖等待時間
        if stats["lock_contentions"] > 0:
            stats["avg_lock_wait_time"] = stats["lock_wait_time"] / stats["lock_contentions"]
        else:
            stats["avg_lock_wait_time"] = 0

        # 計算當前並發狀態
        stats["current_concurrent_signals"] = len(self._processing_signals)
        stats["current_concurrent_orders"] = len(self._processing_orders)

        # 添加活躍處理列表
        stats["active_signal_operations"] = list(self._processing_signals)
        stats["active_order_operations"] = list(self._processing_orders)

        return stats

    def _is_duplicate_order(self, price: float, side: str) -> tuple[bool, str]:
        """
        ⭐ 新增：精確的重複訂單檢查邏輯

        Args:
            price: 訂單價格
            side: 訂單方向

        Returns:
            (is_duplicate, reason): 是否重複及原因
        """
        current_time = time.time()
        price_key = f"{price}_{side}"  # 使用價格+方向作為唯一鍵

        # 清理過期的訂單記錄
        self._cleanup_expired_orders(current_time)

        # 檢查是否有相同價格的處理中訂單
        if price_key in self._order_dedup_tracker["pending_orders"]:
            pending_info = self._order_dedup_tracker["pending_orders"][price_key]
            age = current_time - pending_info["timestamp"]

            # 如果處理中訂單超過5秒，認為可能失敗了，允許重試
            if age > 5:
                logger.info(f"處理中訂單已超時，允許重試: {price_key}, 年齡: {age:.1f}s",
                           event_type="pending_order_expired", data={
                               "price_key": price_key,
                               "age": age
                           })
                # 清理過期的處理中記錄
                del self._order_dedup_tracker["pending_orders"][price_key]
                return False, "pending_order_expired"
            else:
                return True, f"order_pending_processing_{age:.1f}s"

        # 檢查是否有相同價格的現有訂單
        if price_key in self._order_dedup_tracker["price_to_order"]:
            order_id = self._order_dedup_tracker["price_to_order"][price_key]

            # 檢查訂單是否還在活躍狀態
            if order_id in self._order_dedup_tracker["order_timestamps"]:
                order_age = current_time - self._order_dedup_tracker["order_timestamps"][order_id]

                # 如果訂單年齡小於追蹤期限，檢查是否在活躍訂單中
                if order_age < self._order_dedup_tracker["order_age_limit"]:
                    if order_id in self.active_orders:
                        return True, f"active_order_exists_{order_id}"
                    else:
                        # 訂單不在活躍列表中，可能已成交或取消，清理記錄
                        logger.debug(f"清理不活躍訂單記錄: {order_id}")
                        del self._order_dedup_tracker["price_to_order"][price_key]
                        del self._order_dedup_tracker["order_timestamps"][order_id]
                else:
                    # 訂單過期，清理記錄
                    logger.debug(f"清理過期訂單記錄: {order_id}, 年齡: {order_age:.1f}s")
                    del self._order_dedup_tracker["price_to_order"][price_key]
                    del self._order_dedup_tracker["order_timestamps"][order_id]
                    self._order_dedup_tracker["expired_orders"].add(order_id)

        # 檢查價格相近的訂單（防止浮點數精度問題）
        tolerance = self._order_dedup_tracker["price_tolerance"]
        for existing_price_key, existing_order_id in self._order_dedup_tracker["price_to_order"].items():
            try:
                existing_price_str = existing_price_key.split("_")[0]
                existing_price = float(existing_price_str)
                existing_side = existing_price_key.split("_")[1]

                if existing_side == side and abs(existing_price - price) <= tolerance:
                    # 找到價格相近的訂單
                    if existing_order_id in self.active_orders:
                        return True, f"similar_price_order_exists_{existing_order_id}_{existing_price}"
            except (ValueError, IndexError):
                continue

        return False, "no_duplicate"

    def _cleanup_expired_orders(self, current_time: float):
        """清理過期的訂單記錄"""
        age_limit = self._order_dedup_tracker["order_age_limit"]

        # 清理過期的處理中訂單
        expired_pending = []
        for price_key, info in self._order_dedup_tracker["pending_orders"].items():
            if current_time - info["timestamp"] > 10:  # 處理中訂單10秒超時
                expired_pending.append(price_key)

        for price_key in expired_pending:
            del self._order_dedup_tracker["pending_orders"][price_key]
            logger.debug(f"清理過期處理訂單: {price_key}")

        # 清理過期的訂單時間戳
        expired_timestamps = []
        for order_id, timestamp in self._order_dedup_tracker["order_timestamps"].items():
            if current_time - timestamp > age_limit:
                expired_timestamps.append(order_id)

        for order_id in expired_timestamps:
            del self._order_dedup_tracker["order_timestamps"][order_id]
            # 同時清理價格映射
            for price_key, oid in list(self._order_dedup_tracker["price_to_order"].items()):
                if oid == order_id:
                    del self._order_dedup_tracker["price_to_order"][price_key]
                    break

            self._order_dedup_tracker["expired_orders"].add(order_id)

    def _register_order_creation(self, price: float, side: str, order_id: int):
        """註冊新創建的訂單"""
        current_time = time.time()
        price_key = f"{price}_{side}"

        # 註冊價格到訂單的映射
        self._order_dedup_tracker["price_to_order"][price_key] = order_id

        # 註冊訂單時間戳
        self._order_dedup_tracker["order_timestamps"][order_id] = current_time

        # 清理處理中記錄
        if price_key in self._order_dedup_tracker["pending_orders"]:
            del self._order_dedup_tracker["pending_orders"][price_key]

        logger.debug(f"註冊訂單創建: {price_key} -> {order_id}")

    def _register_pending_order(self, price: float, side: str):
        """註冊處理中訂單"""
        current_time = time.time()
        price_key = f"{price}_{side}"

        self._order_dedup_tracker["pending_orders"][price_key] = {
            "timestamp": current_time,
            "price": price,
            "side": side
        }

    def _remove_pending_order(self, price: float, side: str):
        """移除處理中訂單"""
        price_key = f"{price}_{side}"
        if price_key in self._order_dedup_tracker["pending_orders"]:
            del self._order_dedup_tracker["pending_orders"][price_key]

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

                # 更新連接狀態（線程安全）
                if self.session_id and self.main_loop:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._update_ws_state(WSConnectionState.DISCONNECTED),
                            self.main_loop
                        )
                    except Exception as e:
                        logger.error(f"更新 WebSocket 狀態失敗: {e}")

                # 如果機器人還在運行且應該重連，則觸發重連
                if self.is_running and self.ws_should_reconnect:
                    logger.info("檢測到 WebSocket 意外關閉，準備重連")
                    # 使用線程安全的方式調度重連任務
                    if (self.ws_reconnect_task is None or self.ws_reconnect_task.done()) and self.main_loop:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                self._handle_ws_reconnect(),
                                self.main_loop
                            )
                        except Exception as e:
                            logger.error(f"觸發 WebSocket 重連失敗: {e}")

            def on_error(_, error):
                """WebSocket 錯誤處理"""
                logger.error(f"WebSocket 錯誤: {error}", event_type="websocket_error")
                if "authentication" in str(error).lower() or "auth" in str(error).lower():
                    logger.critical("WebSocket 認證失敗，停止交易")
                    if self.main_loop:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                self.stop_grid_trading(),
                                self.main_loop
                            )
                        except Exception as e:
                            logger.error(f"停止交易失敗: {e}")
                    return

                # 更新連接狀態為失敗（線程安全）
                if self.session_id and self.main_loop:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._update_ws_state(WSConnectionState.FAILED),
                            self.main_loop
                        )
                    except Exception as e:
                        logger.error(f"更新 WebSocket 狀態失敗: {e}")

                # 其他錯誤觸發重連
                if self.is_running and self.ws_should_reconnect:
                    logger.info("WebSocket 錯誤，準備重連")
                    if (self.ws_reconnect_task is None or self.ws_reconnect_task.done()) and self.main_loop:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                self._handle_ws_reconnect(),
                                self.main_loop
                            )
                        except Exception as e:
                            logger.error(f"觸發 WebSocket 重連失敗: {e}")

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
                                    "symbol": symbol,
                                    "fill_id": fill_id
                                }
                                if self.event_queue and self.main_loop:
                                    event = Event(EventType.ORDER_FILLED, fill_data)
                                    # 線程安全地調度到主事件循環
                                    asyncio.run_coroutine_threadsafe(
                                        self.event_queue.add_event(event),
                                        self.main_loop
                                    )

                        elif msg_type == "ORDER_CANCELLATION":
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
                            symbol = content_json.get("symbol") or ""
                            side = content_json.get("side")
                            cancel_reason = content_json.get("cancelReason", "UNKNOWN")
                            cancel_timestamp = content_json.get("cancelTimestamp", 0)

                            if order_id is None:
                                logger.warning(f"ORDER_CANCELLATION 通知缺少 orderId，原始資料: {data}")
                                return

                            logger.info("訂單取消", event_type="order_cancellation", data={
                                "order_id": order_id,
                                "symbol": symbol,
                                "side": side,
                                "cancel_reason": cancel_reason,
                                "timestamp": cancel_timestamp
                            })

                            metrics.increment_counter("orders.cancelled", tags={"reason": cancel_reason})

                            if self.event_queue:
                                cancel_data = {
                                    "order_id": order_id,
                                    "symbol": symbol,
                                    "side": side,
                                    "cancel_reason": cancel_reason,
                                    "timestamp": cancel_timestamp
                                }
                                if self.event_queue and self.main_loop:
                                    event = Event(EventType.ORDER_CANCELLATION, cancel_data)
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
                await self._setup_websocket(
                    account_id=self.ws_credentials['account_id'],
                    orderly_key=self.ws_credentials['orderly_key'],
                    orderly_secret=self.ws_credentials['orderly_secret'],
                    orderly_testnet=self.ws_credentials['orderly_testnet']
                )

                if not self.wss_client:
                    raise Exception("WebSocket 客戶端創建失敗")

                # 啟動連線並訂閱通知（作為背景任務）
                if hasattr(self.wss_client, "run"):
                    # 以背景任務方式運行 WebSocket，避免阻塞
                    asyncio.create_task(self.wss_client.run())
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
            if self.market_info and symbol and symbol != self.market_info.symbol:
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

    async def _handle_order_cancellation_event(self, cancel_data: Dict[str, Any]):
        """處理 WebSocket 訂單取消事件"""
        try:
            order_id = cancel_data.get('order_id')
            symbol = cancel_data.get('symbol', '')
            side = cancel_data.get('side')
            cancel_reason = cancel_data.get('cancel_reason', 'UNKNOWN')
            timestamp = cancel_data.get('timestamp', 0)

            if not order_id:
                logger.warning(f"取消事件缺少必要字段: {cancel_data}")
                return

            # 🛡️ 安全檢查：確保只處理網格交易的取消
            if self.market_info and symbol != self.market_info.symbol:
                logger.debug(f"忽略非網格交易對的取消: {symbol} (網格: {self.market_info.symbol})")
                return

            # 🛡️ 安全檢查：確保是我們的訂單
            if order_id not in self.active_orders:
                logger.debug(f"收到非網格訂單的取消通知: {order_id}, symbol: {symbol}")
                return

            cancel_type = self.restoration_config.get_cancellation_type(cancel_reason)

            logger.info("檢測到網格訂單取消", event_type="order_cancellation_detected", data={
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "cancel_reason": cancel_reason,
                "cancel_type": cancel_type.value,
                "timestamp": timestamp
            })

            metrics.increment_counter("orders.cancelled", tags={
                "reason": cancel_reason,
                "type": cancel_type.value
            })

            # 更新訂單狀態為已取消
            async with self._orders_lock:
                if order_id in self.active_orders:
                    self.active_orders[order_id]["status"] = OrderStatus.CANCELLED
                    logger.info(f"訂單 {order_id} 狀態已更新為 CANCELLED")

            # 記錄取消事件
            if "cancellation_history" not in self.order_statistics:
                self.order_statistics["cancellation_history"] = []

            self.order_statistics["cancellation_history"].append({
                "timestamp": time.time(),
                "order_id": order_id,
                "cancel_reason": cancel_reason,
                "cancel_type": cancel_type.value,
                "will_attempt_restoration": self.restoration_config.should_restore_order(cancel_reason)
            })

            # 限制歷史記錄數量
            if len(self.order_statistics["cancellation_history"]) > 100:
                self.order_statistics["cancellation_history"] = self.order_statistics["cancellation_history"][-50:]

            # 檢查是否需要恢復訂單
            await self._check_and_restore_cancelled_order(order_id, cancel_reason, timestamp)

        except Exception as e:
            logger.error(f"處理取消事件失敗: {e}, 數據: {cancel_data}")

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
    

    
    @_track_concurrency("order")
    async def _create_grid_order(self, price: float, side: str, quantity: Optional[float] = None):
        """創建網格訂單"""
        start_time = time.time()
        try:
            # ⭐ 新增：統計訂單嘗試
            self.order_statistics["orders_attempted"] += 1

            # ⭐ 新增：追蹤鎖競爭
            lock_start = time.time()
            try:
                await self._track_lock_contention("orders_lock")
            except:
                pass  # 忽略追蹤錯誤

            async with self._orders_lock:
                lock_acquired_time = time.time() - lock_start
                if lock_acquired_time > 0.01:  # 如果鎖等待超過10ms
                    logger.debug(f"訂單鎖獲取耗時: {lock_acquired_time:.3f}s",
                               event_type="lock_acquisition_time", data={
                                   "lock_name": "orders_lock",
                                   "wait_time": lock_acquired_time
                               })

                # ⭐ 新增：使用精確的去重檢查
                is_duplicate, duplicate_reason = self._is_duplicate_order(price, side)

                if is_duplicate:
                    # 統計重複訂單預防
                    self.order_statistics["duplicate_prevented"] += 1
                    self._record_failure_reason("duplicate_order", duplicate_reason)

                    # 檢查是否是舊的grid_orders記錄需要清理
                    if price in self.grid_orders:
                        old_order_id = self.grid_orders[price]
                        if old_order_id == "PENDING" or old_order_id not in self.active_orders:
                            # 清理無效的舊記錄
                            del self.grid_orders[price]
                            logger.debug(f"清理無效的grid_orders記錄: {price} -> {old_order_id}")
                        else:
                            # 有有效的現有訂單，跳過
                            logger.warning(f"檢測到重複訂單: {duplicate_reason}",
                                         event_type="duplicate_order_detected", data={
                                             "price": price,
                                             "side": side,
                                             "reason": duplicate_reason,
                                             "existing_order_id": old_order_id,
                                             "duplicates_prevented": self.order_statistics["duplicate_prevented"]
                                         })
                            return
                    else:
                        # 在去重追蹤器中找到重複但不在grid_orders中，跳過
                        logger.warning(f"檢測到重複訂單: {duplicate_reason}",
                                     event_type="duplicate_order_detected", data={
                                         "price": price,
                                         "side": side,
                                         "reason": duplicate_reason,
                                         "duplicates_prevented": self.order_statistics["duplicate_prevented"]
                                     })
                        return

                # ⭐ 新增：註冊處理中訂單
                self._register_pending_order(price, side)
                self.grid_orders[price] = "PENDING"
            
            # ⭐ 使用固定數量或指定數量
            if quantity is None:
                quantity = float(self.signal_generator.quantity_per_grid)
            
            # 驗證並標準化訂單
            if self.market_info:
                try:
                    norm_price, norm_quantity = self.validator.validate_order(
                        self.market_info.symbol, Decimal(str(price)), Decimal(str(quantity))
                    )
                    price, quantity = float(norm_price), float(norm_quantity)
                except ValidationError as e:
                    # ⭐ 新增：統計驗證失敗
                    self.order_statistics["validation_failed"] += 1
                    reason = f"訂單驗證失敗: {e}"
                    self._record_failure_reason("validation_error", reason)

                    logger.error(f"訂單驗證失敗: {e}",
                               event_type="order_validation_failed", data={
                                   "price": price,
                                   "quantity": quantity,
                                   "error": str(e),
                                   "validation_failures": self.order_statistics["validation_failed"]
                               })
                    async with self._orders_lock:
                        self.grid_orders.pop(price, None)
                    return
            
            # 創建限價訂單
            symbol = self.market_info.symbol
            api_start_time = time.time()
            response = await self.client.create_limit_order(
                symbol=symbol,
                side=side,
                price=price,
                quantity=quantity
            )
            api_response_time = time.time() - api_start_time

            async with self._orders_lock:
                if response.get('success', True):
                    order_id = response.get('data', {}).get('order_id')
                    if order_id:
                        # ⭐ 新增：統計成功創建訂單
                        self.order_statistics["orders_created"] += 1
                        self.order_statistics["last_order_time"] = time.time()

                        self.active_orders[order_id] = {
                            "price": price,
                            "side": side,
                            "quantity": quantity,
                            "order_type": "LIMIT"  # 標記為限價單
                        }
                        self.grid_orders[price] = order_id

                        # ⭐ 新增：註冊訂單到去重追蹤器
                        self._register_order_creation(price, side, order_id)

                        self.order_tracker.add_order(
                            order_id=order_id,
                            symbol=symbol,
                            side=side,
                            order_type="LIMIT",
                            price=Decimal(str(price)),
                            quantity=Decimal(str(quantity))
                        )

                        total_processing_time = time.time() - start_time
                        success_rate = (self.order_statistics["orders_created"] /
                                      max(self.order_statistics["orders_attempted"], 1)) * 100

                        logger.info(f"網格訂單創建成功: ID={order_id}, 價格={price}, 方向={side}",
                                   event_type="order_created", data={
                                       "order_id": order_id,
                                       "price": price,
                                       "side": side,
                                       "quantity": quantity,
                                       "api_response_time": api_response_time,
                                       "total_processing_time": total_processing_time,
                                       "orders_created": self.order_statistics["orders_created"],
                                       "orders_attempted": self.order_statistics["orders_attempted"],
                                       "success_rate": f"{success_rate:.1f}%"
                                   })
                    else:
                        # ⭐ 新增：統計API失敗
                        self.order_statistics["api_failed"] += 1
                        reason = f"API 響應中缺少 order_id: {response}"
                        self._record_failure_reason("missing_order_id", reason)

                        logger.error(f"API 響應中缺少 order_id: {response}",
                                   event_type="api_response_missing_order_id", data={
                                       "price": price,
                                       "side": side,
                                       "response": response,
                                       "api_failures": self.order_statistics["api_failed"]
                                   })
                        self.grid_orders.pop(price, None)
                else:
                    # ⭐ 新增：統計API失敗
                    self.order_statistics["api_failed"] += 1
                    reason = f"創建訂單失敗: {response}"
                    self._record_failure_reason("api_rejection", reason)

                    logger.error(f"創建訂單失敗: {response}",
                               event_type="order_creation_failed", data={
                                   "price": price,
                                   "side": side,
                                   "response": response,
                                   "api_failures": self.order_statistics["api_failed"],
                                   "api_response_time": api_response_time
                               })
                    self.grid_orders.pop(price, None)
            
        except Exception as e:
            # ⭐ 新增：統計異常失敗
            self.order_statistics["orders_failed"] += 1
            reason = f"創建網格訂單異常: {e}"
            self._record_failure_reason("exception", reason)

            logger.error(f"創建網格訂單失敗: {e}",
                       event_type="order_creation_exception", data={
                           "price": price,
                           "side": side,
                           "error": str(e),
                           "exceptions": self.order_statistics["orders_failed"],
                           "processing_time": time.time() - start_time
                       })
            async with self._orders_lock:
                self.grid_orders.pop(price, None)
                # ⭐ 新增：清理處理中訂單記錄
                self._remove_pending_order(price, side)

    def _record_failure_reason(self, reason_type: str, reason: str):
        """記錄失敗原因用於分析"""
        if reason_type not in self.order_statistics["failure_reasons"]:
            self.order_statistics["failure_reasons"][reason_type] = {
                "count": 0,
                "last_reason": "",
                "last_time": None
            }

        self.order_statistics["failure_reasons"][reason_type]["count"] += 1
        self.order_statistics["failure_reasons"][reason_type]["last_reason"] = reason
        self.order_statistics["failure_reasons"][reason_type]["last_time"] = time.time()

    def get_order_statistics(self) -> Dict[str, Any]:
        """獲取訂單統計信息"""
        stats = self.order_statistics.copy()

        # 計算成功率
        if stats["orders_attempted"] > 0:
            stats["success_rate"] = (stats["orders_created"] / stats["orders_attempted"]) * 100
            stats["failure_rate"] = ((stats["orders_failed"] + stats["api_failed"] +
                                   stats["validation_failed"] + stats["duplicate_prevented"]) /
                                   stats["orders_attempted"]) * 100
        else:
            stats["success_rate"] = 0
            stats["failure_rate"] = 0

        # 計算信號處理率
        if stats["signals_received"] > 0:
            stats["signal_processing_rate"] = (stats["signals_processed"] / stats["signals_received"]) * 100
        else:
            stats["signal_processing_rate"] = 0

        # 計算運行時間
        if stats["last_signal_time"] and stats["last_order_time"]:
            stats["last_signal_to_order_delay"] = stats["last_order_time"] - stats["last_signal_time"]
        else:
            stats["last_signal_to_order_delay"] = None

        return stats

    async def _event_handler(self, event: Event):
        """統一事件處理器"""
        try:
            if event.event_type == EventType.SIGNAL:
                await self._handle_signal_event(event.data)
            elif event.event_type == EventType.ORDER_FILLED:
                await self._handle_order_filled_event(event.data)
            elif event.event_type == EventType.ORDER_CANCELLATION:
                await self._handle_order_cancellation_event(event.data)
        except Exception as e:
            logger.error(f"事件處理失敗: {e}")
    
    async def signal_handler(self, signal: TradingSignal):
        """處理交易訊號的回調函數"""
        if self.event_queue:
            event = Event(EventType.SIGNAL, signal)
            await self.event_queue.add_event(event)
        else:
            await self._handle_signal_event(signal)
    
    @_track_concurrency("signal")
    async def _handle_signal_event(self, signal: TradingSignal):
        """實際處理交易訊號"""
        try:
            # ⭐ 新增：統計信號接收
            self.order_statistics["signals_received"] += 1
            self.order_statistics["last_signal_time"] = time.time()

            logger.info(f"處理訊號: {signal.symbol} {signal.side.value} @ {signal.price} 數量:{signal.size}",
                       event_type="signal_received", data={
                           "signal_type": signal.signal_type,
                           "side": signal.side.value,
                           "price": float(signal.price),
                           "size": float(signal.size),
                           "signals_total": self.order_statistics["signals_received"]
                       })

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

            # ⭐ 新增：統計信號成功處理
            self.order_statistics["signals_processed"] += 1

        except Exception as e:
            logger.error(f"處理訊號失敗: {e}", event_type="signal_processing_error", data={
                "signal_type": getattr(signal, 'signal_type', 'unknown'),
                "error": str(e),
                "signals_processed": self.order_statistics.get("signals_processed", 0)
            })
    
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

            # ⭐ 新增：記錄開始時間
            self.start_time = datetime.utcnow()
            print("test0")

            # ⭐ 新增：初始化網格總結服務
            from src.services.database_connection import db_manager
            from src.services.grid_summary_service import GridSummaryService
            database = await db_manager.get_database()
            print("test1")
            self.grid_summary_service = GridSummaryService(database)
            print("test2")

            # 確保索引存在
            await self.grid_summary_service.ensure_indexes()
            print("test3")
            logger.info("網格總結服務已初始化")
            
            # 創建並啟動事件隊列
            self.event_queue = SessionEventQueue(
                session_id=f"{config['user_id']}_{config['ticker']}",
                event_handler=self._event_handler
            )
            logger.info("事件隊列已初始化")
            await self.event_queue.start()

            # 設置 WebSocket 連接
            await self._setup_websocket(
                account_id=config['orderly_account_id'],
                orderly_key=config['orderly_key'],
                orderly_secret=config['orderly_secret'],
                orderly_testnet=config['orderly_testnet']
            )

            # 驗證 WebSocket 客戶端是否成功創建
            if not self.wss_client:
                error_msg = "WebSocket 客戶端初始化失敗，無法啟動網格交易"
                logger.error(error_msg, event_type="websocket_init_failed")
                raise Exception(error_msg)

            logger.info("WebSocket 客戶端已初始化")

            # 啟用 WebSocket 重連
            self.ws_should_reconnect = True
            self.ws_reconnect_attempts = 0

            # 啟動 WebSocket 連線並監聽（作為背景任務）
            try:
                if hasattr(self.wss_client, "run"):
                    # 以背景任務方式運行 WebSocket，避免阻塞主流程
                    asyncio.create_task(self.wss_client.run())
                    logger.info("WebSocket 背景任務已啟動")
                else:
                    logger.warning("WebSocket 客戶端缺少 run()，可能無法啟動連線")
                self.wss_client.get_notifications()
                logger.info("WebSocket 訂閱 notifications 成功")
            except Exception as e:
                logger.error(f"WebSocket 啟動或訂閱 notifications 失敗: {e}")
                # 注意：這裡不直接拋出異常，允許機器人繼續運行（稍後會重連）

            # 啟動定期訂單同步任務
            self.order_sync_task = asyncio.create_task(self._periodic_order_sync())
            logger.info("定期訂單同步任務已啟動")
            
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
    
    async def stop_grid_trading(self, stop_reason: StopReason = StopReason.MANUAL):
        """停止網格交易"""
        logger.info("停止網格交易機器人", data={"stop_reason": stop_reason.value})

        # 收集所有清理過程中的錯誤
        cleanup_errors = []

        # 禁用 WebSocket 重連
        self.ws_should_reconnect = False

        # 🛠️ 安全地處理 WebSocket 重連任務
        if self.ws_reconnect_task:
            if not self.ws_reconnect_task.done():
                logger.info("正在停止 WebSocket 重連任務...")
                try:
                    # 短超時取消任務
                    self.ws_reconnect_task.cancel()
                    await asyncio.wait_for(self.ws_reconnect_task, timeout=2.0)
                    logger.info("WebSocket 重連任務已停止")
                except asyncio.TimeoutError:
                    cleanup_errors.append("WebSocket 重連任務停止超時")
                    logger.warning("WebSocket 重連任務停止超時，跳過")
                except asyncio.CancelledError:
                    logger.info("WebSocket 重連任務已取消")
                except Exception as e:
                    cleanup_errors.append(f"WebSocket 重連任務停止錯誤: {str(e)}")
                    logger.warning(f"停止 WebSocket 重連任務時發生錯誤: {e}")

            # 清除引用
            self.ws_reconnect_task = None

        # 停止定期訂單同步任務
        if hasattr(self, 'order_sync_task') and self.order_sync_task:
            if not self.order_sync_task.done():
                logger.info("正在停止定期訂單同步任務...")
                try:
                    self.order_sync_task.cancel()
                    await asyncio.wait_for(self.order_sync_task, timeout=2.0)
                    logger.info("定期訂單同步任務已停止")
                except asyncio.TimeoutError:
                    cleanup_errors.append("定期訂單同步任務停止超時")
                    logger.warning("定期訂單同步任務停止超時，跳過")
                except asyncio.CancelledError:
                    logger.info("定期訂單同步任務已取消")
                except Exception as e:
                    cleanup_errors.append(f"定期訂單同步任務停止錯誤: {str(e)}")
                    logger.warning(f"停止定期訂單同步任務時發生錯誤: {e}")
            self.order_sync_task = None

        # 停止信號生成器
        if self.signal_generator:
            try:
                await self.signal_generator.stop_by_signal()
            except Exception as e:
                cleanup_errors.append(f"信號生成器停止錯誤: {str(e)}")
                logger.warning(f"停止信號生成器時發生錯誤: {e}")

        # 停止事件隊列
        if self.event_queue:
            try:
                await self.event_queue.stop()
                self.event_queue = None
            except Exception as e:
                cleanup_errors.append(f"事件隊列停止錯誤: {str(e)}")
                logger.warning(f"停止事件隊列時發生錯誤: {e}")

        # 清理訂單追蹤器
        if self.order_tracker:
            self.order_tracker.clear()

        # 清理已處理的成交記錄
        if self.processed_fills:
            self.processed_fills.clear()

        # 🛡️ 安全改進：取消所有訂單
        try:
            if self.market_info:
                await self._handle_cancel_all_signal(self.market_info.symbol)
            else:
                # 後備方案：取消所有訂單（這種情況應該很少見）
                logger.warning("缺少 market_info，使用後備方案取消所有訂單")
                await self.client.cancel_all_orders()
        except Exception as e:
            cleanup_errors.append(f"取消訂單錯誤: {str(e)}")
            logger.warning(f"取消訂單時發生錯誤: {e}")

        # 🔄 自動平倉邏輯 - 非關鍵操作，失敗不影響停止流程
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
                                    cleanup_errors.append(f"平倉失敗: {close_result.get('message', '未知錯誤')}")
                                    logger.warning(f"平倉失敗: {close_result.get('message', '未知錯誤')}")
                                break
                    else:
                        logger.info(f"{self.market_info.symbol} 無持倉，無需平倉")
                else:
                    cleanup_errors.append("無法獲取持倉信息進行平倉檢查")
                    logger.warning("無法獲取持倉信息")

            except Exception as e:
                cleanup_errors.append(f"檢查或平倉時發生錯誤: {str(e)}")
                logger.warning(f"檢查或平倉時發生錯誤: {e}")

        # 關閉 WebSocket 連接
        if self.wss_client:
            try:
                self._safe_close_ws()
            except Exception as e:
                cleanup_errors.append(f"WebSocket 關閉錯誤: {str(e)}")
                logger.warning(f"關閉 WebSocket 連接時發生錯誤: {e}")

        # ⭐ 保存網格總結數據 - 非關鍵操作
        try:
            await self._save_grid_summary(stop_reason)
        except Exception as e:
            cleanup_errors.append(f"保存網格總結錯誤: {str(e)}")
            logger.warning(f"保存網格總結時發生錯誤: {e}")

        # 設置運行狀態為停止
        self.is_running = False

        # 記錄最終結果
        if cleanup_errors:
            logger.warning(f"網格交易機器人已停止，但有 {len(cleanup_errors)} 個警告: {'; '.join(cleanup_errors)}",
                          event_type="bot_stopped_with_warnings",
                          data={"stop_reason": stop_reason.value, "warnings": cleanup_errors})
        else:
            logger.info("網格交易機器人已成功停止",
                       event_type="bot_stopped",
                       data={"stop_reason": stop_reason.value})
    
    async def get_status(self):
        """獲取機器人狀態（包含利潤統計和訂單統計）"""
        status = {
            "is_running": self.is_running,
            "active_orders_count": len(self.active_orders),
            "active_orders": self.active_orders,
            "grid_orders": self.grid_orders,
            "order_statistics": self.order_tracker.get_statistics(),
            "order_tracking_stats": self.get_order_statistics(),  # ⭐ 新增：詳細訂單統計
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
                current_price = None

                # 首先嘗試從持倉信息獲取價格
                try:
                    positions = await self.client.get_positions()
                    for position in positions.get('data', {}).get('rows', []):
                        if position.get('symbol') == self.profit_tracker.symbol:
                            mark_price = position.get('mark_price')
                            if mark_price and mark_price != 0:
                                current_price = Decimal(str(mark_price))
                                logger.debug(f"從持倉獲取價格: {current_price}")
                                break
                except Exception as e:
                    logger.warning(f"從持倉獲取價格失敗: {e}")

                # 如果沒有持倉，嘗試從訂單簿獲取中間價
                if current_price is None:
                    try:
                        orderbook = await self.client.get_orderbook(self.profit_tracker.symbol)
                        if orderbook and orderbook.get('data'):
                            asks = orderbook['data'].get('asks', [])
                            bids = orderbook['data'].get('bids', [])
                            if asks and bids:
                                best_ask = Decimal(str(asks[0][0])) if asks and len(asks[0]) > 0 else None
                                best_bid = Decimal(str(bids[0][0])) if bids and len(bids[0]) > 0 else None
                                if best_ask and best_bid:
                                    current_price = (best_ask + best_bid) / 2
                                    logger.debug(f"從訂單簿計算中間價: {current_price}")
                    except Exception as e:
                        logger.warning(f"從訂單簿獲取價格失敗: {e}")

                # 獲取利潤統計摘要
                profit_summary = self.profit_tracker.get_summary(current_price)

                # 添加調試信息
                profit_summary["debug_info"] = {
                    "current_price_source": "positions" if current_price else "none",
                    "current_price_value": str(current_price) if current_price else None,
                    "has_positions": len(self.current_positions) > 0 if hasattr(self, 'current_positions') else False
                }

                status["profit_statistics"] = profit_summary

                # 記錄調試日誌
                logger.info(f"利潤統計已生成 - 當前價格: {current_price}, 網格收益: {profit_summary.get('grid_profit')}")

            except Exception as e:
                logger.error(f"獲取利潤統計失敗: {e}", exc_info=True)
                status["profit_statistics"] = {"error": str(e)}

        # ⭐ 新增：包含API速率統計
        try:
            status["api_rate_statistics"] = self.client.get_rate_statistics()
        except Exception as e:
            logger.error(f"獲取API速率統計失敗: {e}")
            status["api_rate_statistics"] = {"error": str(e)}

        # ⭐ 新增：包含並發處理統計
        try:
            status["concurrency_statistics"] = self.get_concurrency_statistics()
        except Exception as e:
            logger.error(f"獲取並發統計失敗: {e}")
            status["concurrency_statistics"] = {"error": str(e)}
        
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
            current_price = None

            # 首先嘗試從持倉信息獲取價格
            try:
                positions = await self.client.get_positions()
                for position in positions.get('data', {}).get('rows', []):
                    if position.get('symbol') == self.profit_tracker.symbol:
                        mark_price = position.get('mark_price')
                        if mark_price and mark_price != 0:
                            current_price = Decimal(str(mark_price))
                            break
            except Exception as e:
                logger.warning(f"從持倉獲取價格失敗: {e}")

            # 如果沒有持倉，嘗試從訂單簿獲取中間價
            if current_price is None:
                try:
                    orderbook = await self.client.get_orderbook(self.profit_tracker.symbol)
                    if orderbook and orderbook.get('data'):
                        asks = orderbook['data'].get('asks', [])
                        bids = orderbook['data'].get('bids', [])
                        if asks and bids:
                            best_ask = Decimal(str(asks[0][0])) if asks and len(asks[0]) > 0 else None
                            best_bid = Decimal(str(bids[0][0])) if bids and len(bids[0]) > 0 else None
                            if best_ask and best_bid:
                                current_price = (best_ask + best_bid) / 2
                except Exception as e:
                    logger.warning(f"從訂單簿獲取價格失敗: {e}")

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

    async def _save_grid_summary(self, stop_reason: StopReason):
        """
        ⭐ 新增：保存網格交易總結數據

        Args:
            stop_reason: 停止原因
        """
        try:
            if not self.start_time or not self.grid_summary_service:
                logger.warning("無法保存網格總結：缺少必要信息")
                return

            # 獲取最終的利潤數據
            if not self.profit_tracker:
                logger.warning("無法保存網格總結：利潤追蹤器未初始化")
                return

            # 獲取當前價格
            current_price = None

            # 首先嘗試從持倉信息獲取價格
            try:
                positions = await self.client.get_positions()
                for position in positions.get('data', {}).get('rows', []):
                    if position.get('symbol') == self.profit_tracker.symbol:
                        mark_price = position.get('mark_price')
                        if mark_price and mark_price != 0:
                            current_price = Decimal(str(mark_price))
                            break
            except Exception as e:
                logger.warning(f"從持倉獲取價格失敗: {e}")

            # 如果沒有持倉，嘗試從訂單簿獲取中間價
            if current_price is None:
                try:
                    orderbook = await self.client.get_orderbook(self.profit_tracker.symbol)
                    if orderbook and orderbook.get('data'):
                        asks = orderbook['data'].get('asks', [])
                        bids = orderbook['data'].get('bids', [])
                        if asks and bids:
                            best_ask = Decimal(str(asks[0][0])) if asks and len(asks[0]) > 0 else None
                            best_bid = Decimal(str(bids[0][0])) if bids and len(bids[0]) > 0 else None
                            if best_ask and best_bid:
                                current_price = (best_ask + best_bid) / 2
                except Exception as e:
                    logger.warning(f"從訂單簿獲取價格失敗: {e}")

            # 獲取利潤摘要
            profit_summary = self.profit_tracker.get_summary(current_price)

            # 構建網格配置快照
            grid_config = {}
            if self.signal_generator:
                grid_config = {
                    "ticker": self.signal_generator.ticker,
                    "direction": self.signal_generator.direction.value if hasattr(self.signal_generator.direction, 'value') else str(self.signal_generator.direction),
                    "grid_type": self.signal_generator.grid_type.value if hasattr(self.signal_generator.grid_type, 'value') else str(self.signal_generator.grid_type),
                    "grid_levels": self.signal_generator.grid_levels,
                    "upper_bound": self.signal_generator.upper_bound,
                    "lower_bound": self.signal_generator.lower_bound,
                    "total_margin": self.signal_generator.total_margin
                }

            # 解析用戶ID
            user_id = None
            if self.session_id:
                try:
                    user_id, _ = self.session_id.split('_', 1)
                except ValueError:
                    logger.warning(f"無法解析用戶ID從session_id: {self.session_id}")
                    return

            # 創建網格總結
            end_time = datetime.utcnow()
            summary = GridSummary.create_from_bot_data(
                session_id=self.session_id,
                user_id=user_id,
                start_time=self.start_time,
                end_time=end_time,
                profit_data={
                    "total_profit": float(profit_summary.get("total_profit", 0)),
                    "grid_profit": float(profit_summary.get("grid_profit", 0)),
                    "unpaired_profit": float(profit_summary.get("unpaired_profit", 0)),
                    "arbitrage_times": profit_summary.get("arbitrage_times", 0)
                },
                grid_config=grid_config,
                stop_reason=stop_reason,
                max_drawdown=profit_summary.get("max_drawdown"),
                capital_utilization=profit_summary.get("capital_utilization")
            )

            # 保存到數據庫
            document_id = await self.grid_summary_service.save_grid_summary(summary)

            logger.info("網格總結已保存", event_type="grid_summary_saved", data={
                "document_id": document_id,
                "session_id": self.session_id,
                "user_id": user_id,
                "total_profit": summary.total_profit,
                "arbitrage_times": summary.arbitrage_times,
                "stop_reason": stop_reason.value
            })

        except Exception as e:
            logger.error("保存網格總結失敗", event_type="grid_summary_save_error", data={
                "session_id": self.session_id,
                "error": str(e)
            })
            # 不拋出異常，避免影響正常的停止流程

    def get_comprehensive_analysis(self) -> Dict[str, Any]:
        """
        ⭐ 新增：獲取綜合分析報告
        包含訂單、API、並發等各方面的統計和分析

        Returns:
            綜合分析報告
        """
        current_time = time.time()
        analysis = {
            "report_timestamp": current_time,
            "session_id": self.session_id,
            "is_running": self.is_running,
            "uptime_seconds": (current_time - time.time()) if hasattr(self, 'start_time') else 0
        }

        try:
            # 1. 訂單統計分析
            order_stats = self.get_order_statistics()
            analysis["order_analysis"] = {
                **order_stats,
                "health_score": self._calculate_order_health_score(order_stats),
                "recommendations": self._get_order_recommendations(order_stats)
            }

            # 2. API速率統計分析
            api_stats = self.client.get_rate_statistics()
            analysis["api_analysis"] = {
                **api_stats,
                "health_score": self._calculate_api_health_score(api_stats),
                "recommendations": self._get_api_recommendations(api_stats)
            }

            # 3. 並發處理分析
            concurrency_stats = self.get_concurrency_statistics()
            analysis["concurrency_analysis"] = {
                **concurrency_stats,
                "health_score": self._calculate_concurrency_health_score(concurrency_stats),
                "recommendations": self._get_concurrency_recommendations(concurrency_stats)
            }

            # 4. 綜合健康評分
            analysis["overall_health_score"] = self._calculate_overall_health_score(analysis)
            analysis["overall_recommendations"] = self._get_overall_recommendations(analysis)

            # 5. 趨勢分析
            analysis["trends"] = self._analyze_trends()

            # 6. 異常檢測
            analysis["anomalies"] = self._detect_anomalies(analysis)

        except Exception as e:
            logger.error(f"生成綜合分析報告失敗: {e}")
            analysis["error"] = str(e)

        return analysis

    def _calculate_order_health_score(self, order_stats: Dict) -> float:
        """計算訂單處理健康評分 (0-100)"""
        try:
            scores = []

            # 成功率評分 (40%)
            if order_stats.get("orders_attempted", 0) > 0:
                success_rate = order_stats.get("success_rate", 0)
                scores.append(min(success_rate, 100) * 0.4)
            else:
                scores.append(50 * 0.4)  # 中性評分

            # 信號處理率評分 (20%)
            signal_rate = order_stats.get("signal_processing_rate", 0)
            scores.append(min(signal_rate, 100) * 0.2)

            # 重複率評分 (20%) - 重複率越低越好
            if order_stats.get("orders_attempted", 0) > 0:
                duplicate_rate = (order_stats.get("duplicate_prevented", 0) /
                                order_stats["orders_attempted"]) * 100
                # 重複率 < 10% 得滿分， > 50% 得0分
                duplicate_score = max(0, (50 - duplicate_rate) * 2)
                scores.append(min(duplicate_score, 100) * 0.2)
            else:
                scores.append(80 * 0.2)

            # 錯誤類型分布評分 (20%) - 錯誤類型分散度
            failure_reasons = order_stats.get("failure_reasons", {})
            if failure_reasons:
                # 錯誤類型越少越好（表示問題集中）
                error_variety_score = max(0, 100 - len(failure_reasons) * 10)
                scores.append(error_variety_score * 0.2)
            else:
                scores.append(100 * 0.2)  # 沒有錯誤

            return sum(scores)

        except Exception:
            return 50  # 默認中性評分

    def _calculate_api_health_score(self, api_stats: Dict) -> float:
        """計算API健康評分 (0-100)"""
        try:
            scores = []

            # 成功率評分 (40%)
            success_rate = api_stats.get("success_rate", 0)
            scores.append(min(success_rate, 100) * 0.4)

            # 速率限制觸發率評分 (30%) - 觸發率越低越好
            rate_limit_rate = api_stats.get("rate_limit_hit_rate", 0)
            rate_limit_score = max(0, 100 - rate_limit_rate * 10)
            scores.append(rate_limit_score * 0.3)

            # 響應時間評分 (20%) - 響應時間越快越好
            avg_response_time = api_stats.get("avg_response_time", 0)
            if avg_response_time == 0:
                response_score = 50
            elif avg_response_time < 0.5:
                response_score = 100
            elif avg_response_time < 1.0:
                response_score = 80
            elif avg_response_time < 2.0:
                response_score = 60
            else:
                response_score = max(0, 40 - avg_response_time * 10)
            scores.append(response_score * 0.2)

            # 慢請求比例評分 (10%) - 慢請求越少越好
            if api_stats.get("total_requests", 0) > 0:
                slow_rate = (api_stats.get("slow_requests", 0) /
                           api_stats["total_requests"]) * 100
                slow_score = max(0, 100 - slow_rate * 5)
                scores.append(slow_score * 0.1)
            else:
                scores.append(80 * 0.1)

            return sum(scores)

        except Exception:
            return 50

    def _calculate_concurrency_health_score(self, concurrency_stats: Dict) -> float:
        """計算並發處理健康評分 (0-100)"""
        try:
            scores = []

            # 鎖競爭評分 (40%) - 競爭越少越好
            lock_contentions = concurrency_stats.get("lock_contentions", 0)
            contention_score = max(0, 100 - lock_contentions * 20)
            scores.append(contention_score * 0.4)

            # 並發信號處理評分 (30%) - 併發度適中為好
            max_concurrent_signals = concurrency_stats.get("max_concurrent_signals", 0)
            if max_concurrent_signals == 0:
                concurrent_score = 50
            elif max_concurrent_signals == 1:
                concurrent_score = 100  # 理想情況
            elif max_concurrent_signals <= 3:
                concurrent_score = 80
            else:
                concurrent_score = max(0, 80 - (max_concurrent_signals - 3) * 10)
            scores.append(concurrent_score * 0.3)

            # 當前併發負載評分 (20%) - 當前併發數
            current_concurrent = (concurrency_stats.get("current_concurrent_signals", 0) +
                                concurrency_stats.get("current_concurrent_orders", 0))
            if current_concurrent == 0:
                load_score = 100
            elif current_concurrent <= 2:
                load_score = 80
            else:
                load_score = max(0, 80 - current_concurrent * 10)
            scores.append(load_score * 0.2)

            # 處理碰撞評分 (10%) - 碰撞越少越好
            collisions = concurrency_stats.get("processing_collisions", 0)
            collision_score = max(0, 100 - collisions * 25)
            scores.append(collision_score * 0.1)

            return sum(scores)

        except Exception:
            return 50

    def _calculate_overall_health_score(self, analysis: Dict) -> float:
        """計算整體健康評分"""
        try:
            order_score = analysis.get("order_analysis", {}).get("health_score", 50)
            api_score = analysis.get("api_analysis", {}).get("health_score", 50)
            concurrency_score = analysis.get("concurrency_analysis", {}).get("health_score", 50)

            # 權重分配：訂單 50%, API 30%, 並發 20%
            overall_score = (order_score * 0.5 + api_score * 0.3 + concurrency_score * 0.2)
            return round(overall_score, 1)

        except Exception:
            return 50

    def _get_order_recommendations(self, order_stats: Dict) -> list:
        """獲取訂單處理建議"""
        recommendations = []

        try:
            success_rate = order_stats.get("success_rate", 0)
            if success_rate < 80:
                recommendations.append({
                    "priority": "high",
                    "type": "success_rate",
                    "message": f"訂單成功率偏低 ({success_rate:.1f}%)，建議檢查網格配置和市場條件"
                })

            duplicate_rate = (order_stats.get("duplicate_prevented", 0) /
                            max(order_stats.get("orders_attempted", 1), 1)) * 100
            if duplicate_rate > 20:
                recommendations.append({
                    "priority": "medium",
                    "type": "duplicate_orders",
                    "message": f"重複訂單比例較高 ({duplicate_rate:.1f}%)，可能存在並發問題或信號頻繁變化"
                })

            validation_failures = order_stats.get("validation_failed", 0)
            if validation_failures > 0:
                recommendations.append({
                    "priority": "medium",
                    "type": "validation",
                    "message": f"有 {validation_failures} 個訂單驗證失敗，建議檢查市場規則和參數設定"
                })

        except Exception as e:
            recommendations.append({
                "priority": "low",
                "type": "analysis_error",
                "message": f"訂單分析時發生錯誤: {e}"
            })

        return recommendations

    def _get_api_recommendations(self, api_stats: Dict) -> list:
        """獲取API使用建議"""
        recommendations = []

        try:
            rate_limit_rate = api_stats.get("rate_limit_hit_rate", 0)
            if rate_limit_rate > 5:
                recommendations.append({
                    "priority": "high",
                    "type": "rate_limit",
                    "message": f"API速率限制觸發率較高 ({rate_limit_rate:.1f}%)，建議降低請求頻率"
                })

            avg_response_time = api_stats.get("avg_response_time", 0)
            if avg_response_time > 2.0:
                recommendations.append({
                    "priority": "medium",
                    "type": "response_time",
                    "message": f"API平均響應時間較慢 ({avg_response_time:.3f}s)，可能影響系統性能"
                })

            success_rate = api_stats.get("success_rate", 0)
            if success_rate < 90:
                recommendations.append({
                    "priority": "high",
                    "type": "api_reliability",
                    "message": f"API成功率偏低 ({success_rate:.1f}%)，建議檢查網絡連接和API憑證"
                })

        except Exception as e:
            recommendations.append({
                "priority": "low",
                "type": "analysis_error",
                "message": f"API分析時發生錯誤: {e}"
            })

        return recommendations

    def _get_concurrency_recommendations(self, concurrency_stats: Dict) -> list:
        """獲取並發處理建議"""
        recommendations = []

        try:
            lock_contentions = concurrency_stats.get("lock_contentions", 0)
            if lock_contentions > 5:
                recommendations.append({
                    "priority": "medium",
                    "type": "lock_contention",
                    "message": f"檢測到 {lock_contentions} 次鎖競爭，可能影響性能，建議優化並發邏輯"
                })

            max_concurrent = concurrency_stats.get("max_concurrent_signals", 0)
            if max_concurrent > 5:
                recommendations.append({
                    "priority": "medium",
                    "type": "high_concurrency",
                    "message": f"最大併發信號數較高 ({max_concurrent})，可能導致資源競爭"
                })

            collisions = concurrency_stats.get("processing_collisions", 0)
            if collisions > 10:
                recommendations.append({
                    "priority": "high",
                    "type": "processing_collision",
                    "message": f"檢測到 {collisions} 次處理碰撞，建議加強信號去重機制"
                })

        except Exception as e:
            recommendations.append({
                "priority": "low",
                "type": "analysis_error",
                "message": f"並發分析時發生錯誤: {e}"
            })

        return recommendations

    def _get_overall_recommendations(self, analysis: Dict) -> list:
        """獲取整體建議"""
        recommendations = []
        overall_score = analysis.get("overall_health_score", 50)

        try:
            if overall_score < 60:
                recommendations.append({
                    "priority": "high",
                    "type": "overall_health",
                    "message": f"系統整體健康評分偏低 ({overall_score})，建議立即檢查和優化"
                })
            elif overall_score < 80:
                recommendations.append({
                    "priority": "medium",
                    "type": "overall_health",
                    "message": f"系統健康評分良好但有改善空間 ({overall_score})"
                })

            # 收集高優先級建議
            all_recommendations = []
            all_recommendations.extend(analysis.get("order_analysis", {}).get("recommendations", []))
            all_recommendations.extend(analysis.get("api_analysis", {}).get("recommendations", []))
            all_recommendations.extend(analysis.get("concurrency_analysis", {}).get("recommendations", []))

            high_priority = [r for r in all_recommendations if r.get("priority") == "high"]
            if high_priority:
                recommendations.append({
                    "priority": "high",
                    "type": "summary",
                    "message": f"發現 {len(high_priority)} 個高優先級問題需要立即處理"
                })

        except Exception as e:
            recommendations.append({
                "priority": "low",
                "type": "analysis_error",
                "message": f"整體分析時發生錯誤: {e}"
            })

        return recommendations

    def _analyze_trends(self) -> Dict[str, Any]:
        """分析趨勢"""
        # 這裡可以實現基於歷史數據的趨勢分析
        # 目前返回基礎信息
        return {
            "note": "趨勢分析功能需要歷史數據支持，當前版本提供基礎統計",
            "potential_improvements": [
                "基於時間序列的成功率趨勢",
                "API響應時間變化趨勢",
                "併發負載變化趨勢"
            ]
        }

    def _detect_anomalies(self, analysis: Dict) -> list:
        """檢測異常情況"""
        anomalies = []

        try:
            order_stats = analysis.get("order_analysis", {})
            api_stats = analysis.get("api_analysis", {})
            concurrency_stats = analysis.get("concurrency_analysis", {})

            # 檢測訂單異常
            if order_stats.get("success_rate", 100) < 50:
                anomalies.append({
                    "type": "order_success_anomaly",
                    "severity": "critical",
                    "description": "訂單成功率異常偏低",
                    "value": order_stats.get("success_rate", 0)
                })

            # 檢測API異常
            if api_stats.get("rate_limit_hit_rate", 0) > 20:
                anomalies.append({
                    "type": "rate_limit_anomaly",
                    "severity": "high",
                    "description": "API速率限制觸發頻率異常",
                    "value": api_stats.get("rate_limit_hit_rate", 0)
                })

            # 檢測並發異常
            if concurrency_stats.get("processing_collisions", 0) > 20:
                anomalies.append({
                    "type": "concurrency_anomaly",
                    "severity": "medium",
                    "description": "並發處理碰撞異常頻繁",
                    "value": concurrency_stats.get("processing_collisions", 0)
                })

        except Exception as e:
            anomalies.append({
                "type": "analysis_error",
                "severity": "low",
                "description": f"異常檢測時發生錯誤: {e}"
            })

        return anomalies

    async def _check_and_restore_cancelled_order(self, order_id: str, cancel_reason: str, timestamp: int):
        """檢查並恢復被取消的訂單"""
        try:
            # 檢查是否是用戶取消且需要恢復
            if not self._should_restore_order(cancel_reason):
                logger.info(f"訂單 {order_id} 取消原因為 {cancel_reason}，無需恢復")
                return

            # 獲取被取消訂單的信息
            cancelled_order = None
            tracker_order = None
            async with self._orders_lock:
                if order_id in self.active_orders:
                    cancelled_order = self.active_orders[order_id]
            try:
                tracker_order = self.order_tracker.get_order(int(order_id))
            except Exception:
                tracker_order = None

            if not cancelled_order and not tracker_order:
                logger.warning(f"無法找到被取消的訂單 {order_id}")
                return

            # 檢查恢復條件
            if await self._can_restore_order(cancelled_order or tracker_order, timestamp):
                logger.info(f"開始恢復被取消的訂單 {order_id}")
                await self._restore_cancelled_order(tracker_order or cancelled_order)
            else:
                logger.info(f"訂單 {order_id} 不滿足恢復條件")

        except Exception as e:
            logger.error(f"檢查和恢復訂單 {order_id} 失敗: {e}")

    def _should_restore_order(self, cancel_reason: str) -> bool:
        """根據配置判斷是否應該恢復訂單"""
        return self.restoration_config.should_restore_order(cancel_reason)

    async def _can_restore_order(self, cancelled_order: 'OrderInfo', timestamp: int) -> bool:
        """檢查是否可以恢復訂單"""
        try:
            import time
            current_time = time.time()

            # 檢查是否還在運行狀態
            if not self.is_running:
                logger.info("網格機器人已停止，跳過恢復訂單")
                return False

            # 檢查時間窗口（如果啟用）
            if self.restoration_config.enable_time_window_check:
                if timestamp > 0 and (current_time - timestamp/1000) > self.restoration_config.max_restore_window_seconds:
                    logger.info(f"訂單取消時間超過恢復窗口，跳過恢復")
                    return False

            # 檢查當前市場價格是否還在合理範圍內（如果啟用）
            if self.restoration_config.enable_price_check and self.market_info:
                current_price = await self._get_current_price()
                if current_price:
                    price_deviation = abs(cancelled_order.price - current_price) / current_price
                    max_deviation = self.restoration_config.max_price_deviation_percent / 100
                    if price_deviation > max_deviation:
                        logger.warning(f"價格偏差過大 {price_deviation:.2%}，跳過恢復訂單")
                        return False

            # 檢查恢復頻率限制
            if not self._check_restoration_rate_limit():
                logger.warning("恢復頻率超過限制，跳過恢復訂單")
                return False

            return True

        except Exception as e:
            logger.error(f"檢查訂單恢復條件失敗: {e}")
            return False

    def _check_restoration_rate_limit(self) -> bool:
        """檢查恢復頻率限制"""
        try:
            current_time = time.time()

            # 清理過期的記錄（每小時）
            if current_time - self.last_restoration_cleanup > 3600:
                self._cleanup_restoration_attempts()
                self.last_restoration_cleanup = current_time

            # 計算當前小時的恢復次數
            current_hour = int(current_time // 3600)
            attempts_this_hour = self.restoration_attempts.get(current_hour, 0)

            max_attempts = self.restoration_config.max_restoration_attempts_per_hour

            if attempts_this_hour >= max_attempts:
                logger.warning(f"已達到本小時恢復次數限制: {attempts_this_hour}/{max_attempts}")
                return False

            # 記錄這次恢復嘗試
            self.restoration_attempts[current_hour] = attempts_this_hour + 1
            return True

        except Exception as e:
            logger.error(f"檢查恢復頻率限制失敗: {e}")
            # 出錯時允許恢復，避免阻塞正常功能
            return True

    def _cleanup_restoration_attempts(self):
        """清理過期的恢復嘗試記錄"""
        try:
            current_time = time.time()
            current_hour = int(current_time // 3600)

            # 只保留最近24小時的記錄
            hours_to_keep = 24
            cutoff_hour = current_hour - hours_to_keep

            # 清理舊記錄
            old_hours = [h for h in self.restoration_attempts.keys() if h < cutoff_hour]
            for hour in old_hours:
                del self.restoration_attempts[hour]

            if old_hours:
                logger.debug(f"清理了 {len(old_hours)} 個過期的恢復嘗試記錄")

        except Exception as e:
            logger.error(f"清理恢復嘗試記錄失敗: {e}")

    async def _restore_cancelled_order(self, cancelled_order: 'OrderInfo'):
        """恢復被取消的訂單"""
        try:
            original_order_id = getattr(cancelled_order, 'order_id', None) or cancelled_order.get('order_id')
            price_to_use = (getattr(cancelled_order, 'original_price', None) 
                            if hasattr(cancelled_order, 'original_price') else cancelled_order.get('price'))
            side_to_use = getattr(cancelled_order, 'side', None) or cancelled_order.get('side')
            quantity_to_use = (getattr(cancelled_order, 'original_quantity', None) 
                               if hasattr(cancelled_order, 'original_quantity') else cancelled_order.get('quantity'))

            logger.info("開始恢復訂單", event_type="order_restoration_start", data={
                "original_order_id": original_order_id,
                "price": price_to_use,
                "side": side_to_use,
                "quantity": quantity_to_use
            })

            # 創建新的訂單
            await self._create_grid_order(
                price=float(price_to_use),
                side=side_to_use,
                quantity=float(quantity_to_use) if quantity_to_use is not None else None
            )

            new_order_id = None
            async with self._orders_lock:
                try:
                    new_order_id = self.grid_orders.get(float(price_to_use))
                except Exception:
                    new_order_id = None

            if new_order_id and new_order_id != "PENDING":
                logger.info("訂單恢復成功", event_type="order_restoration_success", data={
                    "original_order_id": original_order_id,
                    "new_order_id": new_order_id,
                    "price": price_to_use,
                    "side": side_to_use
                })
                metrics.increment_counter("orders.restored", tags={"side": cancelled_order.side})

                # 更新統計信息
                self.order_statistics["orders_restored"] = self.order_statistics.get("orders_restored", 0) + 1

                # 記錄恢復詳細信息
                if "restoration_history" not in self.order_statistics:
                    self.order_statistics["restoration_history"] = []

                self.order_statistics["restoration_history"].append({
                    "timestamp": time.time(),
                    "original_order_id": original_order_id,
                    "new_order_id": new_order_id,
                    "price": price_to_use,
                    "side": side_to_use
                })

                # 限制歷史記錄數量
                if len(self.order_statistics["restoration_history"]) > 100:
                    self.order_statistics["restoration_history"] = self.order_statistics["restoration_history"][-50:]

            else:
                logger.error("訂單恢復失敗", event_type="order_restoration_failed", data={
                    "original_order_id": original_order_id,
                    "price": price_to_use,
                    "side": side_to_use,
                    "reason": "order_creation_failed"
                })
                metrics.increment_counter("orders.restoration_failed", tags={"side": side_to_use})

        except Exception as e:
            original_order_id = getattr(cancelled_order, 'order_id', None) or cancelled_order.get('order_id')
            side_to_use = getattr(cancelled_order, 'side', None) or cancelled_order.get('side')
            logger.error("訂單恢復異常", event_type="order_restoration_error", data={
                "original_order_id": original_order_id,
                "error": str(e)
            })
            metrics.increment_counter("orders.restoration_errors", tags={"side": side_to_use})

    async def _get_current_price(self) -> Optional[float]:
        """獲取當前市場價格"""
        try:
            try:
                positions = await self.client.get_positions()
                for position in positions.get('data', {}).get('rows', []):
                    if position.get('symbol') == (self.market_info.symbol if self.market_info else None):
                        mark_price = position.get('mark_price')
                        if mark_price and mark_price != 0:
                            return float(mark_price)
            except Exception:
                pass

            try:
                orderbook = await self.client.get_orderbook(self.market_info.symbol)
                if orderbook and orderbook.get('data'):
                    asks = orderbook['data'].get('asks', [])
                    bids = orderbook['data'].get('bids', [])
                    if asks and bids and len(asks[0]) > 0 and len(bids[0]) > 0:
                        best_ask = float(asks[0][0])
                        best_bid = float(bids[0][0])
                        return (best_ask + best_bid) / 2.0
            except Exception:
                pass

            return None

        except Exception as e:
            logger.error(f"獲取當前價格失敗: {e}")
            return None

    async def _periodic_order_sync(self):
        """定期同步訂單狀態，捕獲錯過的取消事件"""
        try:
            sync_interval = self.restoration_config.order_sync_interval_seconds
            logger.info(f"開始定期訂單同步，間隔: {sync_interval}秒")

            while self.is_running:
                try:
                    await asyncio.sleep(sync_interval)

                    if not self.is_running:
                        break

                    await self._sync_order_states()

                except asyncio.CancelledError:
                    logger.info("定期訂單同步任務被取消")
                    break
                except Exception as e:
                    logger.error(f"定期訂單同步失敗: {e}")
                    # 繼續運行，不因單次失敗而停止

        except Exception as e:
            logger.error(f"定期訂單同步任務異常: {e}")

    async def _sync_order_states(self):
        """同步訂單狀態"""
        try:
            if not self.client or not self.market_info:
                return

            # 獲取當前所有活躍訂單
            response = await self.client.get_orders(
                symbol=self.market_info.symbol,
                status='OPEN'
            )

            if not response or not response.get('data'):
                return

            # 創建當前訂單ID集合
            current_rows = response.get('data', {}).get('rows', [])
            current_order_ids = {str(order.get('order_id')) for order in current_rows}

            # 檢查我們追蹤的訂單中哪些已經不在交易所
            cancelled_orders = []
            async with self._orders_lock:
                for order_id, order_info in list(self.active_orders.items()):
                    status_val = order_info.get("status")
                    if (order_id not in current_order_ids and
                        status_val != OrderStatus.CANCELLED and
                        status_val != OrderStatus.FILLED):

                        # 標記為可能被外部取消
                        order_info["status"] = OrderStatus.CANCELLED
                        cancelled_orders.append(order_info)

            # 處理被取消的訂單
            for cancelled_order in cancelled_orders:
                logger.info(f"檢測到外部取消的訂單: {cancelled_order.order_id}")

                # 觸發恢復邏輯
                await self._check_and_restore_cancelled_order(
                    str(cancelled_order.order_id),
                    "EXTERNAL_CANCEL_DETECTED",
                    int(time.time() * 1000)
                )

        except Exception as e:
            logger.error(f"同步訂單狀態失敗: {e}")

    def configure_restoration(self, config: Dict[str, Any]):
        """配置訂單恢復設置"""
        try:
            from src.config.order_restoration_config import OrderRestorationConfig
            self.restoration_config = OrderRestorationConfig.from_dict(config)
            logger.info(f"訂單恢復配置已更新: {config}")
        except Exception as e:
            logger.error(f"更新訂單恢復配置失敗: {e}")

    def get_restoration_config(self) -> Dict[str, Any]:
        """獲取當前恢復配置"""
        return self.restoration_config.to_dict()

    def get_restoration_statistics(self) -> Dict[str, Any]:
        """獲取恢復統計信息"""
        current_time = time.time()
        current_hour = int(current_time // 3600)
        attempts_this_hour = self.restoration_attempts.get(current_hour, 0)

        # 計算最近24小時的總恢復次數
        recent_attempts = sum(
            count for hour, count in self.restoration_attempts.items()
            if hour >= current_hour - 24
        )

        return {
            "orders_restored": self.order_statistics.get("orders_restored", 0),
            "restoration_config": self.get_restoration_config(),
            "active_orders_count": len(self.active_orders),
            "is_restoration_enabled": self.restoration_config.restoration_policy.value != "never",
            "rate_limit": {
                "attempts_this_hour": attempts_this_hour,
                "max_attempts_per_hour": self.restoration_config.max_restoration_attempts_per_hour,
                "attempts_last_24h": recent_attempts
            },
            "recent_restorations": self.order_statistics.get("restoration_history", [])[-10:],  # 最近10次
            "restoration_rate_limit_hours": list(self.restoration_attempts.keys())[-5:]  # 最近5小時的記錄
        }
