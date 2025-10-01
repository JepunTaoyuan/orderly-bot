# Orderly Grid Trading Bot

一個企業級的網格交易系統，具備完整的監控、驗證和可靠性功能。專為 Orderly Network 設計的 MVP 實現。

## 🏗️ 項目結構

```
orderly_bot/
├── app.py                 # 主程式入口點
├── requirements.txt       # Python 依賴項
├── setup.py              # 項目安裝配置
├── src/
│   ├── api/               # FastAPI 伺服器和端點
│   │   └── server.py      # API 路由和伺服器設置
│   ├── core/              # 核心交易邏輯
│   │   ├── grid_bot.py    # 主要交易機器人實現
│   │   ├── grid_signal.py # 訊號生成和策略
│   │   └── client.py      # 交易所 API 客戶端
│   └── utils/             # 工具和基礎設施
│       ├── session_manager.py    # 多會話管理
│       ├── event_queue.py        # 順序事件處理
│       ├── market_validator.py   # 價格/數量驗證
│       ├── retry_handler.py      # 彈性 API 調用
│       ├── order_tracker.py      # 成交追踪
│       ├── logging_config.py     # 結構化日誌
│       ├── error_codes.py        # 統一錯誤碼系統
│       ├── api_helpers.py        # API 輔助工具
│       └── settings.py           # 環境變數設置
├── .env.example           # 環境變數模板
├── .gitignore            # Git 忽略文件
└── README.md             # 項目文檔（本文件）
```

## 🚀 快速開始

### 1. 環境設置

```bash
# 複製環境變數模板並填入值
cp .env.example .env

# 編輯 .env 文件，填入您的 Orderly Network 憑證
# ORDERLY_KEY=your_orderly_key
# ORDERLY_SECRET=your_orderly_secret  
# ORDERLY_ACCOUNT_ID=your_account_id
```

### 2. 安裝依賴

```bash
# 方法 1: 使用 requirements.txt
pip install -r requirements.txt

# 方法 2: 開發模式安裝（推薦）
pip install -e .

# 方法 3: 手動安裝核心依賴
pip install fastapi uvicorn httpx pydantic orderly-evm-connector
```

### 3. 啟動伺服器

```bash
# 方法 1: 使用 uvicorn（推薦）
uvicorn src.api.server:app --host 0.0.0.0 --port 8000 --reload

# 方法 2: 使用 Python 入口點
python app.py

# 方法 3: 如果已安裝為包
grid-trading-bot
```

### 4. 驗證運行

```bash
# 健康檢查
curl http://localhost:8000/health

# 查看系統指標
curl http://localhost:8000/metrics

# 查看 API 文檔
open http://localhost:8000/docs
```

## 📊 API 端點

### 核心交易端點
- `POST /api/grid/start` - 啟動網格交易
- `POST /api/grid/stop` - 停止網格交易
- `GET /api/grid/status/{session_id}` - 獲取會話狀態
- `GET /api/grid/sessions` - 列出所有會話

### 用戶管理端點
- `POST /api/user/enable` - 註冊用戶並啟用機器人交易
- `PUT /api/user/update` - 更新用戶 API 憑證

### 系統監控端點
- `GET /health` - 健康檢查
- `GET /health/ready` - 就緒檢查
- `GET /metrics` - 系統指標
- `GET /` - 根端點

### 用戶管理端點
- `POST /api/enable` - 啟用機器人交易（預留）

## 🎯 核心功能

### ✅ 交易功能
- **多會話支援**：同時運行多個網格交易
- **三種策略**：做多、做空、雙向網格
- **智能訊號生成**：事件驅動的交易訊號
- **訂單追踪**：完整的成交記錄和統計

### ✅ 安全性功能
- **重複掛單防護**：防止同一價格重複掛單
- **事件去重機制**：防止 WebSocket 重複事件
- **狀態一致性保護**：API 失敗時自動回滾
- **併發安全**：使用鎖保護共享狀態

