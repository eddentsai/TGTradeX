"""
TGTradeX 綜合策略 Ensemble 交易服務

同時運行三個策略，需要 N/3 以上策略同時給出開倉信號才執行交易。
出場邏輯由各策略獨立判斷（先觸發者優先）。

策略組合：
  - FibonacciStrategy  → 上升趨勢回調做多
  - VwapPocStrategy    → 震盪市場均值回歸
  - DipVolumeStrategy  → 急跌爆量反彈

啟動範例：
    # Binance，最多 5 個倉位，需 2/3 策略確認
    python run_mix_strategies.py --exchange binance --max-positions 5

    # 嚴格模式：需 3/3 策略全部確認
    python run_mix_strategies.py --exchange binance --max-positions 3 --min-confirm 3

    # Bitunix，最多 3 個倉位，最低 1 億成交量，模擬模式
    python run_mix_strategies.py --exchange bitunix --max-positions 3 --min-volume 100000000 --dry-run

必要環境變數（或 .env）：
    Binance: BINANCE_API_KEY / BINANCE_SECRET_KEY
    Bitunix: BITUNIX_API_KEY / BITUNIX_SECRET_KEY
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import config.settings as settings
from services.position_sizer import PositionSizer
from services.runner_manager import RunnerManager
from services.strategies.dip_volume import DipVolumeStrategy
from services.strategies.fibonacci import FibonacciStrategy
from services.strategies.vwap_poc import VwapPocStrategy
from services.symbol_scanner import SymbolScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 各交易所預設最低 24h 成交量門檻（USDT）
# 掃山寨幣用 Binance 成交量當基準，2 億可篩出 50+ 個活躍合約，候選池夠大
_DEFAULT_MIN_VOLUME: dict[str, float] = {
    "binance": 200_000_000,  # 2 億（山寨幣適用）
    "bitunix": 100_000_000,  # 1 億
}

# Ensemble 策略清單（固定三個）
_ENSEMBLE_STRATEGIES = [
    FibonacciStrategy,
    VwapPocStrategy,
    DipVolumeStrategy,
]


def _build_exchange(name: str):
    if name == "bitunix":
        from exchanges.bitunix.adapter import BitunixExchange

        return BitunixExchange(
            api_key=settings.BITUNIX_API_KEY,
            secret_key=settings.BITUNIX_SECRET_KEY,
        )
    if name == "binance":
        from exchanges.binance.adapter import BinanceExchange

        return BinanceExchange(
            api_key=settings.BINANCE_API_KEY,
            secret_key=settings.BINANCE_SECRET_KEY,
        )
    raise ValueError(f"不支援的交易所: {name}")


def _build_scan_exchange(name: str | None, trade_exchange):
    """
    建立掃描用的交易所實例。
    - name=None：直接用交易用的 exchange（預設）
    - name="binance"：用 Binance 公開 ticker（不需要 API key）
    """
    if name is None or name == trade_exchange.name:
        return trade_exchange
    if name == "binance":
        from exchanges.binance.adapter import BinanceExchange
        # get_tickers 使用公開端點，不需要 API key
        return BinanceExchange(api_key="", secret_key="")
    raise ValueError(f"不支援的掃描交易所: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TGTradeX 綜合策略 Ensemble 交易服務",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--exchange",
        required=True,
        choices=["bitunix", "binance"],
        help="交易所名稱",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=5,
        help="最多同時持倉幣種數量（預設 5）",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=None,
        help="最低 24h USDT 成交量門檻（預設 Binance=5億, Bitunix=1億）",
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=14400,
        help="幣種重新掃描間隔秒數（預設 14400 = 4 小時）",
    )
    parser.add_argument(
        "--interval",
        default="1h",
        choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
        help="K 線週期（預設 1h）",
    )
    parser.add_argument(
        "--leverage",
        type=int,
        default=4,
        help="槓桿倍數（預設 4）",
    )
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=1.0,
        help="每次最大風險比例 %%(預設 1.0 = 1%%）",
    )
    parser.add_argument(
        "--min-confirm",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help=(
            "開倉所需最少策略確認數（預設 2）\n"
            "  1 = 任一策略觸發即開倉（等同單策略）\n"
            "  2 = 需 2/3 策略同時確認（建議）\n"
            "  3 = 需 3/3 策略全部確認（最嚴格）"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="模擬模式：只記錄信號，不實際下單",
    )
    parser.add_argument(
        "--include-mainstream",
        action="store_true",
        help="納入主流幣（BTC/ETH/BNB/SOL 等），預設排除",
    )
    parser.add_argument(
        "--scan-exchange",
        default="binance",
        choices=["binance"],
        help="用指定交易所的成交量來掃描幣種（預設 binance）；"
             "Binance 成交量較大且更接近市場真實流動性，建議保持預設",
    )
    args = parser.parse_args()

    # ── 參數驗證 ──────────────────────────────────────────────────────────────
    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間")

    settings.validate(exchange=args.exchange)

    min_volume = args.min_volume or _DEFAULT_MIN_VOLUME.get(
        args.scan_exchange or args.exchange,
        _DEFAULT_MIN_VOLUME[args.exchange],
    )

    # ── 啟動日誌 ──────────────────────────────────────────────────────────────
    log = logging.getLogger(__name__)
    strategy_names = [s().name for s in _ENSEMBLE_STRATEGIES]
    scan_label = args.scan_exchange or args.exchange

    log.info(
        "Ensemble 模式啟動\n"
        f"  exchange       = {args.exchange}\n"
        f"  scan_exchange  = {scan_label}\n"
        f"  strategies     = {' / '.join(strategy_names)}\n"
        f"  min_confirm    = {args.min_confirm}/{len(_ENSEMBLE_STRATEGIES)}\n"
        f"  max_positions  = {args.max_positions}\n"
        f"  min_volume     = {min_volume:,.0f} USDT\n"
        f"  scan_interval  = {args.scan_interval}s\n"
        f"  interval       = {args.interval}\n"
        f"  leverage       = {args.leverage}x\n"
        f"  risk_pct       = {args.risk_pct}%\n"
        f"  dry_run        = {args.dry_run}"
    )

    # ── 建立元件 ──────────────────────────────────────────────────────────────
    exchange = _build_exchange(args.exchange)
    scan_exchange = _build_scan_exchange(args.scan_exchange, exchange)

    scanner = SymbolScanner(
        exchange=scan_exchange,
        min_quote_vol=min_volume,
        top_n=args.max_positions * 3,  # 候選池為最大持倉的 3 倍
        exclude_mainstream=not args.include_mainstream,
    )

    sizer = PositionSizer(
        leverage=args.leverage,
        risk_pct=args.risk_pct / 100.0,
        qty_precision=3,  # 佔位符，RunnerManager 內部會覆寫
    )

    # ── 建立 RunnerManager（Ensemble 模式）────────────────────────────────────
    manager = RunnerManager(
        exchange=exchange,
        scanner=scanner,
        sizer=sizer,
        interval=args.interval,
        max_positions=args.max_positions,
        scan_interval=args.scan_interval,
        dry_run=args.dry_run,
        # Ensemble 相關參數
        enable_ensemble=True,
        ensemble_strategies=[cls() for cls in _ENSEMBLE_STRATEGIES],
        ensemble_min_confirm=args.min_confirm,
    )

    # ── 處理 Ctrl-C / SIGTERM ─────────────────────────────────────────────────
    def _handle_signal(sig, frame):
        log.info(f"收到信號 {sig}，正在停止服務...")
        manager.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    manager.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
