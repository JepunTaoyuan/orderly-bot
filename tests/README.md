# 網格交易系統測試套件

這是一個全面的測試套件，用於驗證網格交易系統的所有組件和功能。測試使用硬編碼的環境變數，確保測試環境的一致性和可重複性。

## 🏗️ 測試結構

```
tests/
├── __init__.py              # 測試包初始化
├── conftest.py              # Pytest 配置和共享 fixtures
├── test_server.py           # FastAPI 伺服器測試
├── test_integration.py      # 端到端集成測試
├── test_components.py       # 單元組件測試
├── run_tests.py            # 測試運行器腳本
└── README.md               # 測試文檔（本文件）
```

## 🔧 環境設置

### 硬編碼環境變數

測試套件使用以下硬編碼的環境變數（從 `src/core/client.py` 獲取）：

```python
TEST_ENV_VARS = {
    "ORDERLY_KEY": "ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T",
    "ORDERLY_SECRET": "ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs",
    "ORDERLY_ACCOUNT_ID": "0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0"
}
```

這些是 Orderly 測試網的憑證，確保所有測試都在隔離的測試環境中運行。

### 依賴安裝

```bash
# 安裝核心依賴
pip install pytest pytest-asyncio httpx fastapi[all] python-multipart

# 可選：安裝覆蓋率工具
pip install pytest-cov
```

## 🚀 運行測試

### 方法 1: 使用測試運行器（推薦）

```bash
# 運行所有測試
python tests/run_tests.py

# 只運行單元測試
python tests/run_tests.py --test-type unit

# 只運行伺服器測試
python tests/run_tests.py --test-type server

# 只運行集成測試
python tests/run_tests.py --test-type integration

# 只運行性能測試
python tests/run_tests.py --test-type performance

# 生成覆蓋率報告
python tests/run_tests.py --coverage

# 自動安裝依賴
python tests/run_tests.py --install-deps
```

### 方法 2: 直接使用 pytest

```bash
# 設置環境變數並運行所有測試
export ORDERLY_KEY="ed25519:EpBR88faPoav78urb4MSeNxRaPTkxohXubgW5vBQwh1T"
export ORDERLY_SECRET="ed25519:FDoEfpUzcMKk5ZDd46Tk6seS6ed79jGmMVCSriQ2Jfqs"
export ORDERLY_ACCOUNT_ID="0x5e2cccd91ac05c8f1a9de15c629deffcf1de88abacf7bb7ac8d3b9d8e9317bb0"

# 運行所有測試
pytest tests/ -v

# 運行特定測試文件
pytest tests/test_server.py -v

# 運行帶覆蓋率的測試
pytest tests/ --cov=src --cov-report=html

# 運行集成測試
pytest tests/test_integration.py --run-integration -v

# 運行性能測試
pytest tests/test_integration.py::TestPerformance --run-performance -v
```

## 📋 測試類型

### 1. 單元測試 (`test_components.py`)

測試各個組件的獨立功能：

- **OrderlyClient**: API 客戶端功能
- **GridSignalGenerator**: 網格訊號生成邏輯
- **MarketValidator**: 市場驗證和價格標準化
- **OrderTracker**: 訂單追踪和成交記錄
- **LoggingAndMetrics**: 日誌記錄和指標收集

```bash
pytest tests/test_components.py -v
```

### 2. 伺服器測試 (`test_server.py`)

測試 FastAPI 伺服器的 API 端點：

- 健康檢查端點
- 網格交易啟動/停止
- 會話狀態查詢
- 指標收集
- 錯誤處理
- 參數驗證

```bash
pytest tests/test_server.py -v
```

### 3. 集成測試 (`test_integration.py`)

測試完整的端到端工作流程：

- 完整的網格交易生命週期
- 多會話並發處理
- 錯誤場景處理
- API 參數驗證
- 會話衝突處理

```bash
pytest tests/test_integration.py --run-integration -v
```

### 4. 性能測試 (`test_integration.py::TestPerformance`)

測試系統性能和併發處理能力：

