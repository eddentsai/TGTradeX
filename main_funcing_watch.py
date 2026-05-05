from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv

from exchanges.binance.adapter import BinanceExchange
from services.log_handler import setup_logging


# 載入 .env
load_dotenv()


# ===== 可調參數 =====
THRESHOLD_PCT = -0.5          # -0.5%
USE_TESTNET = False
LEVERAGE = 5                  # 槓桿 5x
POSITION_RATIO = 0.02         # 2%
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
        now = now_tpe()
        sec = (target - now).total_seconds()
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


def calc_short_tp_sl(price_5958: float, threshold_pct: float) -> tuple[float, float]:
    rate = abs(threshold_pct) / 100.0
    tp = price_5958 * (1 - rate)      # 空單止盈（往下）
    sl = price_5958 * (1 + rate / 2)  # 空單止損（往上，半幅）
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


def run() -> None:
    logger = logging.getLogger(__name__)

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    secret_key = os.getenv("BINANCE_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise RuntimeError("Missing env: BINANCE_API_KEY / BINANCE_SECRET_KEY")

    ex = BinanceExchange(
        api_key=api_key,
        secret_key=secret_key,
        testnet=USE_TESTNET,
    )

    logger.info("Funding watcher started (TZ=Asia/Taipei)")
    logger.info(
        f"Config | THRESHOLD_PCT={THRESHOLD_PCT}% | POSITION_RATIO={POSITION_RATIO*100:.2f}% | "
        f"LEVERAGE={LEVERAGE} | testnet={USE_TESTNET}"
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

        t5958 = t59.replace(second=58)
        sleep_until(t5958)

        top3_price_map: dict[str, float] = {}
        if not top3:
            logger.info(f"[{fmt_dt(now_tpe())}] @59:58 skip (no top3)")
            continue

        try:
            all_prices = ex.fetch_all_latest_prices_v2()
            logger.info(f"[{fmt_dt(now_tpe())}] @59:58 latest prices")
            for i, r in enumerate(top3, start=1):
                sym = r["symbol"]
                px = all_prices.get(sym)
                if px is None:
                    logger.info(f"{i}. {sym} | price=N/A")
                else:
                    top3_price_map[sym] = px
                    logger.info(f"{i}. {sym} | price={px}")
        except Exception as e:
            logger.exception(f"Fetch latest prices failed: {e}")
            continue

        target = top3[0]
        symbol = target["symbol"]
        price_5958 = top3_price_map.get(symbol)
        if price_5958 is None:
            logger.warning(f"[{fmt_dt(now_tpe())}] @00:00 skip, top1 price missing | symbol={symbol}")
            continue

        t00 = t59 + timedelta(minutes=1)
        sleep_until(t00)

        try:
            acct = ex.get_account()
            available = float(acct.get("available") or 0)
            qty_precision = ex.get_qty_precision(symbol)
            qty = calc_order_qty(
                available_balance=available,
                ref_price=price_5958,   # 方案A：固定用59:58價格算倉位
                qty_precision=qty_precision,
                leverage=LEVERAGE,
                ratio=POSITION_RATIO,
            )

            if qty <= 0:
                logger.warning(
                    f"[{fmt_dt(now_tpe())}] @00:00 skip, qty<=0 | symbol={symbol} "
                    f"| available={available} | price_5958={price_5958}"
                )
                continue

            # 00:00 先送單（不做任何額外REST）
            send_ts = now_ms()
            logger.info(
                f"[ORDER_SEND] ts={send_ts} ({fmt_ms(send_ts)}) | symbol={symbol} | side=SELL | "
                f"type=MARKET | qty={qty} | ref_price_5958={price_5958}"
            )

            order_resp = ex.place_order({
                "symbol": symbol,
                "side": "SELL",
                "orderType": "MARKET",
                "qty": str(qty),
                "tradeSide": "OPEN",
            })

            ack_ts = now_ms()
            ack_delay = ack_ts - send_ts
            order_id = order_resp.get("orderId", "")
            logger.info(
                f"[ORDER_ACK] ts={ack_ts} ({fmt_ms(ack_ts)}) | ack_delay_ms={ack_delay} | "
                f"symbol={symbol} | orderId={order_id} | status={order_resp.get('status')}"
            )

            # 下單後再取最新價，只做觀測，不影響交易流程
            try:
                latest_map = ex.fetch_all_latest_prices_v2()
                latest_px = latest_map.get(symbol)
                if latest_px is not None and price_5958 > 0:
                    drift_bps = (latest_px - price_5958) / price_5958 * 10000
                    logger.info(
                        f"[POST_ORDER_PRICE_CHECK] symbol={symbol} | "
                        f"price_5958={price_5958} | latest_after_order={latest_px} | "
                        f"drift_bps={drift_bps:.2f}"
                    )
                else:
                    logger.info(
                        f"[POST_ORDER_PRICE_CHECK] symbol={symbol} | "
                        f"price_5958={price_5958} | latest_after_order=N/A"
                    )
            except Exception as e:
                logger.warning(f"[POST_ORDER_PRICE_CHECK] failed: {e}")

            tp_price, sl_price = calc_short_tp_sl(price_5958, THRESHOLD_PCT)
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

            positions = ex.get_pending_positions(symbol=symbol)
            logger.info(f"[POSITION_SNAPSHOT] symbol={symbol} | rows={len(positions)} | data={positions}")

        except Exception as e:
            logger.exception(f"[{fmt_dt(now_tpe())}] @00:00 order flow failed: {e}")


def main() -> None:
    run()


if __name__ == "__main__":
    setup_logging()
    main()
