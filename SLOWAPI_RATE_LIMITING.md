# SlowAPI 速率限制器集成

## 概述

為 `@orderly_bot/` 項目集成了 `@refer_rebate/` 中使用的 SlowAPI 速率限制器，提供全面的 API 速率限制保護。

## 安裝依賴

```bash
pip install slowapi>=0.1.9
```

已在 `requirements.txt` 中添加依賴：
```
# Rate limiting
slowapi>=0.1.9
```

## 架構設計

### 1. **核心模組**

- `src/utils/slowapi_limiter.py` - SlowAPI 速率限制器核心實現
- `src/utils/slowapi_dependencies.py` - 依賴注入模組

### 2. **速率限制配置**

```python
RATE_LIMITS = {
    'global': '1000/minute',           # 全局：每分鐘1000次
    'per_user': '600/minute',          # 每用戶：每分鐘600次
    'auth': '120/minute',              # 認證端點：每分鐘120次
    'trading': '60/minute',            # 交易操作：每分鐘60次
    'status_check': '300/minute',      # 狀態檢查：每分鐘300次
    'grid_control': '30/minute',       # 網格控制：每分鐘30次
}
```

### 3. **端點分級保護**

#### 🔐 **認證級別** (120/minute)
- `/api/user/enable` - 用戶啟用
- `/api/auth/challenge` - 簽名挑戰

#### 🎮 **網格控制級別** (30/minute)
- `/api/grid/start` - 啟動網格交易
- `/api/grid/stop` - 停止網格交易

#### 📊 **狀態檢查級別** (300/minute)
- `/api/grid/status/{session_id}` - 狀態查詢

#### 🌐 **全局級別** (1000/minute)
- `/health` - 健康檢查
- 其他未分類端點

## 使用方式

### 1. **裝飾器方式**

```python
from src.utils.slowapi_limiter import limiter, RATE_LIMITS

@app.post("/api/user/enable")
@limiter.limit(RATE_LIMITS['auth'])
async def enable_bot_trading(request: Request, config: RegisterConfig):
    # 端點邏輯
    pass
```

### 2. **依賴注入方式**

```python
from src.utils.slowapi_dependencies import auth_rate_limit, trading_rate_limit

@app.post("/api/grid/start")
async def start_grid(
    request: Request,
    config: StartConfig,
    rate_limit_info: dict = Depends(auth_rate_limit)
):
    # 端點邏輯
    pass
```

### 3. **自動速率限制**

```python
from src.utils.slowapi_dependencies import auto_rate_limit

@app.get("/api/some/endpoint")
async def some_endpoint(
    request: Request,
    rate_limit_info: dict = Depends(auto_rate_limit)
):
    # 根據路徑自動選擇速率限制
    pass
```

## 錯誤處理

### **速率限制超出響應**

```json
{
  "error": "Rate limit exceeded",
  "message": "120 per 1 minute",
  "retry_after": 60
}
```

### **HTTP 狀態碼**
- `429 Too Many Requests` - 速率限制超出

## 安全特性

### 🔒 **Key 策略**

1. **IP 地址**: `get_remote_address(request)`
2. **用戶ID**: `request.headers.get("X-User-ID")`
3. **會話ID**: `request.headers.get("X-Session-ID")`
4. **用戶代理**: 組合使用提高唯一性

### 🛡️ **防護機制**

1. **重放攻擊防護**: 組合多個識別因子
2. **分級保護**: 根據端點重要性設置不同限制
3. **自動清理**: 內存存儲自動過期
4. **日誌監控**: 詳細記錄速率限制事件

## 監控和日誌

### **日誌格式**

```json
{
  "timestamp": "2025-10-08T15:30:00Z",
  "level": "WARNING",
  "message": "速率限制觸發: 120 per 1 minute",
  "component": "slowapi_limiter",
  "data": {
    "path": "/api/auth/challenge",
    "method": "GET",
    "ip": "192.168.1.100",
    "user_agent": "Mozilla/5.0...",
    "limit_detail": "120 per 1 minute"
  }
}
```

### **監控指標**

- 速率限制觸發次數
- 受影響的 IP 地址
- 端點使用模式
- 異常流量檢測

## 測試

### **運行測試**

```bash
# 啟動服務器
python app.py

# 運行速率限制測試
python test_slowapi_rate_limit.py
```

### **測試覆蓋**

- ✅ 基本速率限制功能
- ✅ 不同端點類型限制
- ✅ 併發請求處理
- ✅ 錯誤響應格式
- ✅ 配置驗證

## 配置自定義

### **修改限制值**

```python
# 在 src/utils/slowapi_limiter.py 中修改
RATE_LIMITS = {
    'global': '2000/minute',      # 提高全局限制
    'auth': '200/minute',         # 提高認證限制
    # ... 其他限制
}
```

### **添加新的端點類型**

```python
# 添加新的限制類型
RATE_LIMITS['custom_type'] = '50/minute'

# 創建對應的裝飾器
def create_custom_rate_limit():
    return limiter.limit(RATE_LIMITS['custom_type'])
```

## 性能考量

### **內存使用**
- 使用內存存儲，無需外部依賴
- 自動過期清理，防止內存洩漏
- 輕量級實現，最小性能影響

### **併發處理**
- 支持高併發請求
- 異步檢查機制
- 不阻塞主要業務邏輯

## 故障排除

### **常見問題**

1. **速率限制不生效**
   - 檢查裝飾器順序
   - 確認 Request 參數位置
   - 驗證 slowapi 版本

2. **限制過於嚴格**
   - 調整 RATE_LIMITS 配置
   - 考慮業務需求
   - 監控觸發頻率

3. **性能影響**
   - 監控響應時間
   - 調整限制策略
   - 優化 key 函數

## 維護建議

1. **定期監控**: 檢查速率限制觸發情況
2. **調整策略**: 根據實際使用情況調整限制值
3. **日誌分析**: 分析異常流量模式
4. **性能測試**: 定期進行負載測試

---

**集成完成日期**: 2025-10-08
**安全等級**: 🔒 已保護
**測試狀態**: ✅ 通過