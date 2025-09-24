#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
訂單追踪器 - 追踪累積成交和剩餘數量
"""

import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

class OrderStatus(Enum):
    """訂單狀態"""
    PENDING = "pending"           # 等待中
    PARTIALLY_FILLED = "partial"  # 部分成交
    FILLED = "filled"             # 完全成交
    CANCELLED = "cancelled"       # 已取消
    REJECTED = "rejected"         # 已拒絕

@dataclass
class Fill:
    """成交記錄"""
    fill_id: str
    order_id: int
    price: Decimal
    quantity: Decimal
    side: str
    timestamp: float
    
    def __post_init__(self):
        self.price = Decimal(str(self.price))
        self.quantity = Decimal(str(self.quantity))

@dataclass
class OrderInfo:
    """訂單信息"""
    order_id: int
    symbol: str
    side: str
    order_type: str
    original_price: Decimal
    original_quantity: Decimal
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = field(default_factory=lambda: Decimal('0'))
    remaining_quantity: Optional[Decimal] = None
    average_fill_price: Optional[Decimal] = None
    fills: List[Fill] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    
    def __post_init__(self):
        self.original_price = Decimal(str(self.original_price))
        self.original_quantity = Decimal(str(self.original_quantity))
        if self.remaining_quantity is None:
            self.remaining_quantity = self.original_quantity
        else:
            self.remaining_quantity = Decimal(str(self.remaining_quantity))
        
        # 重新計算統計數據
        self._recalculate_stats()
    
    def _recalculate_stats(self):
        """重新計算統計數據"""
        if not self.fills:
            self.filled_quantity = Decimal('0')
            self.remaining_quantity = self.original_quantity
            self.average_fill_price = None
            return
        
        # 計算累積成交量
        total_filled = sum(fill.quantity for fill in self.fills)
        self.filled_quantity = total_filled
        
        # 計算剩餘數量
        self.remaining_quantity = self.original_quantity - total_filled
        
        # 計算平均成交價格（加權平均）
        if total_filled > 0:
            total_value = sum(fill.price * fill.quantity for fill in self.fills)
            self.average_fill_price = total_value / total_filled
        else:
            self.average_fill_price = None
        
        # 更新狀態
        if self.remaining_quantity <= 0:
            self.status = OrderStatus.FILLED
        elif self.filled_quantity > 0:
            self.status = OrderStatus.PARTIALLY_FILLED
    
    def add_fill(self, fill: Fill) -> bool:
        """
        添加成交記錄
        
        Args:
            fill: 成交記錄
            
        Returns:
            是否成功添加（檢查重複）
        """
        # 檢查重複成交
        existing_fill_ids = {f.fill_id for f in self.fills}
        if fill.fill_id in existing_fill_ids:
            logger.warning(f"重複的成交記錄: {fill.fill_id}")
            return False
        
        # 檢查訂單ID匹配
        if fill.order_id != self.order_id:
            logger.error(f"成交記錄訂單ID不匹配: {fill.order_id} != {self.order_id}")
            return False
        
        # 檢查方向匹配
        if fill.side != self.side:
            logger.warning(f"成交記錄方向不匹配: {fill.side} != {self.side}")
        
        # 添加成交記錄
        self.fills.append(fill)
        self.updated_at = time.time()
        
        # 重新計算統計數據
        self._recalculate_stats()
        
        logger.info(
            f"添加成交記錄: 訂單{self.order_id}, 成交{fill.quantity}@{fill.price}, "
            f"累積{self.filled_quantity}/{self.original_quantity}"
        )
        
        return True
    
    def get_fill_percentage(self) -> float:
        """獲取成交百分比"""
        if self.original_quantity == 0:
            return 0.0
        return float(self.filled_quantity / self.original_quantity * 100)
    
    def is_fully_filled(self) -> bool:
        """是否完全成交"""
        return self.status == OrderStatus.FILLED
    
    def is_partially_filled(self) -> bool:
        """是否部分成交"""
        return self.status == OrderStatus.PARTIALLY_FILLED

class OrderTracker:
    """訂單追踪器"""
    
    def __init__(self):
        self.orders: Dict[int, OrderInfo] = {}
        self.fill_ids: set = set()  # 用於快速檢查重複成交
    
    def add_order(self, order_id: int, symbol: str, side: str, order_type: str,
                  price: Decimal, quantity: Decimal) -> OrderInfo:
        """
        添加新訂單
        
        Args:
            order_id: 訂單ID
            symbol: 交易對
            side: 交易方向
            order_type: 訂單類型
            price: 價格
            quantity: 數量
            
        Returns:
            訂單信息
        """
        order_info = OrderInfo(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            original_price=price,
            original_quantity=quantity
        )
        
        self.orders[order_id] = order_info
        logger.info(f"添加訂單追踪: {order_id} {symbol} {side} {quantity}@{price}")
        
        return order_info
    
    def add_fill(self, order_id: int, fill_id: str, price: Decimal,
                 quantity: Decimal, side: str, timestamp: Optional[float] = None) -> bool:
        """
        添加成交記錄
        
        Args:
            order_id: 訂單ID
            fill_id: 成交ID
            price: 成交價格
            quantity: 成交數量
            side: 交易方向
            timestamp: 成交時間
            
        Returns:
            是否成功添加
        """
        # 檢查全局重複成交
        if fill_id in self.fill_ids:
            logger.warning(f"全局重複的成交記錄: {fill_id}")
            return False
        
        # 檢查訂單是否存在
        if order_id not in self.orders:
            logger.warning(f"未找到訂單: {order_id}，創建新的追踪記錄")
            # 創建未知訂單的追踪記錄
            self.orders[order_id] = OrderInfo(
                order_id=order_id,
                symbol="UNKNOWN",
                side=side,
                order_type="UNKNOWN",
                original_price=price,
                original_quantity=quantity
            )
        
        # 創建成交記錄
        fill = Fill(
            fill_id=fill_id,
            order_id=order_id,
            price=price,
            quantity=quantity,
            side=side,
            timestamp=timestamp or time.time()
        )
        
        # 添加到訂單
        order_info = self.orders[order_id]
        success = order_info.add_fill(fill)
        
        if success:
            self.fill_ids.add(fill_id)
        
        return success
    
    def update_order_status(self, order_id: int, status: OrderStatus):
        """
        更新訂單狀態
        
        Args:
            order_id: 訂單ID
            status: 新狀態
        """
        if order_id in self.orders:
            self.orders[order_id].status = status
            self.orders[order_id].updated_at = time.time()
            logger.info(f"更新訂單狀態: {order_id} -> {status.value}")
    
    def get_order(self, order_id: int) -> Optional[OrderInfo]:
        """獲取訂單信息"""
        return self.orders.get(order_id)
    
    def remove_order(self, order_id: int) -> bool:
        """
        移除訂單追踪
        
        Args:
            order_id: 訂單ID
            
        Returns:
            是否成功移除
        """
        if order_id in self.orders:
            order_info = self.orders.pop(order_id)
            
            # 移除相關的成交ID
            for fill in order_info.fills:
                self.fill_ids.discard(fill.fill_id)
            
            logger.info(f"移除訂單追踪: {order_id}")
            return True
        
        return False
    
    def get_active_orders(self) -> List[OrderInfo]:
        """獲取活躍訂單（未完全成交或取消）"""
        return [
            order for order in self.orders.values()
            if order.status in [OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED]
        ]
    
    def get_filled_orders(self) -> List[OrderInfo]:
        """獲取已完全成交的訂單"""
        return [
            order for order in self.orders.values()
            if order.status == OrderStatus.FILLED
        ]
    
    def get_statistics(self) -> Dict[str, any]:
        """獲取統計信息"""
        total_orders = len(self.orders)
        filled_orders = len(self.get_filled_orders())
        active_orders = len(self.get_active_orders())
        total_fills = len(self.fill_ids)
        
        return {
            "total_orders": total_orders,
            "filled_orders": filled_orders,
            "active_orders": active_orders,
            "total_fills": total_fills,
            "fill_rate": filled_orders / total_orders if total_orders > 0 else 0.0
        }
    
    def clear(self):
        """清空所有追踪數據"""
        self.orders.clear()
        self.fill_ids.clear()
        logger.info("清空訂單追踪數據")
