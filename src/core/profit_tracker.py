#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易利潤統計模組
追蹤交易記錄、計算盈虧、統計績效
優化版本：使用累計統計而非無限增長的列表
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
class CurrentPosition:
    """當前持倉記錄（簡化版本，只保留必要資訊）"""
    buy_price: Decimal
    quantity: Decimal
    buy_cost: Decimal
    buy_timestamp: float

@dataclass 
class GridStats:
    """網格統計數據（累計版本）"""
    # 基本交易統計
    total_trades: int = 0
    buy_trades: int = 0
    sell_trades: int = 0
    
    # 套利統計（核心指標）
    arbitrage_count: int = 0  # 套利次數（每次完成買賣配對）
    total_arbitrage_profit: Decimal = Decimal('0')  # 總套利利潤
    
    # 新的收益分類統計
    grid_profit: Decimal = Decimal('0')           # 網格收益（已完成買賣配對的套利利潤）
    unpaired_profit: Decimal = Decimal('0')       # 未配對收益（未平倉持倉的浮動盈虧 + 資金費 + 手續費 + 訂單修改變動）
    total_profit: Decimal = Decimal('0')          # 總收益（前兩項相加）

    # 未配對收益的細分項目
    funding_fees: Decimal = Decimal('0')          # 資金費用收入/支出
    trading_fees: Decimal = Decimal('0')          # 交易手續費（已從realized_pnl中扣除）
    order_modification_pnl: Decimal = Decimal('0') # 訂單修改導致的盈虧變動

    # 盈虧統計（保留向後兼容）
    realized_pnl: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')
    total_pnl: Decimal = Decimal('0')

    # 內部統計（不對外顯示）
    winning_trades: int = 0
    losing_trades: int = 0

    # 金額統計
    total_buy_cost: Decimal = Decimal('0')
    total_sell_revenue: Decimal = Decimal('0')
    total_fees: Decimal = Decimal('0')

    # 網格專用統計
    capital_utilization: Decimal = Decimal('0')  # 資金利用率
    total_margin_used: Decimal = Decimal('0')     # 已使用保證金

    # 持倉統計
    current_position_qty: Decimal = Decimal('0')
    current_position_cost: Decimal = Decimal('0')
    avg_entry_price: Decimal = Decimal('0')

