#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生產環境壓力測試
測試系統在高負載下的表現
"""

import asyncio
import time
import aiohttp
import json
import random
from typing import List, Dict, Any
from dataclasses import dataclass
import logging

# 配置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class TestConfig:
    """測試配置"""
    base_url: str = "http://localhost:8000"
    max_concurrent_requests: int = 50
    test_duration: int = 300  # 5分鐘
    session_creation_rate: int = 5  # 每秒創建的session數
    max_sessions: int = 100

class StressTestSuite:
    """壓力測試套件"""

    def __init__(self, config: TestConfig):
        self.config = config
        self.session = aiohttp.ClientSession()
        self.results = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'created_sessions': 0,
            'errors': []
        }
        self.active_sessions: List[str] = []

    async def cleanup(self):
        """清理資源"""
        await self.session.close()

    async def health_check(self) -> bool:
        """健康檢查"""
        try:
            async with self.session.get(f"{self.config.base_url}/system/health", timeout=10) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"健康檢查失敗: {e}")
            return False

    async def create_session(self, user_id: str) -> Dict[str, Any]:
        """創建測試 session"""
        try:
            # 使用測試用戶憑證（需要預先在數據庫中創建）
            test_config = {
                "user_id": user_id,
                "ticker": "PERP_BTC_USDC",
                "direction": "LONG",
                "upper_bound": 50000,
                "lower_bound": 40000,
                "grid_levels": 10,
                "total_margin": 1000,
                "grid_type": "ARITHMETIC"
            }

            async with self.session.post(
                f"{self.config.base_url}/grid/start",
                json=test_config,
                headers={"Content-Type": "application/json"},
                timeout=30
            ) as response:
                self.results['total_requests'] += 1

                if response.status == 200:
                    self.results['successful_requests'] += 1
                    self.results['created_sessions'] += 1
                    result = await response.json()
                    session_id = result.get('session_id')
                    if session_id:
                        self.active_sessions.append(session_id)
                    return {"success": True, "session_id": session_id}
                else:
                    self.results['failed_requests'] += 1
                    error_text = await response.text()
                    error = f"HTTP {response.status}: {error_text}"
                    self.results['errors'].append(error)
                    logger.error(f"創建 session 失敗: {error}")
                    return {"success": False, "error": error}

        except Exception as e:
            self.results['failed_requests'] += 1
            error_msg = f"創建 session 異常: {e}"
            self.results['errors'].append(error_msg)
            logger.error(error_msg)
            return {"success": False, "error": str(e)}

    async def stop_session(self, session_id: str) -> bool:
        """停止 session"""
        try:
            async with self.session.post(
                f"{self.config.base_url}/grid/stop",
                json={"session_id": session_id},
                headers={"Content-Type": "application/json"},
                timeout=30
            ) as response:
                self.results['total_requests'] += 1

                if response.status == 200:
                    self.results['successful_requests'] += 1
                    if session_id in self.active_sessions:
                        self.active_sessions.remove(session_id)
                    return True
                else:
                    self.results['failed_requests'] += 1
                    return False

        except Exception as e:
            self.results['failed_requests'] += 1
            logger.error(f"停止 session {session_id} 失敗: {e}")
            return False

    async def get_system_metrics(self) -> Dict[str, Any]:
        """獲取系統指標"""
        try:
            async with self.session.get(
                f"{self.config.base_url}/system/metrics",
                timeout=10
            ) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"獲取系統指標失敗: {e}")
        return {}

    async def test_concurrent_session_creation(self):
        """測試並發 session 創建"""
        logger.info("開始並發 session 創建測試")

        start_time = time.time()
        created_count = 0
        failed_count = 0

        # 創建多個並發 session
        tasks = []
        for i in range(min(self.config.max_sessions, 50)):  # 先測試50個
            user_id = f"test_user_{i}_{int(time.time())}"
            task = asyncio.create_task(self.create_session(user_id))
            tasks.append(task)

        # 等待所有任務完成
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                failed_count += 1
                logger.error(f"創建 session 異常: {result}")
            elif result and result.get('success'):
                created_count += 1
            else:
                failed_count += 1

        duration = time.time() - start_time
        logger.info(f"並發創建測試完成: {created_count} 成功, {failed_count} 失敗, 耗時 {duration:.2f}s")

        return created_count, failed_count

    async def test_sustained_load(self):
        """測試持續負載"""
        logger.info(f"開始持續負載測試，持續 {self.config.test_duration}s")

        start_time = time.time()
        end_time = start_time + self.config.test_duration
        user_counter = 0

        # 持續創建和停止 session
        while time.time() < end_time:
            try:
                # 創建新 session
                user_id = f"load_test_user_{user_counter}_{int(time.time())}"
                user_counter += 1

                # 並發創建多個 session
                batch_size = min(self.config.session_creation_rate,
                               self.config.max_sessions - len(self.active_sessions))

                if batch_size > 0:
                    create_tasks = []
                    for i in range(batch_size):
                        test_user_id = f"{user_id}_{i}"
                        create_tasks.append(self.create_session(test_user_id))

                    create_results = await asyncio.gather(*create_tasks, return_exceptions=True)

                    # 等待一段時間
                    await asyncio.sleep(1.0)

                    # 隨機停止一些 session
                    if len(self.active_sessions) > 10:
                        stop_count = random.randint(1, min(3, len(self.active_sessions)))
                        sessions_to_stop = random.sample(self.active_sessions, stop_count)

                        stop_tasks = [self.stop_session(sid) for sid in sessions_to_stop]
                        await asyncio.gather(*stop_tasks, return_exceptions=True)

                # 獲取系統指標
                if user_counter % 10 == 0:  # 每10次檢查一次
                    metrics = await self.get_system_metrics()
                    if metrics:
                        cpu = metrics.get('system', {}).get('cpu_percent', 0)
                        memory = metrics.get('system', {}).get('memory_percent', 0)
                        active_sessions = metrics.get('application', {}).get('active_sessions', 0)

                        logger.info(f"系統狀態 - CPU: {cpu:.1f}%, 記憶體: {memory:.1f}%, 活躍 Sessions: {active_sessions}")

                        # 檢查是否超過閾值
                        if cpu > 90 or memory > 90:
                            logger.warning(f"系統資源使用率過高: CPU {cpu}%, 記憶體 {memory}%")
                            break

            except Exception as e:
                logger.error(f"持續負載測試異常: {e}")
                break

        total_duration = time.time() - start_time
        logger.info(f"持續負載測試完成，總耗時 {total_duration:.2f}s")

    async def test_system_monitoring(self):
        """測試系統監控功能"""
        logger.info("測試系統監控功能")

        # 測試健康檢查
        health_ok = await self.health_check()
        logger.info(f"健康檢查: {'✅ 通過' if health_ok else '❌ 失敗'}")

        # 測試系統指標獲取
        metrics = await self.get_system_metrics()
        if metrics:
            logger.info("✅ 系統指標獲取成功")
            logger.info(f"  - CPU: {metrics.get('system', {}).get('cpu_percent', 'N/A')}%")
            logger.info(f"  - 記憶體: {metrics.get('system', {}).get('memory_percent', 'N/A')}%")
            logger.info(f"  - 活躍 Sessions: {metrics.get('application', {}).get('active_sessions', 'N/A')}")
        else:
            logger.error("❌ 系統指標獲取失敗")

        # 測試垃圾回收
        try:
            async with self.session.post(
                f"{self.config.base_url}/system/gc",
                timeout=10
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info("✅ 垃圾回收測試成功")
                    logger.info(f"  - 回收對象數: {result.get('data', {}).get('objects_collected', 'N/A')}")
                    logger.info(f"  - 釋放記憶體: {result.get('data', {}).get('memory_freed_mb', 'N/A')} MB")
                else:
                    logger.error(f"❌ 垃圾回收測試失敗: HTTP {response.status}")
        except Exception as e:
            logger.error(f"❌ 垃圾回收測試異常: {e}")

    async def cleanup_test_sessions(self):
        """清理測試創建的 sessions"""
        logger.info("清理測試 sessions")

        if self.active_sessions:
            stop_tasks = [self.stop_session(sid) for sid in self.active_sessions]
            results = await asyncio.gather(*stop_tasks, return_exceptions=True)

            stopped_count = sum(1 for r in results if r is True)
            logger.info(f"清理完成: {stopped_count}/{len(self.active_sessions)} 個 sessions 已停止")

    async def run_full_test_suite(self):
        """運行完整測試套件"""
        logger.info("🚀 開始生產環境壓力測試")
        logger.info(f"測試配置:")
        logger.info(f"  - 並發請求數: {self.config.max_concurrent_requests}")
        logger.info(f"  - 測試時長: {self.config.test_duration}s")
        logger.info(f"  - 最大 Sessions: {self.config.max_sessions}")
        logger.info(f"  - 創建速率: {self.config.session_creation_rate}/s")

        try:
            # 1. 健康檢查
            if not await self.health_check():
                logger.error("❌ 系統健康檢查失敗，停止測試")
                return False

            # 2. 系統監控測試
            await self.test_system_monitoring()

            # 3. 並發創建測試
            await self.test_concurrent_session_creation()

            # 4. 持續負載測試
            await self.test_sustained_load()

            # 5. 清理測試數據
            await self.cleanup_test_sessions()

            # 6. 最終健康檢查
            final_health = await self.health_check()
            logger.info(f"最終健康檢查: {'✅ 通過' if final_health else '❌ 失敗'}")

            # 7. 輸出測試結果
            self.print_test_results()

            return final_health

        except Exception as e:
            logger.error(f"測試過程中發生異常: {e}")
            return False

    def print_test_results(self):
        """打印測試結果"""
        logger.info("\n" + "="*60)
        logger.info("📊 壓力測試結果摘要")
        logger.info("="*60)
        logger.info(f"📈 總請求數: {self.results['total_requests']}")
        logger.info(f"✅ 成功請求: {self.results['successful_requests']}")
        logger.info(f"❌ 失敗請求: {self.results['failed_requests']}")
        logger.info(f"🚀 創建的 Sessions: {self.results['created_sessions']}")

        if self.results['total_requests'] > 0:
            success_rate = (self.results['successful_requests'] / self.results['total_requests']) * 100
            logger.info(f"📊 成功率: {success_rate:.2f}%")

        if self.results['errors']:
            logger.info(f"\n🔍 錯誤詳情:")
            for i, error in enumerate(self.results['errors'][:10]):  # 只顯示前10個錯誤
                logger.info(f"  {i+1}. {error}")
            if len(self.results['errors']) > 10:
                logger.info(f"  ... 還有 {len(self.results['errors']) - 10} 個錯誤")

        logger.info("="*60 + "\n")

async def main():
    """主函數"""
    # 測試配置
    config = TestConfig(
        max_concurrent_requests=20,  # 可以根據系統性能調整
        test_duration=180,  # 3分鐘
        session_creation_rate=3,  # 每秒3個
        max_sessions=50
    )

    # 創建測試套件
    test_suite = StressTestSuite(config)

    try:
        # 運行測試
        success = await test_suite.run_full_test_suite()

        if success:
            logger.info("🎉 壓力測試通過！系統可以投入生產環境")
        else:
            logger.error("⚠️  壓力測試失敗！請檢查系統配置和性能")

    finally:
        # 清理資源
        await test_suite.cleanup()

if __name__ == "__main__":
    asyncio.run(main())