# TGTradeX

期貨交易自動化平台，支援多交易所、多策略，提供掃描服務與 Telegram Bot 兩種操作模式。

## 服務模式

| 腳本 | 說明 |
|------|------|
| `run_oi_long.py` | **OI 背離做多服務**：自動掃描波動最高且 OI 異常累積的幣種，只做多，由 OI/多空比結構決定出場（`bn.sh` / `bu.sh` 使用） |
| `run_mix_strategies.py` | **Ensemble 多策略掃描服務**：三策略多數決，雙向交易 |
| `run_auto.py` | **自動掃描服務**：依市場狀態自動切換策略 |
| `run_service.py` | **單一幣種服務**：固定監控指定幣種 |
| `main.py` | **Telegram Bot**：手動控制開平倉 |

## 功能

- OI 48h 背離偵測（OI 大漲但價格未動 → 提前佈局）
- 波動性排序（按 24h 高低振幅選幣，非成交量）
- 結構性出場（OI 下跌或多空比轉向才平倉，不設固定 TP）
- 市場狀態自動識別（上升趨勢 / 下降趨勢 / 震蕩 / 高波動）
- 安全倉位自動計算（固定風險比例，含清算價驗證）
- 開倉前強制設定槓桿（防止帳戶預設槓桿不符）
- SL/TP 與開倉單分離（避免 Bitunix [30031] 錯誤）
- Telegram 手動下單 / 查詢帳戶與持倉
- Dry-run 模擬模式（不實際下單）
- Redis 黑名單持久化（重啟後不再嘗試已知失效合約）

## 目錄結構

```
TGTradeX/
├── exchanges/
│   ├── base.py                   # 統一抽象介面
│   ├── bitunix/                  # Bitunix SDK + Adapter
│   └── binance/                  # Binance SDK + Adapter
├── services/
│   ├── indicators.py             # 技術指標（EMA/ADX/RSI/ATR/BB/VWAP/POC）
│   ├── market_state.py           # 市場狀態識別
│   ├── position_sizer.py         # 安全倉位計算（固定風險比例）
│   ├── position_store.py         # 倉位持久化（JSON）
│   ├── runner.py                 # 服務主迴圈
│   ├── runner_manager.py         # 多幣種執行緒管理
│   ├── symbol_scanner.py         # 幣種掃描（成交量/波動性排序）
│   ├── oi_divergence.py          # OI 48h 背離篩選器
│   ├── risk_guard.py             # 風控守衛（連續虧損/日損上限）
│   ├── notifier.py               # Telegram 通知
│   ├── external_data/
│   │   └── binance_futures.py    # Binance 公開 OI/多空比 API
│   └── strategies/
│       ├── base.py               # Signal / ActivePosition / BaseStrategy
│       ├── long_only_oi.py       # OI 背離做多（只做多，結構性出場）
│       ├── oi_ls_ratio.py        # OI + 多空比軋倉策略（雙向）
│       ├── fibonacci.py          # 斐波那契回調策略
│       ├── vwap_poc.py           # VWAP+POC 均值回歸
│       ├── dip_volume.py         # 急跌爆量反彈
│       └── conservative.py       # 保守觀望（不入場）
├── bot/
│   ├── listener.py               # Telegram Bot
│   └── parser.py                 # 指令解析
├── trader/
│   ├── models.py                 # 共用資料模型
│   └── dispatcher.py             # 路由至對應交易所
├── config/
│   └── settings.py               # 環境變數設定
├── run_oi_long.py                # OI 背離做多服務入口
├── run_mix_strategies.py         # Ensemble 多策略服務入口
├── run_auto.py                   # 自動掃描服務入口
├── run_service.py                # 單一幣種服務入口
├── main.py                       # Telegram Bot 入口
├── bn.sh                         # Binance 服務管理腳本
├── bu.sh                         # Bitunix 服務管理腳本
└── tests/
```

## 環境需求

- Python 3.10+
- Redis（可選，用於黑名單持久化）

## 安裝

```bash
pip install -r requirements.txt
```

## 設定

建立 `.env` 並填寫：

```
TG_BOT_TOKEN=your_telegram_bot_token
TG_CHAT_ID=your_telegram_chat_id

BITUNIX_API_KEY=your_bitunix_api_key
BITUNIX_SECRET_KEY=your_bitunix_secret_key

BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET_KEY=your_binance_secret_key

REDIS_URL=redis://localhost:6379/0
```

