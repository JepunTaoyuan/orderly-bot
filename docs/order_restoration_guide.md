# 網格訂單恢復功能指南

## 概述

網格訂單恢復功能可以自動檢測用戶意外取消的網格訂單並嘗試恢復它們，確保網格交易策略的連續性和穩定性。

## 功能特性

### 🔍 **檢測機制**
- **WebSocket 實時檢測**: 監聽 `ORDER_CANCELLATION` 事件
- **定期同步**: 每分鐘同步訂單狀態，捕獲錯過的事件
- **智能分類**: 自動區分用戶取消、系統取消、過期等類型

### 🔄 **恢復策略**
- **SMART** (推薦): 僅恢復用戶取消和外部檢測到的訂單
- **USER_ONLY**: 僅恢復用戶取消的訂單
- **ALL**: 恢復所有取消的訂單
- **NEVER**: 從不恢復訂單

### 🛡️ **安全機制**
- **頻率限制**: 每小時最大恢復次數限制
- **時間窗口**: 避免恢復太久以前的訂單
- **價格檢查**: 確保當前價格仍在合理範圍內
- **歷史記錄**: 詳細的恢復和取消歷史

## 配置選項

### 基本配置

```python
{
    "restoration_policy": "smart",           # 恢復策略
    "max_restore_window_seconds": 300,       # 恢復時間窗口 (5分鐘)
    "max_price_deviation_percent": 2.0,      # 最大價格偏差 (2%)
    "max_restoration_attempts_per_hour": 10, # 每小時最大恢復次數
    "order_sync_interval_seconds": 60,       # 同步間隔 (60秒)
    "enable_price_check": true,              # 啟用價格檢查
    "enable_time_window_check": true         # 啟用時間窗口檢查
}
```

### 取消原因映射

```python
{
    "USER_CANCELLED": "user_cancelled",
    "USER_CANCELED": "user_cancelled",
    "CANCELLED_BY_USER": "user_cancelled",
    "USER_REQUESTED_CANCEL": "user_cancelled",
    "INSUFFICIENT_MARGIN": "system_cancelled",
    "POSITION_LIMIT": "system_cancelled",
    "RISK_LIMIT": "system_cancelled",
    "EXPIRED": "expired",
    "EXTERNAL_CANCEL_DETECTED": "external_detected"
}
```

## 使用方法

### 1. 動態配置恢復設置

```python
# 配置恢復策略
config = {
    "restoration_policy": "smart",
    "max_restore_window_seconds": 600,  # 10分鐘窗口
    "max_restoration_attempts_per_hour": 5
}

grid_bot.configure_restoration(config)
```

### 2. 獲取當前配置

```python
current_config = grid_bot.get_restoration_config()
print(current_config)
```

### 3. 查看恢復統計

```python
stats = grid_bot.get_restoration_statistics()
print(f"已恢復訂單數: {stats['orders_restored']}")
print(f"當前小時恢復次數: {stats['rate_limit']['attempts_this_hour']}")
print(f"最近24小時恢復次數: {stats['rate_limit']['attempts_last_24h']}")
```

## 監控和日誌

### 關鍵指標

- **orders.restored**: 成功恢復的訂單數量
- **orders.restoration_failed**: 恢復失敗的訂單數量
- **orders.restoration_errors**: 恢復過程中的錯誤數量
- **orders.cancelled**: 取消的訂單數量（按原因和類型分類）

### 事件類型

- `order_cancellation_detected`: 檢測到訂單取消
- `order_restoration_start`: 開始恢復訂單
- `order_restoration_success`: 訂單恢復成功
- `order_restoration_failed`: 訂單恢復失敗
- `order_restoration_error`: 恢復過程異常

### 日誌示例

```
2024-01-01 12:00:00 INFO  檢測到網格訂單取消 event_type=order_cancellation_detected data={
    "order_id": "123456",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "cancel_reason": "USER_CANCELLED",
    "cancel_type": "user_cancelled"
}

2024-01-01 12:00:01 INFO  開始恢復訂單 event_type=order_restoration_start data={
    "original_order_id": "123456",
    "price": "50000.0",
    "side": "BUY"
}

2024-01-01 12:00:02 INFO  訂單恢復成功 event_type=order_restoration_success data={
    "original_order_id": "123456",
    "new_order_id": "789012",
    "price": "50000.0",
    "side": "BUY"
}
```

## 最佳實踐

### 1. **推薦配置**
- 使用 `SMART` 恢復策略
- 設置合理的時間窗口 (5-10分鐘)
- 啟用價格檢查避免極端情況
- 限制恢復頻率防止循環

### 2. **監控要點**
- 監控恢復成功率
- 關注恢復頻率限制觸發
- 檢查價格偏差導致的恢復失敗
- 觀察取消原因分布

### 3. **故障排除**
- 如果恢復頻繁失敗，檢查網絡連接和API限制
- 如果價格偏差過大，考慲調整偏差閾值
- 如果恢復次數過多，可能需要檢查網格策略配置

## 安全注意事項

1. **頻率限制**: 防止因錯誤配置導致的大量恢復嘗試
2. **價格檢查**: 避免在市場劇烈波動時恢復訂單
3. **時間窗口**: 防止恢復過時的訂單
4. **系統狀態**: 確保機器人運行狀態正常時才進行恢復

## 技術細節

### 恢復流程
1. 檢測訂單取消事件
2. 分類取消原因
3. 判斷是否需要恢復
4. 檢查恢復條件 (頻率、時間、價格)
5. 創建新訂單
6. 記錄恢復結果

### 數據結構
- `restoration_history`: 恢復歷史記錄
- `cancellation_history`: 取消歷史記錄
- `restoration_attempts`: 頻率限制追蹤
- `order_statistics`: 訂單統計信息

---

通過這個功能，網格交易系統可以更好地處理意外情況，提高系統的穩定性和用戶體驗。