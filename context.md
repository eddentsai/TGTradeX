# TGTradeX — 技術背景與設計脈絡

此文件記錄專案的架構決策、模組職責與擴充慣例，供後續開發參考。

---

## 專案目標

提供兩種期貨交易自動化模式：

1. **自動交易服務**（`run_service.py`）：持續輪詢 K 線，自動識別市場狀態、切換策略、計算倉位並下單，無需人工介入
2. **Telegram Bot**（`main.py`）：接收使用者的文字指令，手動控制開平倉

兩者共用 `exchanges/` 層，互不干擾，可同時運行。

---

## 模組職責劃分

### `exchanges/`

**`exchanges/base.py`**
定義 `BaseExchange` 抽象類別，規定每個交易所都必須實作的方法：
- `name` — 交易所識別字串
- `get_account()` — 帳戶資訊（餘額、未實現盈虧）
- `get_pending_positions(symbol)` — 持倉查詢
- `get_pending_orders(symbol)` — 未完成訂單查詢
- `place_order(payload)` — 下單
- `cancel_order(order_id, symbol)` — 取消訂單
- `get_klines(symbol, interval, limit)` — K 線資料（由舊到新）

**`exchanges/bitunix/`**
Bitunix 非官方 Python SDK，原位於 `bitunix_sdk/`，重構後移入此處。內部相對 import 不受影響。`adapter.py` 將 `BitunixClient` 包裝成 `BaseExchange`，並處理欄位轉換（字串 → 浮點、時間排序）。

加入新交易所時，只需在 `exchanges/` 新增子目錄並實作 `adapter.py`，上層不需變動。

---

### `services/`

自動交易服務的核心，與 Telegram 完全無關。

**`services/indicators.py`**
純函式模組，無任何副作用，僅依賴標準函式庫（無 numpy/pandas）。

- `Candle` — 標準化 K 線資料結構
- `IndicatorSnapshot` — 單一時間點所有指標的快照
- `candles_from_raw(data)` — 將交易所原始 dict 轉為 `Candle` 列表
- `compute_indicators(candles)` — 計算所有指標，回傳 `IndicatorSnapshot`

計算的指標：
| 指標 | 週期 | 用途 |
|------|------|------|
| EMA | 20, 50, 200 | 趨勢方向、支撐阻力 |
| ADX / ±DI | 14 | 趨勢強度 |
| RSI | 14 | 超買超賣 |
| ATR | 14 | 波動幅度參考 |
| 布林帶 | 20, ±2σ | 帶寬、位置百分比 |
| 線性回歸斜率 | 20 | 趨勢方向驗證（正/負） |
| 近期波動率 | 20 | 報酬率標準差（%）|
| 成交量分佈（VA/POC）| 後 100 根 | 價值區上下限、成交量最大價 |

EMA 和 ADX 使用 Wilder 平滑法（與多數交易所圖表一致）。

**`services/market_state.py`**
`classify_market(snap)` 根據快照判斷市場狀態，優先順序：

1. 高波動率（> 3%）→ `HIGH_VOLATILITY`
2. ADX > 25 + EMA20 > EMA50 + 斜率 > 0 → `UPTREND`
3. ADX > 25 + EMA20 < EMA50 + 斜率 < 0 → `DOWNTREND`
4. ADX < 20 + BB 寬 < 3% → `RANGING`
5. 其餘 → `RANGING`（保守預設）

**`services/position_sizer.py`**
`PositionSizer` 實作固定風險比例（Fixed Fractional）倉位計算。

核心公式：
```
risk_amount    = account_balance × risk_pct
position_value = risk_amount ÷ sl_distance_pct
qty            = position_value ÷ entry_price
required_margin = position_value ÷ leverage
```

清算價估算（孤立保證金模式）：
```
多單清算 = entry × (1 - 1/leverage + mm_rate)
空單清算 = entry × (1 + 1/leverage - mm_rate)
```

