#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for MongoDB manager module
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.utils.mongo_manager import MongoManager


class TestMongoManager:
    """Test MongoManager class."""

    @pytest.fixture
    def mock_mongo_uri(self):
        """Mock MongoDB URI."""
        return "mongodb://localhost:27017/test_grid_bot"

    @pytest.fixture
    def mock_motor_client(self):
        """Mock Motor client."""
        client = Mock()
        client.get_database = Mock()
        client.close = Mock()
        return client

    @pytest.fixture
    def mock_database(self):
        """Mock MongoDB database."""
        db = Mock()
        db.get_collection = Mock()
        return db

    @pytest.fixture
    def mock_collection(self):
        """Mock MongoDB collection."""
        collection = Mock()
        collection.insert_one = AsyncMock()
        collection.find_one = AsyncMock()
        collection.update_one = AsyncMock()
        collection.delete_one = AsyncMock()
        collection.find = Mock()
        return collection

    @pytest.mark.asyncio
    async def test_mongo_manager_initialization(self, mock_mongo_uri):
        """Test MongoManager initialization."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client_class.return_value = Mock()

            manager = MongoManager(mock_mongo_uri)

            assert manager.uri == mock_mongo_uri
            assert manager.db_name == "grid_bot"
            mock_client_class.assert_called_once_with(mock_mongo_uri)

    @pytest.mark.asyncio
    async def test_get_database(self, mock_mongo_uri):
        """Test getting database connection."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)
            db = await manager.get_database()

            assert db == mock_database
            mock_client.get_database.assert_called_once_with("grid_bot")

    @pytest.mark.asyncio
    async def test_get_collection(self, mock_mongo_uri):
        """Test getting collection."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)
            collection = await manager.get_collection("users")

            assert collection == mock_collection
            mock_database.get_collection.assert_called_once_with("users")

    @pytest.mark.asyncio
    async def test_create_user(self, mock_mongo_uri):
        """Test creating a user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            # Setup mocks
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_result = Mock()
            mock_result.inserted_id = "test_user_123"

            mock_collection.insert_one.return_value = mock_result
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            # Test user creation
            result = await manager.create_user(
                user_id="test_user_123",
                api_key="test_api_key",
                api_secret="test_api_secret",
                wallet_address="0x1234567890123456789012345678901234567890"
            )

            assert result == mock_result
            mock_collection.insert_one.assert_called_once()

            # Check the data being inserted
            call_args = mock_collection.insert_one.call_args[0][0]
            assert call_args["user_id"] == "test_user_123"
            assert call_args["api_key"] == "test_api_key"
            assert call_args["api_secret"] == "test_api_secret"
            assert call_args["wallet_address"] == "0x1234567890123456789012345678901234567890"

    @pytest.mark.asyncio
    async def test_create_user_with_additional_fields(self, mock_mongo_uri):
        """Test creating a user with additional fields."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_result = Mock()
            mock_result.inserted_id = "test_user_123"

            mock_collection.insert_one.return_value = mock_result
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.create_user(
                user_id="test_user_123",
                api_key="test_api_key",
                api_secret="test_api_secret",
                wallet_address="0x1234567890123456789012345678901234567890",
                email="test@example.com",
                created_at="2023-01-01T00:00:00Z"
            )

            assert result == mock_result

            # Check additional fields
            call_args = mock_collection.insert_one.call_args[0][0]
            assert call_args["email"] == "test@example.com"
            assert call_args["created_at"] == "2023-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_get_user_existing(self, mock_mongo_uri):
        """Test getting an existing user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_user_data = {
                "user_id": "test_user_123",
                "api_key": "test_api_key",
                "api_secret": "test_api_secret",
                "wallet_address": "0x1234567890123456789012345678901234567890"
            }

            mock_collection.find_one.return_value = mock_user_data
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.get_user("test_user_123")

            assert result == mock_user_data
            mock_collection.find_one.assert_called_once_with({"user_id": "test_user_123"})

    @pytest.mark.asyncio
    async def test_get_user_non_existing(self, mock_mongo_uri):
        """Test getting a non-existing user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()

            mock_collection.find_one.return_value = None
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.get_user("non_existing_user")

            assert result is None
            mock_collection.find_one.assert_called_once_with({"user_id": "non_existing_user"})

    @pytest.mark.asyncio
    async def test_update_user(self, mock_mongo_uri):
        """Test updating a user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_result = Mock()
            mock_result.matched_count = 1
            mock_result.modified_count = 1

            mock_collection.update_one.return_value = mock_result
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            update_data = {
                "api_key": "new_api_key",
                "api_secret": "new_api_secret"
            }

            result = await manager.update_user("test_user_123", update_data)

            assert result == mock_result
            mock_collection.update_one.assert_called_once_with(
                {"user_id": "test_user_123"},
                {"$set": update_data}
            )

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, mock_mongo_uri):
        """Test updating a non-existing user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_result = Mock()
            mock_result.matched_count = 0
            mock_result.modified_count = 0

            mock_collection.update_one.return_value = mock_result
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            update_data = {"api_key": "new_api_key"}
            result = await manager.update_user("non_existing_user", update_data)

            assert result == mock_result
            assert result.matched_count == 0
            assert result.modified_count == 0

    @pytest.mark.asyncio
    async def test_delete_user(self, mock_mongo_uri):
        """Test deleting a user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_result = Mock()
            mock_result.deleted_count = 1

            mock_collection.delete_one.return_value = mock_result
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.delete_user("test_user_123")

            assert result == mock_result
            mock_collection.delete_one.assert_called_once_with({"user_id": "test_user_123"})

    @pytest.mark.asyncio
    async def test_user_exists_true(self, mock_mongo_uri):
        """Test checking if user exists (true case)."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()

            mock_collection.find_one.return_value = {"user_id": "test_user_123"}
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.user_exists("test_user_123")

            assert result is True
            mock_collection.find_one.assert_called_once_with({"user_id": "test_user_123"})

    @pytest.mark.asyncio
    async def test_user_exists_false(self, mock_mongo_uri):
        """Test checking if user exists (false case)."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()

            mock_collection.find_one.return_value = None
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.user_exists("non_existing_user")

            assert result is False

    @pytest.mark.asyncio
    async def test_list_users(self, mock_mongo_uri):
        """Test listing users."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_cursor = Mock()

            users_data = [
                {"user_id": "user_1", "api_key": "key_1"},
                {"user_id": "user_2", "api_key": "key_2"}
            ]

            mock_cursor.to_list = AsyncMock(return_value=users_data)
            mock_collection.find.return_value = mock_cursor
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.list_users()

            assert result == users_data
            mock_collection.find.assert_called_once_with({})
            mock_cursor.to_list.assert_called_once_with(length=None)

    @pytest.mark.asyncio
    async def test_list_users_with_limit(self, mock_mongo_uri):
        """Test listing users with limit."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_cursor = Mock()

            users_data = [{"user_id": "user_1", "api_key": "key_1"}]

            mock_cursor.to_list = AsyncMock(return_value=users_data)
            mock_collection.find.return_value = mock_cursor
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.list_users(limit=1)

            assert result == users_data
            mock_cursor.to_list.assert_called_once_with(length=1)

    @pytest.mark.asyncio
    async def test_close_connection(self, mock_mongo_uri):
        """Test closing MongoDB connection."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_client.close = Mock()
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)
            await manager.close()

            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_mongo_uri):
        """Test MongoManager as context manager."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_client.close = Mock()
            mock_client_class.return_value = mock_client

            async with MongoManager(mock_mongo_uri) as manager:
                assert manager is not None
                assert manager.uri == mock_mongo_uri

            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_handling_create_user(self, mock_mongo_uri):
        """Test error handling when creating user."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()

            mock_collection.insert_one.side_effect = Exception("Database error")
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            with pytest.raises(Exception) as exc_info:
                await manager.create_user("test_user", "key", "secret", "wallet")

            assert str(exc_info.value) == "Database error"

    @pytest.mark.asyncio
    async def test_create_user_with_indexed_fields(self, mock_mongo_uri):
        """Test creating user with automatic indexing."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_index_model = Mock()

            mock_collection.insert_one = AsyncMock()
            mock_collection.create_index = AsyncMock()
            mock_collection.index_information = AsyncMock(return_value={})
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            await manager.create_user(
                user_id="test_user_123",
                api_key="test_api_key",
                api_secret="test_api_secret",
                wallet_address="0x1234567890123456789012345678901234567890"
            )

            # Check that indexes are created
            mock_collection.create_index.assert_called()

    @pytest.mark.asyncio
    async def test_find_user_by_api_key(self, mock_mongo_uri):
        """Test finding user by API key."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_user_data = {
                "user_id": "test_user_123",
                "api_key": "test_api_key",
                "api_secret": "test_api_secret"
            }

            mock_collection.find_one.return_value = mock_user_data
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.find_user_by_api_key("test_api_key")

            assert result == mock_user_data
            mock_collection.find_one.assert_called_once_with({"api_key": "test_api_key"})

    @pytest.mark.asyncio
    async def test_find_user_by_wallet_address(self, mock_mongo_uri):
        """Test finding user by wallet address."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()
            mock_user_data = {
                "user_id": "test_user_123",
                "wallet_address": "0x1234567890123456789012345678901234567890"
            }

            mock_collection.find_one.return_value = mock_user_data
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            wallet_address = "0x1234567890123456789012345678901234567890"
            result = await manager.find_user_by_wallet_address(wallet_address)

            assert result == mock_user_data
            mock_collection.find_one.assert_called_once_with({"wallet_address": wallet_address})

    @pytest.mark.asyncio
    async def test_health_check(self, mock_mongo_uri):
        """Test database health check."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()

            # Mock successful ping command
            mock_collection.command = AsyncMock(return_value={"ok": 1})
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.health_check()

            assert result is True
            mock_collection.command.assert_called_once_with("ping")

    @pytest.mark.asyncio
    async def test_health_check_failure(self, mock_mongo_uri):
        """Test database health check failure."""
        with patch('src.utils.mongo_manager.AsyncIOMotorClient') as mock_client_class:
            mock_client = Mock()
            mock_database = Mock()
            mock_collection = Mock()

            # Mock failed ping command
            mock_collection.command = AsyncMock(side_effect=Exception("Connection failed"))
            mock_database.get_collection.return_value = mock_collection
            mock_client.get_database.return_value = mock_database
            mock_client_class.return_value = mock_client

            manager = MongoManager(mock_mongo_uri)

            result = await manager.health_check()

            assert result is False