class ProfitTracker:
    """網格交易利潤追蹤器（記憶體優化版本）"""
    
    def __init__(self, symbol: str, fee_rate: Decimal = Decimal('0.001')):
        """
        初始化利潤追蹤器
        
        Args:
            symbol: 交易對符號
            fee_rate: 手續費率（默認 0.1%）
        """
        self.symbol = symbol
        self.fee_rate = fee_rate
        
        # 只保留當前持倉（FIFO 佇列）
        self.current_positions: List[CurrentPosition] = []
        
        # 累計統計數據
        self.stats = GridStats()

        # 資金利用率相關
        self.total_margin_allocated: Decimal = Decimal('0')  # 總分配保證金

    def set_total_margin(self, total_margin: Decimal):
        """
        設置總保證金（用於計算資金利用率）

        Args:
            total_margin: 總保證金金額
        """
        self.total_margin_allocated = total_margin
        logger.info(f"設置總保證金: {total_margin} USDT")

    def _update_capital_utilization(self):
        """更新資金利用率"""
        if self.total_margin_allocated > Decimal('0'):
            # 計算當前持倉的保證金需求
            current_position_margin = sum(pos.buy_cost for pos in self.current_positions)
            self.stats.total_margin_used = current_position_margin
            self.stats.capital_utilization = (
                (current_position_margin / self.total_margin_allocated) * Decimal('100')
            ).quantize(Decimal('0.01'))

    def add_funding_fee(self, fee: Decimal, timestamp: float = None):
        """
        添加資金費用記錄

        Args:
            fee: 資金費用（正數為收入，負數為支出）
            timestamp: 時間戳（可選）
        """
        if timestamp is None:
            timestamp = datetime.now().timestamp()

        self.stats.funding_fees += fee
        logger.info(f"添加資金費用: {fee} USDT")

    def add_order_modification_pnl(self, pnl: Decimal, timestamp: float = None):
        """
        添加訂單修改導致的盈虧變動

        Args:
            pnl: 盈虧變動（正數為收益，負數為損失）
            timestamp: 時間戳（可選）
        """
        if timestamp is None:
            timestamp = datetime.now().timestamp()

        self.stats.order_modification_pnl += pnl
        logger.info(f"添加訂單修改盈虧: {pnl} USDT")

    def add_trade(self, side: OrderSide, price: Decimal, quantity: Decimal, 
                  timestamp: float = None, fee: Decimal = None) -> Dict:
        """
        添加交易記錄
        
        Args:
            side: 買入或賣出
            price: 成交價格
            quantity: 成交數量
            timestamp: 時間戳（可選）
            fee: 手續費（可選，不提供則自動計算）
            
        Returns:
            Dict: 交易結果摘要
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
        
        # 更新基本統計
        self.stats.total_trades += 1
        self.stats.total_fees += fee
        
        if side == OrderSide.BUY:
            self.stats.buy_trades += 1
            self.stats.total_buy_cost += cost
        else:
            self.stats.sell_trades += 1
            self.stats.total_sell_revenue += cost
        
        # 處理持倉和套利計算
        arbitrage_info = self._process_position(side, price, quantity, cost, timestamp)

        # 更新統計
        self._update_stats()

        # 更新資金利用率
        self._update_capital_utilization()
        
        logger.info(f"添加交易記錄: {side.value} {quantity} @ {price}, 成本/收入: {cost}")
        
        return {
            "side": side.value,
            "price": float(price),
            "quantity": float(quantity),
            "cost": float(cost),
            "fee": float(fee),
            "arbitrage_info": arbitrage_info
        }
    
    def _process_position(self, side: OrderSide, price: Decimal, quantity: Decimal, 
                         cost: Decimal, timestamp: float) -> Dict:
        """處理持倉和套利計算"""
        arbitrage_info = {"arbitrage_occurred": False, "arbitrage_profit": Decimal('0')}
        
        if side == OrderSide.BUY:
            # 買入：添加到當前持倉
            position = CurrentPosition(
                buy_price=price,
                quantity=quantity,
                buy_cost=cost,
                buy_timestamp=timestamp
            )
            self.current_positions.append(position)
        
        else:  # SELL
            # 賣出：配對最早的買單（FIFO）進行套利計算
            remaining_qty = quantity
            total_revenue = cost  # 已扣除手續費
            total_arbitrage_profit = Decimal('0')
            
            while remaining_qty > Decimal('0') and self.current_positions:
                position = self.current_positions[0]
                
                if position.quantity <= remaining_qty:
                    # 完全賣出這個持倉
                    matched_qty = position.quantity
                    
                    # 計算這部分的收入（按比例）
                    revenue_ratio = matched_qty / quantity
                    matched_revenue = total_revenue * revenue_ratio
                    
                    # 計算套利利潤
                    arbitrage_profit = matched_revenue - position.buy_cost
                    total_arbitrage_profit += arbitrage_profit
                    
                    # 更新統計
                    self.stats.arbitrage_count += 1
                    self.stats.total_arbitrage_profit += arbitrage_profit
                    self.stats.realized_pnl += arbitrage_profit
                    # 更新新的收益分類：网格收益
                    self.stats.grid_profit += arbitrage_profit
                    
                    # 移除已完全賣出的持倉
                    self.current_positions.pop(0)
                    remaining_qty -= matched_qty
                
                else:
                    # 部分賣出持倉
                    matched_qty = remaining_qty
                    
                    # 計算這部分的收入和成本
                    revenue_ratio = matched_qty / quantity
                    matched_revenue = total_revenue * revenue_ratio
                    
                    cost_ratio = matched_qty / position.quantity
                    matched_cost = position.buy_cost * cost_ratio
                    
                    # 計算套利利潤
                    arbitrage_profit = matched_revenue - matched_cost
                    total_arbitrage_profit += arbitrage_profit
                    
                    # 更新統計
                    self.stats.arbitrage_count += 1
                    self.stats.total_arbitrage_profit += arbitrage_profit
                    self.stats.realized_pnl += arbitrage_profit
                    # 更新新的收益分類：网格收益
                    self.stats.grid_profit += arbitrage_profit
                    
                    # 更新原持倉（減少數量和成本）
                    position.quantity -= matched_qty
                    position.buy_cost -= matched_cost
                    
                    remaining_qty = Decimal('0')
            
            if total_arbitrage_profit != Decimal('0'):
                arbitrage_info = {
                    "arbitrage_occurred": True,
                    "arbitrage_profit": float(total_arbitrage_profit)
                }
        
        return arbitrage_info
    
    def _update_stats(self):
        """更新統計數據"""
        # 當前持倉統計
        self.stats.current_position_qty = sum(pos.quantity for pos in self.current_positions)
        self.stats.current_position_cost = sum(pos.buy_cost for pos in self.current_positions)

        if self.stats.current_position_qty > 0:
            self.stats.avg_entry_price = (
                self.stats.current_position_cost / self.stats.current_position_qty
            ).quantize(Decimal('0.01'))
        else:
            self.stats.avg_entry_price = Decimal('0')

        # 總盈虧（向後兼容）
        self.stats.total_pnl = self.stats.realized_pnl + self.stats.unrealized_pnl

        # 計算未配對收益 = 未實現盈虧 - 交易手續費 + 資金費 + 訂單修改盈虧
        # 注意：交易手續費是成本，所以用減法
        self.stats.unpaired_profit = (
            self.stats.unrealized_pnl
            - self.stats.total_fees
            + self.stats.funding_fees
            + self.stats.order_modification_pnl
        )

        # 總收益 = 網格收益 + 未配對收益
        self.stats.total_profit = self.stats.grid_profit + self.stats.unpaired_profit
    
    def calculate_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """
        計算未實現盈虧
        
        Args:
            current_price: 當前市場價格
            
        Returns:
            未實現盈虧
        """
        unrealized = Decimal('0')
        
        for position in self.current_positions:
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
            
            # 套利統計（核心指標）
            "arbitrage_count": self.stats.arbitrage_count,
            "total_arbitrage_profit": f"{self.stats.total_arbitrage_profit:.2f} USDT",
            
            # 新的收益分類統計
            "grid_profit": f"{self.stats.grid_profit:.2f} USDT",
            "unpaired_profit": f"{self.stats.unpaired_profit:.2f} USDT",
            "total_profit": f"{self.stats.total_profit:.2f} USDT",

            # 未配對收益的細分
            "funding_fees": f"{self.stats.funding_fees:.2f} USDT",
            "trading_fees": f"{self.stats.total_fees:.2f} USDT",
            "order_modification_pnl": f"{self.stats.order_modification_pnl:.2f} USDT",

            # 盈虧統計（向後兼容）
            "realized_pnl": f"{self.stats.realized_pnl:.2f} USDT",
            "unrealized_pnl": f"{self.stats.unrealized_pnl:.2f} USDT",
            "total_pnl": f"{self.stats.total_pnl:.2f} USDT",

            # 資金利用率統計
            "capital_utilization": f"{self.stats.capital_utilization:.2f}%",
            "total_margin_used": f"{self.stats.total_margin_used:.2f} USDT",
            
            # 金額統計
            "total_buy_cost": f"{self.stats.total_buy_cost:.2f} USDT",
            "total_sell_revenue": f"{self.stats.total_sell_revenue:.2f} USDT",
            "total_fees": f"{self.stats.total_fees:.2f} USDT",
            
            # 持倉統計
            "current_position_qty": f"{self.stats.current_position_qty}",
            "current_position_cost": f"{self.stats.current_position_cost:.2f} USDT",
            "avg_entry_price": f"{self.stats.avg_entry_price:.2f} USDT",
            "open_positions_count": len(self.current_positions),
        }
    
    def get_current_positions(self) -> List[Dict]:
        """獲取當前持倉記錄"""
        return [
            {
                "buy_time": datetime.fromtimestamp(pos.buy_timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "buy_price": f"{pos.buy_price:.2f}",
                "quantity": f"{pos.quantity:.6f}",
                "buy_cost": f"{pos.buy_cost:.2f}",
            }
            for pos in self.current_positions
        ]
    
    def get_stats_summary(self) -> Dict:
        """獲取統計摘要（不包含歷史記錄）"""
        return {
            "arbitrage_statistics": {
                "total_arbitrage_count": self.stats.arbitrage_count,
                "total_arbitrage_profit": f"{self.stats.total_arbitrage_profit:.2f} USDT",
            },
            "trading_statistics": {
                "total_trades": self.stats.total_trades,
                "buy_trades": self.stats.buy_trades,
                "sell_trades": self.stats.sell_trades,
            },
            "capital_statistics": {
                "capital_utilization": f"{self.stats.capital_utilization:.2f}%",
                "total_margin_used": f"{self.stats.total_margin_used:.2f} USDT",
            },
            "profit_breakdown": {
                "grid_profit": f"{self.stats.grid_profit:.2f} USDT",
                "unpaired_profit": f"{self.stats.unpaired_profit:.2f} USDT",
                "total_profit": f"{self.stats.total_profit:.2f} USDT",
            },
            "unpaired_profit_details": {
                "funding_fees": f"{self.stats.funding_fees:.2f} USDT",
                "trading_fees": f"{self.stats.total_fees:.2f} USDT",
                "order_modification_pnl": f"{self.stats.order_modification_pnl:.2f} USDT",
            },
            "pnl_statistics": {
                "realized_pnl": f"{self.stats.realized_pnl:.2f} USDT",
                "unrealized_pnl": f"{self.stats.unrealized_pnl:.2f} USDT",
                "total_pnl": f"{self.stats.total_pnl:.2f} USDT",
            },
            "position_statistics": {
                "current_positions": len(self.current_positions),
                "current_position_qty": f"{self.stats.current_position_qty}",
                "current_position_cost": f"{self.stats.current_position_cost:.2f} USDT",
                "avg_entry_price": f"{self.stats.avg_entry_price:.2f} USDT",
            }
        }
    
    def export_stats_to_json(self, filepath: str):
        """導出統計數據到 JSON 文件（不包含歷史記錄）"""
        data = {
            "summary": self.get_summary(),
            "detailed_stats": self.get_stats_summary(),
            "current_positions": self.get_current_positions(),
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def print_summary(self, current_price: Decimal = None):
        """打印統計摘要"""
        summary = self.get_summary(current_price)
        
        print("\n" + "="*60)
        print(f"網格交易統計 - {summary['symbol']} (記憶體優化版)")
        print("="*60)
        
        print(f"\n📊 交易統計")
        print(f"  總交易數: {summary['total_trades']}")
        print(f"  買入次數: {summary['buy_trades']}")
        print(f"  賣出次數: {summary['sell_trades']}")
        
        print(f"\n🔄 套利統計")
        print(f"  套利次數: {summary['arbitrage_count']}")
        print(f"  總套利利潤: {summary['total_arbitrage_profit']}")
        
        print(f"\n💰 收益分類統計")
        print(f"  網格收益: {summary['grid_profit']}")
        print(f"  未配對收益: {summary['unpaired_profit']}")
        print(f"  總收益: {summary['total_profit']}")

        print(f"\n📊 未配對收益細分")
        print(f"  資金費用: {summary['funding_fees']}")
        print(f"  交易手續費: {summary['trading_fees']}")
        print(f"  訂單修改變動: {summary['order_modification_pnl']}")

        print(f"\n💰 盈虧統計（向後兼容）")
        print(f"  已實現盈虧: {summary['realized_pnl']}")
        print(f"  未實現盈虧: {summary['unrealized_pnl']}")
        print(f"  總盈虧: {summary['total_pnl']}")
        
        print(f"\n💰 資金統計")
        print(f"  資金利用率: {summary['capital_utilization']}")
        print(f"  已使用保證金: {summary['total_margin_used']}")
        
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

    # 為了向後兼容，保留一些舊方法但返回空列表或提示
    def get_trade_history(self, limit: int = None) -> List[Dict]:
        """獲取交易歷史（記憶體優化版本不保存歷史記錄）"""
        logger.warning("記憶體優化版本不保存交易歷史記錄，請使用 get_stats_summary() 獲取統計資料")
        return []
    
    def get_closed_positions(self, limit: int = None) -> List[Dict]:
        """獲取已平倉記錄（記憶體優化版本不保存歷史記錄）"""
        logger.warning("記憶體優化版本不保存已平倉記錄，請使用 get_stats_summary() 獲取統計資料")
        return []
    
    def get_open_positions(self) -> List[Dict]:
        """獲取未平倉記錄（重定向到 get_current_positions）"""
        return self.get_current_positions()
    
    def export_to_json(self, filepath: str):
        """導出統計數據到 JSON 文件（重定向到 export_stats_to_json）"""
        self.export_stats_to_json(filepath)
