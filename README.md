# Orderly Grid Trading Bot

An enterprise-grade grid trading system designed for Orderly Network with comprehensive monitoring, validation, and reliability features. This MVP implementation provides a robust foundation for automated grid trading strategies.

## Architecture Overview

The system follows a modular architecture with clear separation of concerns:

```
orderly_bot/
├── app.py                              # Application entry point
├── requirements.txt                    # Production dependencies
├── .env.example                        # Environment configuration template
├── src/
│   ├── api/                           # REST API layer
│   │   └── server.py                  # FastAPI application and endpoints
│   ├── core/                          # Core trading logic
│   │   ├── grid_bot.py                # Main trading bot implementation
│   │   ├── grid_signal.py             # Signal generation and strategy logic
│   │   ├── client.py                  # Orderly Network API client
│   │   └── profit_tracker.py          # Profit/loss tracking
│   ├── services/                      # Business services layer
│   │   ├── session_service.py         # Multi-session management
│   │   ├── database_service.py        # Database operations
│   │   ├── grid_summary_service.py    # Grid summary analytics
│   │   └── database_connection.py     # MongoDB connection management
│   ├── auth/                          # Authentication & authorization
│   │   ├── wallet_signature.py        # Wallet signature verification
│   │   └── auth_decorators.py         # Authentication decorators
│   ├── models/                        # Data models and schemas
│   │   └── grid_summary.py            # Grid session summary model
│   ├── utils/                         # Infrastructure utilities
│   │   ├── session_manager.py         # Session state management
│   │   ├── event_queue.py             # Ordered event processing
│   │   ├── market_validator.py        # Price/quantity validation
│   │   ├── retry_handler.py           # Resilient API calls
│   │   ├── order_tracker.py           # Order execution tracking
│   │   ├── logging_config.py          # Structured logging system
│   │   ├── error_codes.py             # Centralized error handling
│   │   ├── websocket_manager.py       # WebSocket connection management
│   │   ├── system_monitor.py          # System health monitoring
│   │   └── mongodb_health.py          # Database health checks
│   └── config/                        # Configuration management
│       └── production_config.py       # Production environment settings
└── tests/                             # Comprehensive test suite
    ├── unit/                          # Unit tests
    ├── integration/                   # Integration tests
    ├── conftest.py                    # Test configuration
    └── mocks.py                       # Test utilities
```

## Quick Start for Developers

### Prerequisites

- Python 3.8+
- MongoDB 4.4+
- Orderly Network API credentials

### Environment Setup

```bash
# Clone the repository
git clone <repository-url>
cd orderly-bot

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Set up environment variables
cp .env.example .env
# Edit .env with your credentials
```

### Required Environment Variables

```bash
# Orderly Network credentials (required)
ORDERLY_KEY=your_orderly_api_key
ORDERLY_SECRET=your_orderly_secret_key
ORDERLY_ACCOUNT_ID=your_account_id

# Database connection (required)
MONGODB_URI=mongodb://localhost:27017/orderly_bot

# Optional configuration
ORDERLY_TESTNET=true                    # Use testnet (default: true)
UVICORN_HOST=0.0.0.0                   # Server host (default: 0.0.0.0)
UVICORN_PORT=8001                      # Server port (default: 8001)
PYTHONDONTWRITEBYTECODE=1              # Prevent __pycache__ generation
```

### Installation and Development Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Install in development mode (recommended for contributions)
pip install -e .

# Run the application
python app.py
# OR
uvicorn src.api.server:app --host 0.0.0.0 --port 8001 --reload
```

### Verify Installation

```bash
# Health check
curl http://localhost:8001/health

# System metrics
curl http://localhost:8001/metrics

