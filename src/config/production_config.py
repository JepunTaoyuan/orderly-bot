#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生產環境配置管理
集中管理所有生產環境參數
"""

import os
from typing import Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# 加載環境變數
load_dotenv()

@dataclass
class SystemConfig:
    """系統配置"""
    # 資源限制
    max_concurrent_sessions: int = int(os.getenv("MAX_CONCURRENT_SESSIONS", "10"))
    max_sessions_per_second: int = int(os.getenv("MAX_SESSIONS_PER_SECOND", "20"))
    max_websocket_connections: int = int(os.getenv("MAX_WEBSOCKET_CONNECTIONS", "100"))
    max_queue_size: int = int(os.getenv("MAX_QUEUE_SIZE", "5000"))

    # 警告閾值
    cpu_warning_threshold: float = float(os.getenv("SYSTEM_CPU_WARNING_THRESHOLD", "70.0"))
    memory_warning_threshold: float = float(os.getenv("SYSTEM_MEMORY_WARNING_THRESHOLD", "80.0"))
    disk_warning_threshold: float = float(os.getenv("SYSTEM_DISK_WARNING_THRESHOLD", "85.0"))

    # 監控間隔
    monitoring_interval: float = float(os.getenv("MONITORING_INTERVAL", "30.0"))
    cleanup_interval: float = float(os.getenv("CLEANUP_INTERVAL", "60.0"))

@dataclass
class WebSocketConfig:
    """WebSocket 配置"""
    max_connections: int = int(os.getenv("WEBSOCKET_MAX_CONNECTIONS", "100"))
    connection_timeout: float = float(os.getenv("WEBSOCKET_CONNECTION_TIMEOUT", "300"))
    heartbeat_interval: float = 30.0
    reconnect_attempts: int = 5
    reconnect_delay: float = 5.0

@dataclass
class DatabaseConfig:
    """數據庫配置"""
    connection_string: str = os.getenv("MONGODB_URI", "")
    max_pool_size: int = 100
    min_pool_size: int = 20
    max_idle_time_ms: int = 30000
    server_selection_timeout_ms: int = 3000

@dataclass
class SecurityConfig:
    """安全配置"""
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "")
    api_secret_key: str = os.getenv("API_SECRET_KEY", "")
    session_timeout: int = 3600  # 1小時
    max_login_attempts: int = 5
    lockout_duration: int = 900  # 15分鐘

class ProductionConfig:
    """生產環境配置管理器"""

    def __init__(self):
        self.system = SystemConfig()
        self.websocket = WebSocketConfig()
        self.database = DatabaseConfig()
        self.security = SecurityConfig()

        # 驗證配置
        self._validate_config()

    def _validate_config(self):
        """驗證配置參數"""
        errors = []

        # 檢查必需的配置
        if not self.database.connection_string:
            errors.append("MONGODB_URI is required")

        if not self.security.jwt_secret_key:
            errors.append("JWT_SECRET_KEY is required")

        if len(self.security.jwt_secret_key) < 32:
            errors.append("JWT_SECRET_KEY must be at least 32 characters")

        # 檢查數值範圍
        if self.system.cpu_warning_threshold < 0 or self.system.cpu_warning_threshold > 100:
            errors.append("SYSTEM_CPU_WARNING_THRESHOLD must be between 0 and 100")

        if self.system.memory_warning_threshold < 0 or self.system.memory_warning_threshold > 100:
            errors.append("SYSTEM_MEMORY_WARNING_THRESHOLD must be between 0 and 100")

        if self.system.max_concurrent_sessions < 1 or self.system.max_concurrent_sessions > 100:
            errors.append("MAX_CONCURRENT_SESSIONS must be between 1 and 100")

        if errors:
            raise ValueError(f"Configuration validation failed: {'; '.join(errors)}")

        print("✅ 生產環境配置驗證通過")

    def get_monitoring_config(self) -> Dict[str, Any]:
        """獲取監控配置"""
        return {
            "cpu_threshold": self.system.cpu_warning_threshold,
            "memory_threshold": self.system.memory_warning_threshold,
            "disk_threshold": self.system.disk_warning_threshold,
            "monitoring_interval": self.system.monitoring_interval,
            "cleanup_interval": self.system.cleanup_interval
        }

    def get_resource_limits(self) -> Dict[str, int]:
        """獲取資源限制配置"""
        return {
            "max_sessions": self.system.max_concurrent_sessions,
            "max_sessions_per_second": self.system.max_sessions_per_second,
            "max_websockets": self.websocket.max_connections,
            "max_queue_size": self.system.max_queue_size
        }

    def get_database_config(self) -> Dict[str, Any]:
        """獲取數據庫配置"""
        return {
            "connection_string": self.database.connection_string,
            "max_pool_size": self.database.max_pool_size,
            "min_pool_size": self.database.min_pool_size,
            "max_idle_time_ms": self.database.max_idle_time_ms,
            "server_selection_timeout_ms": self.database.server_selection_timeout_ms
        }

    def get_websocket_config(self) -> Dict[str, Any]:
        """獲取 WebSocket 配置"""
        return {
            "max_connections": self.websocket.max_connections,
            "connection_timeout": self.websocket.connection_timeout,
            "heartbeat_interval": self.websocket.heartbeat_interval,
            "reconnect_attempts": self.websocket.reconnect_attempts,
            "reconnect_delay": self.websocket.reconnect_delay
        }

    def print_config_summary(self):
        """打印配置摘要"""
        print("\n📊 生產環境配置摘要")
        print("=" * 50)
        print(f"🚀 最大並發 Sessions: {self.system.max_concurrent_sessions}")
        print(f"⚡ 每秒最大 Sessions: {self.system.max_sessions_per_second}")
        print(f"🔌 最大 WebSocket 連接: {self.websocket.max_connections}")
        print(f"📦 最大隊列大小: {self.system.max_queue_size}")
        print(f"🖥️  CPU 警告閾值: {self.system.cpu_warning_threshold}%")
        print(f"💾 記憶體警告閾值: {self.system.memory_warning_threshold}%")
        print(f"💿 磁盤警告閾值: {self.system.disk_warning_threshold}%")
        print(f"⏱️  監控間隔: {self.system.monitoring_interval}s")
        print("=" * 50 + "\n")

# 全局配置實例
production_config = ProductionConfig()