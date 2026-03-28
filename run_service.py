"""
TGTradeX 自動交易服務進入點

啟動方式（自動倉位計算）：
    python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4

啟動方式（固定數量覆蓋）：
    python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --qty 0.001

模擬模式：
    python run_service.py --exchange bitunix --symbol BTCUSDT --leverage 4 --dry-run

必要環境變數（或 .env）：
    BITUNIX_API_KEY / BITUNIX_SECRET_KEY
"""
from __future__ import annotations

import argparse
import logging

import config.settings as settings
from services.position_sizer import PositionSizer
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
    raise ValueError(f"不支援的交易所: {name}（目前支援: bitunix）")


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
        choices=["bitunix"],
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
        "--qty-precision", type=int, default=3,
        help="數量小數位數（預設 3，BTC 用 3，ETH 可用 2）",
    )
    parser.add_argument(
        "--qty", default=None,
        help="固定開倉數量（設定後覆蓋自動計算，適合測試）",
    )
    parser.add_argument(
        "--interval", default="15m",
        choices=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"],
        help="K 線週期（預設 15m）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="模擬模式：只記錄信號，不實際下單",
    )
    parser.add_argument(
        "--start-delay", type=int, default=0,
        help="啟動前等待秒數（同時跑多個 instance 時用來錯開 API 請求，例如 0 / 30 / 60）",
    )
    args = parser.parse_args()

    # 驗證參數
    if args.leverage < 1 or args.leverage > 125:
        parser.error("--leverage 必須在 1–125 之間")
    if not (0.1 <= args.risk_pct <= 5.0):
        parser.error("--risk-pct 建議在 0.1–5.0 之間（超過 5% 風險過高）")

    settings.validate()

    # 建立交易所
    exchange = _build_exchange(args.exchange)

    # 建立倉位計算器（fixed_qty 模式時仍建立，用於顯示清算資訊）
    sizer = PositionSizer(
        leverage=args.leverage,
        risk_pct=args.risk_pct / 100.0,   # 轉為小數
        qty_precision=args.qty_precision,
    )

    # 啟動服務
    runner = ServiceRunner(
        exchange=exchange,
        symbol=args.symbol,
        interval=args.interval,
        sizer=None if args.qty else sizer,  # 有固定 qty 時停用 sizer
        fixed_qty=args.qty,
        dry_run=args.dry_run,
    )

    if args.qty:
        logging.getLogger(__name__).warning(
            f"使用固定數量 qty={args.qty}，已停用自動倉位計算。"
            f"清算價參考（{args.leverage}x 多單）: "
            f"entry × {(1 - 1/args.leverage + 0.005):.3f}"
        )

    if args.start_delay > 0:
        logging.getLogger(__name__).info(
            f"等待 {args.start_delay}s 後啟動（錯開 API 請求）..."
        )
        import time as _time
        _time.sleep(args.start_delay)

    runner.run()


if __name__ == "__main__":
    main()
