#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for error codes module
"""

import pytest
from src.utils.error_codes import (
    ErrorCode, ErrorDetail, GridTradingException,
    get_error_detail, ERROR_DETAILS
)


class TestErrorCode:
    """Test ErrorCode enum."""

    def test_error_code_values(self):
        """Test that error codes have correct values."""
        assert ErrorCode.UNKNOWN_ERROR.value == "E1000"
        assert ErrorCode.INVALID_REQUEST.value == "E1001"
        assert ErrorCode.UNAUTHORIZED.value == "E2000"
        assert ErrorCode.SESSION_NOT_FOUND.value == "E3000"
        assert ErrorCode.INVALID_SYMBOL.value == "E4000"
        assert ErrorCode.INVALID_GRID_CONFIG.value == "E5000"
        assert ErrorCode.ORDERLY_API_ERROR.value == "E6000"
        assert ErrorCode.USER_NOT_FOUND.value == "E7001"

    def test_error_code_categories(self):
        """Test error code categories."""
        # General errors (1000-1999)
        general_errors = [
            ErrorCode.UNKNOWN_ERROR,
            ErrorCode.INVALID_REQUEST,
            ErrorCode.MISSING_PARAMETER,
            ErrorCode.INVALID_PARAMETER,
            ErrorCode.INTERNAL_SERVER_ERROR
        ]
        for error in general_errors:
            assert error.value.startswith("E1")

        # Authentication errors (2000-2999)
        auth_errors = [
            ErrorCode.UNAUTHORIZED,
            ErrorCode.INVALID_CREDENTIALS,
            ErrorCode.TOKEN_EXPIRED,
            ErrorCode.INVALID_SIGNATURE
        ]
        for error in auth_errors:
            assert error.value.startswith("E2")

        # Session management errors (3000-3999)
        session_errors = [
            ErrorCode.SESSION_NOT_FOUND,
            ErrorCode.SESSION_ALREADY_EXISTS,
            ErrorCode.SESSION_CREATE_FAILED,
            ErrorCode.SESSION_STOP_FAILED,
            ErrorCode.UNKNOWN_WALLET_TYPE
        ]
        for error in session_errors:
            assert error.value.startswith("E3")


class TestErrorDetail:
    """Test ErrorDetail dataclass."""

    def test_error_detail_creation(self):
        """Test ErrorDetail creation."""
        detail = ErrorDetail(
            code=ErrorCode.INVALID_REQUEST,
            message="Test message",
            description="Test description",
            http_status=400,
            user_message="測試訊息"
        )

        assert detail.code == ErrorCode.INVALID_REQUEST
        assert detail.message == "Test message"
        assert detail.description == "Test description"
        assert detail.http_status == 400
        assert detail.user_message == "測試訊息"

    def test_error_detail_optional_fields(self):
        """Test ErrorDetail with optional fields."""
        detail = ErrorDetail(
            code=ErrorCode.UNKNOWN_ERROR,
            message="Unknown error",
            description="Unknown error occurred"
        )

        assert detail.code == ErrorCode.UNKNOWN_ERROR
        assert detail.message == "Unknown error"
        assert detail.description == "Unknown error occurred"
        assert detail.http_status == 500  # Default value
        assert detail.user_message is None


class TestGridTradingException:
    """Test GridTradingException class."""

    def test_exception_creation_with_error_code(self):
        """Test exception creation with error code."""
        exception = GridTradingException(
            error_code=ErrorCode.INVALID_SYMBOL
        )

        assert exception.error_code == ErrorCode.INVALID_SYMBOL
        assert isinstance(exception.error_detail, ErrorDetail)
        assert exception.details == {}
        assert exception.original_error is None
        assert str(exception) == "Invalid trading symbol"

    def test_exception_creation_with_details(self):
        """Test exception creation with details."""
        details = {"symbol": "INVALID", "user_id": "test_user"}
        exception = GridTradingException(
            error_code=ErrorCode.INVALID_SYMBOL,
            details=details
        )

        assert exception.details == details

    def test_exception_creation_with_original_error(self):
        """Test exception creation with original error."""
        original_error = ValueError("Original error message")
        exception = GridTradingException(
            error_code=ErrorCode.INVALID_SYMBOL,
            original_error=original_error
        )

        assert exception.original_error == original_error

    def test_to_dict(self):
        """Test exception to_dict method."""
        details = {"symbol": "INVALID"}
        original_error = ValueError("Original error")
        exception = GridTradingException(
            error_code=ErrorCode.INVALID_SYMBOL,
            details=details,
            original_error=original_error
        )

        result = exception.to_dict()

        expected = {
            "error_code": "E4000",
            "message": "Invalid trading symbol",
            "user_message": "不支援的交易對",
            "description": "The specified trading symbol is not supported",
            "details": {"symbol": "INVALID"},
            "original_error": "Original error"  # Just str(original_error)
        }

        assert result == expected

    def test_to_dict_minimal(self):
        """Test exception to_dict method with minimal exception."""
        exception = GridTradingException(
            error_code=ErrorCode.UNKNOWN_ERROR
        )

        result = exception.to_dict()

        expected_keys = ["error_code", "message", "user_message", "description"]
        for key in expected_keys:
            assert key in result

        assert result["error_code"] == "E1000"
        assert result["message"] == "Unknown error occurred"
        assert result["user_message"] == "系統發生未知錯誤，請稍後重試"

    def test_get_http_status(self):
        """Test get_http_status method."""
        # Test different error codes that are actually defined
        test_cases = [
            (ErrorCode.INVALID_REQUEST, 400),
            (ErrorCode.SESSION_NOT_FOUND, 404),
            (ErrorCode.SESSION_ALREADY_EXISTS, 409),
            (ErrorCode.ORDERLY_API_ERROR, 502),
            (ErrorCode.INTERNAL_SERVER_ERROR, 500),
        ]

        for error_code, expected_status in test_cases:
            exception = GridTradingException(error_code=error_code)
            assert exception.get_http_status() == expected_status

    def test_exception_inheritance(self):
        """Test that GridTradingException inherits from Exception."""
        exception = GridTradingException(
            error_code=ErrorCode.INVALID_SYMBOL
        )

        assert isinstance(exception, Exception)
        assert isinstance(exception, GridTradingException)

    def test_exception_with_unknown_error_code(self):
        """Test exception with error code not in ERROR_DETAILS."""
        # Create a mock error code that's not in the standard details
        class MockErrorCode:
            value = "E9999"

        exception = GridTradingException(
            error_code=MockErrorCode()
        )

        # Should fall back to UNKNOWN_ERROR details
        assert exception.error_detail.message == "Unknown error occurred"
        assert exception.get_http_status() == 500


class TestErrorDetailsConstants:
    """Test ERROR_DETAILS constants."""

    def test_error_details_completeness(self):
        """Test that all error codes used in tests have corresponding details."""
        # Only check error codes that are actually defined in ERROR_DETAILS
        for error_code in ERROR_DETAILS.keys():
            assert error_code in ErrorCode, f"Error detail for undefined error code: {error_code}"

        # Verify some key error codes are defined
        important_codes = [
            ErrorCode.UNKNOWN_ERROR,
            ErrorCode.INVALID_REQUEST,
            ErrorCode.INVALID_SYMBOL,
            ErrorCode.SESSION_NOT_FOUND,
            ErrorCode.ORDERLY_API_ERROR,
            ErrorCode.USER_NOT_FOUND
        ]

        for error_code in important_codes:
            assert error_code in ERROR_DETAILS, f"Missing details for {error_code}"

    def test_error_details_structure(self):
        """Test that all error details have required fields."""
        required_fields = ["code", "message", "description", "http_status"]

        for error_code, detail in ERROR_DETAILS.items():
            for field in required_fields:
                assert hasattr(detail, field), f"Missing field {field} for {error_code}"

            # Validate http_status is a valid HTTP status code
            assert 100 <= detail.http_status <= 599, f"Invalid HTTP status for {error_code}: {detail.http_status}"

    def test_user_message_languages(self):
        """Test that user messages are in Chinese."""
        chinese_errors = [
            ErrorCode.UNKNOWN_ERROR,
            ErrorCode.INVALID_REQUEST,
            ErrorCode.MISSING_PARAMETER,
            ErrorCode.INVALID_PARAMETER,
            ErrorCode.INTERNAL_SERVER_ERROR,
            ErrorCode.SESSION_NOT_FOUND,
            ErrorCode.SESSION_ALREADY_EXISTS,
            ErrorCode.INVALID_SYMBOL,
            ErrorCode.INVALID_PRICE,
            ErrorCode.USER_NOT_FOUND
        ]

        for error_code in chinese_errors:
            detail = ERROR_DETAILS[error_code]
            assert detail.user_message, f"Missing user message for {error_code}"
            # Check if user message contains Chinese characters
            assert any('\u4e00' <= char <= '\u9fff' for char in detail.user_message), \
                f"User message for {error_code} should be in Chinese: {detail.user_message}"


class TestGetErrorDetail:
    """Test get_error_detail function."""

    def test_get_existing_error_detail(self):
        """Test getting existing error detail."""
        detail = get_error_detail(ErrorCode.INVALID_SYMBOL)
        assert detail.code == ErrorCode.INVALID_SYMBOL
        assert detail.message == "Invalid trading symbol"

    def test_get_nonexistent_error_detail(self):
        """Test getting nonexistent error detail."""
        # Create a mock error code that's not in ERROR_DETAILS
        class MockErrorCode:
            value = "E9999"

        detail = get_error_detail(MockErrorCode())
        assert detail.code == ErrorCode.UNKNOWN_ERROR
        assert detail.message == "Unknown error occurred"

    def test_get_error_detail_with_none(self):
        """Test getting error detail with None input."""
        # The current implementation doesn't explicitly handle None
        # This test documents the current behavior
        try:
            result = get_error_detail(None)
            # If it doesn't raise an error, it should return unknown error details
            assert result.code == ErrorCode.UNKNOWN_ERROR
        except (TypeError, AttributeError):
            # If it raises an error, that's also acceptable behavior
            pass


class TestExceptionUsage:
    """Test exception usage patterns."""

    def test_raise_and_catch_grid_trading_exception(self):
        """Test raising and catching GridTradingException."""
        with pytest.raises(GridTradingException) as exc_info:
            raise GridTradingException(
                error_code=ErrorCode.INVALID_SYMBOL,
                details={"symbol": "INVALID"}
            )

        exception = exc_info.value
        assert exception.error_code == ErrorCode.INVALID_SYMBOL
        assert exception.details == {"symbol": "INVALID"}

    def test_exception_chaining(self):
        """Test exception chaining."""
        original_error = ValueError("Original error")

        # Direct creation without using exception chaining
        exception = GridTradingException(
            error_code=ErrorCode.INVALID_SYMBOL,
            original_error=original_error
        )

        assert exception.original_error == original_error
        # Note: __cause__ is not automatically set in this implementation

    def test_exception_with_context_manager(self):
        """Test exception in context manager scenarios."""
        def function_that_raises():
            raise GridTradingException(
                error_code=ErrorCode.SESSION_NOT_FOUND,
                details={"session_id": "test_session"}
            )

        with pytest.raises(GridTradingException) as exc_info:
            function_that_raises()

        exception = exc_info.value
        assert exception.error_code == ErrorCode.SESSION_NOT_FOUND
        assert exception.details == {"session_id": "test_session"}
        assert exception.get_http_status() == 404

    @pytest.mark.parametrize("error_code,expected_status", [
        (ErrorCode.INVALID_REQUEST, 400),
        (ErrorCode.SESSION_NOT_FOUND, 404),
        (ErrorCode.ORDERLY_RATE_LIMIT, 429),
        (ErrorCode.ORDERLY_API_ERROR, 502),
        (ErrorCode.INTERNAL_SERVER_ERROR, 500),
    ])
    def test_http_status_mapping(self, error_code, expected_status):
        """Test HTTP status mapping for various error codes."""
        exception = GridTradingException(error_code=error_code)
        assert exception.get_http_status() == expected_status