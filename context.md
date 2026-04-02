# TGTradeX — 技術背景與設計脈絡

此文件記錄專案的架構決策、模組職責與擴充慣例，供後續開發參考。

---

## 專案目標

提供三種期貨交易自動化模式：

1. **Ensemble 多策略掃描服務**（`run_mix_strategies.py`）：自動掃描高流動性山寨幣，三策略多數決開倉，推薦用於山寨幣交易
2. **自動掃描交易服務**（`run_auto.py`）：自動掃描並依市場狀態自動切換策略，適合市場狀態較明確時
3. **單一幣種自動服務**（`run_service.py`）：固定監控指定幣種，自動識別市場狀態、切換策略、計算倉位並下單
4. **Telegram Bot**（`main.py`）：接收使用者文字指令，手動控制開平倉

三者共用 `exchanges/` 層，互不干擾，可同時運行。

---

## 模組職責劃分

### `exchanges/`

**`exchanges/base.py`**
定義 `BaseExchange` 抽象類別，規定每個交易所都必須實作的方法：

| 方法 | 說明 |
|------|------|
| `name` | 交易所識別字串（小寫） |
| `get_account()` | 帳戶資訊（餘額、未實現盈虧） |
| `get_pending_positions(symbol?)` | 持倉查詢；`symbol=None` 回傳全部 |
| `get_pending_orders(symbol?)` | 未完成訂單查詢 |
| `place_order(payload)` | 下單 |
| `cancel_order(order_id, symbol)` | 取消單筆訂單 |
| `cancel_all_orders(symbol)` | 取消該交易對所有掛單（含 SL/TP 條件單）|
| `place_sl_tp_orders(symbol, side, qty, sl_price, tp_price, position_id?)` | 對現有倉位補掛 SL/TP |
| `get_klines(symbol, interval, limit)` | K 線資料（由舊到新） |
| `get_qty_precision(symbol)` | 數量小數位數 |
| `get_tickers()` | 所有合約行情摘要（正規化欄位：`symbol`, `last_price`, `quote_vol`, `base_vol`, `high`, `low`, `change_pct`）|

**`exchanges/bitunix/`**

`BitunixHttpTransport`（`http.py`）在 HTTP 層實作速率限制：模組級 `threading.Lock` + `time.sleep(0.125s)`，8 req/s 上限，多執行緒共用同一個 lock。

`BitunixFuturesPrivateHttp`（`futures_private_http.py`）包含下列端點：
- `place_order` / `cancel_orders` / `cancel_all_orders`
- `place_tpsl_order` — 專用 tpsl 端點（`/api/v1/futures/tpsl/place_order`），對現有倉位設定條件 SL
- `get_pending_tpsl_orders` / `cancel_tpsl_order` — 查詢與取消 tpsl 條件單

`BitunixExchange`（`adapter.py`）的重要邏輯：
- `place_order`：自動剝離 `tpPrice/tpStopType/tpOrderType/tpOrderPrice` 欄位，開倉完成後補掛獨立限價減倉單作為 TP（maker 費率）；SL 使用 `slPrice` + `slStopType=MARK_PRICE`
- `place_sl_tp_orders`：SL 呼叫 `place_tpsl_order`（條件市價單，需 `position_id`）；TP 補掛限價 `reduceOnly` 單
- `cancel_all_orders`：批次取消一般掛單，再逐筆取消 tpsl 條件單
- `get_price_precision`：從 `quotePrecision`（或 `pricePrecision`/`priceDecimal`）欄位取得，用於 `slPrice` 對齊
- `_get_pair_info`：快取交易對規格，避免重複 API 呼叫

**`exchanges/binance/`**

`BinanceExchange`（`adapter.py`）的重要邏輯：
- `_load_symbol_filters()`：啟動時一次性從 `/fapi/v1/exchangeInfo` 取得全部 `PRICE_FILTER.tickSize` 和數量精度，快取於 `_tick_size_cache` / `_qty_precision_cache`
- `_align_price(price, symbol)`：按 `tickSize` 對齊價格（Binance `-4014` 錯誤的根因是 `pricePrecision` ≠ `tickSize`，必須用後者對齊）
- `place_order` 與 `place_sl_tp_orders` 均呼叫 `_align_price` 確保價格合規

加入新交易所時，只需在 `exchanges/` 新增子目錄並實作 `adapter.py`，上層不需變動。

---

### `services/`

**`services/symbol_scanner.py`**

`SymbolScanner` 從交易所取得所有合約 24h ticker，過濾出適合監控的交易對。

