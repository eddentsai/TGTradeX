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
import json as _json
import logging
import math
import os
import time as _time

import aiohttp
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


async def dry_run_short(
    threshold_pct: float,
    leverage: float,
    position_ratio: float,
    taker_fee: float,
    wait_secs: int,
) -> None:
    connection = None
    _aio_session = None
    ws_stream = None
    try:
        ex = BinanceExchange(
            api_key=settings.BINANCE_API_KEY.strip(),
            secret_key=settings.BINANCE_SECRET_KEY.strip(),
        )

        # 1. 取得 top3 高費率 symbols
        premium = await asyncio.to_thread(ex.fetch_premium_index_all)
        top3 = await asyncio.to_thread(
            ex.get_top3_symbols_nearest_funding, premium, threshold_pct
        )
        if not top3:
            logging.info(f"[DRY_RUN] 無符合門檻（{threshold_pct}%）的 symbols")
            return

        logging.info(f"[DRY_RUN] top3 symbols: {[t['symbol'] for t in top3]}")

        # 2. 取可用餘額
        acct = await asyncio.to_thread(ex.get_account)
        available = float(acct.get("available") or 0)
        logging.info(f"[DRY_RUN] available balance = {available}")

        # 3. 取進場 snapshot（bid1/ask1）via WS API
        connection = await client.websocket_api.create_connection()
        entry: dict[str, dict] = {}
        for t in top3:
            symbol = t["symbol"]
            resp = await connection.order_book(symbol=symbol, limit=5)
            result = resp.data().result
            bids = result.bids or []
            asks = result.asks or []
            if not bids or not asks:
                logging.warning(f"[DRY_RUN] {symbol} 無 bid/ask，跳過")
                continue
            bid1 = float(bids[0][0])
            ask1 = float(asks[0][0])
            qty_precision = await asyncio.to_thread(ex.get_qty_precision, symbol)
            qty = floor_to_precision(
                available * position_ratio * leverage / bid1, qty_precision
            )
            tick = await asyncio.to_thread(ex._tick_size, symbol)
            dec = BinanceExchange._decimals_from_tick(tick)
            entry[symbol] = {
                "entry_bid1": bid1,
                "bid1": bid1,
                "ask1": ask1,
                "qty": qty,
                "tick": tick,
                "dec": dec,
                "rate_pct": t["fundingRatePct"],
            }
            logging.info(
                f"[DRY_RUN] {symbol} | entry bid1={bid1:.{dec}f} ask1={ask1:.{dec}f} | qty={qty} | "
                f"rate={t['fundingRatePct']:.4f}%"
            )
        await connection.close_connection(close_session=True)
        connection = None

        if not entry:
            logging.info("[DRY_RUN] 無有效進場資料")
            return

        # 4. 訂閱 fstream depth5@100ms，即時追蹤 wait_secs 秒
        streams = "/".join(f"{s.lower()}@depth5@100ms" for s in entry)
        stream_url = f"wss://fstream.binance.com/stream?streams={streams}"
        logging.info(f"[DRY_RUN] 訂閱 fstream: {stream_url}")

        print(f"\n{'─'*72}")
        print(f"  {'時間':>8}  {'Symbol':<12}  {'bid1':>10}  {'ask1':>10}  {'即時PnL':>10}  {'PnL%':>7}")
        print(f"{'─'*72}")

        _aio_session = aiohttp.ClientSession()
        ws_stream = await _aio_session.ws_connect(stream_url, ssl=True)

        async def _stream_loop() -> None:
            async for raw_msg in ws_stream:
                if raw_msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    msg = _json.loads(raw_msg.data)
                except Exception:
                    continue
                stream_name = msg.get("stream", "")
                symbol = stream_name.split("@")[0].upper()
                if symbol not in entry:
                    continue
                data = msg.get("data", {})
                bids = data.get("b") or []
                asks = data.get("a") or []
                if not bids or not asks:
                    continue
                d = entry[symbol]
                tick = d["tick"]
                new_bid1 = round(round(float(bids[0][0]) / tick) * tick, 10) if tick > 0 else float(bids[0][0])
                new_ask1 = round(round(float(asks[0][0]) / tick) * tick, 10) if tick > 0 else float(asks[0][0])
                if new_bid1 == d["bid1"] and new_ask1 == d["ask1"]:
                    continue
                d["bid1"] = new_bid1
                d["ask1"] = new_ask1
                notional = d["entry_bid1"] * d["qty"]
                pnl_gross = (d["entry_bid1"] - new_ask1) * d["qty"]
                fees = (notional + new_ask1 * d["qty"]) * taker_fee
                pnl_net = pnl_gross - fees
                pnl_pct = pnl_net / notional * 100 if notional > 0 else 0.0
                ts = _time.strftime("%H:%M:%S")
                dec = d["dec"]
                w = dec + 6  # 整數部分最多5位 + 小數點 + dec位
                print(
                    f"  {ts:>8}  {symbol:<12}  {new_bid1:>{w}.{dec}f}  {new_ask1:>{w}.{dec}f}"
                    f"  {pnl_net:>10.4f}  {pnl_pct:>7.4f}%"
                )

        try:
            await asyncio.wait_for(_stream_loop(), timeout=wait_secs)
        except asyncio.TimeoutError:
            pass

        # 5. 最終 P&L 匯總
        print(f"\n{'─'*80}")
        print(f"  {'Symbol':<12}  {'entry_bid1':>10}  {'exit_ask1':>10}  {'qty':>8}"
              f"  {'PnL_net':>10}  {'PnL%':>7}  {'Rate%':>7}")
        print(f"{'─'*80}")
        for symbol, d in entry.items():
            entry_bid1 = d["entry_bid1"]
            exit_ask1  = d["ask1"]
            qty        = d["qty"]
            dec        = d["dec"]
            w          = dec + 6
            notional   = entry_bid1 * qty
            pnl_gross  = (entry_bid1 - exit_ask1) * qty
            fees       = (notional + exit_ask1 * qty) * taker_fee
            pnl_net    = pnl_gross - fees
            pnl_pct    = pnl_net / notional * 100 if notional > 0 else 0.0
            print(
                f"  {symbol:<12}  {entry_bid1:>{w}.{dec}f}  {exit_ask1:>{w}.{dec}f}  {qty:>8}"
                f"  {pnl_net:>10.4f}  {pnl_pct:>7.4f}%  {d['rate_pct']:>7.4f}%"
            )
        print(f"{'─'*80}\n")

    except Exception as e:
        logging.error(f"dry_run_short() error: {e}")
    finally:
        try:
            if ws_stream is not None:
                await ws_stream.close()
            if _aio_session is not None:
                await _aio_session.close()
        except Exception:
            pass
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
    p.add_argument("--threshold", type=float, default=-1.0,
                   help="dryrun: 入選費率門檻 %（預設 -1.0）")
    p.add_argument("--leverage", type=float, default=5.0,
                   help="dryrun: 槓桿倍數（預設 5）")
    p.add_argument("--ratio", type=float, default=0.15,
                   help="dryrun: 每筆動用保證金比例（預設 0.15）")
    p.add_argument("--taker-fee", type=float, default=0.0005,
                   help="dryrun: 吃單手續費率（預設 0.0005）")
    p.add_argument("--wait", type=int, default=20,
                   help="dryrun: 模擬持倉秒數（預設 20）")
    args = p.parse_args()

    if args.action == "book":
        asyncio.run(order_book(args.symbol, args.limit))
    elif args.action == "order":
        asyncio.run(new_order(args.symbol))
    else:
        asyncio.run(dry_run_short(
            threshold_pct=args.threshold,
            leverage=args.leverage,
            position_ratio=args.ratio,
            taker_fee=args.taker_fee,
            wait_secs=args.wait,
        ))
