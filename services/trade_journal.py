"""
交易日誌（Trade Journal）

以 CSV 格式記錄每筆交易的策略層資訊，供事後與交易所匯出明細對照分析使用。
價格損益以交易所匯出為準，本日誌只記錄交易所匯出裡沒有的策略維度資訊。

使用方式：
    record_open()  — 開倉時呼叫
    record_close() — 平倉時呼叫（不論策略觸發或交易所 SL/TP）

CSV 欄位：
    open_time     開倉時間（ISO 8601 UTC）
    close_time    平倉時間（ISO 8601 UTC，以訊號偵測時間為準）
    duration_min  持倉時長（分鐘，以訊號時間估算）
    symbol        交易對
    exchange      交易所名稱
    side          方向（BUY / SELL）
    strategy      策略名稱
    interval      K 線週期
    entry_price   進場價（實際成交價）
    qty           數量
    exit_reason   出場原因（用來對照交易所明細）
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_FIELDNAMES = [
    "open_time",
    "close_time",
    "duration_min",
    "symbol",
    "exchange",
    "side",
    "strategy",
    "interval",
    "entry_price",
    "qty",
    "exit_reason",
]


class TradeJournal:
    """
    Args:
        path: CSV 檔案路徑（預設 logs/trade_journal.csv）
    """

    def __init__(self, path: str = "logs/trade_journal.csv") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._open_times: dict[str, datetime] = {}
        self._ensure_file()

    def _ensure_file(self) -> None:
        """確保目錄與 CSV 標頭存在"""
        dir_part = os.path.dirname(self._path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        if not os.path.exists(self._path):
            with open(self._path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=_FIELDNAMES).writeheader()
            logger.info(f"[TradeJournal] 建立交易日誌 {self._path}")

    def record_open(
        self,
        symbol: str,
        exchange: str,
        side: str,
        strategy: str,
        interval: str,
        entry_price: float,
        qty: str,
    ) -> None:
        """記錄開倉（暫存開倉時間，平倉時才寫入 CSV）"""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._open_times[symbol] = now
        logger.debug(f"[TradeJournal] 開倉 {symbol} side={side} entry={entry_price}")

    def record_close(
        self,
        symbol: str,
        exchange: str,
        side: str,
        strategy: str,
        interval: str,
        entry_price: float,
        qty: str,
        exit_reason: str,
    ) -> None:
        """記錄平倉並寫入 CSV"""
        now = datetime.now(timezone.utc)
        with self._lock:
            open_time = self._open_times.pop(symbol, None)

        open_time_str = open_time.strftime("%Y-%m-%dT%H:%M:%SZ") if open_time else ""
        duration_min  = (
            round((now - open_time).total_seconds() / 60, 1) if open_time else ""
        )

        row = {
            "open_time":    open_time_str,
            "close_time":   now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_min": duration_min,
            "symbol":       symbol,
            "exchange":     exchange,
            "side":         side,
            "strategy":     strategy,
            "interval":     interval,
            "entry_price":  entry_price,
            "qty":          qty,
            "exit_reason":  exit_reason,
        }

        with self._lock:
            try:
                with open(self._path, "a", newline="", encoding="utf-8") as f:
                    csv.DictWriter(f, fieldnames=_FIELDNAMES).writerow(row)
                logger.info(
                    f"[TradeJournal] {symbol} {side}"
                    f" entry={entry_price} qty={qty}"
                    f" duration={duration_min}min reason={exit_reason}"
                )
            except Exception as e:
                logger.error(f"[TradeJournal] 寫入失敗: {e}")