建構參數：
- `exchange`：取成交量排名用的交易所（建議 Binance，流動性數據更準）
- `trade_exchange`：實際下單用的交易所；若與 `exchange` 不同，掃描結果自動取兩邊的**上市交集**，避免掃到下單交易所沒有的合約
- `min_quote_vol`：24h 最低 USDT 成交量門檻
- `max_change_pct`：24h 漲跌幅絕對值上限（預設 40%）；超過此值表示近期出現異常行情（例如上幣炒作或崩盤），K 線形態已失真，排除

過濾規則（依序）：
1. 必須以 `USDT` 結尾
2. 排除穩定幣對（USDC、BUSD、TUSD、DAI 等）
3. 排除槓桿代幣（UP/DOWN/BULL/BEAR/3L/3S 等結尾）
4. 排除主流幣黑名單（BTC/ETH/BNB/SOL/XRP/ADA/DOGE/AVAX/DOT/LTC/LINK/UNI/ATOM/PAXG/XAU/XAG 等，`exclude_mainstream=True` 時生效）
5. 若指定 `trade_exchange`，排除 `trade_exchange` 未上市的合約
6. 24h USDT 成交量 ≥ `min_quote_vol`（已持倉幣種無條件保留）
7. 排除 24h 漲跌幅絕對值 > `max_change_pct` 的幣種（已持倉幣種不受此限）
8. 按成交量降序，回傳前 `top_n` 個（0 = 不限）

**典型用法（Bitunix 下單 + Binance 排名）**：
```python
scanner = SymbolScanner(
    exchange=binance_exchange,      # 用 Binance 成交量排名
    trade_exchange=bitunix_exchange,  # 只保留 Bitunix 有上市的
    min_quote_vol=200_000_000,      # Binance 2 億門檻
    max_change_pct=40.0,
)
```

**`services/runner_manager.py`**

`RunnerManager` 管理多個 `ServiceRunner` 執行緒，維護 `{symbol → (runner, thread)}` 字典。

主要職責：
- 定期（`scan_interval` 秒）呼叫 `SymbolScanner` 取得候選列表
- 候選幣種全部啟動 runner 監控；退出候選且無持倉的幣種停止 runner
- 每個 runner 開倉前自行查詢全域持倉數，達上限時跳過（`max_positions` 在 runner 層強制）
- 維護 `_invalid_symbols` 永久黑名單，寫入來源：
  - `get_qty_precision` 失敗（交易所不存在該合約）→ 直接加入
  - runner 回報 `[710002]` 不支援 API 交易 → 透過 `on_symbol_banned` 回呼加入

**Redis 黑名單持久化**：
- 建構時傳入 `redis_url`（預設 `redis://localhost:6379/0`，可透過環境變數 `REDIS_URL` 覆寫）
- 啟動時自動從 Redis 載入歷史黑名單（key：`tgtraderx:invalid_symbols:{exchange_name}`）
- 每次新增黑名單時同步寫入 Redis，重啟後不再嘗試已知失效的合約
- Redis 不可用時自動退回記憶體模式，不影響正常運行

手動管理黑名單（Redis CLI）：
```bash
# 查看
redis-cli smembers "tgtraderx:invalid_symbols:bitunix"
# 新增（已知不支援 API 的合約）
redis-cli sadd "tgtraderx:invalid_symbols:bitunix" ZECUSDT ALPHAUSDT EDGEUSDT
# 移除（交易所後來新增了此合約）
redis-cli srem "tgtraderx:invalid_symbols:bitunix" ZECUSDT
```

**`services/runner.py`**

`ServiceRunner` 的職責：
- 每根 K 線結束後執行一次 `_run_cycle()`
- 維護本地 `_active_pos: ActivePosition | None`，每週期與交易所倉位核對（`_reconcile_position`）
- 開倉前若 `max_positions > 0`，查詢全域持倉數，達上限則跳過
- 偵測永久禁用錯誤（`[710002]` / `does not currently support trading via openapi`）→ 呼叫 `on_symbol_banned` 回呼並停止自身
- 暫時性網路錯誤（timeout、connection reset、rate limit、`[10006]`）→ 最多重試 3 次，每次間隔 20s
- `dry_run=True` 時只記錄，不呼叫 `place_order`
- `stop()` 透過 `threading.Event` 通知主迴圈在當前週期後退出

**倉位核對流程（`_reconcile_position`）**：

