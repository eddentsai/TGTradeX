"""
印出指定合約的掛單簿（前 N 檔買賣掛單）

用法：
    python print_order_book.py
    python print_order_book.py --symbol ETHUSDT --limit 10
"""
import argparse
import asyncio
import logging
import os

from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    ConfigurationWebSocketAPI,
)

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

configuration_ws_api = ConfigurationWebSocketAPI(
    api_key=os.getenv("BINANCE_API_KEY", ""),
    api_secret=os.getenv("BINANCE_SECRET_KEY", ""),
    stream_url=os.getenv("STREAM_URL", DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL),
)

client = DerivativesTradingUsdsFutures(config_ws_api=configuration_ws_api)


async def order_book(symbol: str, limit: int) -> None:
    connection = None
    try:
        connection = await client.websocket_api.create_connection()

        response = await connection.order_book(symbol=symbol, limit=limit)

        rate_limits = response.rate_limits
        logging.info(f"order_book() rate limits: {rate_limits}")

        data = response.data()

        bids = data.bids or []
        asks = data.asks or []

        print(f"\n{'─'*50}")
        print(f"  {symbol}  掛單簿（前 {limit} 檔）  lastUpdateId={data.last_update_id}")
        print(f"{'─'*50}")
        print(f"  {'Ask 價格':>14}  {'Ask 數量':>10}    {'Bid 價格':<14}  {'Bid 數量':<10}")
        print(f"{'─'*50}")

        for i in range(max(len(bids), len(asks))):
            bid_p, bid_q = (bids[i][0], bids[i][1]) if i < len(bids) else ("", "")
            ask_p, ask_q = (asks[i][0], asks[i][1]) if i < len(asks) else ("", "")
            print(f"  {ask_p:>14}  {ask_q:>10}    {bid_p:<14}  {bid_q:<10}")

        print(f"{'─'*50}")

    except Exception as e:
        logging.error(f"order_book() error: {e}")
    finally:
        if connection:
            await connection.close_connection(close_session=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="印出合約掛單簿")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--limit", type=int, default=5,
                   choices=[5, 10, 20, 50, 100, 500, 1000])
    args = p.parse_args()

    asyncio.run(order_book(args.symbol, args.limit))