### ✅ 可靠性功能
- **指數退避重試**：智能錯誤分類和重試
- **順序事件處理**：防止競爭條件
- **市場驗證**：價格和數量標準化
- **統一錯誤處理**：結構化錯誤碼系統

### ✅ 監控功能
- **結構化日誌**：JSON 格式，便於分析
- **系統指標**：計數器、量表、直方圖
- **健康檢查**：多層次的系統狀態檢查
- **會話上下文追踪**：完整的操作鏈路追踪

## 🔧 配置說明

### 必要環境變數
```bash
MONGODB_URI=mongodb://...                   # MongoDB 連接字符串
```

### 可選環境變數
```bash
ORDERLY_TESTNET=true                        # 是否使用測試網（預設：true）
UVICORN_HOST=0.0.0.0                       # 伺服器主機（預設：0.0.0.0）
UVICORN_PORT=8000                          # 伺服器端口（預設：8000）
PYTHONDONTWRITEBYTECODE=1                  # 防止生成 __pycache__
```

## 📝 API 使用範例

### 啟動網格交易
```bash
curl -X POST "http://localhost:8000/api/grid/start" \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "BTCUSDT",
    "direction": "BOTH",
    "current_price": 42500,
    "upper_bound": 45000,
    "lower_bound": 40000,
    "grid_levels": 6,
    "total_amount": 1000,
    "user_id": "user123",
    "user_sig": "signature123"
  }'
```

### 停止網格交易
```bash
curl -X POST "http://localhost:8000/api/grid/stop" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "user123_BTCUSDT"
  }'
```

### 查詢會話狀態
```bash
curl "http://localhost:8000/api/grid/status/user123_BTCUSDT"
```

## 🧪 測試

### 運行所有測試
```bash
# 使用測試運行器（推薦）
python tests/run_tests.py

# 或直接使用 pytest
pytest tests/ -v
```

### 運行特定測試
```bash
# 單元測試
pytest tests/test_components.py -v

# API 測試
pytest tests/test_server.py -v

# 安全性測試
pytest tests/test_grid_safety.py -v

# 集成測試
pytest tests/test_integration.py --run-integration -v
```

### 生成覆蓋率報告
```bash
pytest tests/ --cov=src --cov-report=html
```

## 🛡️ 安全性特性

- **環境變數管理**：敏感資訊通過環境變數管理
- **輸入驗證**：Pydantic 模型驗證所有輸入
- **錯誤處理**：統一的錯誤碼和異常處理
- **併發保護**：防止競爭條件和重複操作
- **資源清理**：自動清理連接和狀態

## 📈 監控和日誌

### 結構化日誌
- JSON 格式日誌，便於分析
- 會話上下文追踪
- 事件類型分類
- 錯誤詳情記錄

### 系統指標
- API 請求計數和成功率
- 會話創建和停止統計
- 訂單執行指標
- 系統性能指標

## 🔄 網格交易策略

### 支援的交易方向
- **LONG（做多）**：只在價格下跌時買入
- **SHORT（做空）**：只在價格上漲時賣出
- **BOTH（雙向）**：價格上下波動都進行交易

### 網格邏輯
1. **初始掛單**：根據策略在關鍵價格點掛單
2. **成交觸發**：訂單成交後取消所有掛單
3. **反向掛單**：在新的價格點掛反向訂單
4. **循環執行**：持續執行直到停損或手動停止

## 🚨 注意事項

- 這是一個 **MVP 實現**，適用於測試和學習
- 請在測試網環境中充分測試後再考慮主網使用
- 建議設置適當的停損價格以控制風險
- 系統會自動處理 Orderly Network 的 API 速率限制（10 requests/second）

## 📚 開發指南

### 添加新功能
1. 在適當的模組中實現功能
2. 添加相應的測試
3. 更新文檔
4. 運行完整測試套件

### 調試技巧
- 使用結構化日誌查看詳細操作
- 檢查 `/metrics` 端點了解系統狀態
- 使用 `/health/ready` 檢查系統就緒狀態