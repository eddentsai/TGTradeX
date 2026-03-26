"""
Bitunix SDK 使用範例

執行前請先安裝依賴：
    pip install -r requirements.txt

設定環境變數：
    export BITUNIX_API_KEY="your_api_key"
    export BITUNIX_SECRET_KEY="your_secret_key"
"""
import os
import time

from exchanges.bitunix import BitunixClient, BitunixApiError

API_KEY = os.environ.get("BITUNIX_API_KEY", "")
SECRET_KEY = os.environ.get("BITUNIX_SECRET_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# 1. 公開 HTTP API（不需要 credentials）
# ─────────────────────────────────────────────────────────────────────────────

def demo_public_http():
    client = BitunixClient()

    print("=== Tickers ===")
    tickers = client.futures_public.get_tickers("BTCUSDT,ETHUSDT")
    for t in tickers:
        print(f"  {t.get('symbol')}: last={t.get('lastPrice')}  mark={t.get('markPrice')}")

    print("\n=== Order Book (BTCUSDT, top 5) ===")
    depth = client.futures_public.get_depth("BTCUSDT", limit=5)
    asks = depth.get("asks", [])[:3]
    bids = depth.get("bids", [])[:3]
    print(f"  asks: {asks}")
    print(f"  bids: {bids}")

    print("\n=== Funding Rate (BTCUSDT) ===")
    rate = client.futures_public.get_funding_rate("BTCUSDT")
    print(f"  {rate}")

    print("\n=== Trading Pairs ===")
    pairs = client.futures_public.get_trading_pairs("BTCUSDT")
    for p in pairs:
        print(f"  {p.get('symbol')}: maxLeverage={p.get('maxLeverage')}  status={p.get('symbolStatus')}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. 私有 HTTP API（需要 credentials）
# ─────────────────────────────────────────────────────────────────────────────

def demo_private_http():
    if not API_KEY or not SECRET_KEY:
        print("跳過私有 API 範例（未設定環境變數）")
        return

    client = BitunixClient(api_key=API_KEY, secret_key=SECRET_KEY)

    print("\n=== 帳戶資訊 ===")
    account = client.futures_private.get_account()
    print(f"  available={account.get('available')}  unrealizedPnl={account.get('unrealizedPnl')}")

    print("\n=== 未完成訂單 ===")
    orders = client.futures_private.get_pending_orders(symbol="BTCUSDT")
    if orders:
        for o in orders:
            print(f"  {o.get('orderId')}  {o.get('side')}  qty={o.get('qty')}  price={o.get('price')}")
    else:
        print("  （無未完成訂單）")

    print("\n=== 持倉中倉位 ===")
    positions = client.futures_private.get_pending_positions(symbol="BTCUSDT")
    if positions:
        for p in positions:
            print(f"  {p.get('positionId')}  {p.get('side')}  qty={p.get('qty')}  unrealizedPnl={p.get('unrealizedPnl')}")
    else:
        print("  （無持倉）")


# ─────────────────────────────────────────────────────────────────────────────
# 3. 公開 WebSocket
# ─────────────────────────────────────────────────────────────────────────────

def demo_public_ws():
    client = BitunixClient()

    print("\n=== 公開 WebSocket (BTCUSDT ticker, 5 秒) ===")

    def on_ticker(data):
        if data:
            print(f"  ticker: last={data.get('lastPrice')}  mark={data.get('markPrice')}")

    def on_connected():
        print("  已連線，訂閱 ticker...")
        client.futures_ws_public.subscribe_public([{"symbol": "BTCUSDT", "ch": "ticker"}])

    client.futures_ws_public.on("connected", on_connected)
    client.futures_ws_public.on("ticker", on_ticker)
    client.futures_ws_public.start()

    time.sleep(5)
    client.futures_ws_public.stop()
    print("  WebSocket 已停止")


# ─────────────────────────────────────────────────────────────────────────────
# 4. 私有 WebSocket（帳戶串流）
# ─────────────────────────────────────────────────────────────────────────────

def demo_private_ws():
    if not API_KEY or not SECRET_KEY:
        print("跳過私有 WS 範例（未設定環境變數）")
        return

    client = BitunixClient(api_key=API_KEY, secret_key=SECRET_KEY)

    print("\n=== 私有 WebSocket（帳戶串流，10 秒）===")

    def on_order(data):
        print(f"  [order] {data}")

    def on_position(data):
        print(f"  [position] {data}")

    def on_balance(data):
        print(f"  [balance] {data}")

    def on_connected():
        print("  已連線，訂閱帳戶串流...")
        client.futures_ws_private.subscribe_account_streams()

    client.futures_ws_private.on("connected", on_connected)
    client.futures_ws_private.on("order", on_order)
    client.futures_ws_private.on("position", on_position)
    client.futures_ws_private.on("balance", on_balance)
    client.futures_ws_private.start()

    time.sleep(10)
    client.futures_ws_private.stop()
    print("  WebSocket 已停止")


if __name__ == "__main__":
    demo_public_http()
    demo_private_http()
    demo_public_ws()
    demo_private_ws()
