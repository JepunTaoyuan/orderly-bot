#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for session manager module
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.utils.session_manager import SessionManager


class TestSessionManager:
    """Test SessionManager class."""

    def test_session_manager_initialization(self):
        """Test SessionManager initialization."""
        manager = SessionManager()

        assert manager.sessions == {}
        assert manager._creating_sessions == set()
        assert hasattr(manager, '_sessions_lock')

    @pytest.mark.asyncio
    async def test_create_session_success(self):
        """Test successful session creation."""
        with patch('src.utils.session_manager.MongoManager') as mock_mongo_class:
            # Setup mock
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={
                "user_id": "test_user",
                "api_key": "test_key",
                "api_secret": "test_secret",
                "wallet_address": "0x1234567890123456789012345678901234567890"
            })
            mock_mongo_class.return_value = mock_mongo

            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.start_grid_trading = AsyncMock()
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                config = {
                    "user_id": "test_user",
                    "orderly_account_id": "test_user",
                    "orderly_key": "test_key",
                    "orderly_secret": "test_secret",
                    "orderly_testnet": True
                }

                result = await manager.create_session("test_session", config)

                assert result is True
                assert "test_session" in manager.sessions

    @pytest.mark.asyncio
    async def test_create_session_user_not_found(self):
        """Test session creation when user not found."""
        with patch('src.utils.session_manager.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value=None)
            mock_mongo_class.return_value = mock_mongo

            manager = SessionManager()
            config = {"user_id": "nonexistent_user"}

            with pytest.raises(ValueError):
                await manager.create_session("test_session", config)

    @pytest.mark.asyncio
    async def test_create_session_already_exists(self):
        """Test creating a session that already exists."""
        with patch('src.utils.session_manager.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo_class.return_value = mock_mongo

            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.start_grid_trading = AsyncMock()
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                config = {"user_id": "test_user"}

                # Create first session
                await manager.create_session("test_session", config)

                # Try to create same session again
                result = await manager.create_session("test_session", config)

                assert result is False

    @pytest.mark.asyncio
    async def test_stop_session_success(self):
        """Test successful session stopping."""
        with patch('src.utils.session_manager.MongoManager'):
            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.stop_grid_trading = AsyncMock()
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                manager.sessions["test_session"] = mock_bot

                result = await manager.stop_session("test_session")

                assert result is True
                assert "test_session" not in manager.sessions
                mock_bot.stop_grid_trading.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_session_not_found(self):
        """Test stopping a session that doesn't exist."""
        manager = SessionManager()

        result = await manager.stop_session("nonexistent_session")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_session_status_exists(self):
        """Test getting status for existing session."""
        with patch('src.utils.session_manager.MongoManager'):
            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.get_status = AsyncMock(return_value={"is_running": True})
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                manager.sessions["test_session"] = mock_bot

                status = await manager.get_session_status("test_session")

                assert status == {"is_running": True}
                mock_bot.get_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_status_not_exists(self):
        """Test getting status for non-existing session."""
        manager = SessionManager()

        status = await manager.get_session_status("nonexistent_session")

        assert status is None

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        """Test listing all sessions."""
        with patch('src.utils.session_manager.MongoManager'):
            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                # Create mock bots with different running states
                mock_bot1 = Mock()
                mock_bot1.is_running = True
                mock_bot2 = Mock()
                mock_bot2.is_running = False

                mock_bot_class.side_effect = [mock_bot1, mock_bot2]

                manager = SessionManager()
                manager.sessions["session1"] = mock_bot1
                manager.sessions["session2"] = mock_bot2

                sessions = await manager.list_sessions()

                assert sessions == {"session1": True, "session2": False}

    @pytest.mark.asyncio
    async def test_stop_all_sessions(self):
        """Test stopping all sessions."""
        with patch('src.utils.session_manager.MongoManager'):
            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot1 = Mock()
                mock_bot1.stop_grid_trading = AsyncMock()
                mock_bot2 = Mock()
                mock_bot2.stop_grid_trading = AsyncMock()

                mock_bot_class.side_effect = [mock_bot1, mock_bot2]

                manager = SessionManager()
                manager.sessions["session1"] = mock_bot1
                manager.sessions["session2"] = mock_bot2

                await manager.stop_all_sessions()

                assert len(manager.sessions) == 0
                mock_bot1.stop_grid_trading.assert_called_once()
                mock_bot2.stop_grid_trading.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_session_creation(self):
        """Test concurrent session creation handling."""
        with patch('src.utils.session_manager.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo_class.return_value = mock_mongo

            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.start_grid_trading = AsyncMock()
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                config = {"user_id": "test_user"}

                # Try to create the same session concurrently
                import asyncio
                tasks = [
                    manager.create_session("test_session", config),
                    manager.create_session("test_session", config),
                    manager.create_session("test_session", config)
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Only one should succeed
                success_count = sum(1 for result in results if result is True)
                assert success_count == 1

    @pytest.mark.asyncio
    async def test_session_creation_failure(self):
        """Test session creation when bot start fails."""
        with patch('src.utils.session_manager.MongoManager') as mock_mongo_class:
            mock_mongo = Mock()
            mock_mongo.get_user = AsyncMock(return_value={"user_id": "test_user"})
            mock_mongo_class.return_value = mock_mongo

            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.start_grid_trading = AsyncMock(side_effect=Exception("Bot start failed"))
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                config = {"user_id": "test_user"}

                with pytest.raises(Exception):
                    await manager.create_session("test_session", config)

                # Session should not be added if creation failed
                assert "test_session" not in manager.sessions
                assert "test_session" not in manager._creating_sessions

    @pytest.mark.asyncio
    async def test_session_stop_failure(self):
        """Test session stopping when bot stop fails."""
        with patch('src.utils.session_manager.MongoManager'):
            with patch('src.utils.session_manager.GridTradingBot') as mock_bot_class:
                mock_bot = Mock()
                mock_bot.stop_grid_trading = AsyncMock(side_effect=Exception("Bot stop failed"))
                mock_bot_class.return_value = mock_bot

                manager = SessionManager()
                manager.sessions["test_session"] = mock_bot

                with pytest.raises(Exception):
                    await manager.stop_session("test_session")

                # Session should still be removed even if stop failed
                assert "test_session" not in manager.sessions

    def test_session_manager_str_representation(self):
        """Test SessionManager string representation."""
        manager = SessionManager()

        # Add some mock sessions
        manager.sessions["session1"] = Mock()
        manager.sessions["session2"] = Mock()

        str_repr = str(manager)
        assert "session1" in str_repr
        assert "session2" in str_repr
        assert "sessions" in str_repr.lower()