# TGTradeX

期貨交易自動化平台，提供兩種運行模式：

- **自動交易服務**：持續運行，自動識別市場狀態、切換策略、計算安全倉位並下單
- **Telegram Bot**：接收 TG 指令手動控制開平倉

## 功能

- 市場狀態自動識別（上升趨勢 / 下降趨勢 / 震蕩 / 高波動）
- 策略自動切換（趨勢跟隨 / 成交量分佈 VA-POC / 保守觀望）
- 安全倉位自動計算（固定風險比例，含清算價驗證）
- 透過 Telegram 手動下單、查詢帳戶與持倉
- 支援多交易所（目前：Bitunix；可擴充其他交易所）
- Dry-run 模擬模式（不實際下單）

## 目錄結構

```
TGTradeX/
├── exchanges/                  # 交易所 SDK 集合
│   ├── base.py                 # 統一抽象介面
│   └── bitunix/                # Bitunix SDK + Adapter
│       └── adapter.py          # 實作 BaseExchange
├── services/                   # 自動交易服務
│   ├── indicators.py           # 技術指標（EMA / ADX / BB / RSI / ATR / VolProfile）
│   ├── market_state.py         # 市場狀態識別
│   ├── position_sizer.py       # 安全倉位計算（固定風險比例）
│   ├── runner.py               # 服務主迴圈
│   └── strategies/
│       ├── base.py             # Signal / ActivePosition / BaseStrategy
│       ├── trend_following.py  # 趨勢跟隨策略
│       ├── volume_profile.py   # 成交量分佈策略（VA/POC）
│       └── conservative.py     # 保守觀望策略
├── bot/
│   ├── listener.py             # Telegram Bot（接收 / 回覆訊息）
│   └── parser.py               # 純文字指令解析
├── trader/
│   ├── models.py               # 共用資料模型（OrderRequest 等）
│   └── dispatcher.py           # 路由指令到對應交易所
├── config/
│   └── settings.py             # 從環境變數 / .env 讀取設定
├── run_service.py              # 自動交易服務進入點
├── main.py                     # Telegram Bot 進入點
└── tests/
```

## 環境需求

- Python 3.10+

## 安裝

```bash
pip install -r requirements.txt
```

## 設定

建立 `.env` 並填寫：

```
TG_BOT_TOKEN=your_telegram_bot_token
BITUNIX_API_KEY=your_bitunix_api_key
BITUNIX_SECRET_KEY=your_bitunix_secret_key
```

