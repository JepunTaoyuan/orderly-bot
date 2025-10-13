#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
統一配置驗證器
集中管理所有配置驗證邏輯
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, List, Tuple
from pydantic import BaseModel, field_validator, model_validator, Field
from src.core.grid_signal import Direction, GridType
from src.utils.error_codes import GridTradingException, ErrorCode
from src.utils.logging_config import get_logger
from src.utils.market_validator import MarketValidator

logger = get_logger("config_validator")

class GridConfigValidator(BaseModel):
    """網格交易配置驗證器"""

    # 基本配置
    user_id: str = Field(..., min_length=1, description="用戶ID")
    ticker: str = Field(..., pattern=r"^[A-Z]+USDT$", description="交易對")
    direction: Direction = Field(..., description="交易方向")
    current_price: float = Field(..., gt=0, description="當前價格")
    upper_bound: float = Field(..., gt=0, description="價格上界")
    lower_bound: float = Field(..., gt=0, description="價格下界")
    grid_levels: int = Field(..., ge=2, le=100, description="網格層數")
    total_margin: float = Field(..., gt=0, description="總保證金")

    # 可選配置
    stop_bot_price: Optional[float] = Field(None, gt=0, description="停損下界")
    stop_top_price: Optional[float] = Field(None, gt=0, description="停損上界")
    
    # 網格類型配置
    grid_type: GridType = Field(GridType.ARITHMETIC, description="網格類型：等差或等比")
    grid_ratio: Optional[float] = Field(None, gt=0, lt=1, description="等比網格比例（僅等比網格需要）")

    # 簽名驗證
    user_sig: str = Field(..., min_length=1, description="用戶簽名")
    timestamp: int = Field(..., gt=0, description="時間戳")
    nonce: str = Field(..., min_length=1, description="隨機數")

    @field_validator('upper_bound', 'lower_bound', mode='before')
    def validate_bounds(cls, v):
        """驗證價格邊界"""
        if isinstance(v, str):
            try:
                v = float(v)
            except ValueError:
                raise ValueError("價格必須是數字")
        if v <= 0:
            raise ValueError("價格必須大於0")
        return v

    @field_validator('total_margin', mode='before')
    def validate_margin(cls, v):
        """驗證保證金"""
        if isinstance(v, str):
            try:
                v = float(v)
            except ValueError:
                raise ValueError("保證金必須是數字")
        if v <= 0:
            raise ValueError("保證金必須大於0")
        if v < 10:  # 最小保證金限制
            raise ValueError("保證金不能小於10 USDT")
        return v

    @field_validator('grid_levels', mode='before')
    def validate_grid_levels(cls, v):
        """驗證網格層數"""
        if isinstance(v, str):
            try:
                v = int(v)
            except ValueError:
                raise ValueError("網格層數必須是整數")
        if v < 2:
            raise ValueError("網格層數不能小於2")
        if v > 100:
            raise ValueError("網格層數不能超過100")
        return v

    @field_validator('ticker')
    def validate_ticker(cls, v):
        """驗證交易對格式"""
        if not v.endswith('USDT'):
            raise ValueError("目前只支持 USDT 交易對")
        return v

    @model_validator(mode='after')
    def validate_price_and_grid_relationships(self):
        """驗證價格關係和網格參數"""
        # 驗證停損價格
        if self.stop_bot_price is not None:
            if self.stop_bot_price >= self.lower_bound:
                raise ValueError("停損下界必須小於網格下界")
        
        if self.stop_top_price is not None:
            if self.stop_top_price <= self.upper_bound:
                raise ValueError("停損上界必須大於網格上界")
        
        # 驗證等比網格比例
        if self.grid_type == GridType.GEOMETRIC:
            if self.grid_ratio is None:
                raise ValueError("等比網格必須提供 grid_ratio 參數")
            if self.grid_ratio <= 0 or self.grid_ratio >= 1:
                raise ValueError("等比網格比例必須在 0 到 1 之間")
        elif self.grid_type == GridType.ARITHMETIC and self.grid_ratio is not None:
            # 對於算術網格，忽略 grid_ratio 而不是拋出錯誤
            self.grid_ratio = None
            
        return self

    def validate_price_relationship(self) -> bool:
        """驗證價格關係"""
        # 檢查價格邊界
        if self.lower_bound >= self.upper_bound:
            raise ValueError("價格下界必須小於上界")

        # 檢查當前價格是否在範圍內
        if not (self.lower_bound <= self.current_price <= self.upper_bound):
            raise ValueError("當前價格必須在網格範圍內")

        # 檢查價格間距合理性
        price_range = self.upper_bound - self.lower_bound
        if price_range < self.current_price * 0.01:  # 至少1%的價格範圍
            raise ValueError("網格價格範圍太小，應至少為當前價格的1%")

        # 檢查網格密度
        avg_grid_size = price_range / (self.grid_levels - 1)
        if avg_grid_size < self.current_price * 0.001:  # 網格間距太小
            raise ValueError("網格太密集，請減少網格層數或擴大價格範圍")

        return True

    def calculate_grid_parameters(self) -> Dict[str, Any]:
        """計算網格參數"""
        # 計算網格價格點
        price_range = self.upper_bound - self.lower_bound
        grid_spacing = price_range / (self.grid_levels - 1)

        # 生成網格價格列表
        grid_prices = []
        for i in range(self.grid_levels):
            price = self.lower_bound + i * grid_spacing
            grid_prices.append(price)

        # 計算每格保證金
        margin_per_grid = self.total_margin / self.grid_levels

        return {
            "grid_prices": grid_prices,
            "grid_spacing": grid_spacing,
            "margin_per_grid": margin_per_grid,
            "price_range": price_range
        }