| 情況 | 行為 |
|------|------|
| 交易所無倉位，本地無狀態 | 不做任何事 |
| 交易所無倉位，本地有狀態 | 清除本地快取（SL/TP 已被觸發）|
| 交易所有倉位，本地有快取 | 還原快取；`position_id` 和 `qty` 以交易所為準 |
| 交易所有倉位，本地無快取 | 保守 ±5% 重建 SL/TP，記錄 warning |
| 還原或重建後 | 補掛交易所 SL/TP 條件單（先取消所有舊掛單）|

**策略切換保護（`_handle_strategy_switch`）**：

| 情境 | 行為 |
|------|------|
| 策略未切換 | 不做任何事 |
| 切換 + 持倉**虧損** | 立即平倉 |
| 切換 + 持倉**獲利** | 止損移至保本（entry price），讓新策略接管 |
| `strategy_name == "recovered"` | 跳過（重啟重建，無法判斷原始策略）|

**`services/position_store.py`**

倉位持久化模組，將 `ActivePosition` 存為 JSON 檔案（`storage/positions/{exchange}_{symbol}.json`）。

- `save(exchange, symbol, pos)` — 開倉後儲存；`position_id` 更新時也應呼叫
- `load(exchange, symbol)` → `ActivePosition | None` — 服務啟動時讀取，還原完整 SL/TP
- `delete(exchange, symbol)` — 平倉後清除

儲存欄位：`exchange`, `symbol`, `position_id`, `side`, `entry_price`, `qty`, `stop_loss`, `take_profit`, `strategy_name`, `interval`。

**`services/indicators.py`**

純函式模組，無任何副作用，僅依賴標準函式庫（無 numpy/pandas）。

| 指標 | 週期 | 用途 |
|------|------|------|
| EMA | 20, 50, 200 | 趨勢方向、支撐阻力 |
| ADX / ±DI | 14 | 趨勢強度 |
| RSI | 14 | 超買超賣 |
| ATR | 14 | 波動幅度參考 |
| 布林帶 | 20, ±2σ | 帶寬、位置百分比 |
| 線性回歸斜率 | 20 | 趨勢方向驗證 |
| 近期波動率 | 20 | 報酬率標準差（%）|
| 成交量分佈（VA/POC）| 後 100 根 | 價值區上下限、最大成交量價位 |
| VWAP | 後 24 根 | 成交量加權平均價 ± 1.5σ 帶（`vwap`, `vwap_upper`, `vwap_lower`）|

EMA 和 ADX 使用 Wilder 平滑法（與多數交易所圖表一致）。

**`services/market_state.py`**

`classify_market(snap)` 判斷市場狀態，優先順序：

1. 高波動率（> 3%）→ `HIGH_VOLATILITY`
2. ADX > 25 + EMA20 > EMA50 + 斜率 > 0 → `UPTREND`
3. ADX > 25 + EMA20 < EMA50 + 斜率 < 0 → `DOWNTREND`
4. 其餘 → `RANGING`（保守預設）

**`services/position_sizer.py`**

`PositionSizer` 實作固定風險比例（Fixed Fractional）倉位計算。

核心公式：
```
risk_amount    = account_balance × risk_pct
position_value = risk_amount ÷ sl_distance_pct
qty            = position_value ÷ entry_price
required_margin = position_value ÷ leverage
```

三道安全保護：
1. 止損距清算價緩衝 ≥ `min_sl_buffer_pct`（預設 15%），否則拒絕並給出建議 SL
2. `position_value` 上限 = `account_balance × leverage × max_position_pct`（預設 80%）
3. `qty` 不得低於精度最小值

**`services/strategies/`**

`BaseStrategy.on_candle(snap, position)` → `Signal`。`position=None` 時判斷入場，否則判斷出場。

| 策略 | 對應市場 | 入場條件 | 出場條件 |
|------|---------|---------|---------|
| `FibonacciStrategy` | UPTREND | EMA20 > EMA50 + 回調至 Fib 0.618/0.5/0.382 + 錘子線確認 | 前高（TP）/ Fib 0.786 下方（SL）|
| `VwapPocStrategy` | RANGING | POC > VWAP + 跌至 VWAP-1.5σ + RSI < 40 + R:R ≥ 1.5 | VWAP 提前出場 / POC 全倉出場 / SL=VWAP-2.5σ |
| `DipVolumeStrategy` | HIGH_VOLATILITY | 近 5 根跌幅 > 3% + 量比 > 3x + 止跌確認（無新低+下影線）| 反彈至 EMA20 / TP +3% / SL -1.5% / 時間止損 10 根 |
| `ConservativeStrategy` | DOWNTREND | 不入場 | 有倉位則平倉 |
| `EnsembleStrategy` | 可選模式 | ≥ 2 個策略同時確認開倉；SL 取最高，TP 取最低（最保守）| 任一策略觸發出場即出場 |

