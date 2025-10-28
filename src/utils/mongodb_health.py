#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MongoDB 連接健康檢查和恢復機制
"""

import asyncio
import time
from typing import Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from src.utils.logging_config import get_logger
from src.utils.error_recovery import get_error_recovery_manager, ErrorSeverity

logger = get_logger("mongodb_health")

class MongoDBHealthMonitor:
    """MongoDB 連接健康監控器"""

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.is_monitoring = False
        self.health_check_interval = 30  # 30秒檢查一次
        self.last_health_check = 0
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3

    async def start_monitoring(self):
        """開始健康監控"""
        if self.is_monitoring:
            return

        self.is_monitoring = True
        logger.info("MongoDB 健康監控已啟動")

        # 在背景運行健康檢查
        asyncio.create_task(self._health_check_loop())

    async def stop_monitoring(self):
        """停止健康監控"""
        self.is_monitoring = False
        logger.info("MongoDB 健康監控已停止")

    async def _health_check_loop(self):
        """健康檢查循環"""
        while self.is_monitoring:
            try:
                await self.check_health()
                await asyncio.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(f"健康檢查循環錯誤: {e}")
                await asyncio.sleep(5)  # 短暫等待後重試

    async def check_health(self) -> Dict[str, Any]:
        """執行健康檢查"""
        current_time = time.time()
        self.last_health_check = current_time

        try:
            # 檢查客戶端是否存在
            if not self.db_manager.client:
                return {
                    "status": "unhealthy",
                    "reason": "客戶端未初始化",
                    "timestamp": current_time
                }

            # 執行 ping 命令
            ping_result = await self.db_manager.client.admin.command('ping')

            # 執行簡單的讀寫測試
            test_result = await self._perform_read_write_test()

            # 重置失敗計數
            if self.consecutive_failures > 0:
                logger.info(f"MongoDB 連接已恢復正常", data={
                    "previous_failures": self.consecutive_failures
                })
                self.consecutive_failures = 0

            return {
                "status": "healthy",
                "ping": ping_result,
                "test_result": test_result,
                "timestamp": current_time
            }

        except Exception as e:
            self.consecutive_failures += 1
            error_message = str(e)

            logger.warning(f"MongoDB 健康檢查失敗 ({self.consecutive_failures}/{self.max_consecutive_failures}): {error_message}")

            # 檢查是否需要觸發恢復機制
            if self.consecutive_failures >= self.max_consecutive_failures:
                await self._trigger_recovery(e)

            return {
                "status": "unhealthy",
                "error": error_message,
                "consecutive_failures": self.consecutive_failures,
                "timestamp": current_time
            }

    async def _perform_read_write_test(self) -> Dict[str, Any]:
        """執行簡單的讀寫測試"""
        try:
            test_collection = self.db_manager.db.get_collection("health_check_test")

            # 寫入測試
            test_doc = {
                "test_id": f"health_check_{int(time.time())}",
                "timestamp": time.time(),
                "type": "health_check"
            }

            insert_result = await test_collection.insert_one(test_doc)

            # 讀取測試
            found_doc = await test_collection.find_one({"_id": insert_result.inserted_id})

            # 清理測試文檔
            await test_collection.delete_one({"_id": insert_result.inserted_id})

            if found_doc and found_doc.get("test_id") == test_doc["test_id"]:
                return {"status": "success", "message": "讀寫測試通過"}
            else:
                return {"status": "failed", "message": "讀寫測試失敗：文檔不匹配"}

        except Exception as e:
            return {"status": "failed", "message": f"讀寫測試失敗: {e}"}

    async def _trigger_recovery(self, original_error: Exception):
        """觸發連接恢復機制"""
        logger.error(f"MongoDB 連接連續失敗 {self.consecutive_failures} 次，觸發恢復機制")

        try:
            # 使用錯誤恢復管理器
            recovery_manager = get_error_recovery_manager()
            await recovery_manager.handle_error(
                error=original_error,
                context={
                    "component": "mongodb_connection",
                    "consecutive_failures": self.consecutive_failures,
                    "operation": "health_monitoring"
                },
                severity=ErrorSeverity.HIGH,
                component="mongodb_health"
            )

            # 嘗試重建連接
            await self._rebuild_connection()

        except Exception as recovery_error:
            logger.error(f"MongoDB 連接恢復失敗: {recovery_error}")

    async def _rebuild_connection(self):
        """重建 MongoDB 連接"""
        try:
            logger.info("開始重建 MongoDB 連接")

            # 保存舊的連接信息
            old_connection_string = self.db_manager.connection_string

            # 關閉舊連接
            if self.db_manager.client:
                self.db_manager.client.close()

            # 重置連接狀態
            self.db_manager.client = None
            self.db_manager.db = None
            self.db_manager.mongo_manager = None

            # 重新初始化連接
            await self.db_manager.initialize(old_connection_string)

            logger.info("MongoDB 連接重建成功")

        except Exception as e:
            logger.error(f"MongoDB 連接重建失敗: {e}")
            raise

    async def get_connection_stats(self) -> Dict[str, Any]:
        """獲取連接統計信息"""
        try:
            if not self.db_manager.client:
                return {"error": "客戶端未初始化"}

            server_status = await self.db_manager.client.admin.command('serverStatus')
            connections = server_status.get('connections', {})

            return {
                "current_connections": connections.get('current'),
                "available_connections": connections.get('available'),
                "total_created": connections.get('totalCreated'),
                "health_check_interval": self.health_check_interval,
                "consecutive_failures": self.consecutive_failures,
                "last_health_check": self.last_health_check,
                "monitoring_active": self.is_monitoring
            }

        except Exception as e:
            logger.error(f"獲取連接統計失敗: {e}")
            return {"error": str(e)}

# 全局健康監控器實例
_health_monitor: Optional[MongoDBHealthMonitor] = None

def get_mongodb_health_monitor(db_manager) -> MongoDBHealthMonitor:
    """獲取 MongoDB 健康監控器實例"""
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = MongoDBHealthMonitor(db_manager)
    return _health_monitor

async def start_mongodb_health_monitoring(db_manager):
    """啟動 MongoDB 健康監控"""
    monitor = get_mongodb_health_monitor(db_manager)
    await monitor.start_monitoring()
    return monitor

async def stop_mongodb_health_monitoring():
    """停止 MongoDB 健康監控"""
    global _health_monitor
    if _health_monitor:
        await _health_monitor.stop_monitoring()