class UserConfigValidator(BaseModel):
    """用戶配置驗證器"""

    user_id: str = Field(..., min_length=1, description="用戶ID")
    api_key: str = Field(..., min_length=10, description="API密鑰")
    api_secret: str = Field(..., min_length=10, description="API密碼")
    wallet_address: str = Field(..., min_length=10, description="錢包地址")

    @field_validator('user_id')
    def validate_user_id(cls, v):
        """驗證用戶ID"""
        if not v or len(v.strip()) == 0:
            raise ValueError("用戶ID不能為空")
        if len(v) > 100:
            raise ValueError("用戶ID長度不能超過100")
        return v.strip()

    @field_validator('api_key', 'api_secret')
    def validate_api_credentials(cls, v):
        """驗證API憑證"""
        if not v or len(v.strip()) == 0:
            raise ValueError("API憑證不能為空")
        if len(v) < 10:
            raise ValueError("API憑證長度不能小於10")
        return v.strip()

    @field_validator('wallet_address')
    def validate_wallet_address(cls, v):
        """驗證錢包地址"""
        v = v.strip()
        if not v:
            raise ValueError("錢包地址不能為空")

        # 簡單的地址格式驗證
        if v.startswith('0x'):
            # EVM 地址
            if len(v) != 42:
                raise ValueError("EVM 錢包地址長度不正確")
            try:
                int(v[2:], 16)
            except ValueError:
                raise ValueError("EVM 錢包地址格式不正確")
        else:
            # Solana 地址
            if len(v) not in [43, 44]:  # Solana 地址通常是 43-44 字符
                raise ValueError("Solana 錢包地址長度不正確")

        return v

