#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格訂單恢復配置
管理訂單取消檢測和恢復的配置選項
"""

from dataclasses import dataclass
from typing import Set, Dict, Any
from enum import Enum


class RestorationPolicy(Enum):
    """訂單恢復策略"""
    NEVER = "never"           # 從不恢復
    USER_ONLY = "user_only"   # 僅恢復用戶取消的訂單
    ALL = "all"               # 恢復所有取消的訂單
    SMART = "smart"           # 智能恢復（基於條件判斷）


class CancellationType(Enum):
    """訂單取消類型"""
    USER_CANCELLED = "user_cancelled"           # 用戶手動取消
    SYSTEM_CANCELLED = "system_cancelled"       # 系統取消（如保證金不足）
    EXPIRED = "expired"                         # 訂單過期
    EXTERNAL_CANCEL_DETECTED = "external_detected"  # 外部檢測到取消
    UNKNOWN = "unknown"                         # 未知原因


@dataclass
class OrderRestorationConfig:
    """訂單恢復配置"""

    # 恢復策略
    restoration_policy: RestorationPolicy = RestorationPolicy.SMART

    # 恢復條件
    max_restore_window_seconds: int = 300  # 最大恢復時間窗口（5分鐘）
    max_price_deviation_percent: float = 2.0  # 最大價格偏差（2%）

    # 取消原因映射
    cancel_reason_mapping: Dict[str, CancellationType] = None

    # 恢復控制
    max_restoration_attempts_per_hour: int = 10  # 每小時最大恢復次數
    enable_price_check: bool = True  # 是否啟用價格檢查
    enable_time_window_check: bool = True  # 是否啟用時間窗口檢查

    # 同步設置
    order_sync_interval_seconds: int = 120  # 訂單同步間隔（增加到2分鐘，降低API調用頻率）

    def __post_init__(self):
        """初始化後處理"""
        if self.cancel_reason_mapping is None:
            # 預設的取消原因映射
            self.cancel_reason_mapping = {
                # 用戶取消
                "USER_CANCELLED": CancellationType.USER_CANCELLED,
                "USER_CANCELED": CancellationType.USER_CANCELLED,
                "CANCELLED_BY_USER": CancellationType.USER_CANCELLED,
                "USER_REQUESTED_CANCEL": CancellationType.USER_CANCELLED,

                # 系統取消
                "INSUFFICIENT_MARGIN": CancellationType.SYSTEM_CANCELLED,
                "POSITION_LIMIT": CancellationType.SYSTEM_CANCELLED,
                "RISK_LIMIT": CancellationType.SYSTEM_CANCELLED,
                "ACCOUNT_SUSPENDED": CancellationType.SYSTEM_CANCELLED,

                # 過期
                "EXPIRED": CancellationType.EXPIRED,
                "TIME_IN_FORCE": CancellationType.EXPIRED,

                # 外部檢測
                "EXTERNAL_CANCEL_DETECTED": CancellationType.EXTERNAL_CANCEL_DETECTED,

                # 未知原因
                "UNKNOWN": CancellationType.UNKNOWN,
            }

    def should_restore_order(self, cancel_reason: str) -> bool:
        """根據配置判斷是否應該恢復訂單"""
        cancel_type = self.get_cancellation_type(cancel_reason)

        if self.restoration_policy == RestorationPolicy.NEVER:
            return False
        elif self.restoration_policy == RestorationPolicy.USER_ONLY:
            return cancel_type == CancellationType.USER_CANCELLED
        elif self.restoration_policy == RestorationPolicy.ALL:
            return cancel_type != CancellationType.UNKNOWN
        elif self.restoration_policy == RestorationPolicy.SMART:
            # 智能策略：僅恢復用戶取消和外部檢測到的取消
            return cancel_type in {
                CancellationType.USER_CANCELLED,
                CancellationType.EXTERNAL_CANCEL_DETECTED
            }

        return False

    def get_cancellation_type(self, cancel_reason: str) -> CancellationType:
        """獲取取消類型"""
        if not cancel_reason:
            return CancellationType.UNKNOWN

        # 直接查找
        if cancel_reason.upper() in self.cancel_reason_mapping:
            return self.cancel_reason_mapping[cancel_reason.upper()]

        # 模糊匹配
        for reason_pattern, cancel_type in self.cancel_reason_mapping.items():
            if reason_pattern.lower() in cancel_reason.lower():
                return cancel_type

        return CancellationType.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典格式"""
        return {
            "restoration_policy": self.restoration_policy.value,
            "max_restore_window_seconds": self.max_restore_window_seconds,
            "max_price_deviation_percent": self.max_price_deviation_percent,
            "cancel_reason_mapping": {k: v.value for k, v in self.cancel_reason_mapping.items()},
            "max_restoration_attempts_per_hour": self.max_restoration_attempts_per_hour,
            "enable_price_check": self.enable_price_check,
            "enable_time_window_check": self.enable_time_window_check,
            "order_sync_interval_seconds": self.order_sync_interval_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderRestorationConfig':
        """從字典創建配置"""
        # 處理取消原因映射
        cancel_reason_mapping = data.get("cancel_reason_mapping", {})
        if cancel_reason_mapping:
            cancel_reason_mapping = {
                k: CancellationType(v) for k, v in cancel_reason_mapping.items()
            }

        return cls(
            restoration_policy=RestorationPolicy(data.get("restoration_policy", "smart")),
            max_restore_window_seconds=data.get("max_restore_window_seconds", 300),
            max_price_deviation_percent=data.get("max_price_deviation_percent", 2.0),
            cancel_reason_mapping=cancel_reason_mapping,
            max_restoration_attempts_per_hour=data.get("max_restoration_attempts_per_hour", 10),
            enable_price_check=data.get("enable_price_check", True),
            enable_time_window_check=data.get("enable_time_window_check", True),
            order_sync_interval_seconds=data.get("order_sync_interval_seconds", 60),
        )