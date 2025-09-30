# Grid Trading Bot API 文檔

## 基本資訊

- **Base URL**: `http://localhost:8000`
- **API Version**: `1.0.0`
- **Content-Type**: `application/json`

## 標準回應格式

所有 API 回應遵循統一格式：

### 成功回應
```json
{
  "success": true,
  "data": {
    // 回應資料
  }
}
```

### 錯誤回應
```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "錯誤描述",
    "details": {
      // 錯誤詳細資訊
    }
  }
}
```

---

## API Endpoints

### 1. 認證相關

#### 1.1 獲取簽名挑戰
生成用於錢包簽名驗證的挑戰資料。

**Endpoint**: `GET /api/auth/challenge`

**回應範例**:
```json
{
  "success": true,
  "data": {
    "timestamp": 1234567890,
    "nonce": "randomBase64String==",
    "message": "Please sign this message to confirm your identity.\nTimestamp: 1234567890\nNonce: randomBase64String=="
  }
}
```

**說明**:
- `timestamp`: Unix 時間戳（秒），用於防止重放攻擊
- `nonce`: 隨機生成的 base64 編碼字串
- `message`: 需要用戶錢包簽名的完整訊息

---

### 2. 用戶管理

#### 2.1 啟用機器人交易
註冊新用戶並儲存 API 憑證。

**Endpoint**: `POST /api/user/enable`

**請求體**:
```json
{
  "user_id": "user123",
  "user_api_key": "your_orderly_api_key",
  "user_api_secret": "your_orderly_api_secret",
  "user_wallet_address": "0x1234...abcd"
}
```

**欄位說明**:
- `user_id`: 用戶唯一識別碼
- `user_api_key`: Orderly Network API Key
- `user_api_secret`: Orderly Network API Secret
- `user_wallet_address`: 用戶錢包地址（EVM 或 Solana）

**成功回應**:
```json
{
  "success": true,
  "data": {
    "user_id": "user123"
  }
}
```

**錯誤代碼**:
- `USER_ALREADY_EXISTS`: 用戶已存在
- `USER_CREATION_FAILED`: 創建用戶失敗

---

#### 2.2 檢查用戶是否存在
檢查指定用戶是否已註冊。

**Endpoint**: `GET /api/user/check/{user_id}`

**路徑參數**:
- `user_id`: 用戶ID

**成功回應（用戶存在）**:
```json
{
  "success": true,
  "data": {
    "exists": true,
    "user_id": "user123",
    "wallet_address": "0x1234...abcd"
  }
}
```

**成功回應（用戶不存在）**:
```json
{
  "success": true,
  "data": {
    "exists": false,
    "user_id": "user123"
  }
}
```

**錯誤代碼**:
- `INTERNAL_SERVER_ERROR`: 檢查過程發生錯誤

---

#### 2.3 更新用戶資料
更新用戶的 API 憑證。

**Endpoint**: `PUT /api/user/update`

**請求體**:
```json
{
  "user_id": "user123",
  "user_api_key": "new_api_key",
  "user_api_secret": "new_api_secret"
}
```

**成功回應**:
```json
{
  "success": true,
  "data": {
    "user_id": "user123"
  }
}
```

**錯誤代碼**:
- `USER_NOT_FOUND`: 用戶不存在
- `USER_UPDATE_FAILED`: 更新失敗

---

### 3. 網格交易管理

#### 3.1 啟動網格交易
創建並啟動新的網格交易會話。

**Endpoint**: `POST /api/grid/start`

**請求體**:
```json
{
  "ticker": "BTCUSDT",
  "direction": "BOTH",
  "current_price": 42500,
  "upper_bound": 45000,
  "lower_bound": 40000,
  "grid_levels": 6,
  "total_amount": 100,
  "stop_bot_price": 38000,
  "stop_top_price": 47000,
  "user_id": "user123",
  "user_sig": "0x...",
  "timestamp": 1234567890,
  "nonce": "randomNonce"
}
```

**欄位說明**:
- `ticker`: 交易對（必須以 USDT 結尾）
- `direction`: 交易方向
  - `LONG`: 做多（只掛買單）
  - `SHORT`: 做空（只掛賣單）
  - `BOTH`: 雙向（買賣都掛）
- `current_price`: 當前價格（必須在上下界範圍內）
- `upper_bound`: 價格上界
- `lower_bound`: 價格下界
- `grid_levels`: 網格格數（≥2）
- `total_amount`: 總投入金額（USDT）
- `stop_bot_price`: 可選，下界停損價格（必須 < lower_bound）
- `stop_top_price`: 可選，上界停損價格（必須 > upper_bound）
- `user_id`: 用戶ID
- `user_sig`: 錢包簽名（需先調用 `/api/auth/challenge` 獲取挑戰）
- `timestamp`: 簽名時的時間戳（5分鐘內有效）
- `nonce`: 簽名時使用的 nonce

