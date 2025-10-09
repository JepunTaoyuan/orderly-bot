#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易利潤統計模組
追蹤交易記錄、計算盈虧、統計績效
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
from src.utils.logging_config import get_logger

logger = get_logger("profit_tracker")

class OrderSide(Enum):
    BUY = "買入"
    SELL = "賣出"

@dataclass
class Trade:
    """單筆交易記錄"""
    timestamp: float
    side: OrderSide
    price: Decimal
    quantity: Decimal
    cost: Decimal  # 買入成本或賣出收入（含手續費）
    fee: Decimal = Decimal('0')
    trade_id: str = ""
    
    def __post_init__(self):
        if not self.trade_id:
            self.trade_id = f"{int(self.timestamp)}_{self.side.value}_{self.price}"

@dataclass
class Position:
    """持倉記錄（用於配對計算盈虧）"""
    buy_price: Decimal
    quantity: Decimal
    buy_timestamp: float
    buy_cost: Decimal
    matched: bool = False
    sell_price: Optional[Decimal] = None
    sell_timestamp: Optional[float] = None
    sell_revenue: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None

@dataclass
class GridStats:
    """網格統計數據"""
    total_trades: int = 0
    buy_trades: int = 0
    sell_trades: int = 0
    
    # 盈虧統計
    realized_pnl: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')
    total_pnl: Decimal = Decimal('0')
    
    # 交易統計
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal = Decimal('0')
    
    # 金額統計
    total_buy_cost: Decimal = Decimal('0')
    total_sell_revenue: Decimal = Decimal('0')
    total_fees: Decimal = Decimal('0')
    
    # 平均值
    avg_profit_per_trade: Decimal = Decimal('0')
    avg_win: Decimal = Decimal('0')
    avg_loss: Decimal = Decimal('0')
    
    # 最大值
    max_win: Decimal = Decimal('0')
    max_loss: Decimal = Decimal('0')
    
    # 持倉統計
    current_position_qty: Decimal = Decimal('0')
    current_position_cost: Decimal = Decimal('0')
    avg_entry_price: Decimal = Decimal('0')

