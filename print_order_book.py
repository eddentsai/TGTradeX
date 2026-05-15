"""
掛單簿查詢 + 市價空單測試

用法：
    python print_order_book.py                        # 印掛單簿
    python print_order_book.py --symbol ETHUSDT --limit 10
    python print_order_book.py --action order         # 下最小單位市價空單
    python print_order_book.py --action order --symbol ETHUSDT
"""
import argparse
import asyncio
import logging
import os

from dotenv import load_dotenv

from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    ConfigurationWebSocketAPI,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_api.models import (
    NewOrderNewOrderRespTypeEnum,
    NewOrderSideEnum,
)

from exchanges.binance.adapter import BinanceExchange
from config import settings

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

        result = response.data().result

        bids = result.bids or []
        asks = result.asks or []

        print(f"\n{'─'*50}")
        print(f"  {symbol}  掛單簿（前 {limit} 檔）  lastUpdateId={result.last_update_id}")
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


async def new_order(symbol: str) -> None:
    connection = None
    try:
        connection = await client.websocket_api.create_connection()

        # 取最小下單數量：1 step = 10^(-qty_precision)
        ex = BinanceExchange(
            api_key=settings.BINANCE_API_KEY.strip(),
            secret_key=settings.BINANCE_SECRET_KEY.strip(),
        )
        qty_precision = await asyncio.to_thread(ex.get_qty_precision, symbol)
        min_qty = 10 ** (-qty_precision) if qty_precision > 0 else 1.0
        logging.info(f"new_order() symbol={symbol} | qty_precision={qty_precision} | min_qty={min_qty}")

        response = await connection.new_order(
            symbol=symbol,
            side=NewOrderSideEnum["SELL"].value,
            type="MARKET",
            quantity=min_qty,
            reduce_only="false",
            new_order_resp_type=NewOrderNewOrderRespTypeEnum.RESULT,
        )

        rate_limits = response.rate_limits
        logging.info(f"new_order() rate limits: {rate_limits}")

        result = response.data().result
        logging.info(
            f"new_order() | orderId={result.order_id} | symbol={result.symbol} | "
            f"status={result.status} | side={result.side} | type={result.type} | "
            f"origQty={result.orig_qty} | executedQty={result.executed_qty} | "
            f"avgPrice={result.avg_price} | updateTime={result.update_time}"
        )

        if result.status != "FILLED":
            logging.warning(f"new_order() 未成交，跳過平倉 | status={result.status}")
            return

        # 成交後等 3 秒再市價平倉
        logging.info("等待 3 秒後平倉...")
        await asyncio.sleep(3)

        close_response = await connection.new_order(
            symbol=symbol,
            side=NewOrderSideEnum["BUY"].value,
            type="MARKET",
            quantity=float(result.executed_qty),
            reduce_only="true",
            new_order_resp_type=NewOrderNewOrderRespTypeEnum.RESULT,
        )

        close_result = close_response.data().result
        logging.info(
            f"close_order() | orderId={close_result.order_id} | "
            f"status={close_result.status} | executedQty={close_result.executed_qty} | "
            f"avgPrice={close_result.avg_price}"
        )

    except Exception as e:
        logging.error(f"new_order() error: {e}")
    finally:
        if connection:
            await connection.close_connection(close_session=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="掛單簿查詢 / 市價空單測試")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--action", default="book", choices=["book", "order"],
                   help="book=印掛單簿, order=下最小單位市價空單")
    p.add_argument("--limit", type=int, default=5,
                   choices=[5, 10, 20, 50, 100, 500, 1000],
                   help="掛單檔數（--action book 時有效）")
    args = p.parse_args()

    if args.action == "book":
        asyncio.run(order_book(args.symbol, args.limit))
    else:
        asyncio.run(new_order(args.symbol))
