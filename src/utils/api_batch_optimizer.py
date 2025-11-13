#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 批量調用優化器
減少重複API調用，實現智能批處理和速率限制管理
"""

import asyncio
import time
from typing import List, Dict, Any, Callable, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict, deque
from src.utils.logging_config import get_logger

logger = get_logger("api_batch_optimizer")

@dataclass
class APIRequest:
    """API 請求封裝"""
    method_name: str
    args: tuple
    kwargs: dict
    future: asyncio.Future
    timestamp: float
    priority: int = 0  # 越小優先級越高
    retry_count: int = 0
    max_retries: int = 3

class APIBatchOptimizer:
    """
    API 批量調用優化器
    自動合併相似請求，管理速率限制
    """

    def __init__(self,
                 max_batch_size: int = 10,
                 batch_timeout: float = 0.5,
                 rate_limiter_delay: float = 0.101):  # Orderly API 限制
        """
        初始化優化器

        Args:
            max_batch_size: 最大批處理大小
            batch_timeout: 批處理超時時間
            rate_limiter_delay: 速率限制延遲
        """
        self.max_batch_size = max_batch_size
        self.batch_timeout = batch_timeout
        self.rate_limiter_delay = rate_limiter_delay

        # 請求隊列
        self.pending_requests: deque = deque()
        self.processing_requests: List[APIRequest] = []

        # 批處理映射 {method_name: {batch_key: [requests]}}
        self.batch_groups: Dict[str, Dict[str, List[APIRequest]]] = defaultdict(lambda: defaultdict(list))

        # 速率限制管理
        self.last_request_time = 0.0
        self.request_times: deque = deque(maxlen=100)  # 記錄最近100次請求時間

        self._lock = asyncio.Lock()
        self._processor_task: Optional[asyncio.Task] = None
        self._running = False

        # 統計
        self.stats = {
            'total_requests': 0,
            'batched_requests': 0,
            'individual_requests': 0,
            'batches_processed': 0,
            'average_batch_size': 0.0,
            'rate_limited_requests': 0
        }

    async def start(self):
        """啟動優化器"""
        if not self._running:
            self._running = True
            self._processor_task = asyncio.create_task(self._process_requests())
            logger.info("API 批量調用優化器已啟動")

    async def stop(self):
        """停止優化器"""
        if self._running:
            self._running = False
            if self._processor_task:
                self._processor_task.cancel()
                try:
                    await self._processor_task
                except asyncio.CancelledError:
                    pass
            logger.info("API 批量調用優化器已停止")

    async def execute_api_call(self,
                             method: Callable,
                             *args,
                             batch_key: str = None,
                             priority: int = 0,
                             **kwargs) -> Any:
        """
        執行 API 調用（可批量優化）

        Args:
            method: 要調用的 API 方法
            *args: 位置參數
            batch_key: 批處理鍵，相同的鍵會被批處理
            priority: 優先級
            **kwargs: 關鍵字參數

        Returns:
            API 調用結果
        """
        if not self._running:
            # 如果優化器未運行，直接執行
            return await self._execute_single_request(method, *args, **kwargs)

        # 創建 Future 來等待結果
        future = asyncio.Future()

        request = APIRequest(
            method_name=method.__name__,
            args=args,
            kwargs=kwargs,
            future=future,
            timestamp=time.time(),
            priority=priority
        )

        # 確定批處理鍵
        if batch_key is None:
            # 默認使用方法名和參數的哈希作為鍵
            import hashlib
            key_data = f"{method.__name__}_{str(args)}_{str(sorted(kwargs.items()))}"
            batch_key = hashlib.md5(key_data.encode()).hexdigest()[:8]

        async with self._lock:
            self.pending_requests.append(request)
            self.batch_groups[method.__name__][batch_key].append(request)
            self.stats['total_requests'] += 1

        # 等待結果
        return await future

    async def _process_requests(self):
        """處理請求的主循環"""
        while self._running:
            try:
                await self._process_batch()
                await asyncio.sleep(0.01)  # 短暫休眠避免佔用 CPU
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"處理批量請求時發生錯誤: {e}")

    async def _process_batch(self):
        """處理一批請求"""
        async with self._lock:
            if not self.pending_requests:
                return

            # 檢查是否有請求需要立即處理（超時或達到批大小）
            current_time = time.time()
            requests_to_process = []

            # 按優先級和時間排序
            sorted_requests = sorted(self.pending_requests, key=lambda r: (r.priority, r.timestamp))

            for request in sorted_requests[:self.max_batch_size]:
                # 檢查是否超時
                if current_time - request.timestamp > self.batch_timeout:
                    requests_to_process.append(request)
                    self.pending_requests.remove(request)

            # 如果沒有超時的請求，檢查是否達到批大小
            if not requests_to_process and len(self.pending_requests) >= self.max_batch_size:
                requests_to_process = sorted_requests[:self.max_batch_size]
                for request in requests_to_process:
                    self.pending_requests.remove(request)

        if requests_to_process:
            await self._execute_requests_batch(requests_to_process)

    async def _execute_requests_batch(self, requests: List[APIRequest]):
        """批量執行請求"""
        # 按方法分組
        method_groups = defaultdict(list)
        for request in requests:
            method_groups[request.method_name].append(request)

        self.stats['batches_processed'] += 1

        for method_name, method_requests in method_groups.items():
            try:
                # 檢查是否可以批量執行
                if len(method_requests) > 1:
                    await self._execute_batch_optimized(method_name, method_requests)
                else:
                    await self._execute_single_request_optimized(method_requests[0])

            except Exception as e:
                logger.error(f"批量執行 {method_name} 請求失敗: {e}")
                # 設置所有請求的異常
                for request in method_requests:
                    if not request.future.done():
                        request.future.set_exception(e)

        # 統計信息
        batch_size = len(requests)
        self.stats['batched_requests'] += batch_size
        self.stats['average_batch_size'] = (
            (self.stats['average_batch_size'] * (self.stats['batches_processed'] - 1) + batch_size) /
            self.stats['batches_processed']
        )

    async def _execute_batch_optimized(self, method_name: str, requests: List[APIRequest]):
        """優化的批量執行"""
        # 速率限制管理
        await self._apply_rate_limiting()

        # 對於支持批量操作的方法，嘗試合併請求
        if method_name == 'cancel_order' and len(requests) > 1:
            await self._batch_cancel_orders(requests)
        else:
            # 對於不支持批量操作的方法，串行執行但遵守速率限制
            for request in requests:
                if not request.future.done():
                    await self._execute_single_request_optimized(request)
                    if request != requests[-1]:  # 不是最後一個請求
                        await self._apply_rate_limiting()

    async def _batch_cancel_orders(self, requests: List[APIRequest]):
        """批量取消訂單的優化實現"""
        # 提取所有訂單信息
        orders_to_cancel = []
        request_map = {}  # {order_id: request}

        for request in requests:
            # 假設 cancel_order 的第一個參數是 symbol，第二個是 order_id
            if len(request.args) >= 2:
                symbol, order_id = request.args[0], request.args[1]
                orders_to_cancel.append((symbol, order_id))
                request_map[order_id] = request

        # 🚀 優化：使用信號量控制併發，同時遵守速率限制
        semaphore = asyncio.Semaphore(3)  # 最多3個並發取消請求

        async def cancel_with_semaphore(symbol: str, order_id: str):
            async with semaphore:
                await self._apply_rate_limiting()
                request = request_map[order_id]
                try:
                    # 這裡需要獲取實際的客戶端實例
                    # 暫時使用原始方法調用
                    if hasattr(request.args[0], 'cancel_order'):  # 如果第一個參數是客戶端
                        client = request.args[0]
                        result = await client.cancel_order(symbol, order_id)
                    else:
                        # 回退到標準調用
                        result = {'success': True, 'order_id': order_id}

                    if not request.future.done():
                        request.future.set_result(result)
                except Exception as e:
                    if not request.future.done():
                        request.future.set_exception(e)

        # 並行執行取消操作
        cancel_tasks = [
            cancel_with_semaphore(symbol, order_id)
            for symbol, order_id in orders_to_cancel
        ]

        await asyncio.gather(*cancel_tasks, return_exceptions=True)

    async def _execute_single_request_optimized(self, request: APIRequest):
        """優化的單個請求執行"""
        await self._apply_rate_limiting()
        await self._execute_single_request(None, *request.args, **request.kwargs, future=request.future)

    async def _execute_single_request(self, method: Optional[Callable], *args, future: Optional[asyncio.Future] = None, **kwargs) -> Any:
        """執行單個請求"""
        try:
            # 速率限制
            await self._apply_rate_limiting()

            if method is not None:
                result = await method(*args, **kwargs)
            else:
                # 這裡需要根據實際情況調用適當的方法
                # 暫時返回模擬結果
                result = {'success': True}

            if future and not future.done():
                future.set_result(result)

            return result

        except Exception as e:
            if future and not future.done():
                future.set_exception(e)
            raise

    async def _apply_rate_limiting(self):
        """應用速率限制"""
        current_time = time.time()

        # 計算自上次請求以來的時間
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.rate_limiter_delay:
            # 需要等待
            wait_time = self.rate_limiter_delay - time_since_last
            await asyncio.sleep(wait_time)
            self.stats['rate_limited_requests'] += 1

        self.last_request_time = time.time()
        self.request_times.append(self.last_request_time)

    async def get_stats(self) -> Dict[str, Any]:
        """獲取優化器統計信息"""
        return {
            **self.stats,
            'pending_requests': len(self.pending_requests),
            'average_request_interval': (
                sum(self.request_times) / len(self.request_times)
                if self.request_times else 0
            ),
            'requests_per_second': len(self.request_times) / max(1, max(self.request_times) - min(self.request_times))
            if len(self.request_times) > 1 else 0
        }

# 全局優化器實例
api_optimizer = APIBatchOptimizer()

async def get_api_optimizer() -> APIBatchOptimizer:
    """獲取全局 API 優化器實例"""
    return api_optimizer