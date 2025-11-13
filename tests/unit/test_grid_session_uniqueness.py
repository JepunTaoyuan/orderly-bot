#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
簡化版的網格會話唯一性保護測試
專注於測試核心功能
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.services.session_service import SessionManager
from src.services.database_connection import DatabaseManager
from src.utils.error_codes import GridTradingException, ErrorCode


class TestGridSessionUniquenessSimple:
    """Test grid session uniqueness protection mechanisms - simplified version."""

    @pytest.mark.asyncio
    async def test_session_uniqueness_validation_basic(self):
        """Test basic session uniqueness validation."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 測試沒有重複會話的情況
            session_id = "test_user_PERP_ETH_USDC"
            config = {"user_id": "test_user", "ticker": "PERP_ETH_USDC"}

            # 應該不拋出異常
            await manager._validate_session_uniqueness(session_id, config)

    @pytest.mark.asyncio
    async def test_session_uniqueness_validation_detects_memory_duplicate(self):
        """Test detection of duplicate session in memory."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])  # 保持異步mock
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 創建一個現有的活躍會話
            mock_bot = Mock()
            mock_bot.is_running = True
            manager.sessions["test_user_PERP_ETH_USDC"] = mock_bot

            # 嘗試創建相同用戶和交易對的新會話
            session_id = "test_user_PERP_ETH_USDC_2"
            config = {"user_id": "test_user", "ticker": "PERP_ETH_USDC"}

            # 應該拋出重複會話異常
            with pytest.raises(GridTradingException) as exc_info:
                await manager._validate_session_uniqueness(session_id, config)

            assert exc_info.value.error_code == ErrorCode.DUPLICATE_GRID_SESSION
            assert "test_user" in exc_info.value.details["user_id"]
            assert "PERP_ETH_USDC" in exc_info.value.details["ticker"]

    @pytest.mark.asyncio
    async def test_session_uniqueness_validation_different_users_allowed(self):
        """Test that same ticker with different users is allowed."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 創建一個其他用戶的現有會話
            mock_bot = Mock()
            mock_bot.is_running = True
            manager.sessions["other_user_PERP_ETH_USDC"] = mock_bot

            # 嘗試創建相同交易對但不同用戶的會話
            session_id = "test_user_PERP_ETH_USDC"
            config = {"user_id": "test_user", "ticker": "PERP_ETH_USDC"}

            # 應該不拋出異常
            await manager._validate_session_uniqueness(session_id, config)

    @pytest.mark.asyncio
    async def test_session_uniqueness_validation_different_tickers_allowed(self):
        """Test that same user with different tickers is allowed."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 創建一個其他交易對的現有會話
            mock_bot = Mock()
            mock_bot.is_running = True
            manager.sessions["test_user_PERP_BTC_USDC"] = mock_bot

            # 嘗試創建相同用戶但不同交易對的會話
            session_id = "test_user_PERP_ETH_USDC"
            config = {"user_id": "test_user", "ticker": "PERP_ETH_USDC"}

            # 應該不拋出異常
            await manager._validate_session_uniqueness(session_id, config)

    @pytest.mark.asyncio
    async def test_session_uniqueness_validation_ignores_inactive_sessions(self):
        """Test that inactive sessions are ignored."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 創建一個非活躍的現有會話
            mock_bot = Mock()
            mock_bot.is_running = False
            manager.sessions["test_user_PERP_ETH_USDC"] = mock_bot

            # 嘗試創建相同用戶和交易對的新會話
            session_id = "test_user_PERP_ETH_USDC_2"
            config = {"user_id": "test_user", "ticker": "PERP_ETH_USDC"}

            # 應該不拋出異常
            await manager._validate_session_uniqueness(session_id, config)

    @pytest.mark.asyncio
    async def test_database_manager_check_duplicate_basic(self):
        """Test basic duplicate session check in database."""
        with patch('motor.motor_asyncio.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_db = Mock()
            mock_collection = Mock()

            mock_client.admin.command = AsyncMock(return_value={"ok": 1})
            mock_client.get_default_database.return_value = mock_db
            mock_db.sessions = mock_collection
            mock_collection.find_one = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_client

            db_manager = DatabaseManager()
            db_manager.client = mock_client
            db_manager.db = mock_db

            # 測試沒有重複會話
            result = await db_manager.check_duplicate_grid_session("test_user", "PERP_ETH_USDC")
            assert result is None

            # 測試有重複會話
            mock_collection.find_one.return_value = {
                "session_id": "test_user_PERP_ETH_USDC_existing",
                "user_id": "test_user",
                "ticker": "PERP_ETH_USDC",
                "status": "active"
            }

            result = await db_manager.check_duplicate_grid_session("test_user", "PERP_ETH_USDC")
            assert result is not None
            assert result["session_id"] == "test_user_PERP_ETH_USDC_existing"

    @pytest.mark.asyncio
    async def test_create_session_with_uniqueness_check(self):
        """Test session creation with uniqueness validation."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={
                "user_id": "test_user",
                "api_key": "test_key",
                "api_secret": "test_secret",
                "wallet_address": "0x1234567890123456789012345678901234567890"
            })
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            with patch('src.services.session_service.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.start_grid_trading = AsyncMock()
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                manager.mongo_manager = mock_mongo

                session_id = "test_user_PERP_ETH_USDC"
                config = {
                    "user_id": "test_user",
                    "ticker": "PERP_ETH_USDC",
                    "orderly_account_id": "test_user",
                    "orderly_key": "test_key",
                    "orderly_secret": "test_secret",
                    "orderly_testnet": True
                }

                result = await manager.create_session(session_id, config)
                assert result is True
                assert session_id in manager.sessions

    @pytest.mark.asyncio
    async def test_create_session_blocked_by_duplicate(self):
        """Test session creation blocked by duplicate detection."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 創建一個現有的活躍會話
            mock_bot_existing = Mock()
            mock_bot_existing.is_running = True
            manager.sessions["test_user_PERP_ETH_USDC"] = mock_bot_existing

            session_id = "test_user_PERP_ETH_USDC_new"
            config = {
                "user_id": "test_user",
                "ticker": "PERP_ETH_USDC",
                "direction": "BOTH",
                "current_price": 4000,
                "upper_bound": 4500,
                "lower_bound": 3500,
                "grid_type": "ARITHMETIC",
                "grid_levels": 5,
                "total_margin": 1000
            }

            # 應該拋出重複會話異常
            with pytest.raises(GridTradingException) as exc_info:
                await manager.create_session(session_id, config)

            assert exc_info.value.error_code == ErrorCode.DUPLICATE_GRID_SESSION

    def test_error_code_enum_value(self):
        """Test that DUPLICATE_GRID_SESSION error code enum works correctly."""
        # 測試枚舉值
        assert ErrorCode.DUPLICATE_GRID_SESSION.value == "E3007"

        # 測試錯誤詳情存在
        from src.utils.error_codes import ERROR_DETAILS
        assert ErrorCode.DUPLICATE_GRID_SESSION in ERROR_DETAILS

        detail = ERROR_DETAILS[ErrorCode.DUPLICATE_GRID_SESSION]
        assert detail.http_status == 409
        assert "活躍的網格會話" in detail.user_message

    @pytest.mark.asyncio
    async def test_validate_session_uniqueness_invalid_session_id(self):
        """Test handling of invalid session ID format."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(return_value=[])
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            # 測試無效的 session_id 格式
            session_id = "invalid_no_underscore"
            config = {
                "user_id": "test_user",
                "ticker": "PERP_ETH_USDC"
            }

            # 應該不拋出異常，但會記錄警告
            await manager._validate_session_uniqueness(session_id, config)

    @pytest.mark.asyncio
    async def test_session_uniqueness_validation_database_error(self):
        """Test handling of database errors during validation."""
        with patch('src.services.session_service.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo.get_user_sessions = AsyncMock(side_effect=Exception("Database error"))
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            manager.mongo_manager = mock_mongo

            session_id = "test_user_PERP_ETH_USDC"
            config = {
                "user_id": "test_user",
                "ticker": "PERP_ETH_USDC"
            }

            # 應該不拋出異常，只記錄錯誤
            await manager._validate_session_uniqueness(session_id, config)