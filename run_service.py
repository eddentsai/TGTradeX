"""
TGTradeX 自動交易服務進入點

啟動方式（自動倉位計算）：
    python run_service.py --exchange binance --symbol BTCUSDT --leverage 4
    python run_service.py --exchange binance --symbol ETHUSDT --leverage 4

啟動方式（固定數量覆蓋）：
    python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --qty 0.001

模擬模式：
    python run_service.py --exchange binance --symbol BTCUSDT --leverage 4 --dry-run

同時跑多個幣種（錯開 API 請求）：
    python run_service.py --exchange binance --symbol BTCUSDT --start-delay 0  &
    python run_service.py --exchange binance --symbol ETHUSDT --start-delay 30 &
    python run_service.py --exchange binance --symbol SOLUSDT --start-delay 60 &
    python run_service.py --exchange binance --symbol BNBUSDT --start-delay 90 &

必要環境變數（或 .env）：
    Bitunix: BITUNIX_API_KEY / BITUNIX_SECRET_KEY
    Binance: BINANCE_API_KEY / BINANCE_SECRET_KEY
    通知（可選）: TG_BOT_TOKEN / TG_CHAT_ID
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
from services.runner import ServiceRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


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
    raise ValueError(f"不支援的交易所: {name}（目前支援: bitunix, binance）")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TGTradeX 自動交易服務（市場狀態識別 + 策略自動切換 + 安全倉位計算）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
倉位計算範例（帳戶 1000U，4x 槓桿，止損 3%）：
  risk_amount    = 1000 × 1% = 10 U
  position_value = 10 ÷ 3%  = 333 U
  qty            = 333 ÷ 50000 = 0.00667 BTC
  required_margin = 333 ÷ 4  = 83 U
  清算價（多單）= entry × (1 - 1/4 + 0.005) = entry × 0.755
        """,
    )
    parser.add_argument(
        "--exchange", required=True,
        choices=["bitunix", "binance"],
        help="交易所名稱",
    )
    parser.add_argument(
        "--symbol", required=True,
        help="交易對，例如 BTCUSDT",
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
        "--qty-precision", type=int, default=None,
        help="數量小數位數（不填則自動從交易所查詢）",
    )
    parser.add_argument(
        "--qty", default=None,
        help="固定開倉數量（設定後覆蓋自動計算，適合測試）",
    )
    parser.add_argument(
        "--interval", default="1h",
        choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
        help="K 線週期（預設 1h）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="模擬模式：只記錄信號，不實際下單",
    )
    parser.add_argument(
        "--start-delay", type=int, default=0,
        help="啟動前等待秒數（同時跑多個 instance 時用來錯開 API 請求，例如 0 / 30 / 60）",
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

    # 驗證參數
    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間（超過 5% 風險過高）")

    settings.validate(exchange=args.exchange)

    log = logging.getLogger(__name__)

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

    # ── 建立交易所 ────────────────────────────────────────────────────────────
    exchange = _build_exchange(args.exchange)

    # 自動查詢數量精度（未手動指定時）
    qty_precision = args.qty_precision
    if qty_precision is None:
        qty_precision = exchange.get_qty_precision(args.symbol)
        log.info(f"自動偵測 {args.symbol} 數量精度: {qty_precision} 位小數")

    # ── 建立倉位計算器 ────────────────────────────────────────────────────────
    sizer = PositionSizer(
        leverage=args.leverage,
        risk_pct=args.risk_pct / 100.0,
        qty_precision=qty_precision,
    )

    if args.qty:
        log.warning(
            f"使用固定數量 qty={args.qty}，已停用自動倉位計算。"
            f"清算價參考（{args.leverage}x 多單）: "
            f"entry × {(1 - 1/args.leverage + 0.005):.3f}"
        )

    if args.start_delay > 0:
        log.info(f"等待 {args.start_delay}s 後啟動（錯開 API 請求）...")
        import time as _time
        _time.sleep(args.start_delay)

    # ── 啟動服務 ──────────────────────────────────────────────────────────────
    runner = ServiceRunner(
        exchange=exchange,
        symbol=args.symbol,
        interval=args.interval,
        sizer=None if args.qty else sizer,
        fixed_qty=args.qty,
        dry_run=args.dry_run,
        notifier=notifier,
        risk_guard=risk_guard,
    )

    def _handle_signal(sig, _):
        log.info(f"收到信號 {sig}，正在停止服務...")
        runner.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if notifier is not None:
        notifier.notify_start(
            exchange=args.exchange,
            mode=f"Single/{args.symbol}",
            interval=args.interval,
        )

    runner.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
