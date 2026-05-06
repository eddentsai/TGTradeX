"""
WS 下單流程測試腳本

測試項目：
  1. WS Stream  — mark price 訂閱
  2. WS API     — 市價空單（最小量 0.001 BTCUSDT）
  3. User Data Stream — 等待持倉確認
  4. SL/TP      — 成交後掛止損止盈

用法：
    python test_ws_order_flow.py
    python test_ws_order_flow.py --symbol ETHUSDT --qty 0.01
    python test_ws_order_flow.py --dry-run   # 僅連線測試，不實際下單
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from binance_common.configuration import ConfigurationWebSocketAPI, ConfigurationWebSocketStreams
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_api.models import (
    NewOrderNewOrderRespTypeEnum,
    NewOrderSideEnum,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_api.websocket_api import (
    DerivativesTradingUsdsFuturesWebSocketAPI,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    AccountUpdate,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.websocket_streams import (
    DerivativesTradingUsdsFuturesWebSocketStreams,
)

from config import settings
from exchanges.binance.adapter import BinanceExchange
from services.log_handler import setup_logging

load_dotenv()

TZ = timezone(timedelta(hours=8))

TP_PCT = 1.0   # 止盈：低於進場 1%
SL_PCT = 0.5   # 止損：高於進場 0.5%


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=TZ).strftime("%H:%M:%S.%f")[:-3]


async def run_test(symbol: str, qty: float, dry_run: bool) -> None:
    logger = logging.getLogger(__name__)
    logger.info(f"{'[DRY-RUN] ' if dry_run else ''}測試開始 | symbol={symbol} | qty={qty}")

    ex = BinanceExchange(
        api_key=settings.BINANCE_API_KEY.strip(),
        secret_key=settings.BINANCE_SECRET_KEY.strip(),
        testnet=False,
    )

    # ── 1. 建立 WS Stream 連線 ────────────────────────────────────────────────
    stream_cfg = ConfigurationWebSocketStreams(
        stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL
    )
    ws_streams = DerivativesTradingUsdsFuturesWebSocketStreams(stream_cfg)
    await ws_streams.create_connection()

    latest_price: list[float] = [0.0]

    stream_handle = await ws_streams.mark_price_stream(symbol=symbol, update_speed="1000ms")

    def on_mark_price(msg) -> None:
        if msg.p:
            latest_price[0] = float(msg.p)

    stream_handle.on("message", on_mark_price)
    logger.info(f"[WS_STREAM] subscribed markPrice for {symbol}")

    # ── 2. 建立 WS API 連線 ───────────────────────────────────────────────────
    ws_api_cfg = ConfigurationWebSocketAPI(
        api_key=settings.BINANCE_API_KEY.strip(),
        api_secret=settings.BINANCE_SECRET_KEY.strip(),
        stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    )
    ws_api = DerivativesTradingUsdsFuturesWebSocketAPI(ws_api_cfg)
    await ws_api.create_connection()
    logger.info("[WS_API] connection established")

    # ── 3. 訂閱 User Data Stream ──────────────────────────────────────────────
    position_ready = asyncio.Event()
    ud_handle = None

    try:
        listen_resp = await ws_api.start_user_data_stream()
        listen_key = listen_resp.data().result.listen_key
        ud_handle = await ws_streams.user_data(listen_key)

        def on_user_data(msg) -> None:
            # SDK passes a raw dict for UserDataStreamEventsResponse (one_of_schemas model)
            if isinstance(msg, dict):
                if msg.get("e") != "ACCOUNT_UPDATE":
                    return
                for pos in (msg.get("a") or {}).get("P") or []:
                    if pos.get("s") == symbol and float(pos.get("pa") or "0") < 0:
                        logger.info(
                            f"[USER_DATA] ACCOUNT_UPDATE received | "
                            f"symbol={pos.get('s')} | pa={pos.get('pa')} | ep={pos.get('ep')}"
                        )
                        position_ready.set()
            else:
                instance = msg.actual_instance
                if not isinstance(instance, AccountUpdate):
                    return
                if not (instance.a and instance.a.P):
                    return
                for pos in instance.a.P:
                    if pos.s == symbol and pos.pa and float(pos.pa) < 0:
                        logger.info(
                            f"[USER_DATA] ACCOUNT_UPDATE received | "
                            f"symbol={pos.s} | pa={pos.pa} | ep={pos.ep}"
                        )
                        position_ready.set()

        ud_handle.on("message", on_user_data)
        logger.info(f"[USER_DATA_STREAM] subscribed | listenKey={listen_key[:8]}...")
    except Exception as e:
        logger.warning(f"[USER_DATA_STREAM] setup failed, will fallback to REST: {e}")

    # ── 4. 取 mark price（WS stream 在 Taiwan 網路環境無法收到資料，用 REST 取代）
    await asyncio.sleep(1.0)  # 等 WS 連線穩定
    if latest_price[0] == 0.0:
        try:
            prices = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ex.fetch_all_latest_prices_v2()
            )
            latest_price[0] = prices.get(symbol, 0.0)
            logger.info(f"[PRICE] mark price via REST = {latest_price[0]}")
        except Exception as e:
            logger.warning(f"[PRICE] REST fallback failed: {e}")
    else:
        logger.info(f"[PRICE] mark price via WS = {latest_price[0]}")

    if dry_run:
        logger.info("[DRY-RUN] 連線測試完成，不下單")
        await _cleanup(stream_handle, ud_handle, ws_streams, ws_api)
        return

    # ── 5. 下市價空單 ─────────────────────────────────────────────────────────
    send_ts = now_ms()
    logger.info(
        f"[ORDER_SEND] ts={send_ts} ({fmt_ms(send_ts)}) | "
        f"symbol={symbol} | side=SELL | qty={qty} | mark_price={latest_price[0]}"
    )

    try:
        resp = await ws_api.new_order(
            symbol=symbol,
            side=NewOrderSideEnum.SELL,
            type="MARKET",
            quantity=qty,
            reduce_only="false",
            new_order_resp_type=NewOrderNewOrderRespTypeEnum.RESULT,
        )

        ack_ts = now_ms()
        ack_delay = ack_ts - send_ts
        result = resp.data().result

        order_id = str(getattr(result, "order_id", "") or "")
        status = str(getattr(result, "status", "") or "")
        avg_price = float(getattr(result, "avg_price", None) or latest_price[0])
        executed_qty = str(getattr(result, "executed_qty", None) or qty)
        fill_ts = getattr(result, "update_time", None)

        logger.info(
            f"[ORDER_FILL] ack_delay={ack_delay}ms | orderId={order_id} | "
            f"status={status} | avgPrice={avg_price} | executedQty={executed_qty} | "
            f"fillTime={fmt_ms(fill_ts) if fill_ts else 'N/A'}"
        )

        if status != "FILLED":
            logger.error(f"[ORDER_FILL] unexpected status: {status}")
            await _cleanup(stream_handle, ud_handle, ws_streams, ws_api)
            return

        # ── 6. 等持倉確認 ─────────────────────────────────────────────────────
        if not position_ready.is_set():
            try:
                await asyncio.wait_for(position_ready.wait(), timeout=3.0)
                logger.info("[POSITION_READY] confirmed via user data stream")
            except asyncio.TimeoutError:
                logger.warning("[POSITION_READY] timeout, falling back to REST")

        positions = ex.get_pending_positions(symbol=symbol)
        logger.info(f"[POSITION_SNAPSHOT] rows={len(positions)}")
        for p in positions:
            logger.info(
                f"  symbol={p['symbol']} | side={p['side']} | qty={p['qty']} | "
                f"openPrice={p['openPrice']} | unrealizedPnl={p['unrealizedPnl']}"
            )

        if not positions:
            logger.warning("[POSITION_SNAPSHOT] no position found after fill")
            await _cleanup(stream_handle, ud_handle, ws_streams, ws_api)
            return

        # ── 7. 掛止損 / 止盈 ──────────────────────────────────────────────────
        tp_price = avg_price * (1 - TP_PCT / 100)
        sl_price = avg_price * (1 + SL_PCT / 100)
        logger.info(
            f"[SL_TP_CALC] entry={avg_price} | "
            f"TP={tp_price:.4f} (-{TP_PCT}%) | SL={sl_price:.4f} (+{SL_PCT}%)"
        )

        try:
            ex.place_sl_tp_orders(
                symbol=symbol,
                side="SELL",
                qty=str(qty),
                sl_price=sl_price,
                tp_price=tp_price,
            )
            logger.info(f"[RISK_ORDERS] SL/TP placed successfully")
        except Exception as e:
            logger.exception(f"[RISK_ORDERS] failed: {e}")

    except Exception as e:
        logger.exception(f"[ORDER] failed: {e}")

    finally:
        await _cleanup(stream_handle, ud_handle, ws_streams, ws_api)


async def _cleanup(stream_handle, ud_handle, ws_streams, ws_api) -> None:
    try:
        await stream_handle.unsubscribe()
        if ud_handle is not None:
            await ud_handle.unsubscribe()
        await ws_streams.close_connection()
    except Exception:
        pass
    try:
        await ws_api.close_connection()
    except Exception:
        pass


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--qty", type=float, default=0.001)
    p.add_argument("--dry-run", action="store_true", help="只測試連線，不下單")
    args = p.parse_args()

    setup_logging()
    asyncio.run(run_test(args.symbol, args.qty, args.dry_run))


if __name__ == "__main__":
    main()