> Telegram Bot Token 請透過 [@BotFather](https://t.me/BotFather) 建立。

---

## 自動交易服務

### 啟動

```bash
# 標準啟動（4x 槓桿，每次風險 1%，自動計算倉位）
python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4

# 調整風險比例（每次最多虧 0.5%）
python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --risk-pct 0.5

# ETH 合約（2 位小數精度）
python run_service.py --exchange bitunix --symbol ETHUSDT --leverage 3 --risk-pct 1.0 --qty-precision 2

# 15 分鐘 K 線改為 1 小時
python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --interval 1h

# Dry-run 模擬模式（不實際下單，只記錄信號）
python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --dry-run

# 固定數量（覆蓋自動計算，適合測試）
python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --qty 0.001
```

### 參數說明

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--exchange` | 必填 | 交易所名稱（目前：`bitunix`）|
| `--symbol` | 必填 | 交易對，例如 `BTCUSDT` |
| `--leverage` | `4` | 槓桿倍數 |
| `--risk-pct` | `1.0` | 每次最大風險比例（%），建議 0.5–2.0 |
| `--qty-precision` | `3` | 數量小數位（BTC=3，ETH=2）|
| `--interval` | `15m` | K 線週期：`1m` `5m` `15m` `1h` `4h` `1d` |
| `--qty` | 無 | 固定數量（設定後停用自動計算）|
| `--dry-run` | `false` | 模擬模式，不實際下單 |

### 安全倉位計算

服務每次開倉前自動計算數量，確保單次虧損不超過帳戶的指定比例：

```
風險金額    = 帳戶餘額 × 風險比例
倉位市值   = 風險金額 ÷ 止損距離%
開倉數量   = 倉位市值 ÷ 入場價
所需保證金 = 倉位市值 ÷ 槓桿
```

**範例**（帳戶 1000U，4x 槓桿，1% 風險，止損距入場 3%）：

| 項目 | 數值 |
|------|------|
| 風險金額 | 10 USDT |
| 倉位市值 | 333 USDT |
| 開倉數量 | 0.007 BTC（@50,000）|
| 所需保證金 | 83 USDT |
| 估算清算價（多單）| 37,750（-24.5%）|
| SL 距清算緩衝 | 28.5%（安全）|

**安全機制：**
- 止損距清算價必須 ≥ 15%，否則拒絕下單並提示安全 SL 位置
- 最大倉位 = 帳戶 × 槓桿 × 80%，避免過度集中
- 最小數量檢查，餘額不足時不下單

### 市場狀態與策略對照

| 市場狀態 | 判斷條件 | 使用策略 |
|---------|---------|---------|
| 上升趨勢 | ADX > 25，EMA20 > EMA50，線性回歸斜率 > 0 | 趨勢跟隨 |
| 下降趨勢 | ADX > 25，EMA20 < EMA50，線性回歸斜率 < 0 | 保守觀望 |
| 震蕩市場 | ADX < 20，布林帶寬 < 3% | 成交量分佈（VA/POC）|
| 高波動 | 近期波動率 > 3% | 保守觀望（平倉）|

### 趨勢跟隨策略

**入場：** 上升趨勢 + 價格在 EMA20 ±2% + RSI 30–65 + BB 位置 20%–60%

**出場：** EMA20 下彎 / RSI > 75 / 止損（EMA50 × 0.97）/ 止盈（入場價 × 1.05）

### 成交量分佈策略（VA/POC）

**入場：** 震蕩市場 + 價格在 VAL ±1.5% + RSI < 40

**出場：** 價格在 POC ±1.5% / 止損（VAL × 0.97）/ 止盈（POC）

---

## Telegram Bot

### 啟動

```bash
python main.py
```

### 開單指令

| 指令 | 說明 |
|------|------|
| `/buy <exchange> <symbol> <qty>` | 市價買入 |
| `/buy <exchange> <symbol> <qty> <price>` | 限價買入 |
| `/sell <exchange> <symbol> <qty>` | 市價賣出 |
| `/sell <exchange> <symbol> <qty> <price>` | 限價賣出 |
| `/open <exchange> <symbol> <BUY\|SELL> <qty>` | 市價開倉 |
| `/open <exchange> <symbol> <BUY\|SELL> <qty> <price>` | 限價開倉 |
| `/close <exchange> <symbol> <BUY\|SELL> <qty> <position_id>` | 市價平倉 |
| `/close <exchange> <symbol> <BUY\|SELL> <qty> <position_id> <price>` | 限價平倉 |

### 查詢指令

| 指令 | 說明 |
|------|------|
| `/account <exchange>` | 查詢帳戶資訊 |
| `/positions <exchange> [symbol]` | 查詢持倉 |
| `/orders <exchange> [symbol]` | 查詢未完成訂單 |

### 範例

```
/buy bitunix BTCUSDT 0.001
/buy bitunix BTCUSDT 0.001 50000
/open bitunix BTCUSDT BUY 0.001
/close bitunix BTCUSDT SELL 0.001 pos_abc123
/account bitunix
/positions bitunix BTCUSDT
```

---

## 加入新交易所

1. 在 `exchanges/` 新增資料夾，例如 `exchanges/binance/`
2. 實作 `adapter.py`，繼承 `exchanges/base.py` 的 `BaseExchange`
3. 在 `run_service.py` 的 `_build_exchange()` 加入 `elif name == "binance"`
4. 在 `main.py` 呼叫 `dispatcher.register(BinanceExchange(...))`
5. 在 `config/settings.py` 新增對應的環境變數

## 執行測試

```bash
pytest tests/ -v
```

## 授權

MIT