**成功回應**:
```json
{
  "success": true,
  "data": {
    "status": "started",
    "session_id": "user123_BTCUSDT"
  }
}
```

**錯誤代碼**:
- `USER_NOT_FOUND`: 用戶不存在
- `INVALID_SIGNATURE`: 簽名驗證失敗
- `UNKNOWN_WALLET_TYPE`: 無法識別的錢包類型
- `SESSION_ALREADY_EXISTS`: 會話已存在
- `INVALID_GRID_CONFIG`: 網格配置無效

**簽名流程**:
1. 調用 `GET /api/auth/challenge` 獲取 `timestamp`, `nonce`, `message`
2. 使用錢包對 `message` 進行簽名，得到 `user_sig`
3. 在 5 分鐘內使用 `timestamp`, `nonce`, `user_sig` 調用此 API

---

#### 3.2 停止網格交易
停止指定的網格交易會話。

**Endpoint**: `POST /api/grid/stop`

**請求體**:
```json
{
  "session_id": "user123_BTCUSDT",
  "user_sig": "0x...",
  "timestamp": 1234567890,
  "nonce": "randomNonce"
}
```

**欄位說明**:
- `session_id`: 會話ID（格式: `{user_id}_{ticker}`）
- `user_sig`: 錢包簽名
- `timestamp`: 簽名時的時間戳
- `nonce`: 簽名時使用的 nonce

**成功回應**:
```json
{
  "success": true,
  "data": {
    "status": "stopped",
    "session_id": "user123_BTCUSDT"
  }
}
```

**錯誤代碼**:
- `INVALID_SESSION_ID`: 會話ID格式無效
- `USER_NOT_FOUND`: 用戶不存在
- `INVALID_SIGNATURE`: 簽名驗證失敗
- `SESSION_NOT_FOUND`: 會話不存在
- `SESSION_STOP_FAILED`: 停止失敗

---

#### 3.3 獲取會話狀態
查詢指定網格交易會話的詳細狀態。

**Endpoint**: `GET /api/grid/status/{session_id}`

**路徑參數**:
- `session_id`: 會話ID

**成功回應**:
```json
{
  "success": true,
  "data": {
    "is_running": true,
    "active_orders_count": 5,
    "active_orders": {
      "123456": {
        "price": 42000,
        "side": "BUY",
        "quantity": 0.01
      }
    },
    "grid_orders": {
      "42000": 123456
    },
    "order_statistics": {
      "total_orders": 10,
      "filled_orders": 5,
      "active_orders": 5,
      "total_fills": 8,
      "fill_rate": 0.5
    },
    "event_queue_size": 0,
    "account_info": { /* Orderly 帳戶信息 */ },
    "positions": { /* 持倉信息 */ }
  }
}
```

**錯誤代碼**:
- `SESSION_NOT_FOUND`: 會話不存在

---

#### 3.4 列出所有會話
獲取當前所有活躍會話列表。

**Endpoint**: `GET /api/grid/sessions`

**成功回應**:
```json
{
  "success": true,
  "data": {
    "sessions": {
      "user123_BTCUSDT": true,
      "user456_ETHUSDT": false
    }
  }
}
```

**說明**:
- Key: 會話ID
- Value: 是否正在運行

---

### 4. 健康檢查

#### 4.1 基本健康檢查
檢查服務是否正常運行。

**Endpoint**: `GET /health`

**成功回應**:
```json
{
  "status": "healthy",
  "timestamp": 1234567890.123,
  "version": "1.0.0"
}
```

---

#### 4.2 就緒檢查
檢查服務是否準備好接收請求。

**Endpoint**: `GET /health/ready`

**成功回應**:
```json
{
  "status": "ready",
  "timestamp": 1234567890.123,
  "active_sessions": 3
}
```

---

### 5. 系統監控

#### 5.1 獲取系統指標
獲取系統運行指標數據。

**Endpoint**: `GET /metrics`

**查詢參數**:
- `limit_counters`: 限制 counter 數量（默認: 10）
- `limit_gauges`: 限制 gauge 數量（默認: 5）
- `limit_histograms`: 限制 histogram 數量（默認: 3）

**成功回應**:
```json
{
  "counters": {
    "api.grid.start.requests": 150,
    "api.grid.start.success": 145,
    "api.grid.stop.requests": 50
  },
  "gauges": {
    "active_sessions": 5
  },
  "histograms": {
    "order.fill_price": [42000, 42100, 42200]
  }
}
```

---

### 6. 其他

#### 6.1 根路徑
API 基本信息。

**Endpoint**: `GET /`

**成功回應**:
```json
{
  "message": "Dexless Bot API",
  "version": "1.0.0",
  "WHATUP": "BRO"
}
```

---

## 錯誤代碼參考

