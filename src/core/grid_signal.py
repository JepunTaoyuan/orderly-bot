#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易訊號生成器 - 支持等差和等比網格

## 網格類型選擇指南

### 等差網格 (ARITHMETIC)
- 固定價格間距
- 適合震盪行情、價格波動相對穩定
- 每格數量固定
- 例：BTC 40000-50000，間距 1000

### 等比網格 (GEOMETRIC)  
- 固定百分比間距
- 適合趨勢行情、價格大幅波動
- 每格金額固定、數量動態調整
- 例：BTC 價格翻倍時，低價區數量多、高價區數量少

## 等比網格參數說明

grid_ratio: 比例參數，建議範圍 0.01-0.1
- 0.01 (1%): 網格密集，適合小波動
- 0.05 (5%): 網格適中，通用場景
- 0.10 (10%): 網格稀疏，適合大波動

注意事項：
1. grid_ratio 過大可能導致實際網格數少於預期
2. 等比網格在極端價格下可能無法精確到達邊界
3. 建議價格範圍至少為當前價格的 ±20%
"""

import math
import asyncio
import time
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

class GridType(Enum):
    ARITHMETIC = "等差網格"  # 固定價格間距
    GEOMETRIC = "等比網格"   # 固定百分比間距

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
    signal_type: str  # 'INITIAL', 'COUNTER', 'STOP', 'MARKET_OPEN', 'CANCEL_ALL'
    timestamp: float = None
    
    def __post_init__(self):
        if self.timestamp is None:
            import time
            self.timestamp = time.time()

logger = get_logger("grid_signal")

class GridSignalGenerator:
    def __init__(self, ticker: str, current_price: float, direction: Direction, upper_bound: float, 
                 lower_bound: float, grid_levels: int, total_margin: float,
                 grid_type: GridType = GridType.ARITHMETIC, grid_ratio: float = None,
                 stop_bot_price: float = None, stop_top_price: float = None,
                 signal_callback: Callable[[TradingSignal], None] = None):
        """
        初始化網格交易訊號生成器（固定數量版本）
        
        Args:
            ticker: 幣種符號 (如 'PERP_BTC_USDC')
            current_price: 當前價格
            direction: 交易方向 (做多/做空/雙向)
            upper_bound: 價格上界
            lower_bound: 價格下界  
            grid_levels: 網格格數
            total_margin: 總保證金(USDT) - 改名但不涉及槓桿邏輯
            grid_type: 網格類型 (等差/等比)，默認為等差網格
            grid_ratio: 等比網格的比率 (如 0.02 表示 2%)，僅在等比網格時使用
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
        self.total_margin = Decimal(str(total_margin))
        self.grid_type = grid_type
        self.grid_ratio = Decimal(str(grid_ratio)) if grid_ratio is not None else None
        self.stop_bot_price = Decimal(str(stop_bot_price)) if stop_bot_price else None
        self.stop_top_price = Decimal(str(stop_top_price)) if stop_top_price else None
        self.signal_callback = signal_callback
        
        # 驗證等比網格參數
        if self.grid_type == GridType.GEOMETRIC:
            if self.grid_ratio is None:
                raise ValueError("等比網格必須提供 grid_ratio 參數")
            if self.grid_ratio <= 0:
                raise ValueError("grid_ratio 必須大於 0")
            if self.grid_ratio >= 1:
                raise ValueError("grid_ratio 必須小於 1 (建議 0.01-0.1 之間)")
        
        # 網格狀態控制
        self.is_active = True  # 網格是否激活
        self.stop_reason = None  # 停止原因
        self.first_trigger = False  # 是否已經觸發第一次成交
        
        # 計算網格參數
        self.grid_levels_above = grid_levels // 2
        self.grid_levels_below = grid_levels - self.grid_levels_above
        
        # 修正網格間距計算，確保不重複 current_price
        if self.grid_type == GridType.ARITHMETIC:
            # 等差網格：固定價格間距
            if self.grid_levels_above > 0:
                self.price_above = (self.upper_bound - self.current_price) / self.grid_levels_above
            else:
                self.price_above = Decimal('0')
                
            if self.grid_levels_below > 0:
                self.price_below = (self.current_price - self.lower_bound) / self.grid_levels_below
            else:
                self.price_below = Decimal('0')
        else:
            # 等比網格：使用比率，不需要計算固定間距
            self.price_above = None
            self.price_below = None
            
        self.grid_prices = self._calculate_grid_prices()
        self.current_pointer = 0  # 初始設為0，第一次成交後設為該價格的index
        
        # ⭐ 新增：每格固定數量（BTC）
        self.quantity_per_grid: Decimal = Decimal('0')
        self.initial_position_size: Decimal = Decimal('0')
        
        # ⭐ 根據方向設置網格
        if self.direction == Direction.LONG:
            self._setup_long_grid()
        elif self.direction == Direction.SHORT:
            self._setup_short_grid()
        else:  # BOTH
            self._setup_both_grid()
        
        # 找到最接近 current_price 的網格 index（用於參考）
        self.center_index = self._find_closest_price_index(self.current_price)
    
    def _calculate_grid_prices(self) -> List[Decimal]:
        """計算所有網格價格點"""
        if self.grid_type == GridType.ARITHMETIC:
            return self._calculate_arithmetic_grid_prices()
        else:  # GridType.GEOMETRIC
            return self._calculate_geometric_grid_prices()
    
    def _calculate_arithmetic_grid_prices(self) -> List[Decimal]:
        """計算等差網格價格點（原有邏輯）"""
        prices = []
        
        # 先計算下方的網格價格（不包含 current_price）
        for i in range(1, self.grid_levels_below + 1):
            price = self.current_price - i * self.price_below
            if price >= self.lower_bound:
                prices.append(price.quantize(Decimal('0.00001'), rounding=ROUND_HALF_UP))
        
        # 按價格從低到高排序
        prices.sort()
        
        # 再計算上方的網格價格（不包含 current_price）
        for i in range(1, self.grid_levels_above + 1):
            price = self.current_price + i * self.price_above
            if price <= self.upper_bound:
                prices.append(price.quantize(Decimal('0.00001'), rounding=ROUND_HALF_UP))
        
        return prices
    
    def _calculate_geometric_grid_prices(self) -> List[Decimal]:
        """
        計算等比網格價格點
        
        等比網格使用幾何級數分佈價格點，適合趨勢行情：
        - 下方網格：price = current_price × (1 - grid_ratio)^i
        - 上方網格：price = current_price × (1 + grid_ratio)^i
        
        其中 i 為網格層級（1, 2, 3...），grid_ratio 為比例參數（0 < grid_ratio < 1）
        """
        prices = []
        
        # 計算下方的網格價格（不包含 current_price）
        for i in range(1, self.grid_levels_below + 1):
            # 等比數列：price = current_price * (1 - grid_ratio)^i
            multiplier = (Decimal('1') - self.grid_ratio) ** i
            price = self.current_price * multiplier
            if price >= self.lower_bound:
                prices.append(price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        
        # 按價格從低到高排序
        prices.sort()
        
        # 計算上方的網格價格（不包含 current_price）
        for i in range(1, self.grid_levels_above + 1):
            # 等比數列：price = current_price * (1 + grid_ratio)^i
            multiplier = (Decimal('1') + self.grid_ratio) ** i
            price = self.current_price * multiplier
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
    
    def _setup_long_grid(self):
        """
        做多網格設置
        - 50% 資金開初始多倉
        - 50% 資金分配到下方網格（加倉用）
        - 用最低價計算固定數量（確保資金夠用）
        """
        self.initial_margin = self.total_margin / Decimal('2')
        self.grid_margin = self.total_margin / Decimal('2')

        # 計算初始倉位大小
        self.initial_position_size = (
            self.initial_margin / self.current_price
        ).quantize(Decimal('1.000000'), rounding=ROUND_HALF_UP)

        # 計算下方網格
        lower_grids = [p for p in self.grid_prices if p < self.current_price]

        # 初始化變量
        reference_price = None
        margin_per_grid = None
        num_grids = 0

        if lower_grids:
            if self.grid_type == GridType.ARITHMETIC:
                # ⭐ 用最低價計算（確保在最低價時保證金也夠用）
                reference_price = min(lower_grids)
                num_grids = len(lower_grids)
                margin_per_grid = self.grid_margin / Decimal(str(num_grids))

                # 固定數量 = 每格保證金 / 參考價格
                self.quantity_per_grid = (
                    margin_per_grid / reference_price
                ).quantize(Decimal('1.000000'), rounding=ROUND_HALF_UP)
            else:  # GEOMETRIC
                # 等比網格：只需要記錄每格保證金，數量動態計算
                num_grids = len(lower_grids)
                self.margin_per_grid = self.grid_margin / Decimal(str(num_grids))
                self.quantity_per_grid = Decimal('0')  # 不使用固定數量
                reference_price = min(lower_grids)  # 用於日誌記錄
                margin_per_grid = self.margin_per_grid

            logger.info(
                "做多網格設置完成",
                event_type="grid_setup_long",
                data={
                    "總保證金": str(self.total_margin),
                    "初始倉位": f"{self.initial_position_size} BTC ({self.initial_margin} USDT @ {self.current_price})",
                    "網格保證金": str(self.grid_margin),
                    "參考價格(最低價)": str(reference_price) if reference_price else None,
                    "下方網格數": num_grids,
                    "每格固定數量": f"{self.quantity_per_grid} BTC",
                    "每格保證金(約)": str(margin_per_grid) if margin_per_grid else None,
                }
            )
        else:
            # 沒有下方網格的備用方案
            num_grids = self.grid_levels
            self.margin_per_grid = self.grid_margin / Decimal(str(num_grids))
            self.quantity_per_grid = (
                self.margin_per_grid / self.current_price
            ).quantize(Decimal('1.000000'), rounding=ROUND_HALF_UP)
            reference_price = self.current_price  # 用於日誌記錄
            logger.warning("沒有下方網格，使用當前價格計算")
    
    def _setup_short_grid(self):
        """
        做空網格設置
        - 50% 資金開初始空倉
        - 50% 資金分配到上方網格（加倉用）
        - 用最高價計算固定數量（確保資金夠用）
        """
        self.initial_margin = self.total_margin / Decimal('2')
        self.grid_margin = self.total_margin / Decimal('2')
        
        # 計算初始倉位大小
        self.initial_position_size = (
            self.initial_margin / self.current_price
        ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        
        # 計算上方網格
        upper_grids = [p for p in self.grid_prices if p > self.current_price]
        
        if upper_grids:
            # ⭐ 用最高價計算（確保在最高價時保證金也夠用）
            reference_price = max(upper_grids)
            num_grids = len(upper_grids)
            self.margin_per_grid = self.grid_margin / Decimal(str(num_grids))

            # 固定數量 = 每格保證金 / 參考價格
            self.quantity_per_grid = (
                self.margin_per_grid / reference_price
            ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            
            logger.info(
                "做空網格設置完成",
                event_type="grid_setup_short",
                data={
                    "總保證金": str(self.total_margin),
                    "初始倉位": f"{self.initial_position_size} BTC ({self.initial_margin} USDT @ {self.current_price})",
                    "網格保證金": str(self.grid_margin),
                    "參考價格(最高價)": str(reference_price),
                    "上方網格數": num_grids,
                    "每格固定數量": f"{self.quantity_per_grid} BTC",
                    "每格保證金(約)": str(self.margin_per_grid),
                }
            )
        else:
            # 沒有上方網格的備用方案
            self.margin_per_grid = self.grid_margin / Decimal(str(self.grid_levels))
            self.quantity_per_grid = (
                self.margin_per_grid / self.current_price
            ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            logger.warning("沒有上方網格，使用當前價格計算")
    
    def _setup_both_grid(self):
        """
        雙向網格設置
        - 不開初始倉位
        - 100% 資金分配到網格
        - 用最高價計算固定數量（保守策略，確保所有格子都夠用）
        """
        self.initial_margin = Decimal('0')
        self.grid_margin = self.total_margin
        self.initial_position_size = Decimal('0')
        
        # ⭐ 用最高價計算（保守策略）
        reference_price = self.upper_bound
        self.margin_per_grid = self.total_margin / Decimal(str(self.grid_levels))

        # 固定數量 = 每格保證金 / 參考價格
        self.quantity_per_grid = (
            self.margin_per_grid / reference_price
        ).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        
        logger.info(
            "雙向網格設置完成",
            event_type="grid_setup_both",
            data={
                "總保證金": str(self.total_margin),
                "參考價格(最高價-保守)": str(reference_price),
                "總網格數": self.grid_levels,
                "每格固定數量": f"{self.quantity_per_grid} BTC",
                "每格保證金(約)": str(self.margin_per_grid),
            }
        )
    
    def _calculate_position_size(self, price: Decimal = None, type: Direction = None) -> Decimal:
        """
        計算倉位大小
        - 等差網格：返回固定數量
        - 等比網格：根據價格計算數量，保持固定投資金額
        """
        if self.grid_type == GridType.ARITHMETIC:
            # 等差網格：固定數量
            return self.quantity_per_grid
        else:
            # 等比網格：固定投資金額，數量隨價格變化
            if price is None:
                raise ValueError("等比網格計算倉位大小時必須提供價格")

            # 使用初始化時計算的每格保證金
            quantity = (self.margin_per_grid / price).quantize(
                Decimal('0.000001'), rounding=ROUND_HALF_UP
            )

        return quantity
    
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
            price=Decimal('0'),
            size=Decimal('0'),
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
        """設置初始網格訊號 - 做多/做空先開倉，再掛網格單"""
        logger.info(
            "設置網格交易訊號",
            event_type="grid_setup",
            data={
                "ticker": self.ticker,
                "direction": self.direction.value,
                "range": f"{self.lower_bound}-{self.upper_bound}",
                "grid_levels": self.grid_levels,
                "total_margin": str(self.total_margin),
                "quantity_per_grid": str(self.quantity_per_grid),
            },
        )
        logger.info(f"每格固定數量: {self.quantity_per_grid} BTC")
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
        
        # 根據網格類型設置初始倉位和掛單
        if self.direction == Direction.LONG:
            # 做多策略：
            # 1. 先用50%資金市價開多倉
            market_signal = self._emit_signal(OrderSide.BUY, self.current_price, self.initial_position_size, "MARKET_OPEN")
            logger.info("市價開多倉", event_type="grid_initial_position", data={"amount": str(self.initial_margin), "size": str(self.initial_position_size)})
            
            # 2. 在 current_price 上下都掛網格單
            for i in range(len(self.grid_prices)):
                price = self.grid_prices[i]
                position_size = self._calculate_position_size(price)  # ⭐ 根據網格類型計算數量
                
                if price < self.current_price:
                    # 下方掛買單（加倉）
                    signal = self._emit_signal(OrderSide.BUY, price, position_size, "INITIAL")
                    logger.info("掛買單（加倉）", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                elif price > self.current_price:
                    # 上方掛賣單（減倉）
                    signal = self._emit_signal(OrderSide.SELL, price, position_size, "INITIAL")
                    logger.info("掛賣單（減倉）", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                    
        elif self.direction == Direction.SHORT:
            # 做空策略：
            # 1. 先用50%資金市價開空倉
            market_signal = self._emit_signal(OrderSide.SELL, self.current_price, self.initial_position_size, "MARKET_OPEN")
            logger.info("市價開空倉", event_type="grid_initial_position", data={"amount": str(self.initial_margin), "size": str(self.initial_position_size)})
            
            # 2. 在 current_price 上下都掛網格單
            for i in range(len(self.grid_prices)):
                price = self.grid_prices[i]
                position_size = self._calculate_position_size(price)  # ⭐ 根據網格類型計算數量
                
                if price < self.current_price:
                    # 下方掛買單（減倉平倉）
                    signal = self._emit_signal(OrderSide.BUY, price, position_size, "INITIAL")
                    logger.info("掛買單（平倉）", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                elif price > self.current_price:
                    # 上方掛賣單（加倉）
                    signal = self._emit_signal(OrderSide.SELL, price, position_size, "INITIAL")
                    logger.info("掛賣單（加倉）", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                    
        elif self.direction == Direction.BOTH:
            # 雙向策略：一次性開啟所有格線的限價單
            for i, price in enumerate(self.grid_prices):
                position_size = self._calculate_position_size(price)  # ⭐ 根據網格類型計算數量
                
                if price < self.current_price:
                    # 下方掛買單
                    signal = self._emit_signal(OrderSide.BUY, price, position_size, "INITIAL")
                    logger.info("掛買單", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
                elif price > self.current_price:
                    # 上方掛賣單
                    signal = self._emit_signal(OrderSide.SELL, price, position_size, "INITIAL")
                    logger.info("掛賣單", event_type="grid_order_initial", data={"price": str(price), "size": str(position_size)})
        
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
            # 保存當前指針位置（成交前的位置）
            previous_pointer = self.current_pointer
            # 更新 current_pointer 到成交的格子
            self.current_pointer = filled_index

            # 使用成交前的位置生成反向訊號
            self._generate_counter_signal(filled_signal, previous_pointer)
            logger.info("更新 current_pointer", event_type="grid_pointer_update", data={
                "previous_pointer": previous_pointer,
                "current_pointer": self.current_pointer
            })

        if self.signal_callback:
            try:
                if inspect.iscoroutinefunction(self.signal_callback):
                    asyncio.create_task(self.signal_callback(filled_signal))
                else:
                    result = self.signal_callback(filled_signal)
                    # 若回調回傳 coroutine，也以非阻塞方式派發
                    if inspect.iscoroutine(result):
                        asyncio.create_task(result)
            except Exception as _:
                # 回調失敗不影響訊號生成（MVP 允許忽略）
                pass
    
    def _generate_counter_signal(self, filled_signal: TradingSignal, previous_pointer: int = None):
        """
        根據成交前的位置生成反向訊號

        Args:
            filled_signal: 成交的訊號
            previous_pointer: 成交前的網格指針位置（如果為None則使用當前current_pointer）
        """
        if not self.is_active:
            logger.info("網格已停止，不生成新訊號")
            return

        try:
            # 使用成交前的位置（如果提供）或者當前位置
            current_idx = previous_pointer if previous_pointer is not None else self.current_pointer
            logger.info("生成反向訊號", event_type="grid_counter_setup", data={
                "previous_pointer": current_idx,
                "current_pointer": self.current_pointer,
                "filled_side": filled_signal.side.value,
                "filled_price": str(filled_signal.price)
            })
            
            if self.direction == Direction.LONG:
                # 做多策略：
                # - 買單成交（價格下跌，加倉） → 掛當前格子的賣單（減倉獲利）+ 掛更低格子的買單（繼續加倉）
                # - 賣單成交（價格上漲，減倉） → 掛當前格子的買單（重新加倉）+ 掛更高格子的賣單（繼續減倉）

                if filled_signal.side == OrderSide.BUY:
                    # 買單成交（價格下跌）：在當前成交價格掛賣單獲利
                    sell_idx = self.current_pointer
                    sell_price = self.grid_prices[sell_idx]
                    sell_size = self._calculate_position_size(sell_price)
                    sell_signal = self._emit_signal(OrderSide.SELL, sell_price, sell_size, "COUNTER")
                    logger.info("買單成交，掛賣單獲利", event_type="grid_order_counter", data={
                        "action": "BUY成交->掛賣單獲利",
                        "sell_index": sell_idx,
                        "sell_price": str(sell_price),
                        "sell_size": str(sell_size)
                    })

                else:  # 賣單成交（價格上漲）
                    # 賣單成交（價格上漲）：在當前成交價格重新掛買單
                    buy_idx = self.current_pointer
                    buy_price = self.grid_prices[buy_idx]
                    buy_size = self._calculate_position_size(buy_price)
                    buy_signal = self._emit_signal(OrderSide.BUY, buy_price, buy_size, "COUNTER")
                    logger.info("賣單成交，重新掛買單", event_type="grid_order_counter", data={
                        "action": "SELL成交->掛買單",
                        "buy_index": buy_idx,
                        "buy_price": str(buy_price),
                        "buy_size": str(buy_size)
                    })
                    
            elif self.direction == Direction.SHORT:
                # 做空策略：
                # - 賣單成交（價格上漲，加倉） → 掛當前格子的買單（減倉平倉）
                # - 買單成交（價格下跌，減倉） → 掛當前格子的賣單（重新加倉）

                if filled_signal.side == OrderSide.SELL:
                    # 賣單成交（價格上漲）：在當前成交價格掛買單平倉
                    buy_idx = self.current_pointer
                    buy_price = self.grid_prices[buy_idx]
                    buy_size = self._calculate_position_size(buy_price)
                    buy_signal = self._emit_signal(OrderSide.BUY, buy_price, buy_size, "COUNTER")
                    logger.info("賣單成交，掛買單平倉", event_type="grid_order_counter", data={
                        "action": "SELL成交->掛買單平倉",
                        "buy_index": buy_idx,
                        "buy_price": str(buy_price),
                        "buy_size": str(buy_size)
                    })

                else:  # 買單成交（價格下跌）
                    # 買單成交（價格下跌）：在當前成交價格重新掛賣單加倉
                    sell_idx = self.current_pointer
                    sell_price = self.grid_prices[sell_idx]
                    sell_size = self._calculate_position_size(sell_price)
                    sell_signal = self._emit_signal(OrderSide.SELL, sell_price, sell_size, "COUNTER")
                    logger.info("買單成交，重新掛賣單加倉", event_type="grid_order_counter", data={
                        "action": "BUY成交->掛賣單加倉",
                        "sell_index": sell_idx,
                        "sell_price": str(sell_price),
                        "sell_size": str(sell_size)
                    })
                    
            elif self.direction == Direction.BOTH:
                # 雙向策略：根據成交方向決定掛單策略

                if filled_signal.side == OrderSide.BUY:
                    # 買單成交：在當前價格掛賣單
                    sell_idx = self.current_pointer
                    sell_price = self.grid_prices[sell_idx]
                    sell_size = self._calculate_position_size(sell_price)
                    sell_signal = self._emit_signal(OrderSide.SELL, sell_price, sell_size, "COUNTER")
                    logger.info("雙向策略-買單成交，掛賣單", event_type="grid_order_counter", data={
                        "action": "BUY成交->掛賣單",
                        "sell_index": sell_idx,
                        "sell_price": str(sell_price),
                        "sell_size": str(sell_size)
                    })

                else:  # 賣單成交
                    # 賣單成交：在當前價格掛買單
                    buy_idx = self.current_pointer
                    buy_price = self.grid_prices[buy_idx]
                    buy_size = self._calculate_position_size(buy_price)
                    buy_signal = self._emit_signal(OrderSide.BUY, buy_price, buy_size, "COUNTER")
                    logger.info("雙向策略-賣單成交，掛買單", event_type="grid_order_counter", data={
                        "action": "SELL成交->掛買單",
                        "buy_index": buy_idx,
                        "buy_price": str(buy_price),
                        "buy_size": str(buy_size)
                    })
                    
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
                "quantity_per_grid": str(self.quantity_per_grid),
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