> Binance 的掃描與 OI 數據使用公開端點，不需要 API Key。只有 Binance 下單才需要填寫。

---

## OI 背離做多服務（主力服務）

### 運作原理

```
每小時掃描一次
    ↓
按 24h 振幅排序，取前 30 名（波動最高）
    ↓
OI 48h 篩選：OI > +20% 且 價格變化 < ±3%
    ↓
等待技術確認：收盤 > EMA20 且 RSI < 70
    ↓
開多倉，止損 -20%
    ↓
持倉直到 OI 從峰值跌 >5% 或多空比較進場漲 >10%
```

### 管理腳本

```bash
# Binance（4h K 線）
./bn.sh start
./bn.sh stop
./bn.sh restart
./bn.sh log
./bn.sh status

# Bitunix（1h K 線）
./bu.sh start
./bu.sh log
```

### 直接啟動

```bash
# Bitunix，標準設定
python run_oi_long.py --exchange bitunix

# 調整參數
python run_oi_long.py --exchange bitunix \
    --leverage 2 --risk-pct 1.0 \
    --sl-pct 20 --oi-exit-pct 5 --ls-shift-pct 10

# 模擬模式
python run_oi_long.py --exchange bitunix --dry-run
```

### 主要參數

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--exchange` | 必填 | `bitunix` 或 `binance` |
| `--max-positions` | `3` | 最多同時持倉數 |
| `--leverage` | `2` | 槓桿（止損 -20% + 高槓桿風險大，謹慎調整）|
| `--risk-pct` | `1.0` | 每筆最大風險 % |
| `--sl-pct` | `20.0` | 硬止損 %（進場價往下）|
| `--oi-exit-pct` | `5.0` | OI 從峰值下跌此 % 出場 |
| `--ls-shift-pct` | `10.0` | 多空比較進場上升此 % 出場 |
| `--interval` | `1h` | K 線週期 |
| `--scan-interval` | `3600` | 幣種重掃間隔（秒）|
| `--top-volatile` | `30` | 先取振幅最高前 N 個再做 OI 篩選 |
| `--dry-run` | `false` | 模擬模式，不實際下單 |

---

## Ensemble 多策略服務

```bash
# Bitunix，Ensemble 三策略，最多 3 倉
python run_mix_strategies.py --exchange bitunix --max-positions 3

# 只跑 OI 軋倉策略
python run_mix_strategies.py --exchange bitunix --strategies oi_ls_ratio

# 嚴格模式：需三策略全確認
python run_mix_strategies.py --exchange binance --min-confirm 3
```

---

## Telegram Bot

### 啟動

```bash
python main.py
```

### 指令

| 指令 | 說明 |
|------|------|
| `/open <exchange> <symbol> <BUY\|SELL> <qty>` | 市價開倉 |
| `/open <exchange> <symbol> <BUY\|SELL> <qty> <price>` | 限價開倉 |
| `/close <exchange> <symbol> <BUY\|SELL> <qty> <position_id>` | 市價平倉 |
| `/account <exchange>` | 查詢帳戶餘額 |
| `/positions <exchange> [symbol]` | 查詢持倉 |
| `/orders <exchange> [symbol]` | 查詢未完成訂單 |

---

## 安全倉位計算

```
風險金額    = 帳戶餘額 × 風險比例
倉位市值    = 風險金額 ÷ 止損距離%
開倉數量    = 倉位市值 ÷ 入場價
所需保證金  = 倉位市值 ÷ 槓桿
```

**範例**（帳戶 1000U，2x 槓桿，1% 風險，止損 20%）：

| 項目 | 數值 |
|------|------|
| 風險金額 | 10 USDT |
| 倉位市值 | 50 USDT |
| 所需保證金 | 25 USDT |
| SL 距清算緩衝 | 充足（2x 槓桿下清算在 -50%）|

**安全機制：**
- 止損距清算價必須 ≥ 15%，否則拒絕下單
- 最大倉位 = 帳戶 × 槓桿 × 80%
- 開倉前強制設定槓桿至配置值

## 執行測試

```bash
pytest tests/ -v
```

## 授權

MIT
