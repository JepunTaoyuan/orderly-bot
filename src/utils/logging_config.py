#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
結構化日誌配置和指標收集
"""

import logging
import json
import time
import uuid
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
from contextvars import ContextVar
import threading

# 上下文變量用於追踪會話ID和相關ID
session_id_context: ContextVar[Optional[str]] = ContextVar('session_id', default=None)
correlation_id_context: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)

@dataclass
class LogRecord:
    """結構化日誌記錄"""
    timestamp: float
    level: str
    message: str
    session_id: Optional[str] = None
    correlation_id: Optional[str] = None
    component: Optional[str] = None
    event_type: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """轉換為字典"""
        result = asdict(self)
        # 過濾None值
        return {k: v for k, v in result.items() if v is not None}

class StructuredLogger:
    """結構化日誌器"""
    
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(name)
        
    def _create_record(self, level: str, message: str, 
                      event_type: Optional[str] = None,
                      data: Optional[Dict[str, Any]] = None) -> LogRecord:
        """創建日誌記錄"""
        return LogRecord(
            timestamp=time.time(),
            level=level,
            message=message,
            session_id=session_id_context.get(),
            correlation_id=correlation_id_context.get(),
            component=self.name,
            event_type=event_type,
            data=data
        )
    
    def info(self, message: str, event_type: Optional[str] = None, 
             data: Optional[Dict[str, Any]] = None):
        """記錄信息日誌"""
        record = self._create_record("INFO", message, event_type, data)
        self.logger.info(json.dumps(record.to_dict(), ensure_ascii=False))
    
    def warning(self, message: str, event_type: Optional[str] = None,
               data: Optional[Dict[str, Any]] = None):
        """記錄警告日誌"""
        record = self._create_record("WARNING", message, event_type, data)
        self.logger.warning(json.dumps(record.to_dict(), ensure_ascii=False))
    
    def error(self, message: str, event_type: Optional[str] = None,
             data: Optional[Dict[str, Any]] = None):
        """記錄錯誤日誌"""
        record = self._create_record("ERROR", message, event_type, data)
        self.logger.error(json.dumps(record.to_dict(), ensure_ascii=False))
    
    def debug(self, message: str, event_type: Optional[str] = None,
             data: Optional[Dict[str, Any]] = None):
        """記錄調試日誌"""
        record = self._create_record("DEBUG", message, event_type, data)
        self.logger.debug(json.dumps(record.to_dict(), ensure_ascii=False))

class MetricsCollector:
    """指標收集器"""
    
    def __init__(self):
        self._counters = defaultdict(int)
        self._gauges = defaultdict(float)
        self._histograms = defaultdict(lambda: deque(maxlen=1000))
        self._lock = threading.Lock()
    
    def increment_counter(self, name: str, value: int = 1, tags: Optional[Dict[str, str]] = None):
        """增加計數器"""
        key = self._make_key(name, tags)
        with self._lock:
            self._counters[key] += value
    
    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """設置量表值"""
        key = self._make_key(name, tags)
        with self._lock:
            self._gauges[key] = value
    
    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """記錄直方圖值"""
        key = self._make_key(name, tags)
        with self._lock:
            self._histograms[key].append(value)
    
    def _make_key(self, name: str, tags: Optional[Dict[str, str]] = None) -> str:
        """創建指標鍵"""
        if not tags:
            return name
        tag_str = ','.join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}[{tag_str}]"
    
    def get_metrics(self) -> Dict[str, Any]:
        """獲取所有指標"""
        with self._lock:
            # 計算直方圖統計數據
            histogram_stats = {}
            for key, values in self._histograms.items():
                if values:
                    values_list = list(values)
                    histogram_stats[key] = {
                        "count": len(values_list),
                        "min": min(values_list),
                        "max": max(values_list),
                        "avg": sum(values_list) / len(values_list),
                        "p50": self._percentile(values_list, 50),
                        "p95": self._percentile(values_list, 95),
                        "p99": self._percentile(values_list, 99)
                    }
            
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": histogram_stats,
                "timestamp": time.time()
            }
    
    def _percentile(self, values: list, percentile: int) -> float:
        """計算百分位數"""
        if not values:
            return 0.0
        sorted_values = sorted(values)
        k = (len(sorted_values) - 1) * percentile / 100
        f = int(k)
        c = k - f
        if f == len(sorted_values) - 1:
            return sorted_values[f]
        return sorted_values[f] * (1 - c) + sorted_values[f + 1] * c
    
    def reset(self):
        """重置所有指標"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

# 全局實例
metrics = MetricsCollector()

def get_logger(name: str) -> StructuredLogger:
    """獲取結構化日誌器"""
    return StructuredLogger(name)

def set_session_context(session_id: str, correlation_id: Optional[str] = None):
    """設置會話上下文"""
    session_id_context.set(session_id)
    if correlation_id:
        correlation_id_context.set(correlation_id)
    else:
        correlation_id_context.set(str(uuid.uuid4()))

def clear_session_context():
    """清除會話上下文"""
    session_id_context.set(None)
    correlation_id_context.set(None)

def configure_logging(level: str = "INFO", format_json: bool = True):
    """配置日誌系統"""
    # 設置日誌級別
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    # 配置根日誌器
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # 移除現有處理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 創建控制台處理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    
    if format_json:
        # JSON格式
        formatter = logging.Formatter('%(message)s')
    else:
        # 傳統格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 配置第三方庫日誌級別
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
