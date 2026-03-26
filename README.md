# TGTradeX

透過 Telegram 訊息控制多交易所期貨開平倉的 Bot。

## 功能

- 在 Telegram 傳送文字指令，Bot 自動執行開單、平倉、查詢
- 支援多交易所（目前：Bitunix；可擴充其他交易所）
- 支援市價單 / 限價單
- 查詢帳戶餘額、持倉、未完成訂單

## 目錄結構

```
TGTradeX/
├── exchanges/              # 交易所 SDK 集合
│   ├── base.py             # 統一抽象介面
│   └── bitunix/            # Bitunix SDK + Adapter
│       └── adapter.py      # 實作 BaseExchange
├── bot/
│   ├── listener.py         # Telegram Bot（接收 / 回覆訊息）
│   └── parser.py           # 純文字指令解析
├── trader/
│   ├── models.py           # 共用資料模型（OrderRequest 等）
│   └── dispatcher.py       # 路由指令到對應交易所
├── config/
│   └── settings.py         # 從環境變數 / .env 讀取設定
├── main.py                 # 進入點
├── examples/               # SDK 使用範例
└── tests/                  # 單元測試
```

## 環境需求

- Python 3.10+

## 安裝

```bash
pip install -r requirements.txt
```

## 設定

複製並填寫環境變數：

```bash
cp .env.example .env
```

`.env` 內容：

```
TG_BOT_TOKEN=your_telegram_bot_token
BITUNIX_API_KEY=your_bitunix_api_key
BITUNIX_SECRET_KEY=your_bitunix_secret_key
```

> Telegram Bot Token 請透過 [@BotFather](https://t.me/BotFather) 建立。

## 啟動

```bash
python main.py
```

## TG 指令格式

### 開單

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

### 查詢

| 指令 | 說明 |
|------|------|
| `/account <exchange>` | 查詢帳戶資訊 |
| `/positions <exchange> [symbol]` | 查詢持倉 |
| `/orders <exchange> [symbol]` | 查詢未完成訂單 |

### 範例

```
/buy bitunix BTCUSDT 0.001
/buy bitunix BTCUSDT 0.001 50000
/sell bitunix ETHUSDT 0.01
/open bitunix BTCUSDT BUY 0.001
/close bitunix BTCUSDT SELL 0.001 pos_abc123
/account bitunix
/positions bitunix BTCUSDT
/orders bitunix
```

## 加入新交易所

1. 在 `exchanges/` 新增資料夾，例如 `exchanges/binance/`
2. 實作 `adapter.py`，繼承 `exchanges/base.py` 的 `BaseExchange`
3. 在 `main.py` 呼叫 `dispatcher.register(BinanceExchange(...))`
4. 在 `config/settings.py` 新增對應的環境變數

## 執行測試

```bash
pytest tests/ -v
```

## 授權

MIT
