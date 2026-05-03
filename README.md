# TGTradeX

期貨交易自動化平台，支援 Binance / Bitunix 雙交易所，以 OI 動能反轉空單為主力策略。

---

## 主力服務：OI 動能反轉空單

### 核心邏輯

OI 動能條件（OI 快速上升 + 價格同步上漲 + 技術確認）通常代表行情已接近短期頂部。與其追多，本服務改為在條件達成時**直接做空**，捕捉動能耗盡後的回落。

```
每 900 秒掃描一次
    ↓
BTC EMA20(1h) 確認（BTC 偏空時跳過掃描）
    ↓
按 24h 振幅排序，取前 100 名
    ↓
OI 動能篩選：OI 8h > +5% 且 價格 8h > +2%（資金跟著行情方向湧入）
    ↓
進場技術確認（在 K 線收盤前 60 秒評估）：
  - 收盤 > EMA20（主週期趨勢確認）
  - 收盤距 EMA20 延伸不超過門檻（避免在均線過遠處入場）
  - RSI < 80（未極端超買）
  - 高週期 EMA20 / RSI 確認（--confirm-period）
  - 近 3 根均量 > 前 10 根均量 × 1.5（量能突破）
    ↓
開空單
  - 止損：ROI -5%（價格漲 1.25% @ 5x）
  - 止盈：ROI +20%（價格跌 4% @ 5x）
  - 由交易所掛 SL/TP 單保護
```

### 服務管理

```bash
# Binance（1h K 線 + 4h 確認週期）
./bnm.sh start
./bnm.sh stop
./bnm.sh restart
./bnm.sh log
./bnm.sh status

# Bitunix（15m K 線 + 1h 確認週期）
./bum.sh start
./bum.sh stop
./bum.sh restart
./bum.sh log
./bum.sh status
```

### 目前參數設定

| 項目 | Binance (`bnm.sh`) | Bitunix (`bum.sh`) |
|------|-------------------|-------------------|
| 主週期 | `1h` | `15m` |
| 確認週期 | `4h` | `1h` |
| 槓桿 | `5x` | `4x` |
| 每筆風險 | `4%` | `4%` |
| 最大持倉數 | `4` | `7` |
| EMA20 最大延伸 | `8%` | `6%` |
| 空單止損 ROI | `-5%` | `-5%` |
| 空單止盈 ROI | `+20%` | `+20%` |
| 每日最大虧損 | `20%` | `20%` |

### 進場參數說明

| 參數 | 說明 |
|------|------|
| `--interval` | 主 K 線週期，指標與進場信號依此計算 |
| `--confirm-period` | 高週期確認：進場前額外檢查此週期 EMA20 / RSI |
| `--pre-close-sec 60` | 在 K 線收盤前 60 秒評估，進場價接近收盤而非下一根開盤 |
| `--max-ema-ext` | 收盤距 EMA20 最大延伸 %，過遠代表追高風險 |
| `--rsi-max 80` | RSI 超買門檻，超過此值不進場 |
| `--enable-reverse` | 開啟反轉模式：條件達成時做空而非做多 |
| `--reverse-tp-pct` | 空單止盈 ROI %（預設 20） |
| `--reverse-sl-pct` | 空單止損 ROI %（預設 5） |
| `--sl-pct 32` | 多單硬止損 ROI %（反轉模式下備用，不做多時不觸發）|
| `--min-sl-buffer 0` | SL 距清算價最低緩衝 %（0 = 停用緩衝檢查）|
| `--scan-interval 900` | 幣種重新掃描間隔（秒）|
| `--min-volume` | 24h 最低成交額門檻（USDT） |
| `--top-volatile` | 取振幅最高前 N 個幣種進行 OI 篩選 |
| `--max-positions` | 最多同時持倉數 |
| `--leverage` | 槓桿倍數 |
| `--risk-pct` | 每筆最大風險佔帳戶餘額 % |
| `--max-daily-loss` | 日虧損上限 %，達到後暫停開倉 |

---

## 其他服務

| 腳本 / 入口 | 說明 |
|------------|------|
| `run_oi_long.py` | OI 背離做多（`bn.sh` / `bu.sh`）：OI 累積但價格未動時佈局做多 |
| `run_mix_strategies.py` | Ensemble 多策略掃描：Fibonacci / VWAP-POC / Dip-Volume 多數決 |
| `run_auto.py` | 自動掃描：依市場狀態自動切換策略 |
| `run_service.py` | 單一幣種固定監控 |
| `main.py` | Telegram Bot：手動開平倉、查帳戶與持倉 |

---

## 目錄結構

