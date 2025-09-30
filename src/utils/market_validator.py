#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市場元數據驗證和價格/數量標準化工具
"""

import logging
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

class ValidationError(Exception):
    """驗證錯誤"""
    pass

@dataclass
class MarketInfo:
    """市場信息"""
    symbol: str
    tick_size: Decimal  # 最小價格變動
    step_size: Decimal  # 最小數量變動
    min_notional: Decimal  # 最小名義價值
    max_price: Optional[Decimal] = None
    min_price: Optional[Decimal] = None
    max_quantity: Optional[Decimal] = None
    min_quantity: Optional[Decimal] = None

class MarketValidator:
    """市場驗證器"""
    
    def __init__(self):
        # 預設市場信息（生產環境應從交易所API獲取）
        self.market_info = {
            "PERP_BTC_USDC": MarketInfo(
                symbol="PERP_BTC_USDC",
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.0001"),
                min_notional=Decimal("1.0"),
                min_price=Decimal("0.01"),
                max_price=Decimal("1000000"),
                min_quantity=Decimal("0.0001"),
                max_quantity=Decimal("1000")
            ),
            "PERP_ETH_USDC": MarketInfo(
                symbol="PERP_ETH_USDC",
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.001"),
                min_notional=Decimal("1.0"),
                min_price=Decimal("0.01"),
                max_price=Decimal("100000"),
                min_quantity=Decimal("0.001"),
                max_quantity=Decimal("100")
            ),
            "PERP_SOL_USDC": MarketInfo(
                symbol="PERP_SOL_USDC",
                tick_size=Decimal("0.001"),
                step_size=Decimal("0.01"),
                min_notional=Decimal("1.0"),
                min_price=Decimal("0.001"),
                max_price=Decimal("10000"),
                min_quantity=Decimal("0.01"),
                max_quantity=Decimal("10000")
            ),
            "PERP_NEAR_USDC": MarketInfo(
                symbol="PERP_NEAR_USDC",
                tick_size=Decimal("0.001"),
                step_size=Decimal("0.1"),
                min_notional=Decimal("1.0"),
                min_price=Decimal("0.001"),
                max_price=Decimal("1000"),
                min_quantity=Decimal("0.1"),
                max_quantity=Decimal("100000")
            ),
            "PERP_ARB_USDC": MarketInfo(
                symbol="PERP_ARB_USDC",
                tick_size=Decimal("0.0001"),
                step_size=Decimal("0.1"),
                min_notional=Decimal("1.0"),
                min_price=Decimal("0.0001"),
                max_price=Decimal("100"),
                min_quantity=Decimal("0.1"),
                max_quantity=Decimal("100000")
            ),
            "PERP_OP_USDC": MarketInfo(
                symbol="PERP_OP_USDC",
                tick_size=Decimal("0.001"),
                step_size=Decimal("0.1"),
                min_notional=Decimal("1.0"),
                min_price=Decimal("0.001"),
                max_price=Decimal("1000"),
                min_quantity=Decimal("0.1"),
                max_quantity=Decimal("100000")
            )
        }
    
    def get_market_info(self, symbol: str) -> Optional[MarketInfo]:
        """獲取市場信息"""
        return self.market_info.get(symbol)
    
    def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        驗證網格配置
        
        Args:
            config: 網格配置
            
        Returns:
            驗證後的配置
            
        Raises:
            ValidationError: 驗證失敗
        """
        ticker = config.get("ticker")
        if not ticker:
            raise ValidationError("缺少ticker")
        
        # 轉換符號
        orderly_symbol = self._convert_symbol(ticker)
        market_info = self.get_market_info(orderly_symbol)
        
        if not market_info:
            logger.warning(f"未找到 {orderly_symbol} 的市場信息，使用默認值")
            market_info = MarketInfo(
                symbol=orderly_symbol,
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.0001"),
                min_notional=Decimal("1.0")
            )
        
        # 驗證價格邊界
        current_price = Decimal(str(config.get("current_price", 0)))
        upper_bound = Decimal(str(config.get("upper_bound", 0)))
        lower_bound = Decimal(str(config.get("lower_bound", 0)))
        
        if current_price <= 0:
            raise ValidationError("當前價格必須大於0")
        
        if upper_bound <= lower_bound:
            raise ValidationError("上界必須大於下界")
        
        if not (lower_bound <= current_price <= upper_bound):
            raise ValidationError("當前價格必須在上下界範圍內")
        
        # 驗證網格數量
        grid_levels = config.get("grid_levels", 0)
        if grid_levels < 2:
            raise ValidationError("網格數量必須至少為2")
        
        # 驗證總金額
        total_amount = Decimal(str(config.get("total_amount", 0)))
        if total_amount <= 0:
            raise ValidationError("總投入金額必須大於0")
        
        # 檢查每格最小名義價值
        amount_per_grid = total_amount / grid_levels
        if amount_per_grid < market_info.min_notional:
            raise ValidationError(
                f"每格投入金額 {amount_per_grid} 小於最小名義價值 {market_info.min_notional}"
            )
        
        # 標準化價格
        config["current_price"] = float(self.normalize_price(current_price, market_info))
        config["upper_bound"] = float(self.normalize_price(upper_bound, market_info))
        config["lower_bound"] = float(self.normalize_price(lower_bound, market_info))
        
        # 添加市場信息
        config["_market_info"] = market_info
        config["_orderly_symbol"] = orderly_symbol
        
        return config
    
    def normalize_price(self, price: Decimal, market_info: MarketInfo) -> Decimal:
        """
        標準化價格到交易所精度
        
        Args:
            price: 原始價格
            market_info: 市場信息
            
        Returns:
            標準化後的價格
        """
        # 向下取整到tick_size的倍數
        normalized = (price / market_info.tick_size).quantize(
            Decimal('1'), rounding=ROUND_DOWN
        ) * market_info.tick_size
        
        # 檢查邊界
        if market_info.min_price and normalized < market_info.min_price:
            normalized = market_info.min_price
        if market_info.max_price and normalized > market_info.max_price:
            normalized = market_info.max_price
            
        return normalized
    
    def normalize_quantity(self, quantity: Decimal, market_info: MarketInfo) -> Decimal:
        """
        標準化數量到交易所精度
        
        Args:
            quantity: 原始數量
            market_info: 市場信息
            
        Returns:
            標準化後的數量
        """
        # 向下取整到step_size的倍數
        normalized = (quantity / market_info.step_size).quantize(
            Decimal('1'), rounding=ROUND_DOWN
        ) * market_info.step_size
        
        # 檢查邊界
        if market_info.min_quantity and normalized < market_info.min_quantity:
            normalized = market_info.min_quantity
        if market_info.max_quantity and normalized > market_info.max_quantity:
            normalized = market_info.max_quantity
            
        return normalized
    
    def validate_order(self, symbol: str, price: Decimal, quantity: Decimal) -> tuple[Decimal, Decimal]:
        """
        驗證並標準化訂單
        
        Args:
            symbol: 交易對符號
            price: 價格
            quantity: 數量
            
        Returns:
            (標準化價格, 標準化數量)
            
        Raises:
            ValidationError: 驗證失敗
        """
        market_info = self.get_market_info(symbol)
        if not market_info:
            raise ValidationError(f"不支持的交易對: {symbol}")
        
        # 標準化
        norm_price = self.normalize_price(price, market_info)
        norm_quantity = self.normalize_quantity(quantity, market_info)
        
        # 檢查最小名義價值
        notional = norm_price * norm_quantity
        if notional < market_info.min_notional:
            raise ValidationError(
                f"名義價值 {notional} 小於最小值 {market_info.min_notional}"
            )
        
        return norm_price, norm_quantity
    
    def _convert_symbol(self, symbol: str) -> str:
        """
        轉換符號格式

        Args:
            symbol: 原始符號 (如 BTCUSDT)

        Returns:
            Orderly格式符號 (如 PERP_BTC_USDC)
        """
        symbol_map = {
            "BTCUSDT": "PERP_BTC_USDC",
            "ETHUSDT": "PERP_ETH_USDC",
            "SOLUSDT": "PERP_SOL_USDC",
            "NEARUSDT": "PERP_NEAR_USDC",
            "ARBUSDT": "PERP_ARB_USDC",
            "OPUSDT": "PERP_OP_USDC",
            "BTCUSDC": "PERP_BTC_USDC",
            "ETHUSDC": "PERP_ETH_USDC",
            "SOLUSDC": "PERP_SOL_USDC",
            "NEARUSDC": "PERP_NEAR_USDC",
            "ARBUSDC": "PERP_ARB_USDC",
            "OPUSDC": "PERP_OP_USDC",
        }

        return symbol_map.get(symbol.upper(), symbol)
