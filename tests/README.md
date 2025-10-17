# Test Suite Documentation

## Overview

This directory contains the comprehensive test suite for the Grid Trading Bot system. The test suite is designed to ensure the reliability, correctness, and robustness of all system components.

## Test Structure

```
tests/
├── __init__.py                 # Test package initialization
├── conftest.py                 # Pytest configuration and shared fixtures
├── README.md                   # This file
├── mocks.py                    # Mock utilities and helper functions
├── test_utils.py               # Test utility functions
├── unit/                       # Unit tests
│   ├── test_error_codes.py     # Error handling system tests
│   ├── test_event_queue.py     # Event queue system tests
│   ├── test_market_validator.py # Market validation tests
│   ├── test_mongo_manager.py   # MongoDB operations tests
│   └── test_session_manager.py # Session management tests
├── integration/                # Integration tests
│   └── test_grid_bot_integration.py
├── api/                        # API endpoint tests
│   ├── test_user_endpoints.py
│   ├── test_grid_endpoints.py
│   └── test_auth_endpoints.py
└── fixtures/                   # Test data
    ├── sample_orders.json
    ├── market_data.json
    └── test_users.json
```

## Running Tests

### Prerequisites

Install the test dependencies:
```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
```

### Basic Test Execution

Run all tests:
```bash
pytest
```

Run with verbose output:
```bash
pytest -v
```

Run with coverage:
```bash
pytest --cov=src --cov-report=html --cov-report=term-missing
```

### Running Specific Test Categories

Run only unit tests:
```bash
pytest tests/unit/
```

Run only integration tests:
```bash
pytest tests/integration/
```

Run only API tests:
```bash
pytest tests/api/
```

Run specific test file:
```bash
pytest tests/unit/test_error_codes.py
```

Run specific test class:
```bash
pytest tests/unit/test_error_codes.py::TestGridTradingException
```

Run specific test method:
```bash
pytest tests/unit/test_error_codes.py::TestGridTradingException::test_to_dict
```

### Test Markers

The test suite uses pytest markers to categorize tests:

```bash
pytest -m unit          # Run unit tests only
pytest -m integration   # Run integration tests only
pytest -m api           # Run API tests only
pytest -m slow          # Run slow tests only
pytest -m "not slow"    # Run all tests except slow ones
```

## Test Coverage

### Coverage Goals

- **Overall Target**: 85% code coverage
- **Core Logic**: 95% coverage for critical trading components
- **Error Handling**: 100% coverage for error scenarios

### Coverage Reports

Generate HTML coverage report:
```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

Generate XML coverage report (for CI):
```bash
pytest --cov=src --cov-report=xml
```

## Test Categories

### 1. Unit Tests (`tests/unit/`)

Test individual modules and functions in isolation.

**Current Coverage:**
- ✅ Error codes system (100% coverage)
- ✅ Event queue system
- ✅ Market validation
- ✅ MongoDB operations
- ✅ Session management

### 2. Integration Tests (`tests/integration/`)

Test interactions between multiple components.

**Planned Tests:**
- Grid bot end-to-end workflow
- WebSocket integration
- Database integration
- External API integration

### 3. API Tests (`tests/api/`)

Test FastAPI endpoints and HTTP request/response handling.

**Planned Tests:**
- User management endpoints
- Grid trading control endpoints
- Authentication and authorization
- Error response handling

## Test Fixtures and Mocks

### Shared Fixtures (`conftest.py`)

Common test fixtures used across multiple test files:

- `mock_orderly_client()`: Mock Orderly API client
- `sample_grid_config()`: Sample grid trading configuration
- `sample_user_data()`: Sample user data
- `mock_websocket_client()`: Mock WebSocket client
- `mock_mongo_manager()`: Mock MongoDB manager

### Mock Utilities (`mocks.py`)

Reusable mock classes and utilities:

- `MockOrderlyRestAPI`: Mock Orderly REST API
- `MockWebSocketAPI`: Mock WebSocket API
- `MockMongoDB`: Mock MongoDB operations
- `MockMetrics`: Mock metrics collector

### Test Utilities (`test_utils.py`)

Helper functions for testing:

- `async_test()`: Decorator for async test functions
- `assert_dicts_equal()`: Dictionary comparison with ignore options
- `convert_decimals_to_floats()`: Convert Decimal values in nested structures
- `create_test_config()`: Create test configuration with overrides

## Test Data

### Sample Data (`fixtures/`)

Pre-defined test data files:

- `sample_orders.json`: Sample order data
- `market_data.json`: Market information for different symbols
- `test_users.json`: Sample user configurations

## Configuration

### Pytest Configuration (`pytest.ini`)

Test configuration includes:
- Test discovery patterns
- Asyncio support
- Coverage settings
- Markers definition
- Warning filters

### Environment Setup

Tests use environment variables for configuration:

```python
# Automatically set in conftest.py
MONGODB_URI=mongodb://localhost:27017/test_grid_bot
UVICORN_HOST=127.0.0.1
UVICORN_PORT=8001
LOG_LEVEL=DEBUG
```

## Debugging Tests

### Running Tests in Debug Mode

```bash
pytest -s -v --pdb  # Stop on first failure and open debugger
pytest --traceback=short  # Short traceback format
pytest --tb=line  # One-line per failure
```

### Logging

Tests include structured logging that can be viewed during test execution:

```bash
pytest -s -v --log-cli-level=DEBUG
```

### Test Database

For tests requiring MongoDB, a separate test database is used:

```python
# In tests, use
MONGODB_URI=mongodb://localhost:27017/test_grid_bot

