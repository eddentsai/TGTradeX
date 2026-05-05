"""
分析資金費率結算後的 1 分鐘 K 線。

用法：
    python analyze_funding_klines.py HIVEUSDT 2026-05-05T20:00:00+08:00 --entry 0.08662 --rate -1.1266
    python analyze_funding_klines.py HIVEUSDT 2026-05-05T20:00:00+08:00   # 只看 K 線，不標 TP/SL

引數：
    symbol      幣對（如 HIVEUSDT）
    settlement  結算時間，ISO 格式含時區（如 2026-05-05T20:00:00+08:00）
    --entry     實際成交價（選填）
    --rate      結算時費率 % （選填，用來算 TP/SL）
    --minutes   觀察幾分鐘（預設 5）
    --factor    TP_RATE_FACTOR（預設 0.8）
    --sl-pct    SL 幅度 %（預設 0.3）
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from config import settings
from exchanges.binance.adapter import BinanceExchange

load_dotenv()

TZ = timezone(timedelta(hours=8))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="分析資費結算後 1m K 線")
    p.add_argument("symbol", help="幣對，如 HIVEUSDT")
    p.add_argument("settlement", help="結算時間 ISO 格式，如 2026-05-05T20:00:00+08:00")
    p.add_argument("--entry", type=float, default=None, help="實際成交價")
    p.add_argument("--rate", type=float, default=None, help="結算費率 %%，如 -1.1266")
    p.add_argument("--minutes", type=int, default=5, help="觀察分鐘數（預設 5）")
    p.add_argument("--factor", type=float, default=0.8, help="TP_RATE_FACTOR（預設 0.8）")
    p.add_argument("--sl-pct", type=float, default=0.3, help="SL 幅度 %%（預設 0.3）")
    return p.parse_args()


def ms_to_dt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=TZ).strftime("%H:%M:%S")


def main() -> None:
    args = parse_args()

    t_start = datetime.fromisoformat(args.settlement).astimezone(timezone.utc)
    t_end = t_start + timedelta(minutes=args.minutes)
    start_ms = int(t_start.timestamp() * 1000)
    end_ms = int(t_end.timestamp() * 1000)

    ex = BinanceExchange(
        api_key=settings.BINANCE_API_KEY.strip(),
        secret_key=settings.BINANCE_SECRET_KEY.strip(),
        testnet=False,
    )

    klines = ex.get_klines(
        symbol=args.symbol,
        interval="1m",
        start_time=start_ms,
        end_time=end_ms,
        limit=args.minutes + 1,
    )

    entry = args.entry
    rate = args.rate

    # 計算 TP/SL
    tp_price: float | None = None
    sl_price: float | None = None
    if entry and rate:
        tp_price = entry * (1 - abs(rate) / 100 * args.factor)
        sl_price = entry * (1 + args.sl_pct / 100)

    # ── 表頭 ──────────────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"  {args.symbol}  結算後 {args.minutes} 分鐘 K 線")
    if entry:
        print(f"  進場: {entry:.5f}", end="")
        if rate:
            print(f"  費率: {rate:.4f}%", end="")
            if tp_price and sl_price:
                print(f"  TP: {tp_price:.5f} ({-abs(rate)*args.factor:.2f}%)"
                      f"  SL: {sl_price:.5f} (+{args.sl_pct:.2f}%)", end="")
        print()
    print(f"{'─'*70}")
    print(f"  {'時間':8}  {'開':>10}  {'高':>10}  {'低':>10}  {'收':>10}  "
          f"{'高%':>7}  {'低%':>7}  {'收%':>7}  {'結果':>6}")
    print(f"{'─'*70}")

    for k in klines:
        t = ms_to_dt(k["time"])
        o, h, l, c = k["open"], k["high"], k["low"], k["close"]
        ref = entry if entry else o

        h_pct = (h - ref) / ref * 100
        l_pct = (l - ref) / ref * 100
        c_pct = (c - ref) / ref * 100

        # 判斷本根 K 線內是否觸發 TP/SL
        result = ""
        if tp_price and sl_price:
            hit_sl = h >= sl_price
            hit_tp = l <= tp_price
            if hit_sl and hit_tp:
                result = "SL+TP?"  # 同根觸發，先後不確定
            elif hit_sl:
                result = "🔴SL"
            elif hit_tp:
                result = "✅TP"

        print(f"  {t:8}  {o:10.5f}  {h:10.5f}  {l:10.5f}  {c:10.5f}  "
              f"{h_pct:+7.3f}%  {l_pct:+7.3f}%  {c_pct:+7.3f}%  {result:>6}")

    print(f"{'─'*70}\n")


if __name__ == "__main__":
    main()
