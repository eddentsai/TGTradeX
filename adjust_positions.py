"""
調整現有倉位快取的 SL / TP

根據入場均價重算 SL 與 TP，並寫回 storage/positions/ 的 JSON 檔案。
預設為 dry-run（只顯示變更，不實際寫入）。

用法：
    python adjust_positions.py                          # 預覽變更（dry-run）
    python adjust_positions.py --apply                  # 實際寫入
    python adjust_positions.py --sl-pct 12.5 --tp-pct 50 --apply
    python adjust_positions.py --exchange binance       # 只處理 binance
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

STORE_DIR = Path("storage/positions")


def recalculate(entry: float, side: str, sl_pct: float, tp_pct: float) -> tuple[float, float]:
    sl_ratio = sl_pct / 100
    tp_ratio = tp_pct / 100
    if side == "BUY":
        sl = round(entry * (1 - sl_ratio), 8)
        tp = round(entry * (1 + tp_ratio), 8)
    else:
        sl = round(entry * (1 + sl_ratio), 8)
        tp = round(entry * (1 - tp_ratio), 8)
    return sl, tp


def main() -> None:
    parser = argparse.ArgumentParser(description="調整現有倉位快取的 SL / TP")
    parser.add_argument("--sl-pct",   type=float, default=12.5, help="止損比例 %（預設 12.5）")
    parser.add_argument("--tp-pct",   type=float, default=50.0, help="止盈比例 %（預設 50.0）")
    parser.add_argument("--exchange", default=None, help="只處理指定交易所（預設全部）")
    parser.add_argument("--apply",    action="store_true", help="實際寫入檔案（預設 dry-run）")
    args = parser.parse_args()

    if not STORE_DIR.exists():
        print(f"找不到 {STORE_DIR}，目前無任何倉位快取。")
        return

    files = sorted(STORE_DIR.glob("*.json"))
    if args.exchange:
        files = [f for f in files if f.stem.startswith(args.exchange + "_")]

    if not files:
        print("沒有符合的倉位快取檔案。")
        return

    mode = "【實際寫入】" if args.apply else "【DRY-RUN，加 --apply 才會寫入】"
    print(f"\n{mode}  SL={args.sl_pct}%  TP={args.tp_pct}%\n")
    print(f"{'檔案':<35} {'side':<5} {'entry':>10} {'舊 SL':>10} {'新 SL':>10} {'舊 TP':>10} {'新 TP':>10}")
    print("-" * 95)

    changed = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  讀取失敗: {path.name}  ({e})")
            continue

        entry = float(data["entry_price"])
        side  = data.get("side", "BUY")
        old_sl = float(data["stop_loss"])
        old_tp = float(data["take_profit"])
        new_sl, new_tp = recalculate(entry, side, args.sl_pct, args.tp_pct)

        tag = ""
        if abs(new_sl - old_sl) / entry > 0.0001 or abs(new_tp - old_tp) / entry > 0.0001:
            tag = " ←"
            changed += 1

        print(
            f"  {path.name:<33} {side:<5} {entry:>10.4f}"
            f" {old_sl:>10.4f} {new_sl:>10.4f}"
            f" {old_tp:>10.4f} {new_tp:>10.4f}{tag}"
        )

        if args.apply and tag:
            data["stop_loss"]  = new_sl
            data["take_profit"] = new_tp
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print("-" * 95)
    if args.apply:
        print(f"\n完成：共更新 {changed} 個檔案。")
    else:
        print(f"\n共 {changed} 個檔案會變動。加上 --apply 後執行才會寫入。")


if __name__ == "__main__":
    main()
