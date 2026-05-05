from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from binance_common.configuration import ConfigurationWebSocketAPI, ConfigurationWebSocketStreams
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_api.websocket_api import (
    DerivativesTradingUsdsFuturesWebSocketAPI,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_api.models import (
    NewOrderNewOrderRespTypeEnum,
    NewOrderSideEnum,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.websocket_streams import (
    DerivativesTradingUsdsFuturesWebSocketStreams,
)

from config import settings
from exchanges.binance.adapter import BinanceExchange
from services.log_handler import setup_logging
from services.notifier import TelegramNotifier


load_dotenv()


# ===== 可調參數 =====
THRESHOLD_PCT = -0.5          # 入選費率門檻（%）
CANCEL_THRESHOLD_PCT = -0.3   # 費率回升到高於此值則放棄下單（%）
USE_TESTNET = False
LEVERAGE = 5
POSITION_RATIO = 0.02
TZ = timezone(timedelta(hours=8))  # Asia/Taipei


def now_tpe() -> datetime:
    return datetime.now(TZ)


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %z")


def fmt_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=TZ).strftime("%Y-%m-%d %H:%M:%S.%f %z")


def sleep_until(target: datetime) -> None:
    while True:
        sec = (target - now_tpe()).total_seconds()
        if sec <= 0:
            return
        time.sleep(min(sec, 0.2))


def next_hh_59(base: datetime) -> datetime:
    t = base.replace(minute=59, second=0, microsecond=0)
    if t <= base:
        t += timedelta(hours=1)
    return t


def floor_to_precision(x: float, p: int) -> float:
    if p < 0:
        return x
    f = 10 ** p
    return math.floor(x * f) / f


def calc_short_tp_sl(price: float, threshold_pct: float) -> tuple[float, float]:
    rate = abs(threshold_pct) / 100.0
    tp = price * (1 - rate)
    sl = price * (1 + rate / 2)
    return tp, sl


def calc_order_qty(
    available_balance: float,
    ref_price: float,
    qty_precision: int,
    leverage: float = LEVERAGE,
    ratio: float = POSITION_RATIO,
) -> float:
    notional = available_balance * ratio * leverage
    raw_qty = notional / ref_price if ref_price > 0 else 0
    qty = floor_to_precision(raw_qty, qty_precision)
    return max(qty, 0.0)


async def _ws_cycle(
    symbol: str,
    funding_rate_pct_59: float,
    t5958: datetime,
    t00: datetime,
    api_key: str,
    secret_key: str,
    ex: BinanceExchange,
    notifier: TelegramNotifier | None,
) -> None:
    logger = logging.getLogger(__name__)

    # ── 1. 訂閱 mark price stream，從此開始持續更新費率 ──────────────────────
    stream_cfg = ConfigurationWebSocketStreams(
        stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL
    )
    ws_streams = DerivativesTradingUsdsFuturesWebSocketStreams(stream_cfg)
    await ws_streams.create_connection()

    latest_funding_rate_pct: list[float] = [funding_rate_pct_59]

    stream_handle = await ws_streams.mark_price_stream(symbol=symbol, update_speed="1000ms")

    def on_mark_price(msg) -> None:
        if msg.r:
            latest_funding_rate_pct[0] = float(msg.r) * 100

    stream_handle.on("message", on_mark_price)
    logger.info(f"[WS_STREAM] subscribed markPrice for {symbol}")

    # 與 stream 同步建立 WS API 連線，讓 00:00 下單時直接使用熱連線
    ws_api_cfg = ConfigurationWebSocketAPI(
        api_key=api_key,
        api_secret=secret_key,
        stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL,
    )
    ws_api = DerivativesTradingUsdsFuturesWebSocketAPI(ws_api_cfg)
    await ws_api.create_connection()
    logger.info(f"[WS_API] connection established for {symbol}")

    try:
        # ── 2. 等到 HH:59:58，抓取參考價 ────────────────────────────────────
        sec = (t5958 - now_tpe()).total_seconds()
        if sec > 0:
            await asyncio.sleep(sec)

        try:
            all_prices = ex.fetch_all_latest_prices_v2()
            price_5958 = all_prices.get(symbol)
            logger.info(f"[{fmt_dt(now_tpe())}] @59:58 {symbol} | price={price_5958}")
        except Exception as e:
            logger.exception(f"@59:58 fetch price failed: {e}")
            return

        if price_5958 is None:
            logger.warning(f"@59:58 skip, price missing | symbol={symbol}")
            return

        # ── 3. 計算倉位 ──────────────────────────────────────────────────────
        try:
            acct = ex.get_account()
            available = float(acct.get("available") or 0)
            qty_precision = ex.get_qty_precision(symbol)
            qty = calc_order_qty(
                available_balance=available,
                ref_price=price_5958,
                qty_precision=qty_precision,
            )
        except Exception as e:
            logger.exception(f"@59:58 get account/qty failed: {e}")
            return

        if qty <= 0:
            logger.warning(
                f"skip, qty<=0 | symbol={symbol} | available={available} | price={price_5958}"
            )
            return

        tp_price, sl_price = calc_short_tp_sl(price_5958, THRESHOLD_PCT)

        # ── 4. 等到 HH:00:00 ─────────────────────────────────────────────────
        sec = (t00 - now_tpe()).total_seconds()
        if sec > 0:
            await asyncio.sleep(sec)

        # ── 5. 確認費率仍符合條件（用 WS stream 最新值） ─────────────────────
        current_rate = latest_funding_rate_pct[0]
        logger.info(
            f"[WS_RATE_CHECK] symbol={symbol} | rate_at_59={funding_rate_pct_59:.4f}% | "
            f"rate_now={current_rate:.4f}% | cancel_threshold={CANCEL_THRESHOLD_PCT}%"
        )

        if current_rate > CANCEL_THRESHOLD_PCT:
            logger.warning(
                f"[WS_CANCEL] funding rate {current_rate:.4f}% > {CANCEL_THRESHOLD_PCT}%, skip"
            )
            if notifier:
                notifier.send(
                    f"⚠️ <b>資金費率空單取消</b>  {symbol}\n"
                    f"費率已回升至 {current_rate:.4f}%（取消門檻 {CANCEL_THRESHOLD_PCT}%），放棄進場"
                )
            return

        # ── 6. 透過 WebSocket API 下市價單（RESULT 模式，等成交回傳） ──────────
        send_ts = now_ms()
        logger.info(
            f"[WS_ORDER_SEND] ts={send_ts} ({fmt_ms(send_ts)}) | symbol={symbol} | side=SELL | "
            f"type=MARKET | qty={qty} | ref_price_5958={price_5958} | funding_rate={current_rate:.4f}%"
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
            avg_price = float(getattr(result, "avg_price", None) or price_5958)
            executed_qty = str(getattr(result, "executed_qty", None) or qty)
            fill_ts = getattr(result, "update_time", None)

            logger.info(
                f"[WS_ORDER_FILL] ts={ack_ts} ({fmt_ms(ack_ts)}) | ack_delay_ms={ack_delay} | "
                f"symbol={symbol} | orderId={order_id} | status={status} | "
                f"avgPrice={avg_price} | executedQty={executed_qty} | "
                f"fillTime={fmt_ms(fill_ts) if fill_ts else 'N/A'}"
            )

            # ── 7. 掛止損 / 止盈 ─────────────────────────────────────────────
            ex.place_sl_tp_orders(
                symbol=symbol,
                side="SELL",
                qty=str(qty),
                sl_price=sl_price,
                tp_price=tp_price,
            )
            logger.info(
                f"[RISK_ORDERS] symbol={symbol} | qty={qty} | "
                f"tp={tp_price} | sl={sl_price} | based_on=price_5958({price_5958})"
            )

            # ── 8. TG 通知 ────────────────────────────────────────────────────
            if notifier:
                if status == "FILLED":
                    notifier.notify_funding_short(
                        symbol=symbol,
                        funding_rate_pct=current_rate,
                        qty=executed_qty,
                        entry_price=avg_price,
                        tp_price=tp_price,
                        sl_price=sl_price,
                        order_id=order_id,
                        ack_delay_ms=ack_delay,
                    )
                else:
                    notifier.send(
                        f"⚠️ <b>資金費率空單異常</b>  {symbol}\n"
                        f"orderId={order_id}  status={status}\n"
                        f"費率: {current_rate:.4f}%"
                    )

            # ── 9. 倉位快照 ───────────────────────────────────────────────────
            positions = ex.get_pending_positions(symbol=symbol)
            logger.info(
                f"[POSITION_SNAPSHOT] symbol={symbol} | rows={len(positions)} | data={positions}"
            )

        except Exception as e:
            logger.exception(f"[WS_ORDER] failed: {e}")
            if notifier:
                notifier.send(
                    f"🔴 <b>資金費率空單失敗</b>  {symbol}\n"
                    f"費率: {current_rate:.4f}%\n"
                    f"錯誤: {e}"
                )

    finally:
        try:
            await stream_handle.unsubscribe()
            await ws_streams.close_connection()
        except Exception:
            pass
        if ws_api is not None:
            try:
                await ws_api.close_connection()
            except Exception:
                pass


def run() -> None:
    logger = logging.getLogger(__name__)

    api_key = settings.BINANCE_API_KEY.strip()
    secret_key = settings.BINANCE_SECRET_KEY.strip()
    if not api_key or not secret_key:
        raise RuntimeError("Missing env: BINANCE_API_KEY / BINANCE_SECRET_KEY")

    ex = BinanceExchange(
        api_key=api_key,
        secret_key=secret_key,
        testnet=USE_TESTNET,
    )

    notifier: TelegramNotifier | None = None
    if settings.TG_BOT_TOKEN and settings.TG_CHAT_ID:
        notifier = TelegramNotifier(settings.TG_BOT_TOKEN, settings.TG_CHAT_ID)

    logger.info("Funding watcher started (TZ=Asia/Taipei)")
    logger.info(
        f"Config | THRESHOLD_PCT={THRESHOLD_PCT}% | CANCEL_THRESHOLD_PCT={CANCEL_THRESHOLD_PCT}% | "
        f"POSITION_RATIO={POSITION_RATIO*100:.2f}% | LEVERAGE={LEVERAGE} | testnet={USE_TESTNET}"
    )

    while True:
        t59 = next_hh_59(now_tpe())
        sleep_until(t59)

        try:
            premium = ex.fetch_premium_index_all()
            top3 = ex.get_top3_symbols_nearest_funding(
                premium_rows=premium,
                threshold_pct=THRESHOLD_PCT,
            )

            logger.info(f"[{fmt_dt(now_tpe())}] @59:00 top3 selected")
            if not top3:
                logger.info(
                    f"No matched symbols (nearest funding batch + funding < {THRESHOLD_PCT}%)"
                )
            else:
                for i, r in enumerate(top3, start=1):
                    logger.info(
                        f"{i}. {r['symbol']} | funding={r['fundingRatePct']:.6f}% | "
                        f"nextFunding={fmt_ms(r['nextFundingTime'])}"
                    )
        except Exception as e:
            logger.exception(f"Select top3 failed: {e}")
            top3 = []

        if not top3:
            continue

        target = top3[0]
        t5958 = t59.replace(second=58)
        t00 = t59 + timedelta(minutes=1)

        try:
            asyncio.run(
                _ws_cycle(
                    symbol=target["symbol"],
                    funding_rate_pct_59=target["fundingRatePct"],
                    t5958=t5958,
                    t00=t00,
                    api_key=api_key,
                    secret_key=secret_key,
                    ex=ex,
                    notifier=notifier,
                )
            )
        except Exception as e:
            logger.exception(f"ws_cycle failed: {e}")


def main() -> None:
    run()


if __name__ == "__main__":
    setup_logging()
    main()
