#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
網格交易總結服務
負責網格交易會話結束時的數據保存和查詢
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from src.models.grid_summary import GridSummary, StopReason, GridSummaryFilter
from src.utils.logging_config import get_logger

logger = get_logger("grid_summary_service")


class GridSummaryService:
    """網格交易總結服務"""

    def __init__(self, database: AsyncIOMotorDatabase):
        self.db = database
        self.collection = self.db['grid_summaries']

    async def save_grid_summary(self, summary: GridSummary) -> str:
        """
        保存網格交易總結

        Args:
            summary: 網格總結數據

        Returns:
            保存的文檔ID
        """
        try:
            # 轉換為字典並添加時間戳
            summary_dict = summary.model_dump(by_alias=True)
            summary_dict['created_at'] = datetime.utcnow()

            # 插入到數據庫
            result = await self.collection.insert_one(summary_dict)

            logger.info("網格總結已保存", event_type="grid_summary_saved", data={
                "session_id": summary.session_id,
                "user_id": summary.user_id,
                "total_profit": summary.total_profit,
                "arbitrage_times": summary.arbitrage_times,
                "stop_reason": summary.stop_reason.value,
                "document_id": str(result.inserted_id)
            })

            return str(result.inserted_id)

        except Exception as e:
            logger.error("保存網格總結失敗", event_type="grid_summary_save_error", data={
                "session_id": summary.session_id,
                "error": str(e)
            })
            raise

    async def get_grid_summaries_by_user(
        self,
        user_id: str,
        filter_data: Optional[GridSummaryFilter] = None
    ) -> Dict[str, Any]:
        """
        獲取用戶的網格總結列表

        Args:
            user_id: 用戶ID
            filter_data: 查詢過濾器

        Returns:
            包含總結列表和總數的字典
        """
        try:
            # 構建查詢條件
            query = {"user_id": user_id}

            if filter_data:
                if filter_data.start_date:
                    query["end_time"] = {"$gte": filter_data.start_date}
                if filter_data.end_date:
                    if "end_time" in query:
                        query["end_time"]["$lte"] = filter_data.end_date
                    else:
                        query["end_time"] = {"$lte": filter_data.end_date}
                if filter_data.stop_reason:
                    query["stop_reason"] = filter_data.stop_reason.value

            # 獲取總數
            total_count = await self.collection.count_documents(query)

            # 執行查詢
            cursor = self.collection.find(query).sort("end_time", -1)

            # 應用分頁
            if filter_data:
                cursor = cursor.skip(filter_data.offset).limit(filter_data.limit)

            # 轉換為列表
            summaries = []
            async for doc in cursor:
                # 轉換 ObjectId 為字符串
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                summaries.append(doc)

            logger.info("查詢用戶網格總結", event_type="grid_summaries_queried", data={
                "user_id": user_id,
                "count": len(summaries),
                "total_count": total_count
            })

            return {
                "summaries": summaries,
                "total_count": total_count,
                "has_more": (filter_data.offset if filter_data else 0) + len(summaries) < total_count
            }

        except Exception as e:
            logger.error("查詢用戶網格總結失敗", event_type="grid_summaries_query_error", data={
                "user_id": user_id,
                "error": str(e)
            })
            raise

    async def get_grid_summary_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        根據會話ID獲取網格總結

        Args:
            session_id: 會話ID

        Returns:
            網格總結數據或 None
        """
        try:
            doc = await self.collection.find_one({"session_id": session_id})

            if doc:
                # 轉換 ObjectId 為字符串
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])

                logger.info("查詢會話網格總結", event_type="grid_summary_queried", data={
                    "session_id": session_id
                })

                return doc
            else:
                logger.info("未找到會話網格總結", event_type="grid_summary_not_found", data={
                    "session_id": session_id
                })

                return None

        except Exception as e:
            logger.error("查詢會話網格總結失敗", event_type="grid_summary_query_error", data={
                "session_id": session_id,
                "error": str(e)
            })
            raise

    async def get_user_statistics(self, user_id: str) -> Dict[str, Any]:
        """
        獲取用戶的網格交易統計信息

        Args:
            user_id: 用戶ID

        Returns:
            統計信息字典
        """
        try:
            # 總會話數
            total_sessions = await self.collection.count_documents({"user_id": user_id})

            if total_sessions == 0:
                return {
                    "total_sessions": 0,
                    "total_profit": 0.0,
                    "total_arbitrage_times": 0,
                    "average_profit": 0.0,
                    "success_rate": 0.0,
                    "stop_reasons": {}
                }

            # 聚合查詢統計信息
            pipeline = [
                {"$match": {"user_id": user_id}},
                {
                    "$group": {
                        "_id": None,
                        "total_profit": {"$sum": "$total_profit"},
                        "total_arbitrage_times": {"$sum": "$arbitrage_times"},
                        "avg_profit": {"$avg": "$total_profit"},
                        "max_profit": {"$max": "$total_profit"},
                        "min_profit": {"$min": "$total_profit"},
                        "total_duration": {"$sum": "$duration_seconds"}
                    }
                }
            ]

            stats = await self.collection.aggregate(pipeline).to_list(length=1)
            stats = stats[0] if stats else {}

            # 按停止原因分組統計
            stop_reasons_pipeline = [
                {"$match": {"user_id": user_id}},
                {
                    "$group": {
                        "_id": "$stop_reason",
                        "count": {"$sum": 1}
                    }
                }
            ]

            stop_reasons_raw = await self.collection.aggregate(stop_reasons_pipeline).to_list(length=10)
            stop_reasons = {item["_id"]: item["count"] for item in stop_reasons_raw}

            # 計算成功率（手動停止算成功）
            manual_stops = stop_reasons.get(StopReason.MANUAL.value, 0)
            success_rate = (manual_stops / total_sessions) * 100 if total_sessions > 0 else 0

            result = {
                "total_sessions": total_sessions,
                "total_profit": stats.get("total_profit", 0.0),
                "total_arbitrage_times": stats.get("total_arbitrage_times", 0),
                "average_profit": stats.get("avg_profit", 0.0),
                "max_profit": stats.get("max_profit", 0.0),
                "min_profit": stats.get("min_profit", 0.0),
                "total_duration_seconds": stats.get("total_duration", 0),
                "success_rate": round(success_rate, 2),
                "stop_reasons": stop_reasons
            }

            logger.info("查詢用戶統計信息", event_type="user_statistics_queried", data={
                "user_id": user_id,
                "total_sessions": total_sessions,
                "total_profit": result["total_profit"]
            })

            return result

        except Exception as e:
            logger.error("查詢用戶統計信息失敗", event_type="user_statistics_query_error", data={
                "user_id": user_id,
                "error": str(e)
            })
            raise

    async def ensure_indexes(self):
        """確保集合索引存在"""
        try:
            # 創建索引
            await self.collection.create_index("session_id", unique=True)
            await self.collection.create_index("user_id")
            await self.collection.create_index("end_time")
            await self.collection.create_index([("user_id", 1), ("end_time", -1)])
            await self.collection.create_index("stop_reason")

            logger.info("網格總結集合索引創建完成")

        except Exception as e:
            logger.error(f"創建網格總結索引失敗: {e}")
            raise

    async def delete_old_summaries(self, days: int = 90) -> int:
        """
        刪除舊的網格總結數據

        Args:
            days: 保留天數

        Returns:
            刪除的文檔數量
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            result = await self.collection.delete_many({
                "end_time": {"$lt": cutoff_date}
            })

            logger.info("刪除舊網格總結", event_type="old_summaries_deleted", data={
                "days": days,
                "deleted_count": result.deleted_count
            })

            return result.deleted_count

        except Exception as e:
            logger.error("刪除舊網格總結失敗", event_type="old_summaries_delete_error", data={
                "days": days,
                "error": str(e)
            })
            raise