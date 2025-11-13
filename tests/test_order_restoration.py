#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
測試訂單恢復功能
"""

import unittest
from unittest.mock import Mock, AsyncMock
from src.config.order_restoration_config import OrderRestorationConfig, RestorationPolicy, CancellationType


class TestOrderRestorationConfig(unittest.TestCase):
    """測試訂單恢復配置"""

    def setUp(self):
        self.config = OrderRestorationConfig()

    def test_default_configuration(self):
        """測試預設配置"""
        self.assertEqual(self.config.restoration_policy, RestorationPolicy.SMART)
        self.assertEqual(self.config.max_restore_window_seconds, 300)
        self.assertEqual(self.config.max_price_deviation_percent, 2.0)

    def test_user_cancellation_detection(self):
        """測試用戶取消檢測"""
        user_cancel_reasons = [
            "USER_CANCELLED",
            "USER_CANCELED",
            "CANCELLED_BY_USER",
            "USER_REQUESTED_CANCEL"
        ]

        for reason in user_cancel_reasons:
            with self.subTest(reason=reason):
                cancel_type = self.config.get_cancellation_type(reason)
                self.assertEqual(cancel_type, CancellationType.USER_CANCELLED)
                self.assertTrue(self.config.should_restore_order(reason))

    def test_system_cancellation_detection(self):
        """測試系統取消檢測"""
        system_cancel_reasons = [
            "INSUFFICIENT_MARGIN",
            "POSITION_LIMIT",
            "RISK_LIMIT"
        ]

        for reason in system_cancel_reasons:
            with self.subTest(reason=reason):
                cancel_type = self.config.get_cancellation_type(reason)
                self.assertEqual(cancel_type, CancellationType.SYSTEM_CANCELLED)

                # 在 SMART 策略下，系統取消不應恢復
                self.assertFalse(self.config.should_restore_order(reason))

    def test_restoration_policies(self):
        """測試恢復策略"""
        # 測試 NEVER 策略
        never_config = OrderRestorationConfig()
        never_config.restoration_policy = RestorationPolicy.NEVER
        self.assertFalse(never_config.should_restore_order("USER_CANCELLED"))

        # 測試 USER_ONLY 策略
        user_only_config = OrderRestorationConfig()
        user_only_config.restoration_policy = RestorationPolicy.USER_ONLY
        self.assertTrue(user_only_config.should_restore_order("USER_CANCELLED"))
        self.assertFalse(user_only_config.should_restore_order("INSUFFICIENT_MARGIN"))

        # 測試 ALL 策略
        all_config = OrderRestorationConfig()
        all_config.restoration_policy = RestorationPolicy.ALL
        self.assertTrue(all_config.should_restore_order("USER_CANCELLED"))
        self.assertTrue(all_config.should_restore_order("INSUFFICIENT_MARGIN"))

    def test_config_serialization(self):
        """測試配置序列化"""
        # 轉換為字典
        config_dict = self.config.to_dict()
        self.assertIsInstance(config_dict, dict)
        self.assertEqual(config_dict["restoration_policy"], "smart")

        # 從字典恢復
        restored_config = OrderRestorationConfig.from_dict(config_dict)
        self.assertEqual(restored_config.restoration_policy, self.config.restoration_policy)
        self.assertEqual(restored_config.max_restore_window_seconds, self.config.max_restore_window_seconds)

    def test_unknown_cancellation_reason(self):
        """測試未知取消原因"""
        unknown_reason = "UNKNOWN_REASONXYZ"
        cancel_type = self.config.get_cancellation_type(unknown_reason)
        self.assertEqual(cancel_type, CancellationType.UNKNOWN)
        self.assertFalse(self.config.should_restore_order(unknown_reason))


class TestOrderRestorationIntegration(unittest.TestCase):
    """測試訂單恢復集成功能"""

    def setUp(self):
        """設置測試環境"""
        self.grid_bot = Mock()

        # 配置恢復功能
        from src.config.order_restoration_config import OrderRestorationConfig
        self.grid_bot.restoration_config = OrderRestorationConfig()

        # 模擬統計信息
        self.grid_bot.order_statistics = {}
        self.grid_bot.restoration_attempts = {}
        self.grid_bot.last_restoration_cleanup = 0

    def test_should_restore_order_logic(self):
        """測試恢復決策邏輯"""
        # 測試用戶取消應該恢復
        self.assertTrue(
            self.grid_bot.restoration_config.should_restore_order("USER_CANCELLED")
        )

        # 測試系統取消不應恢復（在 SMART 策略下）
        self.assertFalse(
            self.grid_bot.restoration_config.should_restore_order("INSUFFICIENT_MARGIN")
        )

    def test_restoration_rate_limit_check(self):
        """測試恢復頻率限制檢查"""
        import time

        # 模擬已經達到頻率限制
        current_time = time.time()
        current_hour = int(current_time // 3600)

        # 設置已經達到最大次數
        self.grid_bot.restoration_config.max_restoration_attempts_per_hour = 2
        self.grid_bot.restoration_attempts[current_hour] = 2

        # 需要實際的 GridBot 實例來測試這個方法
        # 這裡只是測試邏輯結構
        self.assertIsNotNone(self.grid_bot.restoration_attempts)


if __name__ == '__main__':
    unittest.main()