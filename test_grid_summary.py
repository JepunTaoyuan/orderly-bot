#!/usr/bin/env python3
"""
網格總結功能測試腳本
"""

import asyncio
from datetime import datetime
from src.models.grid_summary import GridSummary, StopReason, GridSummaryFilter
from src.services.grid_summary_service import GridSummaryService
from src.services.database_connection import db_manager

async def test_grid_summary():
    """測試網格總結功能"""
    print("🚀 開始測試網格總結功能...")

    try:
        # 初始化數據庫連接
        print("📡 初始化數據庫連接...")
        await db_manager.initialize("mongodb://localhost:27017/test_grid_summary")
        database = await db_manager.get_database()

        # 創建網格總結服務
        print("🔧 創建網格總結服務...")
        service = GridSummaryService(database)
        await service.ensure_indexes()

        # 創建測試數據
        print("📝 創建測試數據...")
        test_summary = GridSummary.create_from_bot_data(
            session_id="test_user_PERP_ETH_USDC",
            user_id="test_user",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            profit_data={
                "total_profit": 150.75,
                "grid_profit": 120.50,
                "unpaired_profit": 30.25,
                "arbitrage_times": 8
            },
            grid_config={
                "ticker": "PERP_ETH_USDC",
                "direction": "BOTH",
                "grid_type": "ARITHMETIC",
                "grid_levels": 10,
                "upper_bound": 45000,
                "lower_bound": 40000,
                "total_margin": 1000
            },
            stop_reason=StopReason.MANUAL
        )

        # 保存網格總結
        print("💾 保存網格總結...")
        document_id = await service.save_grid_summary(test_summary)
        print(f"✅ 網格總結已保存，ID: {document_id}")

        # 測試查詢功能
        print("🔍 測試查詢功能...")

        # 1. 根據用戶ID查詢
        filter_data = GridSummaryFilter(
            user_id="test_user",
            limit=10,
            offset=0
        )
        summaries = await service.get_grid_summaries_by_user("test_user", filter_data)
        print(f"✅ 查詢用戶網格總結: 找到 {len(summaries['summaries'])} 條記錄")

        # 2. 根據會話ID查詢
        summary = await service.get_grid_summary_by_session("test_user_PERP_ETH_USDC")
        if summary:
            print(f"✅ 查詢會話總結成功: 總盈虧 {summary['total_profit']}")
        else:
            print("❌ 未找到會話總結")

        # 3. 獲取用戶統計
        stats = await service.get_user_statistics("test_user")
        print(f"✅ 用戶統計: 總會話 {stats['total_sessions']}, 總盈虧 {stats['total_profit']}")

        print("🎉 所有測試通過！網格總結功能正常工作。")

        # 清理測試數據
        print("🧹 清理測試數據...")
        await service.collection.delete_many({"user_id": "test_user"})
        print("✅ 測試數據已清理")

    except Exception as e:
        print(f"❌ 測試失敗: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 關閉數據庫連接
        await db_manager.close()
        print("📡 數據庫連接已關閉")

if __name__ == "__main__":
    asyncio.run(test_grid_summary())