# The test database is isolated from production data
```

## Continuous Integration

### GitHub Actions Workflow

The test suite runs automatically on:
- Push to `main` or `develop` branches
- Pull requests targeting `main` or `develop`

### CI Pipeline

1. **Test Execution**: Run full test suite with coverage
2. **Linting**: Code style and syntax checking with `flake8` and `black`
3. **Security**: Security scanning with `bandit` and `safety`
4. **Coverage**: Upload coverage reports to Codecov

### Coverage Requirements

- Minimum 80% coverage required for CI to pass
- Coverage reports are generated and uploaded to Codecov
- HTML coverage reports are available in CI artifacts

## Best Practices

### Writing Tests

1. **Test Naming**: Use descriptive test names that explain what is being tested
2. **AAA Pattern**: Arrange, Act, Assert structure
3. **Independent Tests**: Each test should be independent and not rely on others
4. **Mock External Dependencies**: Mock external services (APIs, databases)
5. **Edge Cases**: Test both happy paths and error scenarios
6. **Async Testing**: Use proper async/await patterns for async functions

### Test Organization

1. **Group Related Tests**: Use test classes to group related functionality
2. **Parameterized Tests**: Use `@pytest.mark.parametrize` for testing multiple inputs
3. **Fixtures**: Use fixtures for reusable test setup
4. **Markers**: Use markers to categorize tests (unit, integration, slow, etc.)

### Error Testing

1. **Exception Handling**: Test that exceptions are raised correctly
2. **Error Messages**: Verify error messages are appropriate
3. **Recovery**: Test system recovery from error conditions
4. **Edge Cases**: Test boundary conditions and invalid inputs

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure all dependencies are installed
2. **Async Tests**: Make sure async tests use `@pytest.mark.asyncio`
3. **Database Tests**: Ensure MongoDB is running for integration tests
4. **Mock Issues**: Verify mocks match the actual API being tested

### Debug Steps

1. Run specific failing test: `pytest tests/unit/test_file.py::TestClass::test_method`
2. Enable verbose output: `pytest -v -s`
3. Check test logs: Add logging statements to understand test flow
4. Verify mocks: Ensure mocks match actual implementation

## Contributing

When adding new tests:

1. Follow the existing test structure and naming conventions
2. Add appropriate fixtures for reusable test setup
3. Include both positive and negative test cases
4. Update documentation for new test categories
5. Ensure new tests meet coverage requirements

### Test Review Checklist

- [ ] Test name clearly describes what is being tested
- [ ] Test follows AAA pattern (Arrange, Act, Assert)
- [ ] Test is independent and can run in isolation
- [ ] Appropriate mocks are used for external dependencies
- [ ] Both success and failure scenarios are tested
- [ ] Test coverage is maintained or improved
- [ ] Documentation is updated if needed

## Future Improvements

1. **Performance Testing**: Add load testing for high-volume scenarios
2. **Contract Testing**: Add API contract tests
3. **Property-Based Testing**: Use hypothesis for property-based testing
4. **Visual Testing**: Add UI testing for any web components
5. **Chaos Engineering**: Add fault injection testing