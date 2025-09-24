# Grid Trading Server

An enterprise-grade grid trading system with comprehensive monitoring, validation, and reliability features.

## 🏗️ Project Structure

```
orderly_bot/
├── main.py                 # Main entry point
├── src/
│   ├── api/               # FastAPI server and endpoints
│   │   └── main.py       # API routes and server setup
│   ├── core/             # Core trading logic
│   │   ├── grid_bot.py   # Main trading bot implementation
│   │   ├── grid_signal.py # Signal generation and strategy
│   │   └── client.py     # Exchange API client
│   └── utils/            # Utilities and infrastructure
│       ├── session_manager.py    # Multi-session management
│       ├── event_queue.py        # Sequential event processing
│       ├── market_validator.py   # Price/size validation
│       ├── retry_handler.py      # Resilient API calls
│       ├── order_tracker.py      # Fill tracking
│       └── logging_config.py     # Structured logging
├── docs/                  # Documentation
├── tests/                 # Test files (when added)
├── .agents/              # Codebuff agent configurations
└── codebuff.json         # Codebuff configuration
```

## 🚀 Quick Start

```bash
# Start the server
python main.py

# Check health
curl http://localhost:8000/health

# View metrics
curl http://localhost:8000/metrics
```

## 📊 API Endpoints

- `POST /api/grid/start` - Start grid trading
- `POST /api/grid/stop` - Stop grid trading
- `GET /api/grid/status/{session_id}` - Get session status
- `GET /api/grid/sessions` - List all sessions
- `GET /health` - Health check
- `GET /metrics` - System metrics
- `GET /` - Root endpoint

## 🎯 Features

- ✅ Sequential event processing (prevents race conditions)
- ✅ Market validation and price normalization
- ✅ Exponential backoff with retry logic
- ✅ Comprehensive order fill tracking
- ✅ Structured logging with metrics
- ✅ Multi-session support
- ✅ Health monitoring endpoints