`ServiceRunner` 透過 `strategy` 參數接受外部注入的策略實例（例如 `EnsembleStrategy`）；`strategy=None` 時根據市場狀態自動切換。`RunnerManager` 的 `_build_strategy()` 負責根據 `enable_ensemble` 決定傳入哪種策略。

**`services/external_data/`**  ⚠️ *已實作，尚未整合進交易週期*

外部市場情緒數據層，提供技術指標之外的資訊優勢。

`BaseDataProvider`（`base.py`）— 抽象介面，定義三個必實作方法：
- `get_funding_rate(symbol)` → `float`
- `get_liquidations(symbol, period)` → `{long, short}`
- `get_long_short_ratio(symbol)` → `float`

`CoinglassProvider`（`coinglass.py`）— 呼叫 Coinglass REST API：
- `GET /futures/funding-rate`
- `GET /futures/liquidation`（含 `get_liquidation_heatmap` 額外方法）
- `GET /futures/long-short-ratio`

`MarketBiasCalculator`（`market_bias.py`）— 綜合三個數據源計算市場偏向分數（-100 ~ +100）：

| 數據來源 | 評分邏輯 | 分值範圍 |
|---------|---------|---------|
| 資金費率 | 費率 > 0.05% → 多頭擁擠偏空；< -0.05% → 偏多 | ±15 |
| 清算數據 | 1h 多頭清算 > 5000 萬 → 洗盤偏多 | ±20 |
| 多空比 | 散戶 > 2x 看多 → 反向偏空（反向指標）| ±10 |

各數據源獨立容錯，單一失敗不影響整體分數。

**目前整合狀態**：`MarketBiasCalculator` 已建立但尚未在 `ServiceRunner._run_cycle()` 中呼叫。計畫整合方式：在策略信號分數 < 閾值時，以市場偏向分數作為過濾或加權條件。

---

### `bot/`

只負責與 Telegram 互動，不含任何交易所或策略邏輯。

**`bot/parser.py`** — 無副作用純函式。`parse(text)` 解析為 `OrderRequest` 或 `QueryCommand`，格式錯誤拋出 `ParseError`。

**`bot/listener.py`** — `TGListener` 使用 `python-telegram-bot` v20+（asyncio 架構），依賴注入 `TradeDispatcher`，不自行建立交易所客戶端。

---

### `trader/`

TG Bot 的應用層，協調 bot 和 exchanges 之間的流程。

**`trader/models.py`** — `OrderRequest`（parser 產出、dispatcher 消費）及 enum 型別（`OrderSide`, `OrderType`, `TradeSide`）。qty/price 使用 `str` 避免浮點精度問題。

**`trader/dispatcher.py`** — `TradeDispatcher` 持有 `dict[str, BaseExchange]`，以交易所名稱（小寫）為 key，統一路由下單與查詢請求。

---

### `config/`

**`config/settings.py`** — 從環境變數讀取設定，若安裝 `python-dotenv` 則自動載入 `.env`。`validate(exchange?)` 在進入點最前面呼叫，缺少必要變數時立即報錯。

---

## 資料流

### 自動掃描模式（`run_auto.py`）

```
run_auto.py
    │ 組裝 exchange + scanner + sizer + manager
    ▼
RunnerManager.run() — 每 scan_interval 秒掃描一次
    │
    ├─ SymbolScanner.scan()          過濾高成交量合約
    ├─ 停止退出候選列表的 runner
    └─ 為新候選幣種啟動 ServiceRunner 執行緒
            │
            ▼  （各執行緒獨立運行，每根 K 線執行一次）
        ServiceRunner._run_cycle()
            ├─ exchange.get_pending_positions()  檢查全域持倉上限
            ├─ exchange.get_klines()
            ├─ compute_indicators() + classify_market()
            ├─ strategy.on_candle()              產生 Signal
            └─ exchange.place_order()
```

### 單一幣種模式（`run_service.py`）

```
run_service.py
    │ 組裝 exchange + sizer + runner
    ▼
ServiceRunner.run() — 每 interval 秒執行一次
    │
    ├─ exchange.get_klines()
    ├─ compute_indicators() + classify_market()
    ├─ exchange.get_pending_positions()    核對倉位
    ├─ _handle_strategy_switch()          策略切換保護
    ├─ strategy.on_candle()               產生 Signal
    └─ Signal.action
         ├─ open_long/short → sizer.calculate() + place_order()
         ├─ close           → cancel_all_orders() + place_order(CLOSE)
         └─ hold            → 無動作
```