- 併發 API 調用性能
- 指標端點響應時間
- 大數據量處理

```bash
pytest tests/test_integration.py::TestPerformance --run-performance -v
```

## 🎯 測試功能

### 模擬和存根

- **Orderly API 模擬**: 所有外部 API 調用都被模擬，避免實際網絡請求
- **WebSocket 模擬**: WebSocket 連接被模擬以測試實時功能
- **數據庫模擬**: 避免對實際數據庫的依賴

### 測試覆蓋範圍

- ✅ API 端點測試
- ✅ 業務邏輯測試
- ✅ 錯誤處理測試
- ✅ 參數驗證測試
- ✅ 併發處理測試
- ✅ 性能基準測試
- ✅ 配置驗證測試

### 測試數據

所有測試使用預定義的測試數據：

```python
# 樣本網格配置
{
    "ticker": "BTCUSDT",
    "direction": "BOTH",
    "current_price": 42500.0,
    "upper_bound": 45000.0,
    "lower_bound": 40000.0,
    "grid_levels": 6,
    "total_amount": 1000.0,
    "user_id": "test_user_123",
    "user_sig": "test_signature_456"
}
```

## 📊 測試報告

### 覆蓋率報告

生成 HTML 覆蓋率報告：

```bash
python tests/run_tests.py --coverage
# 或
pytest tests/ --cov=src --cov-report=html
```

報告將生成在 `htmlcov/index.html`

### 測試結果

測試運行後，你將看到：

- ✅ 成功的測試數量
- ❌ 失敗的測試詳情
- ⚠️ 跳過的測試原因
- 📊 覆蓋率統計
- ⏱️ 執行時間統計

## 🔧 故障排除

### 常見問題

1. **模組導入錯誤**
   ```bash
   # 確保在項目根目錄運行測試
   cd /path/to/orderly_bot
   python tests/run_tests.py
   ```

2. **環境變數未設置**
   ```bash
   # 使用測試運行器自動設置
   python tests/run_tests.py
   ```

3. **依賴缺失**
   ```bash
   # 自動安裝依賴
   python tests/run_tests.py --install-deps
   ```

4. **測試超時**
   ```bash
   # 只運行快速測試
   python tests/run_tests.py --test-type unit
   ```

### 調試技巧

1. **詳細輸出**
   ```bash
   pytest tests/ -v -s
   ```

2. **停在第一個失敗**
   ```bash
   pytest tests/ -x
   ```

3. **運行特定測試**
   ```bash
   pytest tests/test_server.py::TestGridTradingServer::test_health_check -v
   ```

4. **查看日誌**
   ```bash
   pytest tests/ --log-cli-level=INFO
   ```

## 🧪 測試最佳實踐

### 編寫新測試

1. **使用描述性測試名稱**
   ```python
   def test_should_create_limit_order_when_valid_parameters_provided(self):
   ```

2. **遵循 AAA 模式**
   ```python
   def test_example(self):
       # Arrange - 設置測試數據
       config = {...}
       
       # Act - 執行被測試的功能
       result = api_call(config)
       
       # Assert - 驗證結果
       assert result["status"] == "success"
   ```

3. **使用適當的 fixtures**
   ```python
   def test_with_client(self, client, mock_orderly_client):
       # 使用預配置的測試客戶端
   ```

4. **模擬外部依賴**
   ```python
   @patch('src.core.client.OrderlyClient')
   def test_with_mock(self, mock_client):
   ```

### 測試維護

- 定期運行完整測試套件
- 保持測試數據的最新性
- 及時更新模擬響應
- 監控測試覆蓋率
- 清理過時的測試

## 📈 持續集成

測試套件設計用於 CI/CD 流水線：

```yaml
# GitHub Actions 示例
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      - run: python tests/run_tests.py --install-deps --coverage
```

## 🤝 貢獻指南

1. 為新功能添加相應測試
2. 確保所有測試通過
3. 維持或提高測試覆蓋率
4. 更新測試文檔
5. 遵循現有的測試風格

---

**Happy Testing! 🧪✨**