三道安全保護：
1. 止損距清算價的緩衝必須 ≥ `min_sl_buffer_pct`（預設 15%），否則拒絕並給出建議 SL
2. `position_value` 上限 = `account_balance × leverage × max_position_pct`（預設 80%）
3. `qty` 不得低於精度最小值

`calculate()` 回傳 `SizeResult`（含 qty、保證金、清算價、實際風險%等），或回傳 `None` 表示不應開倉。

**`services/strategies/`**

`BaseStrategy.on_candle(snap, position)` → `Signal`。`position=None` 時判斷入場，否則判斷出場。`Signal` 包含 `action`（open_long / open_short / close / hold）、止損價、止盈價。

| 策略 | 觸發市場 | 入場條件 | 出場條件 |
|------|---------|---------|---------|
| `TrendFollowingStrategy` | UPTREND | EMA20 ±2% + RSI 30–65 + BB 20%–60% | EMA20 下彎 / RSI > 75 / SL-TP |
| `VolumeProfileStrategy` | RANGING | VAL ±1.5% + RSI < 40 | POC ±1.5% / SL-TP |
| `ConservativeStrategy` | DOWNTREND / HIGH_VOL | 不入場 | 有倉位則平倉 |

止損止盈由策略在 `Signal` 中設定（開倉時），存入 `ActivePosition`，後續由策略的 `_check_exit` 監控。

**`services/runner.py`**
`ServiceRunner` 的職責：
- 每根 K 線結束後執行一次 `_run_cycle()`
- 維護本地 `_active_pos: ActivePosition | None`，每週期與交易所倉位核對（`_reconcile_position`）
- 若服務重啟後發現未追蹤的倉位，以保守預設 SL/TP 重建（並記錄 warning）
- 若未取得 `position_id`（訂單剛填充），在平倉前即時查詢
- `dry_run=True` 時只記錄，不呼叫 `place_order`

初始化時必須提供 `sizer` 或 `fixed_qty` 其中之一，兩者皆無時拋出 `ValueError`。

**策略切換保護（`_handle_strategy_switch`）**

每週期在呼叫 `strategy.on_candle()` 之前，先比對 `active_pos.strategy_name` 與當前策略名稱：

| 情境 | 行為 |
|------|------|
| 策略未切換 | 不做任何事，正常交給策略管理 |
| 切換 + 持倉**虧損** | 立即平倉——開倉的市場條件已消失，不繼續賭反彈 |
| 切換 + 持倉**獲利** | 止損移至保本（entry price），讓新策略接管出場 |
| `strategy_name == "recovered"` | 跳過（重啟重建的倉位無法判斷原始策略）|

設計理由：SL/TP 在開倉時依當時策略邏輯設定，存入 `ActivePosition` 後不隨市場狀態自動更新。策略切換時才做一次性處置，保持行為可預測，避免在每根 K 線上動態修改止損。

---

### `bot/`

只負責與 Telegram 互動，不含任何交易所或策略邏輯。

**`bot/parser.py`**
無副作用的純函式模組。`parse(text)` 將字串解析為 `OrderRequest` 或 `QueryCommand`，格式錯誤時拋出 `ParseError`。指令設計原則：固定位置參數、空格分隔、方便手機輸入。

**`bot/listener.py`**
`TGListener` 使用 `python-telegram-bot` v20+（asyncio 架構）。依賴注入 `TradeDispatcher`，不自行建立交易所客戶端。

---

### `trader/`

TG Bot 的應用層，協調 bot 和 exchanges 之間的流程。

**`trader/models.py`**
`OrderRequest`（parser 產出、dispatcher 消費）及 enum 型別（`OrderSide`, `OrderType`, `TradeSide`）。qty/price 使用 `str` 型別避免浮點精度問題。

**`trader/dispatcher.py`**
`TradeDispatcher` 持有 `dict[str, BaseExchange]`，以交易所名稱（小寫）為 key，統一路由下單與查詢請求。

---

### `config/`

