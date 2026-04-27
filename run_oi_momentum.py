"""
TGTradeX OI 動能做多服務

信號識別：
  1. 按 24h 振幅排序，找出波動性最高的幣種
  2. 篩選過去 8h OI 上升 > 5% 且價格也上升 > 2% 的幣種（資金跟著市場方向進入）
  3. 技術確認：收盤 > EMA20、RSI < 75、近期成交量突破（近 3 根均量 > 前 10 根 × 1.5）

出場邏輯（結構性，不設固定 TP）：
  - 硬止損：進場價 -11%
  - OI 從峰值下跌 > 5%（資金撤退）→ 出場
  - 多空比較進場上升 > 10%（空方增加）→ 出場
  - 移動止損：進場 +15% 後啟動，距現價 -8%

只做多，不做空。

啟動範例：
    python run_oi_momentum.py --exchange bitunix
    python run_oi_momentum.py --exchange binance --dry-run
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

import config.settings as settings
from exchanges.binance.adapter import BinanceExchange
from services.external_data.binance_futures import BinanceFuturesData
from services.notifier import TelegramNotifier
from services.oi_momentum import OiMomentumFilter
from services.position_sizer import PositionSizer
from services.risk_guard import RiskGuard
from services.runner_manager import RunnerManager
from services.strategies.long_oi_momentum import LongOiMomentumStrategy
from services.symbol_scanner import SymbolScanner
from services.trade_journal import TradeJournal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class _OiMomentumScanner:
    def __init__(
        self,
        base_scanner: SymbolScanner,
        oi_filter:    OiMomentumFilter,
    ) -> None:
        self._scanner   = base_scanner
        self._oi_filter = oi_filter

    def scan(self, held_symbols: set[str] | None = None) -> list[str]:
        candidates = self._scanner.scan(held_symbols)
        held       = held_symbols or set()
        return self._oi_filter.filter(candidates, held)


def _build_trade_exchange(name: str):
    if name == "bitunix":
        from exchanges.bitunix.adapter import BitunixExchange
        return BitunixExchange(
            api_key=settings.BITUNIX_API_KEY,
            secret_key=settings.BITUNIX_SECRET_KEY,
        )
    if name == "binance":
        return BinanceExchange(
            api_key=settings.BINANCE_API_KEY,
            secret_key=settings.BINANCE_SECRET_KEY,
        )
    raise ValueError(f"不支援的交易所: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TGTradeX OI 動能做多服務",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--exchange", required=True, choices=["bitunix", "binance"])
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--min-volume", type=float, default=50_000_000)
    parser.add_argument("--top-volatile", type=int, default=100)
    parser.add_argument("--scan-interval", type=int, default=900,
                        help="幣種重新掃描間隔秒數（預設 900 = 15 分鐘）")
    parser.add_argument("--interval", default="15m",
                        choices=["5m", "15m", "30m", "1h", "2h", "4h"])
    parser.add_argument("--leverage", type=int, default=4)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--sl-pct", type=float, default=50.0,
                        help="硬止損 ROI%% 門檻（預設 50.0 = ROI -50%%；4x 槓桿對應價格 -12.5%%）")
    parser.add_argument("--oi-change-min", type=float, default=5.0,
                        help="OI 8h 最低上升比例 %%（預設 5.0）")
    parser.add_argument("--price-change-min", type=float, default=2.0,
                        help="價格 8h 最低上升比例 %%（預設 2.0）")
    parser.add_argument("--recent-price-min", type=float, default=-2.0,
                        help="近 2h 最低價格變化 %%（預設 -2.0；負值=允許小幅回落）")
    parser.add_argument("--peak-retrace-max", type=float, default=5.0,
                        help="距 8h 最高收盤最大回落 %%（預設 5.0）")
    parser.add_argument("--oi-exit-pct", type=float, default=5.0)
    parser.add_argument("--ls-shift-pct", type=float, default=10.0)
    parser.add_argument("--vol-surge-ratio", type=float, default=1.5,
                        help="成交量突破倍數（近 3 根均量 > 前 10 根 × 此值，預設 1.5）")
    parser.add_argument("--rsi-max", type=float, default=75.0,
                        help="RSI 超買門檻（預設 75.0；動能策略建議調高至 80）")
    parser.add_argument("--trail-activate", type=float, default=60.0,
                        help="移動止損啟動 ROI%% 門檻（預設 60.0 = 保證金收益 +60%%）")
    parser.add_argument("--trail-distance", type=float, default=32.0,
                        help="移動止損 ROI%% 距離（預設 32.0；換算價格距離 = 此值 ÷ 槓桿）")
    parser.add_argument("--tp-pct", type=float, default=200.0,
                        help="固定止盈 ROI%% 門檻（預設 200.0 = 保證金收益 +200%%）")
    parser.add_argument("--lock-gain-pct", type=float, default=120.0,
                        help="鎖定觸發 ROI%% 門檻（預設 120.0 = 保證金收益 +120%%）")
    parser.add_argument("--lock-sl-pct", type=float, default=10.0,
                        help="鎖定止損位置：entry × (1 + 此值) %%（價格%%，預設 10.0）")
    parser.add_argument("--min-sl-buffer", type=float, default=12.0,
                        help="SL 距清算價最低緩衝 %%（預設 12.0；小幣 mm_rate 較高故調低）")
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--max-daily-loss", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--redis-url", default=settings.REDIS_URL)
    args = parser.parse_args()

    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間")

    settings.validate(exchange=args.exchange)

    log = logging.getLogger(__name__)
    log.info(
        "OI 動能做多服務啟動\n"
        f"  exchange        = {args.exchange}\n"
        f"  max_positions   = {args.max_positions}\n"
        f"  min_volume      = {args.min_volume:,.0f} USDT\n"
        f"  top_volatile    = {args.top_volatile}\n"
        f"  scan_interval   = {args.scan_interval}s\n"
        f"  interval        = {args.interval}\n"
        f"  leverage        = {args.leverage}x\n"
        f"  risk_pct        = {args.risk_pct}%\n"
        f"  sl_pct          = -{args.sl_pct}%\n"
        f"  oi_change_min   = +{args.oi_change_min}%（OI 8h）\n"
        f"  price_change_min= +{args.price_change_min}%（價格 8h）\n"
        f"  vol_surge_ratio = {args.vol_surge_ratio}x\n"
        f"  dry_run         = {args.dry_run}"
    )

    notifier: TelegramNotifier | None = None
    if settings.TG_BOT_TOKEN and settings.TG_CHAT_ID:
        notifier = TelegramNotifier(settings.TG_BOT_TOKEN, settings.TG_CHAT_ID)

    risk_guard = RiskGuard(
        max_consecutive_losses=args.max_consecutive_losses,
        max_daily_loss_pct=args.max_daily_loss,
        notifier=notifier,
    )

    trade_exchange = _build_trade_exchange(args.exchange)
    scan_exchange  = BinanceExchange(api_key="", secret_key="")

    base_scanner = SymbolScanner(
        exchange=scan_exchange,
        min_quote_vol=args.min_volume,
        top_n=args.top_volatile,
        exclude_mainstream=True,
        sort_by="volatility",
        trade_exchange=trade_exchange,
    )
    oi_filter = OiMomentumFilter(
        binance_data=BinanceFuturesData(),
        exchange=scan_exchange,
        oi_change_min=args.oi_change_min / 100,
        price_change_min=args.price_change_min / 100,
        min_recent_price_change=args.recent_price_min / 100,
        max_peak_retrace=args.peak_retrace_max / 100,
    )
    scanner = _OiMomentumScanner(base_scanner, oi_filter)

    strategy = LongOiMomentumStrategy(
        sl_roi=args.sl_pct / 100,
        oi_exit_pct=args.oi_exit_pct / 100,
        ls_shift_pct=args.ls_shift_pct / 100,
        leverage=args.leverage,
        trail_activate_roi=args.trail_activate / 100,
        trail_distance_roi=args.trail_distance / 100,
        vol_surge_ratio=args.vol_surge_ratio,
        rsi_max=args.rsi_max,
        tp_roi=args.tp_pct / 100,
        lock_gain_roi=args.lock_gain_pct / 100,
        lock_sl_pct=args.lock_sl_pct / 100,
        period=args.interval,
    )

    sizer = PositionSizer(
        leverage=args.leverage,
        risk_pct=args.risk_pct / 100.0,
        qty_precision=3,
        min_sl_buffer_pct=args.min_sl_buffer / 100.0,
    )

    journal = TradeJournal(path=f"logs/trade_journal_momentum_{args.exchange}.csv")

    manager = RunnerManager(
        exchange=trade_exchange,
        scanner=scanner,
        sizer=sizer,
        interval=args.interval,
        max_positions=args.max_positions,
        scan_interval=args.scan_interval,
        dry_run=args.dry_run,
        enable_ensemble=True,
        ensemble_strategies=[strategy],
        ensemble_min_confirm=1,
        redis_url=args.redis_url or None,
        notifier=notifier,
        risk_guard=risk_guard,
        trade_journal=journal,
        trail_activate_roi=args.trail_activate / 100,
        trail_distance_roi=args.trail_distance / 100,
        sl_roi=args.sl_pct / 100,
    )

    def _handle_signal(sig, _):
        log.info(f"收到信號 {sig}，正在停止服務...")
        manager.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if notifier is not None:
        notifier.notify_start(
            exchange=args.exchange,
            mode="OI動能做多",
            interval=args.interval,
        )

    manager.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
