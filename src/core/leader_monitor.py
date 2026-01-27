#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Leader Monitor - 監控 Leader 帳戶的交易活動
使用 WebSocket 監聽 Leader 的 execution_report 並廣播給 Followers
"""

import asyncio
import time
from typing import Dict, Any, Optional, List, Callable, Set
from datetime import datetime
from orderly_evm_connector.websocket.websocket_api import WebsocketPrivateAPIClient
from src.utils.logging_config import get_logger
from src.utils.websocket_manager import get_websocket_manager, WSConnectionState
from src.models.copy_trading import (
    LeaderTradeEvent,
    CopyTradeAction,
    CopyOrderType,
    CopyOrderSide
)

logger = get_logger("leader_monitor")


class LeaderMonitor:
    """
    監控 Leader 帳戶的交易活動

    功能:
    - 使用 Leader 的 API credentials 建立 WebSocket 連線
    - 監聽 execution_report 事件
    - 解析交易事件並廣播給所有註冊的 callback
    """

    # WebSocket 重連配置
    WS_RECONNECT_MAX_RETRIES = 8
    WS_RECONNECT_BASE_DELAY = 3
    WS_RECONNECT_MAX_DELAY = 120
    WS_CONNECTION_TIMEOUT = 45

    def __init__(self, leader_id: str):
        """
        初始化 LeaderMonitor

        Args:
            leader_id: Leader 用戶 ID
        """
        self.leader_id = leader_id
        self.wss_client: Optional[WebsocketPrivateAPIClient] = None
        self.is_monitoring = False
        self._stop_event = asyncio.Event()

        # 回調函數列表
        self._trade_callbacks: List[Callable[[LeaderTradeEvent], Any]] = []
        self._position_callbacks: List[Callable[[Dict[str, Any]], Any]] = []

        # 已處理的訂單 ID (用於去重)
        self._processed_orders: Set[str] = set()
        self._processed_orders_max_size = 1000
        self._processed_orders_cleanup_threshold = 800

        # 連線健康指標
        self.health_metrics = {
            "last_success_time": None,
            "last_error_time": None,
            "total_attempts": 0,
            "success_count": 0,
            "error_count": 0,
            "trades_processed": 0
        }

        # WebSocket 憑證 (將在 start_monitoring 中設置)
        self._ws_credentials: Optional[Dict[str, Any]] = None

        # 主事件循環引用
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # 重連控制
        self._reconnect_attempts = 0
        self._last_reconnect_time = 0

    def register_trade_callback(self, callback: Callable[[LeaderTradeEvent], Any]):
        """
        註冊交易事件回調

        Args:
            callback: 當 Leader 有交易時會被調用的函數
        """
        if callback not in self._trade_callbacks:
            self._trade_callbacks.append(callback)
            logger.info(f"Leader {self.leader_id}: 已註冊交易回調")

    def unregister_trade_callback(self, callback: Callable[[LeaderTradeEvent], Any]):
        """
        取消註冊交易事件回調

        Args:
            callback: 要取消的回調函數
        """
        if callback in self._trade_callbacks:
            self._trade_callbacks.remove(callback)
            logger.info(f"Leader {self.leader_id}: 已取消交易回調")

    def register_position_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        """
        註冊持倉變更回調

        Args:
            callback: 當 Leader 持倉變更時會被調用的函數
        """
        if callback not in self._position_callbacks:
            self._position_callbacks.append(callback)
            logger.info(f"Leader {self.leader_id}: 已註冊持倉回調")

    async def start_monitoring(
        self,
        orderly_key: str,
        orderly_secret: str,
        orderly_testnet: bool = True
    ) -> bool:
        """
        開始監控 Leader 的交易活動

        Args:
            orderly_key: Leader 的 Orderly API Key
            orderly_secret: Leader 的 Orderly API Secret
            orderly_testnet: 是否使用測試網

        Returns:
            是否成功開始監控
        """
        if self.is_monitoring:
            logger.warning(f"Leader {self.leader_id}: 已在監控中")
            return True

        try:
            self._main_loop = asyncio.get_running_loop()
            self._stop_event.clear()

            # 保存憑證用於重連
            self._ws_credentials = {
                "orderly_key": orderly_key,
                "orderly_secret": orderly_secret,
                "orderly_testnet": orderly_testnet
            }

            # 建立 WebSocket 連線
            await self._setup_websocket(orderly_key, orderly_secret, orderly_testnet)

            self.is_monitoring = True
            self.health_metrics["last_success_time"] = time.time()
            self.health_metrics["success_count"] += 1

            logger.info(
                f"Leader {self.leader_id}: 開始監控",
                event_type="leader_monitor_started",
                data={"leader_id": self.leader_id, "testnet": orderly_testnet}
            )

            return True

        except Exception as e:
            self.health_metrics["error_count"] += 1
            self.health_metrics["last_error_time"] = time.time()
            logger.error(
                f"Leader {self.leader_id}: 啟動監控失敗",
                event_type="leader_monitor_start_failed",
                data={"leader_id": self.leader_id, "error": str(e)}
            )
            return False

    async def _setup_websocket(
        self,
        orderly_key: str,
        orderly_secret: str,
        orderly_testnet: bool
    ):
        """
        設置 WebSocket 連線

        Args:
            orderly_key: API Key
            orderly_secret: API Secret
            orderly_testnet: 是否測試網
        """
        self.health_metrics["total_attempts"] += 1

        def on_close(ws):
            """WebSocket 關閉回調"""
            logger.warning(f"Leader {self.leader_id}: WebSocket 連線關閉")
            if self._main_loop and not self._stop_event.is_set():
                asyncio.run_coroutine_threadsafe(
                    self._handle_disconnection(),
                    self._main_loop
                )

        def on_error(ws, error):
            """WebSocket 錯誤回調"""
            self.health_metrics["error_count"] += 1
            self.health_metrics["last_error_time"] = time.time()
            logger.error(f"Leader {self.leader_id}: WebSocket 錯誤: {error}")

        def on_message(ws, message):
            """WebSocket 訊息回調"""
            try:
                if isinstance(message, dict):
                    topic = message.get('topic', '')
                    data = message.get('data', {})

                    if topic == 'executionreport':
                        # 處理執行報告
                        if self._main_loop:
                            asyncio.run_coroutine_threadsafe(
                                self._handle_execution_report(data),
                                self._main_loop
                            )

                    elif topic == 'position':
                        # 處理持倉變更
                        if self._main_loop:
                            asyncio.run_coroutine_threadsafe(
                                self._handle_position_change(data),
                                self._main_loop
                            )

            except Exception as e:
                logger.error(f"Leader {self.leader_id}: 處理 WebSocket 訊息失敗: {e}")

        wss_id = f"leader_monitor_{self.leader_id}"
        self.wss_client = WebsocketPrivateAPIClient(
            orderly_testnet=orderly_testnet,
            orderly_account_id=self.leader_id,
            wss_id=wss_id,
            orderly_key=orderly_key,
            orderly_secret=orderly_secret,
            on_message=on_message,
            on_close=on_close,
            on_error=on_error,
        )

        # 使用 WebSocket 管理器註冊連線
        ws_manager = get_websocket_manager()
        await ws_manager.create_connection(
            session_id=f"leader_{self.leader_id}",
            client=self.wss_client,
            credentials=self._ws_credentials
        )
        await ws_manager.set_connection_state(
            f"leader_{self.leader_id}",
            WSConnectionState.CONNECTED
        )

        # 訂閱執行報告和持倉更新
        self.wss_client.get_execution_report()
        self.wss_client.get_position()

        logger.info(f"Leader {self.leader_id}: WebSocket 連線已建立")

    async def _handle_disconnection(self):
        """處理 WebSocket 斷線"""
        if self._stop_event.is_set():
            return

        logger.warning(f"Leader {self.leader_id}: 處理斷線，嘗試重連...")

        # 更新連線狀態
        ws_manager = get_websocket_manager()
        await ws_manager.set_connection_state(
            f"leader_{self.leader_id}",
            WSConnectionState.RECONNECTING
        )

        # 指數退避重連
        delay = min(
            self.WS_RECONNECT_BASE_DELAY * (2 ** self._reconnect_attempts),
            self.WS_RECONNECT_MAX_DELAY
        )

        if self._reconnect_attempts < self.WS_RECONNECT_MAX_RETRIES:
            self._reconnect_attempts += 1
            logger.info(f"Leader {self.leader_id}: {delay} 秒後嘗試第 {self._reconnect_attempts} 次重連")

            await asyncio.sleep(delay)

            if not self._stop_event.is_set() and self._ws_credentials:
                try:
                    await self._setup_websocket(
                        self._ws_credentials["orderly_key"],
                        self._ws_credentials["orderly_secret"],
                        self._ws_credentials["orderly_testnet"]
                    )
                    self._reconnect_attempts = 0  # 重連成功，重置計數器
                    logger.info(f"Leader {self.leader_id}: 重連成功")
                except Exception as e:
                    logger.error(f"Leader {self.leader_id}: 重連失敗: {e}")
                    # 遞歸嘗試重連
                    await self._handle_disconnection()
        else:
            logger.error(f"Leader {self.leader_id}: 達到最大重連次數，停止監控")
            await ws_manager.set_connection_state(
                f"leader_{self.leader_id}",
                WSConnectionState.FAILED
            )
            self.is_monitoring = False

    async def _handle_execution_report(self, data: Dict[str, Any]):
        """
        處理執行報告 (Leader 的訂單成交)

        Args:
            data: execution_report 數據
        """
        try:
            status = data.get('status', '')

            # 只處理已成交的訂單
            if status not in ['FILLED', 'PARTIAL_FILL']:
                return

            order_id = str(data.get('orderId', ''))

            # 去重檢查
            if order_id in self._processed_orders:
                return

            # 添加到已處理集合
            self._processed_orders.add(order_id)
            self._cleanup_processed_orders()

            # 解析交易事件
            trade_event = self._parse_execution_report(data)
            if trade_event:
                self.health_metrics["trades_processed"] += 1

                logger.info(
                    f"Leader {self.leader_id}: 檢測到交易",
                    event_type="leader_trade_detected",
                    data={
                        "leader_id": self.leader_id,
                        "order_id": order_id,
                        "symbol": trade_event.symbol,
                        "side": trade_event.side.value,
                        "price": trade_event.price,
                        "quantity": trade_event.quantity,
                        "action": trade_event.action.value
                    }
                )

                # 廣播給所有回調
                await self._broadcast_trade_event(trade_event)

        except Exception as e:
            logger.error(f"Leader {self.leader_id}: 處理執行報告失敗: {e}")

    def _parse_execution_report(self, data: Dict[str, Any]) -> Optional[LeaderTradeEvent]:
        """
        解析執行報告為 LeaderTradeEvent

        Args:
            data: 原始執行報告數據

        Returns:
            LeaderTradeEvent 或 None
        """
        try:
            symbol = data.get('symbol', '')
            side = data.get('side', '').upper()
            order_type = data.get('type', 'MARKET').upper()
            executed_price = float(data.get('executedPrice', 0) or data.get('avgPrice', 0) or 0)
            executed_qty = float(data.get('executedQty', 0) or 0)
            order_id = str(data.get('orderId', ''))
            timestamp = data.get('timestamp')

            if not symbol or not side or executed_qty <= 0:
                return None

            # 判斷交易動作 (簡化版本，可根據需要擴展)
            # 這裡假設所有成交都是開倉/加倉，實際應該根據持倉變化判斷
            action = CopyTradeAction.OPEN

            # 根據 reduceOnly 標誌判斷是否為平倉
            if data.get('reduceOnly', False):
                action = CopyTradeAction.CLOSE

            return LeaderTradeEvent(
                leader_id=self.leader_id,
                order_id=order_id,
                symbol=symbol,
                side=CopyOrderSide(side),
                order_type=CopyOrderType(order_type) if order_type in ['MARKET', 'LIMIT'] else CopyOrderType.MARKET,
                price=executed_price,
                quantity=executed_qty,
                action=action,
                timestamp=datetime.utcnow() if not timestamp else datetime.fromtimestamp(timestamp / 1000),
                raw_data=data
            )

        except Exception as e:
            logger.error(f"Leader {self.leader_id}: 解析執行報告失敗: {e}")
            return None

    async def _broadcast_trade_event(self, event: LeaderTradeEvent):
        """
        廣播交易事件給所有註冊的回調

        Args:
            event: 交易事件
        """
        for callback in self._trade_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Leader {self.leader_id}: 執行交易回調失敗: {e}")

    async def _handle_position_change(self, data: Dict[str, Any]):
        """
        處理持倉變更

        Args:
            data: position 數據
        """
        try:
            for callback in self._position_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data)
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Leader {self.leader_id}: 執行持倉回調失敗: {e}")

        except Exception as e:
            logger.error(f"Leader {self.leader_id}: 處理持倉變更失敗: {e}")

    def _cleanup_processed_orders(self):
        """清理已處理訂單集合，防止無限增長"""
        if len(self._processed_orders) > self._processed_orders_cleanup_threshold:
            # 保留最近的一半
            orders_list = list(self._processed_orders)
            self._processed_orders = set(orders_list[-(self._processed_orders_max_size // 2):])
            logger.debug(f"Leader {self.leader_id}: 已清理已處理訂單集合")

    async def stop_monitoring(self):
        """停止監控"""
        if not self.is_monitoring:
            return

        logger.info(f"Leader {self.leader_id}: 停止監控")

        self._stop_event.set()
        self.is_monitoring = False

        # 關閉 WebSocket 連線
        if self.wss_client:
            try:
                self.wss_client.stop()
            except Exception as e:
                logger.warning(f"Leader {self.leader_id}: 關閉 WebSocket 時發生錯誤: {e}")

        # 從 WebSocket 管理器移除
        ws_manager = get_websocket_manager()
        await ws_manager.remove_connection(f"leader_{self.leader_id}")

        # 清理回調
        self._trade_callbacks.clear()
        self._position_callbacks.clear()

        logger.info(
            f"Leader {self.leader_id}: 監控已停止",
            event_type="leader_monitor_stopped",
            data={
                "leader_id": self.leader_id,
                "trades_processed": self.health_metrics["trades_processed"]
            }
        )

    def get_health_status(self) -> Dict[str, Any]:
        """
        獲取健康狀態

        Returns:
            健康狀態字典
        """
        current_time = time.time()

        return {
            "leader_id": self.leader_id,
            "is_monitoring": self.is_monitoring,
            "total_attempts": self.health_metrics["total_attempts"],
            "success_count": self.health_metrics["success_count"],
            "error_count": self.health_metrics["error_count"],
            "trades_processed": self.health_metrics["trades_processed"],
            "last_success_ago": (
                current_time - self.health_metrics["last_success_time"]
                if self.health_metrics["last_success_time"] else None
            ),
            "last_error_ago": (
                current_time - self.health_metrics["last_error_time"]
                if self.health_metrics["last_error_time"] else None
            ),
            "reconnect_attempts": self._reconnect_attempts,
            "callbacks_registered": {
                "trade": len(self._trade_callbacks),
                "position": len(self._position_callbacks)
            }
        }
