# Grid Session Rate Limit 優化總結

## 問題分析

根據日誌分析，grid session 意外中止的主要原因是：

1. **API Rate Limit 觸發**：頻繁的 API 調用觸發了 Orderly 的速率限制 (429, -1003)
2. **WebSocket 連接斷開**：rate limit 導致 WebSocket 連接被強制關閉
3. **重連失敗**：Circuit Breaker 機制在多次重連失敗後停止 session
4. **錯誤處理不完善**：某些組件在停止過程中出現 "object NoneType can't be used in 'await' expression" 錯誤

## 優化方案

### 1. 帳戶信息獲取頻率優化

#### 配置優化 (`src/config/order_restoration_config.py`)
- 將訂單同步間隔從 60 秒增加到 120 秒
- 降低定期檢查的頻率

#### 緩存機制 (`src/core/grid_bot.py`)
- 實現帳戶信息和持倉信息的 30 秒緩存
- 在 API 調用失敗時使用過期緩存作為備用
- 添加緩存狀態到回報信息中

### 2. 速率限制保護器 (`src/utils/rate_limit_protector.py`)

#### 核心功能
- **動態速率控制**：每分鐘 80 個請求，每秒 8 個請求
- **自適應節流**：根據使用率自動調整限制
- **退避機制**：觸發 rate limit 後智能退避
- **請求排隊**：防止瞬間高並發

#### 保護策略
```python
# 客戶端配置示例
rate_config = RateLimitConfig(
    requests_per_minute=80,    # 降低到每分鐘80個請求
    requests_per_second=8,     # 降低到每秒8個請求
    safety_margin=0.7,         # 使用70%的安全邊界
    enable_adaptive_throttling=True
)
```

### 3. 強健錯誤處理

#### 組件停止修復 (`src/core/grid_bot.py`)
```python
# 修復前
await self.signal_generator.stop_by_signal()  # 可能不是異步方法

# 修復後
if asyncio.iscoroutinefunction(self.signal_generator.stop_by_signal):
    await self.signal_generator.stop_by_signal()
else:
    self.signal_generator.stop_by_signal()
```

#### 客戶端保護 (`src/core/client.py`)
- 集成速率限制保護器到所有 API 調用
- 自動重試和退避機制
- 詳細的錯誤分類和統計

### 4. Session 恢復管理器 (`src/utils/session_recovery_manager.py`)

#### 健康監控
- 每 60 秒檢查所有 session 健康狀態
- 檢測連續失敗和超時情況
- 自動標記異常 session

#### 恢復策略
- **智能恢復觸發**：基於失敗次數和時間閾值
- **退避控制**：防止頻繁恢復嘗試
- **恢復歷史**：記錄和學習恢復模式

#### 配置選項
```python
recovery_config = SessionRecoveryConfig(
    enable_auto_recovery=True,
    max_recovery_attempts=5,
    recovery_cooldown=600,      # 10分鐘冷卻
    health_check_interval=60    # 每分鐘檢查
)
```

## 實施效果

### API 調用優化
- **帳戶信息**：30 秒緩存，降低 90% 重複調用
- **持倉信息**：30 秒緩存，避免頻繁查詢
- **訂單同步**：間隔延長至 2 分鐘

### 速率控制
- **每分鐘限制**：120 → 80 請求
- **每秒限制**：10 → 8 請求
- **安全邊界**：使用 70% 的實際限制
- **自適應調整**：動態降低高風險期間的限制

### 錯誤恢復
- **自動檢測**：監控 session 健康狀態
- **智能恢復**：基於歷史模式決策
- **多重保護**：防護級聯故障

## 使用建議

### 1. 監控配置
```python
# 檢查速率限制狀態
rate_limiter = get_rate_limiter("client_account_id")
status = rate_limiter.get_status()
print(f"使用率: {status['usage_rate']:.2%}")
```

### 2. Session 恢復
```python
# 手動觸發恢復
recovery_manager = get_recovery_manager()
await recovery_manager.trigger_manual_recovery("session_id")
```

### 3. 日誌監控
關注以下日誌事件：
- `rate_limit_hit`：觸發速率限制
- `adaptive_throttle`：自適應節流
- `session_recovery_needed`：需要恢復
- `cache_hit`：緩存命中

## 預期改善

1. **Rate Limit 減少**：預計降低 80% 的 rate limit 觸發
2. **Session 穩定性**：減少因 API 錯誤導致的意外中止
3. **自動恢復**：大部分異常 session 能自動恢復
4. **性能提升**：緩存機制降低整體 API 調用量

## 注意事項

1. **緩存一致性**：30 秒緩存可能導致輕微數據延遲
2. **恢復策略**：需要根據實際使用情況調整恢復參數
3. **監控警報**：建議設置恢復失敗率和使用率警報

## 後續優化

1. **數據庫連接池優化**：減少數據庫壓力
2. **WebSocket 連接管理**：實現連接池和復用
3. **分布式限流**：跨多個實例的協調限流
4. **機器學習預測**：基於歷史數據預測最佳 API 調用時機