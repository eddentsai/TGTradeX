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
from services.notifier import TelegramNotifier
from services.position_sizer import PositionSizer
from services.risk_guard import RiskGuard
from services.runner_manager import RunnerManager
from services.symbol_scanner import SymbolScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 各交易所預設最低 24h 成交量門檻（USDT）
_DEFAULT_MIN_VOLUME: dict[str, float] = {
    "binance": 200_000_000,   # 2 億（山寨幣適用，可篩出 50+ 個活躍合約）
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
        return BinanceExchange(api_key="", secret_key="")
    raise ValueError(f"不支援的掃描交易所: {name}")


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
    parser.add_argument(
        "--redis-url",
        default=settings.REDIS_URL,
        help=f"Redis 連線字串，用於黑名單持久化（預設: {settings.REDIS_URL}）；傳入空字串停用",
    )
    parser.add_argument(
        "--include-mainstream", action="store_true",
        help="納入主流幣（BTC/ETH/BNB/SOL 等），預設排除",
    )
    parser.add_argument(
        "--scan-exchange",
        default="binance",
        choices=["binance"],
        help="用指定交易所的成交量來掃描幣種（預設 binance）；"
             "Binance 成交量較大且更接近市場真實流動性，建議保持預設",
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
        help="當日累計虧損百分比上限（預設 10.0）；超過後停止開倉，次日 UTC 00:00 自動恢復",
    )
    args = parser.parse_args()

    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間")

    settings.validate(exchange=args.exchange)

    min_volume = args.min_volume or _DEFAULT_MIN_VOLUME.get(
        args.scan_exchange or args.exchange,
        _DEFAULT_MIN_VOLUME[args.exchange],
    )

    log = logging.getLogger(__name__)
    scan_label = args.scan_exchange or args.exchange
    log.info(
        f"自動掃描模式啟動  exchange={args.exchange} "
        f"scan_exchange={scan_label} "
        f"max_positions={args.max_positions} "
        f"min_volume={min_volume:,.0f} USDT "
        f"scan_interval={args.scan_interval}s "
        f"interval={args.interval}"
    )

    # ── 建立通知器 + 風控守衛 ─────────────────────────────────────────────────
    notifier: TelegramNotifier | None = None
    if settings.TG_BOT_TOKEN and settings.TG_CHAT_ID:
        notifier = TelegramNotifier(settings.TG_BOT_TOKEN, settings.TG_CHAT_ID)
        log.info(f"Telegram 通知已啟用 (chat_id={settings.TG_CHAT_ID})")
    else:
        log.info("Telegram 通知未設定（需同時設定 TG_BOT_TOKEN 和 TG_CHAT_ID）")

    risk_guard = RiskGuard(
        max_consecutive_losses=args.max_consecutive_losses,
        max_daily_loss_pct=args.max_daily_loss,
        notifier=notifier,
    )

    exchange = _build_exchange(args.exchange)
    scan_exchange = _build_scan_exchange(args.scan_exchange, exchange)

    scanner = SymbolScanner(
        exchange=scan_exchange,
        min_quote_vol=min_volume,
        top_n=args.max_positions * 3,  # 候選池為最大持倉的 3 倍，留有餘裕
        exclude_mainstream=not args.include_mainstream,
        trade_exchange=exchange,
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
        redis_url=args.redis_url or None,
        notifier=notifier,
        risk_guard=risk_guard,
    )

    # 處理 Ctrl-C / SIGTERM
    def _handle_signal(sig, _):
        log.info(f"收到信號 {sig}，正在停止服務...")
        manager.stop()

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if notifier is not None:
        notifier.notify_start(
            exchange=args.exchange,
            mode="Auto",
            interval=args.interval,
        )

    manager.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