```
TGTradeX/
├── exchanges/
│   ├── base.py                       # 統一交易所抽象介面
│   ├── binance/                      # Binance SDK + Adapter
│   └── bitunix/                      # Bitunix SDK + Adapter
├── services/
│   ├── indicators.py                 # 技術指標（EMA / RSI / ADX / ATR / VWAP / POC）
│   ├── market_state.py               # 市場狀態識別
│   ├── oi_momentum.py                # OI 動能篩選器（含 BTC EMA20 市場過濾）
│   ├── oi_divergence.py              # OI 48h 背離篩選器
│   ├── position_sizer.py             # 安全倉位計算（固定風險比例 + 清算緩衝驗證）
│   ├── position_store.py             # 倉位本地持久化（JSON）
│   ├── runner.py                     # 單幣種執行主迴圈（含快速價格監控執行緒）
│   ├── runner_manager.py             # 多幣種執行緒管理
│   ├── symbol_scanner.py             # 幣種掃描（成交量 / 波動性排序）
│   ├── risk_guard.py                 # 風控守衛（連續虧損 / 日損上限）
│   ├── notifier.py                   # Telegram 開平倉通知
│   ├── trade_journal.py              # 交易紀錄（CSV）
│   ├── external_data/
│   │   └── binance_futures.py        # Binance 公開 OI / 多空比 API
│   └── strategies/
│       ├── base.py                   # Signal / ActivePosition / BaseStrategy
│       ├── long_oi_momentum.py       # OI 動能策略（做多 / 反轉空單模式）
│       ├── ensemble.py               # Ensemble 多策略包裝
│       ├── long_only_oi.py           # OI 背離做多
│       ├── oi_ls_ratio.py            # OI + 多空比軋倉（雙向）
│       ├── fibonacci.py              # 斐波那契回調
│       ├── vwap_poc.py               # VWAP + POC 均值回歸
│       └── dip_volume.py             # 急跌爆量反彈
├── bot/
│   ├── listener.py                   # Telegram Bot
│   └── parser.py                     # 指令解析
├── config/
│   └── settings.py                   # 環境變數設定
├── run_oi_momentum.py                # OI 動能服務入口（bnm.sh / bum.sh 使用）
├── run_oi_long.py                    # OI 背離做多服務入口
├── run_mix_strategies.py             # Ensemble 多策略服務入口
├── run_auto.py                       # 自動掃描服務入口
├── run_service.py                    # 單一幣種服務入口
├── main.py                           # Telegram Bot 入口
├── bnm.sh                            # Binance OI 動能服務管理
├── bum.sh                            # Bitunix OI 動能服務管理
├── bn.sh                             # Binance OI 背離做多服務管理
├── bu.sh                             # Bitunix OI 背離做多服務管理
└── tests/
```

---

## 環境需求

- Python 3.10+
- Redis（可選，用於黑名單持久化）

## 安裝

```bash
pip install -r requirements.txt
```

## 設定

建立 `.env`：

```env
TG_BOT_TOKEN=your_telegram_bot_token
TG_CHAT_ID=your_telegram_chat_id

BITUNIX_API_KEY=your_bitunix_api_key
BITUNIX_SECRET_KEY=your_bitunix_secret_key

BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET_KEY=your_binance_secret_key

REDIS_URL=redis://localhost:6379/0
```

> Binance 的掃描與 OI 數據使用公開端點，不需要 API Key。只有實際下單才需要填寫。

---

## 安全倉位計算

```
風險金額    = 帳戶餘額 × 風險比例
倉位市值    = 風險金額 ÷ 止損距離%
開倉數量    = 倉位市值 ÷ 入場價
所需保證金  = 倉位市值 ÷ 槓桿
```

**範例**（帳戶 1000U，5x 槓桿，4% 風險，止損 ROI -5% = 價格 1%）：

| 項目 | 數值 |
|------|------|
| 風險金額 | 40 USDT |
| 倉位市值 | 4000 USDT |
| 所需保證金 | 800 USDT |
| 最大損失 | 40 USDT（帳戶 4%）|

---

## Telegram Bot 指令

| 指令 | 說明 |
|------|------|
| `/open <exchange> <symbol> <BUY\|SELL> <qty>` | 市價開倉 |
| `/open <exchange> <symbol> <BUY\|SELL> <qty> <price>` | 限價開倉 |
| `/close <exchange> <symbol> <BUY\|SELL> <qty> <position_id>` | 市價平倉 |
| `/account <exchange>` | 查詢帳戶餘額 |
| `/positions <exchange> [symbol]` | 查詢持倉 |
| `/orders <exchange> [symbol]` | 查詢未完成訂單 |

---

## 執行測試

```bash
pytest tests/ -v
```

## 授權

MIT
