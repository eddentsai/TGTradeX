"""
測試對現有倉位補掛 SL/TP 條件單

用法：
    python test_sl_tp.py storage/positions/binance_SOLUSDT.json
    python test_sl_tp.py storage/positions/binance_DOGEUSDT.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config import settings
from exchanges.binance.adapter import BinanceExchange


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python test_sl_tp.py <position_json_path>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"找不到檔案: {path}")
        sys.exit(1)

    pos = json.loads(path.read_text(encoding="utf-8"))
    symbol    = pos["symbol"]
    side      = pos["side"]
    qty       = pos["qty"]
    sl_price  = float(pos["stop_loss"])
    tp_price  = float(pos["take_profit"])

    print(f"=== 倉位資訊 ===")
    print(f"  交易所:   {pos['exchange']}")
    print(f"  交易對:   {symbol}")
    print(f"  方向:     {side}")
    print(f"  數量:     {qty}")
    print(f"  入場價:   {pos['entry_price']}")
    print(f"  止損價:   {sl_price}")
    print(f"  止盈價:   {tp_price}")
    print(f"  策略:     {pos.get('strategy_name', 'N/A')}")
    print(f"  週期:     {pos.get('interval', 'N/A')}")
    print()

    exchange = BinanceExchange(
        api_key=settings.BINANCE_API_KEY,
        secret_key=settings.BINANCE_SECRET_KEY,
    )

    # 1. 查詢價格精度
    price_prec = exchange.get_price_precision(symbol)
    print(f"價格精度: {price_prec} 位小數")
    print(f"SL 四捨五入後: {round(sl_price, price_prec)}")
    print(f"TP 四捨五入後: {round(tp_price, price_prec)}")
    print()

    # 2. 先取消所有現有掛單
    print("取消現有掛單...")
    try:
        exchange.cancel_all_orders(symbol)
        print("  ✓ 取消成功（或無掛單）")
    except Exception as e:
        print(f"  ✗ 取消失敗: {e}")
    print()

    # 3. 補掛 SL/TP
    print("補掛 SL/TP 條件單...")
    try:
        exchange.place_sl_tp_orders(
            symbol=symbol,
            side=side,
            qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        print("  ✓ SL/TP 掛單成功")
    except Exception as e:
        print(f"  ✗ 掛單失敗: {e}")
        sys.exit(1)

    # 4. 確認掛單是否存在
    print()
    print("確認目前掛單列表...")
    try:
        orders = exchange.get_pending_orders(symbol)
        if not orders:
            print("  （無掛單）")
        for o in orders:
            print(f"  orderId={o.get('orderId')}  side={o.get('side')}  "
                  f"type={o.get('_raw') and getattr(o['_raw'], 'type', 'N/A')}  "
                  f"stopPrice={o.get('_raw') and getattr(o['_raw'], 'stop_price', 'N/A')}")
    except Exception as e:
        print(f"  查詢失敗: {e}")


if __name__ == "__main__":
    main()
