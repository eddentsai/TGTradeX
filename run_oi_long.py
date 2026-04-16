"""
TGTradeX OI 背離做多服務

信號識別：
  1. 按 24h 振幅排序，找出波動性最高的幣種
  2. 篩選過去 48h OI 上升 > 20% 但價格變化 < ±3% 的幣種（OI 累積但市場尚未反應）
  3. 技術確認：收盤 > EMA20 且 RSI < 70

出場邏輯（結構性，不設固定 TP）：
  - 硬止損：進場價 -20%
  - OI 從峰值下跌 > 5%（資金撤退）→ 出場
  - 多空比較進場上升 > 10%（空方增加）→ 出場

只做多，不做空。

啟動範例：
    # 標準啟動（最多 3 個倉位，4x 槓桿）
    python run_oi_long.py --exchange bitunix

    # 調整風險比例和槓桿
    python run_oi_long.py --exchange bitunix --leverage 2 --risk-pct 0.5

    # 模擬模式
    python run_oi_long.py --exchange bitunix --dry-run

必要環境變數（或 .env）：
    Bitunix: BITUNIX_API_KEY / BITUNIX_SECRET_KEY
    Binance（掃描用，公開端點不需要 Key）: 無需設定
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
from services.oi_divergence import OiDivergenceFilter
from services.position_sizer import PositionSizer
from services.risk_guard import RiskGuard
from services.runner_manager import RunnerManager
from services.strategies.long_only_oi import LongOnlyOiStrategy
from services.symbol_scanner import SymbolScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class _OiFilteredScanner:
    """
    SymbolScanner + OiDivergenceFilter 的組合包裝器。
    RunnerManager 呼叫 scan() 時，自動先做波動性掃描，再套用 OI 背離篩選。
    """

    def __init__(
        self,
        base_scanner: SymbolScanner,
        oi_filter:    OiDivergenceFilter,
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
        description="TGTradeX OI 背離做多服務",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--exchange",
        required=True,
        choices=["bitunix", "binance"],
        help="下單交易所",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=3,
        help="最多同時持倉幣種數量（預設 3）",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=50_000_000,
        help="最低 24h USDT 成交量門檻（預設 5000 萬）",
    )
    parser.add_argument(
        "--top-volatile",
        type=int,
        default=30,
        help="先取振幅最高的前 N 個幣種，再套用 OI 篩選（預設 30）",
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=3600,
        help="幣種重新掃描間隔秒數（預設 3600 = 1 小時）",
    )
    parser.add_argument(
        "--interval",
        default="1h",
        choices=["15m", "30m", "1h", "2h", "4h"],
        help="K 線週期（預設 1h）",
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=2,
        help="槓桿倍數（預設 2；止損 -20% 搭配高槓桿風險極大，請謹慎）",
    )
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=1.0,
        help="每次最大風險比例 %（預設 1.0 = 1%%）",
    )
    parser.add_argument(
        "--sl-pct",
        type=float,
        default=20.0,
        help="硬止損比例 %（預設 20.0 = -20%%）",
    )
    parser.add_argument(
        "--oi-exit-pct",
        type=float,
        default=5.0,
        help="OI 從峰值下跌此比例時出場 %（預設 5.0）",
    )
    parser.add_argument(
        "--ls-shift-pct",
        type=float,
        default=10.0,
        help="多空比較進場上升此比例時出場 %（預設 10.0）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模擬模式：只記錄信號，不實際下單",
    )
    parser.add_argument(
        "--redis-url",
        default=settings.REDIS_URL,
        help=f"Redis 連線字串（預設: {settings.REDIS_URL}）；傳入空字串停用",
    )
    parser.add_argument(
        "--max-consecutive-losses",
        type=int,
        default=3,
        help="連續虧損達此次數後停止開倉（預設 3）",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=10.0,
        help="當日累計虧損百分比上限（預設 10.0）",
    )
    args = parser.parse_args()

    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間")

    settings.validate(exchange=args.exchange)

    log = logging.getLogger(__name__)
    log.info(
        "OI 背離做多服務啟動\n"
        f"  exchange        = {args.exchange}\n"
        f"  scan_exchange   = binance（公開 API）\n"
        f"  max_positions   = {args.max_positions}\n"
        f"  min_volume      = {args.min_volume:,.0f} USDT\n"
        f"  top_volatile    = {args.top_volatile}\n"
        f"  scan_interval   = {args.scan_interval}s\n"
        f"  interval        = {args.interval}\n"
        f"  leverage        = {args.leverage}x\n"
        f"  risk_pct        = {args.risk_pct}%\n"
        f"  sl_pct          = -{args.sl_pct}%\n"
        f"  oi_exit_pct     = -{args.oi_exit_pct}%（OI 從峰值）\n"
        f"  ls_shift_pct    = +{args.ls_shift_pct}%（多空比較進場）\n"
        f"  dry_run         = {args.dry_run}"
    )

    # ── 建立通知器 + 風控守衛 ─────────────────────────────────────────────────
    notifier: TelegramNotifier | None = None
    if settings.TG_BOT_TOKEN and settings.TG_CHAT_ID:
        notifier = TelegramNotifier(settings.TG_BOT_TOKEN, settings.TG_CHAT_ID)

    risk_guard = RiskGuard(
        max_consecutive_losses=args.max_consecutive_losses,
        max_daily_loss_pct=args.max_daily_loss,
        notifier=notifier,
    )

    # ── 建立交易所 ────────────────────────────────────────────────────────────
    trade_exchange = _build_trade_exchange(args.exchange)
    # Binance 公開端點不需要 API key（掃描 + OI 數據用）
    scan_exchange = BinanceExchange(api_key="", secret_key="")

    # ── 建立掃描器（波動性排序 + OI 背離篩選）────────────────────────────────
    base_scanner = SymbolScanner(
        exchange=scan_exchange,
        min_quote_vol=args.min_volume,
        top_n=args.top_volatile,
        exclude_mainstream=True,
        sort_by="volatility",          # 按振幅排序，取波動最高前 N 個
        trade_exchange=trade_exchange, # 取兩交易所上市的交集
    )
    oi_filter = OiDivergenceFilter(
        binance_data=BinanceFuturesData(),
        exchange=scan_exchange,        # 用 Binance 取 1h K 線
    )
    scanner = _OiFilteredScanner(base_scanner, oi_filter)

    # ── 建立策略 ──────────────────────────────────────────────────────────────
    strategy = LongOnlyOiStrategy(
        sl_pct=args.sl_pct / 100,
        oi_exit_pct=args.oi_exit_pct / 100,
        ls_shift_pct=args.ls_shift_pct / 100,
        period=args.interval,
    )

    sizer = PositionSizer(
        leverage=args.leverage,
        risk_pct=args.risk_pct / 100.0,
        qty_precision=3,
    )

    # ── 建立 RunnerManager（單策略模式）──────────────────────────────────────
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
    )

    # ── 處理 Ctrl-C / SIGTERM ─────────────────────────────────────────────────
    def _handle_signal(sig, _):
        log.info(f"收到信號 {sig}，正在停止服務...")
        manager.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if notifier is not None:
        notifier.notify_start(
            exchange=args.exchange,
            mode="OI背離做多",
            interval=args.interval,
        )

    manager.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