class ConfigValidator:
    """統一配置驗證器"""

    def __init__(self):
        self.market_validator = MarketValidator()

    async def validate_grid_config(
        self,
        config: Dict[str, Any],
        market_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        驗證網格交易配置

        Args:
            config: 配置字典
            market_info: 市場信息（可選）

        Returns:
            驗證後的配置字典
        """
        try:
            # 使用 Pydantic 驗證基本配置
            validated_config = GridConfigValidator(**config)

            # 驗證價格關係
            validated_config.validate_price_relationship()

            # 獲取市場信息
            if not market_info:
                # 這裡可以從交易所 API 獲取市場信息
                # 暫時使用默認值
                market_info = self._get_default_market_info(validated_config.ticker)

            # 驗證市場限制
            self._validate_market_limits(validated_config, market_info)

            # 計算網格參數
            grid_params = validated_config.calculate_grid_parameters()

            # 構建最終配置
            final_config = {
                "user_id": validated_config.user_id,
                "ticker": validated_config.ticker,
                "direction": validated_config.direction,
                "current_price": validated_config.current_price,
                "upper_bound": validated_config.upper_bound,
                "lower_bound": validated_config.lower_bound,
                "grid_levels": validated_config.grid_levels,
                "total_margin": validated_config.total_margin,
                "grid_type": validated_config.grid_type,
                "grid_ratio": validated_config.grid_ratio,
                "stop_bot_price": validated_config.stop_bot_price,
                "stop_top_price": validated_config.stop_top_price,
                "user_sig": validated_config.user_sig,
                "timestamp": validated_config.timestamp,
                "nonce": validated_config.nonce,
                "grid_params": grid_params,
                "_market_info": market_info,
            }

            logger.info(
                f"網格配置驗證成功",
                extra={
                    "user_id": validated_config.user_id,
                    "ticker": validated_config.ticker,
                    "direction": validated_config.direction.value,
                    "grid_levels": validated_config.grid_levels
                }
            )

            return final_config

        except ValueError as e:
            logger.error(f"配置驗證失敗: {e}")
            raise GridTradingException(
                error_code=ErrorCode.INVALID_GRID_CONFIG,
                details={"validation_error": str(e)}
            )
        except Exception as e:
            logger.error(f"配置驗證異常: {e}")
            raise GridTradingException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                details={"error": str(e)}
            )

    def validate_user_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        驗證用戶配置

        Args:
            config: 用戶配置字典

        Returns:
            驗證後的配置字典
        """
        try:
            validated_config = UserConfigValidator(**config)

            final_config = {
                "user_id": validated_config.user_id,
                "api_key": validated_config.api_key,
                "api_secret": validated_config.api_secret,
                "wallet_address": validated_config.wallet_address
            }

            logger.info(f"用戶配置驗證成功: {validated_config.user_id}")

            return final_config

        except ValueError as e:
            logger.error(f"用戶配置驗證失敗: {e}")
            raise GridTradingException(
                error_code=ErrorCode.INVALID_PARAMETER,
                details={"validation_error": str(e)}
            )

    def _get_default_market_info(self, ticker: str) -> Dict[str, Any]:
        """獲取默認市場信息"""
        return {
            "symbol": ticker,
            "tick_size": Decimal("0.01"),
            "step_size": Decimal("0.0001"),
            "min_notional": Decimal("1.0"),
            "min_price": Decimal("0.01"),
            "max_price": Decimal("1000000"),
            "min_quantity": Decimal("0.0001"),
            "max_quantity": Decimal("1000")
        }

    def _validate_market_limits(self, config: GridConfigValidator, market_info: Dict[str, Any]):
        """驗證市場限制"""
        # 驗證最小訂單金額
        min_notional = market_info.get("min_notional", Decimal("1.0"))
        estimated_order_value = config.total_margin / config.grid_levels

        if Decimal(str(estimated_order_value)) < min_notional:
            raise ValueError(
                f"單格訂單金額 {estimated_order_value} 小於最小限制 {min_notional}"
            )

# 全局實例
config_validator = ConfigValidator()

# 便捷函數
async def validate_grid(config: Dict[str, Any]) -> Dict[str, Any]:
    """驗證網格配置"""
    return await config_validator.validate_grid_config(config)

def validate_user(config: Dict[str, Any]) -> Dict[str, Any]:
    """驗證用戶配置"""
    return config_validator.validate_user_config(config)