| 錯誤代碼 | HTTP 狀態碼 | 說明 |
|---------|-----------|------|
| `USER_ALREADY_EXISTS` | 409 | 用戶已存在 |
| `USER_NOT_FOUND` | 404 | 用戶不存在 |
| `USER_CREATION_FAILED` | 500 | 創建用戶失敗 |
| `USER_UPDATE_FAILED` | 500 | 更新用戶失敗 |
| `INVALID_SIGNATURE` | 401 | 簽名驗證失敗 |
| `UNKNOWN_WALLET_TYPE` | 400 | 無法識別的錢包類型 |
| `INVALID_SESSION_ID` | 400 | 會話ID格式無效 |
| `SESSION_ALREADY_EXISTS` | 409 | 會話已存在 |
| `SESSION_NOT_FOUND` | 404 | 會話不存在 |
| `SESSION_CREATE_FAILED` | 500 | 創建會話失敗 |
| `SESSION_STOP_FAILED` | 500 | 停止會話失敗 |
| `INVALID_GRID_CONFIG` | 400 | 網格配置無效 |
| `INTERNAL_SERVER_ERROR` | 500 | 內部服務器錯誤 |

---

## 安全性

### 錢包簽名驗證
所有需要用戶授權的操作（啟動/停止網格交易）都需要錢包簽名驗證：

1. **防重放攻擊**: 每個簽名包含時間戳和 nonce，5分鐘內有效
2. **支持的錢包類型**:
   - EVM 錢包（如 MetaMask）
   - Solana 錢包（如 Phantom）

### 簽名驗證流程
```javascript
// 前端範例（使用 ethers.js）
const challenge = await fetch('/api/auth/challenge').then(r => r.json());
const signature = await signer.signMessage(challenge.data.message);

// 使用簽名調用 API
await fetch('/api/grid/start', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    // ... 其他參數
    user_sig: signature,
    timestamp: challenge.data.timestamp,
    nonce: challenge.data.nonce
  })
});
```

---

## 網格交易策略說明

### 做多策略 (LONG)
- 在 `current_price` 下方掛買單
- 價格下跌時成交，價格回升時獲利

### 做空策略 (SHORT)
- 在 `current_price` 上方掛賣單
- 價格上漲時成交，價格回落時獲利

### 雙向策略 (BOTH)
- 在 `current_price` 上下各掛一格買賣單
- 價格波動時雙向獲利

### 網格運作機制
1. 初始設置：根據配置在價格範圍內掛單
2. 訂單成交：當價格觸及網格價格時訂單成交
3. 動態調整：成交後取消所有掛單，在新價位重新掛單
4. 停損機制：價格觸及停損價格時自動停止

---

## 使用範例

### 完整流程範例

```bash
# 1. 註冊用戶
curl -X POST http://localhost:8000/api/user/enable \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "alice",
    "user_api_key": "your_api_key",
    "user_api_secret": "your_api_secret",
    "user_wallet_address": "0x1234...abcd"
  }'

# 2. 獲取簽名挑戰
curl http://localhost:8000/api/auth/challenge

# 3. 使用錢包簽名後啟動網格交易
curl -X POST http://localhost:8000/api/grid/start \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "BTCUSDT",
    "direction": "BOTH",
    "current_price": 42500,
    "upper_bound": 45000,
    "lower_bound": 40000,
    "grid_levels": 6,
    "total_amount": 100,
    "user_id": "alice",
    "user_sig": "0x...",
    "timestamp": 1234567890,
    "nonce": "random_nonce"
  }'

# 4. 查詢狀態
curl http://localhost:8000/api/grid/status/alice_BTCUSDT

# 5. 停止交易（需要重新獲取簽名挑戰）
curl -X POST http://localhost:8000/api/grid/stop \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "alice_BTCUSDT",
    "user_sig": "0x...",
    "timestamp": 1234567890,
    "nonce": "new_random_nonce"
  }'
```

---

## 注意事項

1. **時間戳有效期**: 簽名挑戰的時間戳在 5 分鐘內有效
2. **Nonce 唯一性**: 每個 nonce 只能使用一次，防止重放攻擊
3. **會話唯一性**: 同一個用戶在同一個交易對上只能有一個活躍會話
4. **API 密鑰安全**: 用戶的 API 密鑰儲存在 MongoDB 中，建議在生產環境加密存儲
5. **價格範圍**: 確保 `lower_bound < current_price < upper_bound`
6. **網格格數**: 至少需要 2 格，建議 5-20 格之間
7. **總投入金額**: 會根據網格格數平均分配到每個價格水平

---

## 更新日誌

### v1.0.0 (2024-01)
- 初始版本發布
- 支持 EVM 和 Solana 錢包簽名驗證
- 支持 LONG/SHORT/BOTH 三種網格策略
- 支持停損機制
- 實作非同步 MongoDB 連接
- WebSocket 實時訂單監控

---

## 聯繫方式

如有問題或建議，請通過以下方式聯繫：
- GitHub Issues: [項目地址]
- Email: [聯繫郵箱]