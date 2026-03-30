"""
TGTradeX 自動幣種掃描交易服務

自動從交易所掃描高流動性合約，依成交量排名動態分配 runner。

啟動範例：
    # Binance，最多 5 個倉位，最低 5 億 USDT 成交量
    python run_auto.py --exchange binance --max-positions 5 --min-volume 500000000

    # Bitunix，最多 3 個倉位，最低 1 億 USDT 成交量，每 2 小時重新掃描
    python run_auto.py --exchange bitunix --max-positions 3 --min-volume 100000000 --scan-interval 7200

    # 模擬模式
    python run_auto.py --exchange binance --max-positions 3 --dry-run

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
from services.symbol_scanner import SymbolScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 各交易所預設最低 24h 成交量門檻（USDT）
_DEFAULT_MIN_VOLUME: dict[str, float] = {
    "binance": 500_000_000,   # 5 億
    "bitunix": 100_000_000,   # 1 億
}


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TGTradeX 自動幣種掃描交易服務",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--exchange", required=True,
        choices=["bitunix", "binance"],
        help="交易所名稱",
    )
    parser.add_argument(
        "--max-positions", type=int, default=5,
        help="最多同時持倉幣種數量（預設 5）",
    )
    parser.add_argument(
        "--min-volume", type=float, default=None,
        help="最低 24h USDT 成交量門檻（預設 Binance=5億, Bitunix=1億）",
    )
    parser.add_argument(
        "--scan-interval", type=int, default=14400,
        help="幣種重新掃描間隔秒數（預設 14400 = 4 小時）",
    )
    parser.add_argument(
        "--interval", default="1h",
        choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
        help="K 線週期（預設 1h）",
    )
    parser.add_argument(
        "--leverage", type=int, default=4,
        help="槓桿倍數（預設 4）",
    )
    parser.add_argument(
        "--risk-pct", type=float, default=1.0,
        help="每次最大風險比例 %%（預設 1.0 = 1%%）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="模擬模式：只記錄信號，不實際下單",
    )
    args = parser.parse_args()

    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間")

    settings.validate(exchange=args.exchange)

    min_volume = args.min_volume or _DEFAULT_MIN_VOLUME[args.exchange]

    log = logging.getLogger(__name__)
    log.info(
        f"自動掃描模式啟動  exchange={args.exchange} "
        f"max_positions={args.max_positions} "
        f"min_volume={min_volume:,.0f} USDT "
        f"scan_interval={args.scan_interval}s "
        f"interval={args.interval}"
    )

    exchange = _build_exchange(args.exchange)

    scanner = SymbolScanner(
        exchange=exchange,
        min_quote_vol=min_volume,
        top_n=args.max_positions * 3,  # 候選池為最大持倉的 3 倍，留有餘裕
    )

    # qty_precision 由 RunnerManager 對每個幣種單獨查詢
    # 這裡建立一個「範本」sizer，manager 內部會複製並設定正確的 precision
    sizer = PositionSizer(
        leverage=args.leverage,
        risk_pct=args.risk_pct / 100.0,
        qty_precision=3,  # 佔位符，manager 會覆寫
    )

    manager = RunnerManager(
        exchange=exchange,
        scanner=scanner,
        sizer=sizer,
        interval=args.interval,
        max_positions=args.max_positions,
        scan_interval=args.scan_interval,
        dry_run=args.dry_run,
    )

    # 處理 Ctrl-C / SIGTERM
    def _handle_signal(sig, frame):
        log.info(f"收到信號 {sig}，正在停止服務...")
        manager.stop()

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    manager.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
