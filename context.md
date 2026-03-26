# TGTradeX — 技術背景與設計脈絡

此文件記錄專案的架構決策、模組職責與擴充慣例，供後續開發參考。

---

## 專案目標

使用者在 Telegram 發送純文字交易指令，Bot 解析後呼叫對應交易所 API 執行開平倉，並將結果回覆到同一個對話。核心需求：

- **多交易所**：Bitunix 為第一個，之後會加入其他交易所
- **可擴充**：加新交易所不應修改現有模組
- **單一進入點**：`main.py` 組裝所有依賴，Bot 啟動後持續 polling

---

## 模組職責劃分

### `exchanges/`

所有交易所相關程式碼放在這裡。

**`exchanges/base.py`**
定義 `BaseExchange` 抽象類別，規定每個交易所都必須實作的方法：
- `name` — 交易所識別字串（dispatcher 用來路由）
- `get_account()` — 帳戶資訊
- `get_pending_positions(symbol)` — 持倉查詢
- `get_pending_orders(symbol)` — 未完成訂單查詢
- `place_order(payload)` — 下單
- `cancel_order(order_id, symbol)` — 取消訂單

**`exchanges/bitunix/`**
Bitunix 的 Python SDK（非官方）。原本位於 `bitunix_sdk/`，重構後移入此處。
- 原有 SDK 檔案維持不動（相對 import 不受影響）
- `adapter.py` 新增，將 `BitunixClient` 包裝成 `BaseExchange`

加入新交易所時，只需在 `exchanges/` 新增子目錄並實作 `adapter.py`，dispatcher 與 bot 層不需變動。

---

### `bot/`

只負責與 Telegram 互動，不含任何交易所邏輯。

**`bot/parser.py`**
無副作用的純函式模組。`parse(text)` 將字串解析為：
- `OrderRequest` — 開平倉指令
- `QueryCommand` — 查詢指令（帳戶、持倉、訂單）
- `ParseError` — 格式錯誤時拋出

指令格式設計原則：固定位置參數、空格分隔，避免複雜語法，方便手機輸入。

**`bot/listener.py`**
`TGListener` 使用 `python-telegram-bot` v20+（asyncio 版本）。
- 接收 CommandHandler 的回呼
- 呼叫 `parser.parse()` 取得指令
- 交給 `dispatcher.execute()` 或查詢方法
- 將結果格式化後 `reply_text` 回覆

`TGListener` 依賴注入 `TradeDispatcher`，不自行建立任何交易所客戶端。

---

### `trader/`

應用層，協調 bot 和 exchanges 之間的流程。

**`trader/models.py`**
與交易所無關的共用型別：
- `OrderSide` — `BUY` / `SELL`
- `OrderType` — `MARKET` / `LIMIT`
- `TradeSide` — `OPEN` / `CLOSE`
- `OrderRequest` — parser 產出、dispatcher 消費的中介物件

使用 `str` 型別的 `qty` / `price` 欄位，避免浮點精度問題。

**`trader/dispatcher.py`**
`TradeDispatcher` 持有 `dict[str, BaseExchange]`，以交易所名稱（小寫）為 key。
- `register(exchange)` — 啟動時注入交易所實例
- `execute(req)` — 將 `OrderRequest` 轉換為交易所 payload 並呼叫 `place_order`
- `get_account / get_positions / get_orders` — 查詢轉發

payload 組裝邏輯（加 `tradeSide`、`positionId`、限價欄位等）集中在 dispatcher，adapter 只做 API 呼叫，不做業務判斷。

---

### `config/`

**`config/settings.py`**
從環境變數讀取設定。若安裝了 `python-dotenv`，啟動時自動載入 `.env`。
`validate()` 在 `main.py` 最前面呼叫，缺少必要變數時立即報錯。

---

### `main.py`

組裝入口：
1. `settings.validate()` — 驗證環境變數
2. 建立 `TradeDispatcher`，`register` 各交易所 adapter
3. 建立 `TGListener`，注入 dispatcher
4. `bot.run()` — blocking polling

---

## 資料流

```
Telegram 使用者
    │
    ▼ 文字訊息
TGListener._handle_order / _handle_query
    │
    ▼ 呼叫
bot/parser.parse(text)
    │
    ▼ 回傳 OrderRequest / QueryCommand
TradeDispatcher.execute(req) / get_*()
    │
    ▼ 呼叫
BaseExchange.place_order(payload) / get_*()  ← 具體: BitunixExchange
    │
    ▼ 交易所 API 回應
TGListener.reply_text(結果)
    │
    ▼
Telegram 使用者
```

---

## 擴充慣例

### 新增交易所

1. 建立 `exchanges/<name>/` 目錄
2. 實作 `adapter.py`：
   ```python
   from exchanges.base import BaseExchange

   class <Name>Exchange(BaseExchange):
       @property
       def name(self) -> str:
           return "<name>"   # 小寫，使用者在 TG 指令中用這個字串
       # ... 實作其他抽象方法
   ```
3. 在 `config/settings.py` 新增對應的 API key 環境變數
4. 在 `main.py` `dispatcher.register(<Name>Exchange(...))`

### 新增 TG 指令

1. 在 `bot/parser.py` 新增解析函式，回傳 `OrderRequest` 或 `QueryCommand`
2. 在 `bot/listener.py` 新增對應的 `CommandHandler` 和 handler 方法
3. 若涉及新的業務邏輯，在 `trader/dispatcher.py` 新增對應方法

---

## 依賴版本

| 套件 | 用途 | 最低版本 |
|------|------|---------|
| `requests` | Bitunix HTTP API | 2.31 |
| `websocket-client` | Bitunix WebSocket | 1.7 |
| `python-telegram-bot` | Telegram Bot | 20.0（asyncio 版） |
| `python-dotenv` | 載入 .env 檔案 | 1.0 |

> `python-telegram-bot` v20 起採用 asyncio 架構，與 v13 以前的 API 不相容。

---

## 已知限制 / 未來方向

- **未實作 WebSocket 確認成交**：目前 `BitunixExchange.place_order` 只確認訂單送出，不等待成交事件。`exchanges/bitunix/trading_flow.py` 有 `run_futures_trading_flow` 可做完整生命週期管理，未來可整合至 dispatcher。
- **無持久化**：Bot 重啟後無歷史記錄，訂單狀態查詢依賴即時 API。
- **單一帳戶**：每個交易所目前只支援一組 API key，未來可在 adapter 層擴充多帳戶。