# API documentation
open http://localhost:8001/docs
```

## Core Components

### 1. Grid Trading Engine (`src/core/grid_bot.py`)

The main trading bot implementation that orchestrates:
- Multi-session grid trading management
- Event-driven trading signal execution
- Order lifecycle management
- WebSocket integration for real-time updates
- Profit/loss tracking

Key features:
- **Session Management**: Each trading session is isolated with its own state
- **Event Processing**: Ordered event queue prevents race conditions
- **Resilient Operations**: Automatic retry mechanisms with exponential backoff
- **Safety Mechanisms**: Duplicate order prevention and state consistency checks

### 2. Signal Generation (`src/core/grid_signal.py`)

Implements grid trading strategies with support for:
- **Grid Types**: Arithmetic (uniform spacing) and Geometric (exponential spacing)
- **Trading Directions**: LONG, SHORT, or BOTH (bidirectional)
- **Dynamic Signal Generation**: Event-driven signals based on price movements

### 3. Orderly Client (`src/core/client.py`)

Wrapper around Orderly Network API providing:
- Rate-limited API calls (10 requests/second)
- Automatic error handling and retries
- Order management and position tracking
- WebSocket integration for real-time data

### 4. Session Management (`src/services/session_service.py`)

Manages multiple concurrent trading sessions:
- Session isolation and state management
- Resource cleanup and garbage collection
- Session persistence and recovery

## API Endpoints

### Trading Operations

```bash
# Start grid trading session
POST /api/grid/start
{
  "ticker": "BTCUSDT",
  "direction": "BOTH",
  "current_price": 42500,
  "upper_bound": 45000,
  "lower_bound": 40000,
  "grid_levels": 6,
  "total_margin": 1000,
  "grid_type": "ARITHMETIC",  # or "GEOMETRIC"
  "grid_ratio": 0.05,         # Required for GEOMETRIC grids
  "user_auth": {
    "user_id": "user123",
    "signature": "wallet_signature",
    "timestamp": 1234567890,
    "nonce": "random_nonce"
  }
}

# Stop grid trading session
POST /api/grid/stop
{
  "session_id": "user123_BTCUSDT"
}

# Get session status
GET /api/grid/status/{session_id}

# List all sessions
GET /api/grid/sessions

# Get grid summary with filtering
GET /api/grid/summary?user_id=user123&status=active&start_time=2024-01-01
```

### System Monitoring

```bash
# Health check
GET /health

# Readiness check (includes dependencies)
GET /health/ready

# System metrics
GET /metrics

# Root endpoint
GET /
```

## Grid Trading Strategies

### Arithmetic Grid (Default)

Uniform price distribution between bounds.

**Formula**: `price_interval = (upper_bound - lower_bound) / (grid_levels - 1)`

**Use Case**: Ranging markets with predictable volatility patterns.

### Geometric Grid

Exponential price distribution requiring `grid_ratio` parameter.

**Formulas**:
- Lower grid: `price = current_price × (1 - grid_ratio)^i`
- Upper grid: `price = current_price × (1 + grid_ratio)^i`

**Parameters**:
- `grid_ratio`: 0.01 - 0.1 (1% - 10%)

**Use Case**: Trending markets with exponential price movements.

## Development Workflow

### Running Tests

```bash
# Run all tests
python tests/run_tests.py
# OR
pytest tests/ -v

# Run specific test categories
pytest tests/unit/ -v                    # Unit tests only
pytest tests/integration/ -v             # Integration tests
pytest tests/test_server.py -v           # API tests
pytest tests/test_grid_safety.py -v      # Security tests

# Generate coverage report
pytest tests/ --cov=src --cov-report=html
```

### Code Quality

```bash
# Install development dependencies
pip install black flake8 mypy

# Code formatting
black src/ tests/

# Linting
flake8 src/ tests/

