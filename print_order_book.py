"""
掛單簿查詢 + 市價空單測試

用法：
    python print_order_book.py                        # 印掛單簿
    python print_order_book.py --symbol ETHUSDT --limit 10
    python print_order_book.py --action order         # 下最小單位市價空單
    python print_order_book.py --action order --symbol ETHUSDT
    python print_order_book.py --action dryrun        # 模擬空單，不實際下單
"""
import argparse
import asyncio
import logging
import math
import os

from dotenv import load_dotenv

from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    ConfigurationWebSocketAPI,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_api.models import (
    NewAlgoOrderSideEnum,
    NewOrderNewOrderRespTypeEnum,
    NewOrderSideEnum,
)

from exchanges.binance.adapter import BinanceExchange
from config import settings

THRESHOLD_PCT = -1.0    # 入選費率門檻（%）
LEVERAGE      = 5
POSITION_RATIO = 0.15   # 每筆動用 15% 保證金
TAKER_FEE     = 0.0005  # 0.0500%
WAIT_SECS     = 20      # dryrun 模擬持倉秒數


def floor_to_precision(x: float, p: int) -> float:
    if p < 0:
        return x
    f = 10 ** p
    return math.floor(x * f) / f

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

        # 成交後掛止損單：SL 設在進場均價 +0.5%（空單往上止損）
        avg_price = float(result.avg_price)
        sl_price = round(avg_price * 1.005, 2)
        logging.info(f"掛止損單 | avg_price={avg_price} | sl_price={sl_price}")

        algo_response = await connection.new_algo_order(
            algo_type="CONDITIONAL",
            symbol=symbol,
            side=NewAlgoOrderSideEnum["BUY"].value,
            type="STOP_MARKET",
            trigger_price=sl_price,
            working_type="CONTRACT_PRICE",
            close_position="true",
            price_protect="TRUE",
        )

        rate_limits = algo_response.rate_limits
        logging.info(f"new_algo_order() rate limits: {rate_limits}")

        algo_result = algo_response.data().result
        logging.info(
            f"new_algo_order() | algoId={algo_result.algo_id} | "
            f"algoType={algo_result.algo_type} | orderType={algo_result.order_type} | "
            f"symbol={algo_result.symbol} | side={algo_result.side} | "
            f"algoStatus={algo_result.algo_status} | triggerPrice={algo_result.trigger_price} | "
            f"closePosition={algo_result.close_position} | workingType={algo_result.working_type}"
        )

        # 等 3 秒後市價平倉（止損單會隨倉位關閉自動取消）
        logging.info("等待 3 秒後市價平倉...")
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


async def dry_run_short() -> None:
    connection = None
    try:
        ex = BinanceExchange(
            api_key=settings.BINANCE_API_KEY.strip(),
            secret_key=settings.BINANCE_SECRET_KEY.strip(),
        )

        # 1. 取得 top3 高費率 symbols
        premium = await asyncio.to_thread(ex.fetch_premium_index_all)
        top3 = await asyncio.to_thread(
            ex.get_top3_symbols_nearest_funding, premium, THRESHOLD_PCT
        )
        if not top3:
            logging.info(f"[DRY_RUN] 無符合門檻（{THRESHOLD_PCT}%）的 symbols")
            return

        logging.info(f"[DRY_RUN] top3 symbols: {[t['symbol'] for t in top3]}")

        # 2. 取可用餘額
        acct = await asyncio.to_thread(ex.get_account)
        available = float(acct.get("available") or 0)
        logging.info(f"[DRY_RUN] available balance = {available}")

        connection = await client.websocket_api.create_connection()

        # 3. 記錄每個 symbol 的 bid1（模擬進場價）
        entry: dict[str, dict] = {}
        for t in top3:
            symbol = t["symbol"]
            resp = await connection.order_book(symbol=symbol, limit=5)
            result = resp.data().result
            bids = result.bids or []
            if not bids:
                logging.warning(f"[DRY_RUN] {symbol} 無 bid，跳過")
                continue
            bid1 = float(bids[0][0])
            qty_precision = await asyncio.to_thread(ex.get_qty_precision, symbol)
            qty = floor_to_precision(
                available * POSITION_RATIO * LEVERAGE / bid1, qty_precision
            )
            entry[symbol] = {
                "bid1": bid1,
                "qty": qty,
                "qty_precision": qty_precision,
                "rate_pct": t["fundingRatePct"],
            }
            logging.info(
                f"[DRY_RUN] {symbol} | bid1={bid1} | qty={qty} | "
                f"rate={t['fundingRatePct']:.4f}%"
            )

        if not entry:
            logging.info("[DRY_RUN] 無有效進場資料")
            return

        logging.info(f"[DRY_RUN] 等待 {WAIT_SECS} 秒...")
        await asyncio.sleep(WAIT_SECS)

        # 4. 抓 ask1（模擬出場價），計算 P&L
        print(f"\n{'─'*70}")
        print(f"  {'Symbol':<12}  {'bid1':>10}  {'ask1':>10}  {'qty':>8}  "
              f"{'PnL_net':>10}  {'PnL%':>7}  {'Rate%':>7}")
        print(f"{'─'*70}")

        for symbol, d in entry.items():
            resp = await connection.order_book(symbol=symbol, limit=5)
            result = resp.data().result
            asks = result.asks or []
            if not asks:
                logging.warning(f"[DRY_RUN] {symbol} 無 ask，跳過")
                continue
            ask1 = float(asks[0][0])
            bid1 = d["bid1"]
            qty = d["qty"]
            notional = bid1 * qty
            pnl_gross = (bid1 - ask1) * qty
            entry_fee = notional * TAKER_FEE
            exit_fee  = ask1 * qty * TAKER_FEE
            pnl_net   = pnl_gross - entry_fee - exit_fee
            pnl_pct   = pnl_net / notional * 100 if notional > 0 else 0.0
            print(
                f"  {symbol:<12}  {bid1:>10.4f}  {ask1:>10.4f}  {qty:>8}  "
                f"  {pnl_net:>9.4f}  {pnl_pct:>6.4f}%  {d['rate_pct']:>6.4f}%"
            )

        print(f"{'─'*70}\n")

    except Exception as e:
        logging.error(f"dry_run_short() error: {e}")
    finally:
        if connection:
            await connection.close_connection(close_session=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="掛單簿查詢 / 市價空單測試")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--action", default="book", choices=["book", "order", "dryrun"],
                   help="book=印掛單簿, order=下最小單位市價空單, dryrun=模擬空單P&L")
    p.add_argument("--limit", type=int, default=5,
                   choices=[5, 10, 20, 50, 100, 500, 1000],
                   help="掛單檔數（--action book 時有效）")
    args = p.parse_args()

    if args.action == "book":
        asyncio.run(order_book(args.symbol, args.limit))
    elif args.action == "order":
        asyncio.run(new_order(args.symbol))
    else:
        asyncio.run(dry_run_short())