class ProfitTracker:
    """網格交易利潤追蹤器"""
    
    def __init__(self, symbol: str, fee_rate: Decimal = Decimal('0.001')):
        """
        初始化利潤追蹤器
        
        Args:
            symbol: 交易對符號
            fee_rate: 手續費率（默認 0.1%）
        """
        self.symbol = symbol
        self.fee_rate = fee_rate
        
        # 交易記錄
        self.trades: List[Trade] = []
        
        # 持倉記錄（用於配對）
        self.open_positions: List[Position] = []  # 未配對的買單
        self.closed_positions: List[Position] = []  # 已配對的買賣對
        
        # 統計數據
        self.stats = GridStats()
    
    def add_trade(self, side: OrderSide, price: Decimal, quantity: Decimal, 
                  timestamp: float = None, fee: Decimal = None) -> Trade:
        """
        添加交易記錄
        
        Args:
            side: 買入或賣出
            price: 成交價格
            quantity: 成交數量
            timestamp: 時間戳（可選）
            fee: 手續費（可選，不提供則自動計算）
            
        Returns:
            Trade: 交易記錄對象
        """
        if timestamp is None:
            timestamp = datetime.now().timestamp()
        
        # 計算成本/收入
        notional = price * quantity
        
        if fee is None:
            fee = notional * self.fee_rate
        
        if side == OrderSide.BUY:
            cost = notional + fee  # 買入成本含手續費
        else:
            cost = notional - fee  # 賣出收入扣手續費
        
        # 創建交易記錄
        trade = Trade(
            timestamp=timestamp,
            side=side,
            price=price,
            quantity=quantity,
            cost=cost,
            fee=fee
        )
        
        self.trades.append(trade)
        
        # 更新持倉和盈虧
        self._update_positions(trade)
        self._update_stats()
        
        logger.info(f"添加交易記錄: {side.value} {quantity} @ {price}, 成本/收入: {cost}")
        
        return trade
    
    def _update_positions(self, trade: Trade):
        """更新持倉記錄"""
        if trade.side == OrderSide.BUY:
            # 買入：創建新的持倉記錄
            position = Position(
                buy_price=trade.price,
                quantity=trade.quantity,
                buy_timestamp=trade.timestamp,
                buy_cost=trade.cost
            )
            self.open_positions.append(position)
        
        else:  # SELL
            # 賣出：配對最早的買單（FIFO）
            remaining_qty = trade.quantity
            sell_price = trade.price
            sell_timestamp = trade.timestamp
            
            # 計算這筆賣單的總收入
            total_revenue = trade.cost  # 已扣除手續費
            
            while remaining_qty > Decimal('0') and self.open_positions:
                # 取出最早的買單
                position = self.open_positions[0]
                
                if position.quantity <= remaining_qty:
                    # 這個持倉完全賣出
                    matched_qty = position.quantity
                    
                    # 計算這部分的收入（按比例）
                    revenue_ratio = matched_qty / trade.quantity
                    matched_revenue = total_revenue * revenue_ratio
                    
                    # 計算盈虧
                    realized_pnl = matched_revenue - position.buy_cost
                    
                    # 更新持倉記錄
                    position.matched = True
                    position.sell_price = sell_price
                    position.sell_timestamp = sell_timestamp
                    position.sell_revenue = matched_revenue
                    position.realized_pnl = realized_pnl
                    
                    # 移到已平倉列表
                    self.closed_positions.append(position)
                    self.open_positions.pop(0)
                    
                    remaining_qty -= matched_qty
                
                else:
                    # 持倉部分賣出
                    matched_qty = remaining_qty
                    
                    # 計算這部分的收入
                    revenue_ratio = matched_qty / trade.quantity
                    matched_revenue = total_revenue * revenue_ratio
                    
                    # 計算這部分的成本
                    cost_ratio = matched_qty / position.quantity
                    matched_cost = position.buy_cost * cost_ratio
                    
                    # 計算盈虧
                    realized_pnl = matched_revenue - matched_cost
                    
                    # 創建已平倉記錄
                    closed_position = Position(
                        buy_price=position.buy_price,
                        quantity=matched_qty,
                        buy_timestamp=position.buy_timestamp,
                        buy_cost=matched_cost,
                        matched=True,
                        sell_price=sell_price,
                        sell_timestamp=sell_timestamp,
                        sell_revenue=matched_revenue,
                        realized_pnl=realized_pnl
                    )
                    self.closed_positions.append(closed_position)
                    
                    # 更新原持倉（減少數量）
                    position.quantity -= matched_qty
                    position.buy_cost -= matched_cost
                    
                    remaining_qty = Decimal('0')
    
    def _update_stats(self):
        """更新統計數據"""
        # 基本統計
        self.stats.total_trades = len(self.trades)
        self.stats.buy_trades = sum(1 for t in self.trades if t.side == OrderSide.BUY)
        self.stats.sell_trades = sum(1 for t in self.trades if t.side == OrderSide.SELL)
        
        # 計算已實現盈虧
        self.stats.realized_pnl = sum(
            pos.realized_pnl for pos in self.closed_positions
        )
        
        # 總盈虧
        self.stats.total_pnl = self.stats.realized_pnl + self.stats.unrealized_pnl
        
        # 勝率統計
        self.stats.winning_trades = sum(
            1 for pos in self.closed_positions if pos.realized_pnl > 0
        )
        self.stats.losing_trades = sum(
            1 for pos in self.closed_positions if pos.realized_pnl < 0
        )
        
        total_closed = len(self.closed_positions)
        if total_closed > 0:
            self.stats.win_rate = (
                Decimal(str(self.stats.winning_trades)) / Decimal(str(total_closed)) * Decimal('100')
            ).quantize(Decimal('0.01'))
        
        # 金額統計
        self.stats.total_buy_cost = sum(
            t.cost for t in self.trades if t.side == OrderSide.BUY
        )
        self.stats.total_sell_revenue = sum(
            t.cost for t in self.trades if t.side == OrderSide.SELL
        )
        self.stats.total_fees = sum(t.fee for t in self.trades)
        
        # 平均值
        if total_closed > 0:
            self.stats.avg_profit_per_trade = (
                self.stats.realized_pnl / Decimal(str(total_closed))
            ).quantize(Decimal('0.01'))
        
        if self.stats.winning_trades > 0:
            winning_pnls = [pos.realized_pnl for pos in self.closed_positions if pos.realized_pnl > 0]
            self.stats.avg_win = (
                sum(winning_pnls) / Decimal(str(len(winning_pnls)))
            ).quantize(Decimal('0.01'))
        
        if self.stats.losing_trades > 0:
            losing_pnls = [pos.realized_pnl for pos in self.closed_positions if pos.realized_pnl < 0]
            self.stats.avg_loss = (
                sum(losing_pnls) / Decimal(str(len(losing_pnls)))
            ).quantize(Decimal('0.01'))
        
        # 最大值
        if self.closed_positions:
            all_pnls = [pos.realized_pnl for pos in self.closed_positions]
            self.stats.max_win = max(all_pnls)
            self.stats.max_loss = min(all_pnls)
        
        # 當前持倉統計
        self.stats.current_position_qty = sum(pos.quantity for pos in self.open_positions)
        self.stats.current_position_cost = sum(pos.buy_cost for pos in self.open_positions)
        
        if self.stats.current_position_qty > 0:
            self.stats.avg_entry_price = (
                self.stats.current_position_cost / self.stats.current_position_qty
            ).quantize(Decimal('0.01'))
    
    def calculate_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """
        計算未實現盈虧
        
        Args:
            current_price: 當前市場價格
            
        Returns:
            未實現盈虧
        """
        unrealized = Decimal('0')
        
        for position in self.open_positions:
            # 當前市值
            current_value = position.quantity * current_price
            # 扣除賣出手續費
            current_value_after_fee = current_value * (Decimal('1') - self.fee_rate)
            # 未實現盈虧 = 當前市值 - 買入成本
            pnl = current_value_after_fee - position.buy_cost
            unrealized += pnl
        
        self.stats.unrealized_pnl = unrealized.quantize(Decimal('0.01'))
        self.stats.total_pnl = self.stats.realized_pnl + self.stats.unrealized_pnl
        
        return self.stats.unrealized_pnl
    
    def get_summary(self, current_price: Decimal = None) -> Dict:
        """
        獲取完整的統計摘要
        
        Args:
            current_price: 當前價格（用於計算未實現盈虧）
            
        Returns:
            統計摘要字典
        """
        if current_price:
            self.calculate_unrealized_pnl(current_price)
        
        return {
            "symbol": self.symbol,
            "fee_rate": f"{self.fee_rate * 100}%",
            
            # 交易統計
            "total_trades": self.stats.total_trades,
            "buy_trades": self.stats.buy_trades,
            "sell_trades": self.stats.sell_trades,
            "completed_pairs": len(self.closed_positions),
            
            # 盈虧統計
            "realized_pnl": f"{self.stats.realized_pnl:.2f} USDT",
            "unrealized_pnl": f"{self.stats.unrealized_pnl:.2f} USDT",
            "total_pnl": f"{self.stats.total_pnl:.2f} USDT",
            
            # 勝率統計
            "winning_trades": self.stats.winning_trades,
            "losing_trades": self.stats.losing_trades,
            "win_rate": f"{self.stats.win_rate}%",
            
            # 金額統計
            "total_buy_cost": f"{self.stats.total_buy_cost:.2f} USDT",
            "total_sell_revenue": f"{self.stats.total_sell_revenue:.2f} USDT",
            "total_fees": f"{self.stats.total_fees:.2f} USDT",
            
            # 平均值
            "avg_profit_per_trade": f"{self.stats.avg_profit_per_trade:.2f} USDT",
            "avg_win": f"{self.stats.avg_win:.2f} USDT",
            "avg_loss": f"{self.stats.avg_loss:.2f} USDT",
            
            # 最大值
            "max_win": f"{self.stats.max_win:.2f} USDT",
            "max_loss": f"{self.stats.max_loss:.2f} USDT",
            
            # 持倉統計
            "current_position_qty": f"{self.stats.current_position_qty}",
            "current_position_cost": f"{self.stats.current_position_cost:.2f} USDT",
            "avg_entry_price": f"{self.stats.avg_entry_price:.2f} USDT",
            "open_positions_count": len(self.open_positions),
        }
    
    def get_trade_history(self, limit: int = None) -> List[Dict]:
        """獲取交易歷史"""
        trades = self.trades[-limit:] if limit else self.trades
        
        return [
            {
                "timestamp": datetime.fromtimestamp(t.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "side": t.side.value,
                "price": f"{t.price:.2f}",
                "quantity": f"{t.quantity:.6f}",
                "cost": f"{t.cost:.2f}",
                "fee": f"{t.fee:.2f}",
            }
            for t in trades
        ]
    
    def get_closed_positions(self, limit: int = None) -> List[Dict]:
        """獲取已平倉記錄"""
        positions = self.closed_positions[-limit:] if limit else self.closed_positions
        
        return [
            {
                "buy_time": datetime.fromtimestamp(pos.buy_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "buy_price": f"{pos.buy_price:.2f}",
                "sell_time": datetime.fromtimestamp(pos.sell_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "sell_price": f"{pos.sell_price:.2f}",
                "quantity": f"{pos.quantity:.6f}",
                "buy_cost": f"{pos.buy_cost:.2f}",
                "sell_revenue": f"{pos.sell_revenue:.2f}",
                "realized_pnl": f"{pos.realized_pnl:.2f}",
                "pnl_pct": f"{(pos.realized_pnl / pos.buy_cost * 100):.2f}%",
            }
            for pos in positions
        ]
    
    def get_open_positions(self) -> List[Dict]:
        """獲取未平倉記錄"""
        return [
            {
                "buy_time": datetime.fromtimestamp(pos.buy_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "buy_price": f"{pos.buy_price:.2f}",
                "quantity": f"{pos.quantity:.6f}",
                "buy_cost": f"{pos.buy_cost:.2f}",
            }
            for pos in self.open_positions
        ]
    
    def export_to_json(self, filepath: str):
        """導出統計數據到 JSON 文件"""
        data = {
            "summary": self.get_summary(),
            "trade_history": self.get_trade_history(),
            "closed_positions": self.get_closed_positions(),
            "open_positions": self.get_open_positions(),
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def print_summary(self, current_price: Decimal = None):
        """打印統計摘要"""
        summary = self.get_summary(current_price)
        
        print("\n" + "="*60)
        print(f"網格交易統計 - {summary['symbol']}")
        print("="*60)
        
        print(f"\n📊 交易統計")
        print(f"  總交易數: {summary['total_trades']}")
        print(f"  買入次數: {summary['buy_trades']}")
        print(f"  賣出次數: {summary['sell_trades']}")
        print(f"  完成配對: {summary['completed_pairs']}")
        
        print(f"\n💰 盈虧統計")
        print(f"  已實現盈虧: {summary['realized_pnl']}")
        print(f"  未實現盈虧: {summary['unrealized_pnl']}")
        print(f"  總盈虧: {summary['total_pnl']}")
        
        print(f"\n🎯 績效指標")
        print(f"  勝率: {summary['win_rate']}")
        print(f"  盈利次數: {summary['winning_trades']}")
        print(f"  虧損次數: {summary['losing_trades']}")
        print(f"  平均每筆利潤: {summary['avg_profit_per_trade']}")
        print(f"  平均盈利: {summary['avg_win']}")
        print(f"  平均虧損: {summary['avg_loss']}")
        print(f"  最大盈利: {summary['max_win']}")
        print(f"  最大虧損: {summary['max_loss']}")
        
        print(f"\n💵 金額統計")
        print(f"  總買入成本: {summary['total_buy_cost']}")
        print(f"  總賣出收入: {summary['total_sell_revenue']}")
        print(f"  總手續費: {summary['total_fees']}")
        
        print(f"\n📦 持倉情況")
        print(f"  當前持倉數量: {summary['current_position_qty']}")
        print(f"  當前持倉成本: {summary['current_position_cost']}")
        print(f"  平均入場價格: {summary['avg_entry_price']}")
        print(f"  未平倉筆數: {summary['open_positions_count']}")
        
        print("="*60 + "\n")