### Telegram Bot

```
Telegram 使用者 → TGListener → bot/parser.parse()
    ▼ OrderRequest / QueryCommand
TradeDispatcher → BaseExchange.place_order() / get_*()
    ▼ 交易所回應
TGListener.reply_text() → Telegram 使用者
```

---

## 擴充慣例

### 新增交易所

1. 建立 `exchanges/<name>/` 目錄
2. 實作 `adapter.py`，繼承 `BaseExchange`，`name` 屬性回傳小寫識別字串
3. 在 `config/settings.py` 新增 API key 環境變數
4. 在 `run_auto.py` 和 `run_service.py` 的 `_build_exchange()` 加入 `elif name == "<name>"`
5. 在 `main.py` 加入 `dispatcher.register(<Name>Exchange(...))`

注意：`get_tickers()` 回傳格式必須正規化為 `symbol`, `last_price`, `quote_vol`, `base_vol`, `high`, `low`, `change_pct`（Binance 用 `priceChangePercent`；Bitunix 從 `open/lastPrice` 計算）；`place_sl_tp_orders()` 在 Bitunix 需要 `position_id`，在 Binance 則不需要（傳空字串即可）。

### 新增交易策略

1. 在 `services/strategies/` 新增檔案，繼承 `BaseStrategy`
2. 實作 `name`、`on_candle(snap, position)` → `Signal`
3. 在 `services/runner.py` 的 `self._strategies` dict 中指定對應的 `MarketState`

### 新增 TG 指令

1. 在 `bot/parser.py` 新增解析函式
2. 在 `bot/listener.py` 新增 `CommandHandler` 及 handler 方法
3. 若涉及新業務邏輯，在 `trader/dispatcher.py` 新增對應方法

---

## 依賴版本

| 套件 | 用途 | 最低版本 |
|------|------|---------|
| `requests` | Bitunix / Binance HTTP API | 2.31 |
| `websocket-client` | Bitunix WebSocket | 1.7 |
| `python-telegram-bot` | Telegram Bot | 20.0（asyncio 版）|
| `python-dotenv` | 載入 .env 檔案 | 1.0 |
| `redis` | 黑名單持久化（可選）| 4.0 |

`services/` 層的技術指標計算只使用 Python 標準函式庫（無 numpy/pandas），減少部署依賴。

> `python-telegram-bot` v20 起採用 asyncio 架構，與 v13 以前的 API 不相容。

伺服器安裝 Redis：
```bash
sudo apt install redis-server -y
sudo systemctl enable --now redis-server
```

---

## 已知限制 / 未來方向

- **外部數據未整合**：`MarketBiasCalculator`（資金費率、清算數據、多空比）已實作但尚未接入 `ServiceRunner._run_cycle()`。計畫作為開倉前的情緒過濾層（例如偏向分數 < -30 時禁止做多）。
- **分批出場未實作**：`DipVolumeStrategy` 和 `VwapPocStrategy` 的分批平倉邏輯（半倉/全倉）待 `Signal` 新增 `quantity_pct` 欄位後再啟用，目前一律全倉出場。
- **Bitunix 山寨幣流動性偏低**：Bitunix 山寨幣成交量遠低於 Binance（第 8 名 TAO 僅約 5500 萬，同幣在 Binance 有 4.77 億）。建議永遠搭配 `--scan-exchange binance` 用 Binance 成交量排名，再取兩交易所上市的交集。
- **未實作 WebSocket 確認成交**：目前 `place_order` 只確認訂單送出，不等待成交事件。`exchanges/bitunix/trading_flow.py` 有 `run_futures_trading_flow` 可做完整生命週期管理，未來可整合至 runner。
- **position_id 延遲**：市價單填充後 position_id 需等下一週期才能從交易所取得。若需即時取得，可在開倉後加入短暫等待與即時查詢。
- **清算價為估算值**：孤立保證金模式下的清算價因交易所計算細節（維持保證金率、手續費）可能有誤差，實際以交易所顯示為準。
- **單一帳戶**：每個交易所目前只支援一組 API key，未來可在 adapter 層擴充多帳戶。
- **下降趨勢不做空**：`DOWNTREND` 目前對應 `ConservativeStrategy`，尚未實作空頭的趨勢跟隨。
- **Bitunix TP 為限價單**：TP 使用 `reduceOnly` 限價單進委託簿（maker 費率），若快速跳空可能未成交；SL 才是條件市價單。
