#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易訊號生成器
專注於產生交易訊號，不執行實際交易操作
"""

import math
import asyncio
import inspect
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Callable
from enum import Enum
from dataclasses import dataclass
from src.utils.logging_config import get_logger

class Direction(Enum):
    LONG = "做多"
    SHORT = "做空" 
    BOTH = "雙向"

class OrderSide(Enum):
    BUY = "買入"
    SELL = "賣出"

@dataclass
class TradingSignal:
    """交易訊號數據類"""
    symbol: str
    side: OrderSide
    price: Decimal
    size: Decimal
    signal_type: str  # 'INITIAL', 'COUNTER', 'STOP'
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            import time
            self.timestamp = time.time()

logger = get_logger("grid_signal")

class GridSignalGenerator:
    def __init__(self, ticker: str, current_price: float, direction: Direction, upper_bound: float, 
                 lower_bound: float, grid_levels: int, total_amount: float,
                 stop_bot_price: float = None, stop_top_price: float = None,
                 signal_callback: Callable[[TradingSignal], None] = None):
        """
        初始化網格交易訊號生成器
        
        Args:
            ticker: 幣種符號 (如 'BTCUSDT')
            direction: 交易方向 (做多/做空/雙向)
            upper_bound: 價格上界
            lower_bound: 價格下界  
            grid_levels: 網格格數
            total_amount: 總投入金額(USDT)
            stop_bot_price: 可選，停損下界價格
            stop_top_price: 可選，停損上界價格
            signal_callback: 訊號回調函數
        """
        self.ticker = ticker
        self.current_price = Decimal(str(current_price))
        self.direction = direction
        self.upper_bound = Decimal(str(upper_bound))
        self.lower_bound = Decimal(str(lower_bound))
        self.grid_levels = grid_levels
        self.total_amount = Decimal(str(total_amount))
        self.stop_bot_price = Decimal(str(stop_bot_price)) if stop_bot_price else None
        self.stop_top_price = Decimal(str(stop_top_price)) if stop_top_price else None
        self.signal_callback = signal_callback
        
        # 網格狀態控制
        self.is_active = True  # 網格是否激活
        self.stop_reason = None  # 停止原因
        self.first_trigger = False  # 是否已經觸發第一次成交
        
        # 計算網格參數
        self.grid_levels_above = grid_levels // 2
        self.grid_levels_below = grid_levels - self.grid_levels_above
        
        # 修正網格間距計算，確保不重複 current_price
        if self.grid_levels_above > 0:
            self.price_above = (self.upper_bound - self.current_price) / self.grid_levels_above
        else:
            self.price_above = Decimal('0')
            
        if self.grid_levels_below > 0:
            self.price_below = (self.current_price - self.lower_bound) / self.grid_levels_below
        else:
            self.price_below = Decimal('0')
            
        self.grid_prices = self._calculate_grid_prices()
        self.current_pointer = 0  # 初始設為0，第一次成交後設為該價格的index
        self.amount_per_grid = self.total_amount / Decimal(str(grid_levels))
        
        # 找到最接近 current_price 的網格 index（用於參考）
        self.center_index = self._find_closest_price_index(self.current_price)
    
    def _calculate_grid_prices(self) -> List[Decimal]:
        """計算所有網格價格點"""
        prices = []
        
        # 先計算下方的網格價格（不包含 current_price）
        for i in range(1, self.grid_levels_below + 1):
            price = self.current_price - i * self.price_below
            if price >= self.lower_bound:
                prices.append(price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        
        # 按價格從低到高排序
        prices.sort()
        
        # 再計算上方的網格價格（不包含 current_price）
        for i in range(1, self.grid_levels_above + 1):
            price = self.current_price + i * self.price_above
            if price <= self.upper_bound:
                prices.append(price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        
        return prices
    
    def _find_closest_price_index(self, price: Decimal) -> int:
        """找到最接近指定價格的網格 index"""
        min_diff = Decimal('inf')
        closest_index = 0
        for i, grid_price in enumerate(self.grid_prices):
            diff = abs(grid_price - price)
            if diff < min_diff:
                min_diff = diff
                closest_index = i
        return closest_index
    
    def _calculate_position_size(self, price: Decimal) -> Decimal:
        """根據價格和每格金額計算倉位大小"""
        return (self.amount_per_grid / price).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
    
    def _emit_signal(self, side: OrderSide, price: Decimal, size: Decimal, signal_type: str) -> TradingSignal:
        """
        生成交易訊號並通過回調函數發送
        
        Args:
            side: 訂單方向 (買入/賣出)
            price: 限價價格
            size: 訂單數量
            signal_type: 訊號類型
            
        Returns:
            生成的交易訊號
        """
        signal = TradingSignal(
            symbol=self.ticker,
            side=side,
            price=price,
            size=size,
            signal_type=signal_type
        )
        
        logger.info(
            "生成訊號",
            event_type="signal_emit",
            data={
                "ticker": self.ticker,
                "side": side.value,
                "price": str(price),
                "size": str(size),
                "type": signal_type,
            },
        )
        
        # 如果有回調函數，則調用（支援 async 回調）
        if self.signal_callback:
            try:
                if inspect.iscoroutinefunction(self.signal_callback):
                    asyncio.create_task(self.signal_callback(signal))
                else:
                    result = self.signal_callback(signal)
                    # 若回調回傳 coroutine，也以非阻塞方式派發
                    if inspect.iscoroutine(result):
                        asyncio.create_task(result)
            except Exception as _:
                # 回調失敗不影響訊號生成（MVP 允許忽略）
                pass

        return signal
    
    def stop_grid(self, reason: str = "手動停止"):
        """
        停止網格訊號生成
        
        Args:
            reason: 停止原因
        """
        if not self.is_active:
            logger.info("網格已經停止，無需重複操作")
            return
            
        logger.info("停止網格交易訊號生成", event_type="grid_stop", data={"reason": reason})
        
        # 生成停止訊號
        stop_signal = TradingSignal(
            symbol=self.ticker,
            side=OrderSide.BUY,  # 停止訊號的方向不重要
            price=0,
            size=0,
            signal_type="STOP"
        )
        
        if self.signal_callback:
            try:
                if inspect.iscoroutinefunction(self.signal_callback):
                    asyncio.create_task(self.signal_callback(stop_signal))
                else:
                    result = self.signal_callback(stop_signal)
                    if inspect.iscoroutine(result):
                        asyncio.create_task(result)
            except Exception as _:
                pass
        
        # 更新狀態
        self.is_active = False
        self.stop_reason = reason
        
        logger.info("網格訊號生成已停止", event_type="grid_stopped")
    
    def stop_by_signal(self):
        """
        接收外部訊號停止網格
        未來可通過API server調用此方法
        """
        self.stop_grid("接收停止訊號")
    
    def check_stop_conditions(self, current_price: Decimal) -> bool:
        """
        檢查停損條件
        
        Args:
            current_price: 當前價格
            
        Returns:
            是否需要停止
        """
        if not self.is_active:
            return True
            
        # 檢查下界停損
        if self.stop_bot_price and current_price <= self.stop_bot_price:
            self.stop_grid(f"觸及下界停損價格 {self.stop_bot_price}")
            return True
        
        # 檢查上界停損    
        if self.stop_top_price and current_price >= self.stop_top_price:
            self.stop_grid(f"觸及上界停損價格 {self.stop_top_price}")
            return True
            
        return False
    
    def setup_initial_grid(self):
        """設置初始網格訊號 - 只掛 current_price 上下的單"""
        logger.info(
            "設置網格交易訊號",
            event_type="grid_setup",
            data={
                "ticker": self.ticker,
                "direction": self.direction.value,
                "range": f"{self.lower_bound}-{self.upper_bound}",
                "grid_levels": self.grid_levels,
                "total_amount": str(self.total_amount),
            },
        )
        logger.info(f"每格投入: {self.amount_per_grid:.2f} USDT")
        logger.info(f"當前價格: {self.current_price}")
        logger.info(f"中心網格 index: {self.center_index}")

        # 顯示停損價格
        if self.stop_bot_price:
            logger.info("設置下界停損", event_type="grid_stop_loss", data={"lower": str(self.stop_bot_price)})
        if self.stop_top_price:
            logger.info("設置上界停損", event_type="grid_stop_loss", data={"upper": str(self.stop_top_price)})
        
        if not self.is_active:
            logger.info("網格未激活，跳過設置")
            return
        
        # 根據網格類型只掛 current_price 上下的單
        if self.direction == Direction.LONG:
            # 做多策略：只掛 current_price 下方的買單
            for i in range(len(self.grid_prices)):
                price = self.grid_prices[i]
                if price < self.current_price:  # 只掛低於當前價格的買單
                    position_size = self._calculate_position_size(price)
                    signal = self._emit_signal(OrderSide.BUY, price, position_size, "INITIAL")
                    logger.info("掛買單", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                    
        elif self.direction == Direction.SHORT:
            # 做空策略：只掛 current_price 上方的賣單
            for i in range(len(self.grid_prices)):
                price = self.grid_prices[i]
                if price > self.current_price:  # 只掛高於當前價格的賣單
                    position_size = self._calculate_position_size(price)
                    signal = self._emit_signal(OrderSide.SELL, price, position_size, "INITIAL")
                    logger.info("掛賣單", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                    
        elif self.direction == Direction.BOTH:
            # 雙向策略：掛 current_price 上下各一格的買賣單
            center_idx = self.center_index
            
            # 掛下方一格的買單
            if center_idx > 0:
                buy_price = self.grid_prices[center_idx - 1]
                buy_size = self._calculate_position_size(buy_price)
                buy_signal = self._emit_signal(OrderSide.BUY, buy_price, buy_size, "INITIAL")
                logger.info("掛買單", event_type="grid_order_initial", data={"price": str(buy_price), "size": str(buy_size)})
            
            # 掛上方一格的賣單
            if center_idx < len(self.grid_prices) - 1:
                sell_price = self.grid_prices[center_idx + 1]
                sell_size = self._calculate_position_size(sell_price)
                sell_signal = self._emit_signal(OrderSide.SELL, sell_price, sell_size, "INITIAL")
                logger.info("掛賣單", event_type="grid_order_initial", data={"price": str(sell_price), "size": str(sell_size)})
        
        logger.info("初始網格設置完成", event_type="grid_setup_done")
    
    
    def on_order_filled(self, filled_signal: TradingSignal):
        """
        當訂單成交時的處理邏輯
        由實際交易系統調用此方法
        
        Args:
            filled_signal: 已成交的訊號
        """
        logger.info("訂單成交", event_type="order_filled", data={"side": filled_signal.side.value, "size": str(filled_signal.size), "price": str(filled_signal.price)})
        
        # 找到成交價格對應的網格 index
        filled_index = self._find_closest_price_index(filled_signal.price)
        
        # 如果是第一次觸發，設置 current_pointer
        if not self.first_trigger:
            self.current_pointer = filled_index
            self.first_trigger = True
            logger.info("第一次觸發網格", event_type="grid_first_trigger", data={"current_pointer": self.current_pointer})
        else:
            # 更新 current_pointer 到成交的格子
            self.current_pointer = filled_index
            logger.info("更新 current_pointer", event_type="grid_pointer_update", data={"current_pointer": self.current_pointer})
        
        # 取消所有掛單（這裡只是發出取消訊號，實際取消由交易系統處理）
        logger.info("發出取消所有掛單的訊號", event_type="grid_cancel_all")
        cancel_signal = TradingSignal(
            symbol=self.ticker,
            side=OrderSide.BUY,  # 取消訊號的方向不重要
            price=0,
            size=0,
            signal_type="CANCEL_ALL"
        )
        
        if self.signal_callback:
            try:
                if inspect.iscoroutinefunction(self.signal_callback):
                    asyncio.create_task(self.signal_callback(cancel_signal))
                else:
                    result = self.signal_callback(cancel_signal)
                    if inspect.iscoroutine(result):
                        asyncio.create_task(result)
            except Exception as _:
                pass
        
        # 生成新的掛單訊號
        self._generate_counter_signal(filled_signal)
    
    def _generate_counter_signal(self, filled_signal: TradingSignal):
        """根據 current_pointer 掛相鄰格子的單"""
        if not self.is_active:
            logger.info("網格已停止，不生成新訊號")
            return
        
        try:
            current_idx = self.current_pointer
            logger.info("生成相鄰格子的掛單", event_type="grid_counter_setup", data={"current_pointer": current_idx})
            
            if self.direction == Direction.LONG:
                # 做多策略：掛下方的買單
                if current_idx > 0:  # 檢查 index outbound
                    buy_idx = current_idx - 1
                    buy_price = self.grid_prices[buy_idx]
                    buy_size = self._calculate_position_size(buy_price)
                    buy_signal = self._emit_signal(OrderSide.BUY, buy_price, buy_size, "COUNTER")
                    logger.info("掛買單", event_type="grid_order_counter", data={"index": buy_idx, "price": str(buy_price), "size": str(buy_size)})
                else:
                    logger.warning("已到達網格下界，無法掛買單")
                    
            elif self.direction == Direction.SHORT:
                # 做空策略：掛上方的賣單
                if current_idx < len(self.grid_prices) - 1:  # 檢查 index outbound
                    sell_idx = current_idx + 1
                    sell_price = self.grid_prices[sell_idx]
                    sell_size = self._calculate_position_size(sell_price)
                    sell_signal = self._emit_signal(OrderSide.SELL, sell_price, sell_size, "COUNTER")
                    logger.info("掛賣單", event_type="grid_order_counter", data={"index": sell_idx, "price": str(sell_price), "size": str(sell_size)})
                else:
                    logger.warning("已到達網格上界，無法掛賣單")
                    
            elif self.direction == Direction.BOTH:
                # 雙向策略：掛上下相鄰格子的單
                
                # 掛下方的買單
                if current_idx > 0:
                    buy_idx = current_idx - 1
                    buy_price = self.grid_prices[buy_idx]
                    buy_size = self._calculate_position_size(buy_price)
                    buy_signal = self._emit_signal(OrderSide.BUY, buy_price, buy_size, "COUNTER")
                    logger.info("掛買單", event_type="grid_order_counter", data={"index": buy_idx, "price": str(buy_price), "size": str(buy_size)})
                else:
                    logger.warning("已到達網格下界，無法掛買單")
                
                # 掛上方的賣單
                if current_idx < len(self.grid_prices) - 1:
                    sell_idx = current_idx + 1
                    sell_price = self.grid_prices[sell_idx]
                    sell_size = self._calculate_position_size(sell_price)
                    sell_signal = self._emit_signal(OrderSide.SELL, sell_price, sell_size, "COUNTER")
                    logger.info("掛賣單", event_type="grid_order_counter", data={"index": sell_idx, "price": str(sell_price), "size": str(sell_size)})
                else:
                    logger.warning("已到達網格上界，無法掛賣單")
                    
        except Exception as e:
            logger.error(f"生成相鄰格子掛單失敗: {e}")
    
    def get_status(self):
        """獲取當前網格狀態"""
        logger.info(
            "網格狀態",
            event_type="grid_status",
            data={
                "active": self.is_active,
                "stop_reason": self.stop_reason,
                "first_trigger": self.first_trigger,
                "current_pointer": self.current_pointer,
                "grid_prices": [str(p) for p in self.grid_prices],
                "center_index": self.center_index,
                "stop_bot_price": str(self.stop_bot_price) if self.stop_bot_price else None,
                "stop_top_price": str(self.stop_top_price) if self.stop_top_price else None,
            },
        )
    
    def restart_grid(self):
        """重新啟動網格交易訊號生成"""
        if self.is_active:
            logger.info("網格正在運行中")
            return
            
        logger.info("重新啟動網格交易訊號生成", event_type="grid_restart")
        self.is_active = True
        self.stop_reason = None
        
        # 重新設置網格
        self.setup_initial_grid()