**`config/settings.py`**
從環境變數讀取設定，若安裝 `python-dotenv` 則自動載入 `.env`。`validate()` 在進入點最前面呼叫，缺少必要變數時立即報錯。

---

## 資料流

### 自動交易服務

```
run_service.py
    │ 組裝 exchange + sizer + runner
    ▼
ServiceRunner.run() — 每 interval 秒執行一次
    │
    ├─ exchange.get_klines()         取得 250 根 K 線
    ├─ compute_indicators(candles)   計算全部技術指標
    ├─ classify_market(snap)         識別市場狀態
    ├─ exchange.get_pending_positions() 核對倉位
    ├─ strategy.on_candle(snap, pos) 產生 Signal
    │
    └─ Signal.action
         ├─ "open_long/short"
         │    ├─ sizer.calculate(balance, entry, sl)  計算安全數量
         │    └─ exchange.place_order(payload)
         ├─ "close"
         │    └─ exchange.place_order(close_payload)
         └─ "hold"  → 無動作
```

### Telegram Bot

```
Telegram 使用者
    ▼ 文字指令
TGListener → bot/parser.parse(text)
    ▼ OrderRequest / QueryCommand
TradeDispatcher → BaseExchange.place_order() / get_*()
    ▼ 交易所回應
TGListener.reply_text()
    ▼
Telegram 使用者
```

---

## 擴充慣例

### 新增交易所

1. 建立 `exchanges/<name>/` 目錄
2. 實作 `adapter.py`，繼承 `BaseExchange`，`name` 屬性回傳小寫識別字串
3. 在 `config/settings.py` 新增 API key 環境變數
4. 在 `run_service.py` 的 `_build_exchange()` 加入 `elif name == "<name>"`
5. 在 `main.py` 加入 `dispatcher.register(<Name>Exchange(...))`

### 新增交易策略

1. 在 `services/strategies/` 新增檔案，繼承 `BaseStrategy`
2. 實作 `name`、`on_candle(snap, position)` → `Signal`
3. 在 `services/runner.py` 的 `self._strategies` dict 中指定對應的 `MarketState`

### 新增 TG 指令

1. 在 `bot/parser.py` 新增解析函式
2. 在 `bot/listener.py` 新增 `CommandHandler` 及 handler 方法
3. 若涉及新的業務邏輯，在 `trader/dispatcher.py` 新增對應方法

---

## 依賴版本

| 套件 | 用途 | 最低版本 |
|------|------|---------|
| `requests` | Bitunix HTTP API | 2.31 |
| `websocket-client` | Bitunix WebSocket | 1.7 |
| `python-telegram-bot` | Telegram Bot | 20.0（asyncio 版）|
| `python-dotenv` | 載入 .env 檔案 | 1.0 |

`services/` 層的技術指標計算只使用 Python 標準函式庫（無 numpy/pandas），減少部署依賴。

> `python-telegram-bot` v20 起採用 asyncio 架構，與 v13 以前的 API 不相容。

---

## 已知限制 / 未來方向

- **未實作 WebSocket 確認成交**：目前 `place_order` 只確認訂單送出，不等待成交事件。`exchanges/bitunix/trading_flow.py` 有 `run_futures_trading_flow` 可做完整生命週期管理，未來可整合至 runner。
- **position_id 延遲**：市價單填充後 position_id 需等下一週期才能從交易所取得。若需即時取得，可在開倉後加入短暫等待與即時查詢。
- **無持久化**：服務重啟後本地倉位狀態（SL/TP）從交易所重建，重建結果使用保守預設值（±5%）。
- **清算價為估算值**：孤立保證金模式下的清算價因交易所計算細節（維持保證金率、手續費）可能有誤差，實際以交易所顯示為準。
- **單一帳戶**：每個交易所目前只支援一組 API key，未來可在 adapter 層擴充多帳戶。
- **下降趨勢不做空**：`DOWNTREND` 目前對應 `ConservativeStrategy`，尚未實作空頭的趨勢跟隨。