# Type checking
mypy src/
```

### Adding New Features

1. **Implementation**: Add functionality to appropriate module
2. **Testing**: Write comprehensive unit and integration tests
3. **Documentation**: Update API documentation and README
4. **Validation**: Run full test suite and ensure >90% coverage
5. **Security**: Review error handling and input validation

## Error Handling and Monitoring

### Structured Logging

The system uses JSON-structured logging with context tracking:

```python
# Example log entry
{
  "timestamp": "2024-01-01T12:00:00Z",
  "level": "INFO",
  "session_id": "user123_BTCUSDT",
  "event_type": "ORDER_CREATED",
  "message": "Grid order created successfully",
  "order_id": "order_123",
  "price": 42500,
  "quantity": 0.1
}
```

### Error Codes

Centralized error handling with specific error codes in `src/utils/error_codes.py`:
- `SESSION_NOT_FOUND`: Trading session doesn't exist
- `INVALID_MARKET_PARAMS`: Invalid market parameters
- `ORDER_CREATION_FAILED`: Order placement failure
- `RATE_LIMIT_EXCEEDED`: API rate limit exceeded

### System Metrics

Comprehensive metrics collection for monitoring:
- API request counts and success rates
- Session creation/deletion statistics
- Order execution metrics
- System performance indicators
- Database connection health

## Security Considerations

### Authentication

- **Wallet Signature Verification**: Cryptographic signature validation
- **Nonce Prevention**: Replay attack protection
- **Rate Limiting**: API endpoint protection with SlowAPI
- **Input Validation**: Pydantic model validation for all inputs

### Safety Mechanisms

- **Duplicate Order Prevention**: Prevents multiple orders at same price
- **Event Deduplication**: WebSocket event deduplication
- **State Consistency**: Automatic rollback on API failures
- **Concurrency Protection**: Asyncio locks for shared state

## Database Schema

### Sessions Collection

```javascript
{
  "_id": "user123_BTCUSDT",
  "user_id": "user123",
  "ticker": "BTCUSDT",
  "status": "ACTIVE",
  "config": {
    "direction": "BOTH",
    "grid_type": "ARITHMETIC",
    "upper_bound": 45000,
    "lower_bound": 40000,
    "grid_levels": 6,
    "total_margin": 1000
  },
  "created_at": ISODate("2024-01-01T12:00:00Z"),
  "updated_at": ISODate("2024-01-01T12:30:00Z")
}
```

### Grid Summaries Collection

```javascript
{
  "_id": ObjectId("..."),
  "session_id": "user123_BTCUSDT",
  "user_id": "user123",
  "ticker": "BTCUSDT",
  "total_orders": 25,
  "successful_orders": 23,
  "total_profit": 150.50,
  "stop_reason": "MANUAL",
  "created_at": ISODate("2024-01-01T12:00:00Z"),
  "updated_at": ISODate("2024-01-01T12:30:00Z")
}
```

## Performance Considerations

### Rate Limiting

- **Orderly API**: 10 requests/second with automatic rate limiting
- **Application Endpoints**: Configurable rate limits per endpoint
- **WebSocket**: Connection pooling and reconnection logic

### Resource Management

- **Connection Pooling**: MongoDB connection pooling
- **Memory Management**: Automatic cleanup of completed sessions
- **Event Queue**: Bounded queue with backpressure handling

### Scalability

- **Session Isolation**: Each session runs independently
- **Async Processing**: Non-blocking I/O throughout
- **Database Indexing**: Optimized queries for session lookups

## Troubleshooting

### Common Issues

1. **WebSocket Connection Failures**
   - Check network connectivity
   - Verify Orderly Network credentials
   - Review rate limiting status

2. **Order Placement Failures**
   - Insufficient margin
   - Market price outside grid bounds
   - API rate limit exceeded

3. **Session State Inconsistencies**
   - Database connection issues
   - Concurrent access conflicts
   - Network interruptions

### Debug Mode

Enable debug logging by setting:
```bash
LOG_LEVEL=DEBUG
```

### Health Monitoring

Monitor system health via:
- `/health` endpoint for basic status
- `/health/ready` for dependency checks
- `/metrics` for detailed performance metrics