"""
Runner 執行緒管理器

維護一個 {symbol → (ServiceRunner, Thread)} 字典。
定期呼叫 SymbolScanner 取得目標幣種列表，
對所有候選幣種起 runner 監控，
各 runner 開倉前自行查詢交易所持倉數是否達到上限。
"""
from __future__ import annotations

import logging
import threading

from exchanges.base import BaseExchange
from services.position_sizer import PositionSizer
from services.runner import ServiceRunner
from services.symbol_scanner import SymbolScanner

logger = logging.getLogger(__name__)


class RunnerManager:
    """
    Args:
        exchange:        已初始化的交易所客戶端
        scanner:         SymbolScanner 實例
        sizer:           PositionSizer 實例
        interval:        K 線週期，例如 "1h"
        max_positions:   最多同時持倉幣種數量（runner 數量不受此限）
        scan_interval:   掃描間隔秒數（預設 4 小時）
        dry_run:         True = 只記錄信號，不實際下單
    """

    def __init__(
        self,
        exchange: BaseExchange,
        scanner: SymbolScanner,
        sizer: PositionSizer,
        interval: str = "1h",
        max_positions: int = 5,
        scan_interval: int = 14400,
        dry_run: bool = False,
    ) -> None:
        self._exchange      = exchange
        self._scanner       = scanner
        self._sizer         = sizer
        self._interval      = interval
        self._max_positions = max_positions
        self._scan_interval = scan_interval
        self._dry_run       = dry_run

        # symbol -> (runner, thread)
        self._runners: dict[str, tuple[ServiceRunner, threading.Thread]] = {}
        self._lock    = threading.Lock()
        self._stop_ev = threading.Event()

        # 查詢過精度但失敗的幣種（交易所不支援），永久排除
        self._invalid_symbols: set[str] = set()

    # ── 公開介面 ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """主迴圈：定期掃描並同步 runner 列表，直到收到停止信號"""
        logger.info(
            f"RunnerManager 啟動  exchange={self._exchange.name} "
            f"max_positions={self._max_positions} "
            f"scan_interval={self._scan_interval}s"
        )
        while not self._stop_ev.is_set():
            try:
                self._sync_runners()
            except Exception as e:
                logger.error(f"[Manager] 同步 runner 失敗: {e}")
            self._stop_ev.wait(timeout=self._scan_interval)

        self._stop_all()
        logger.info("RunnerManager 已停止")

    def stop(self) -> None:
        """通知管理器及所有 runner 停止（執行緒安全）"""
        self._stop_ev.set()
        with self._lock:
            for sym, (runner, _) in list(self._runners.items()):
                runner.stop()
                logger.info(f"[Manager] 通知 {sym} runner 停止")

    # ── 內部方法 ──────────────────────────────────────────────────────────────

    def _held_symbols(self) -> set[str]:
        """查詢交易所，回傳目前有實際倉位的幣種集合"""
        try:
            positions = self._exchange.get_pending_positions()
            return {p["symbol"] for p in positions if p.get("symbol")}
        except Exception as e:
            logger.warning(f"[Manager] 查詢持倉失敗: {e}")
            return set()

    def _sync_runners(self) -> None:
        """
        根據掃描結果調整 runner 列表：
          - 所有候選幣種都啟動 runner 監控（開倉上限由 runner 自己判斷）
          - 不在候選列表且無持倉 → 停止 runner
          - 查不到合約規格的幣種加入黑名單，下次掃描自動排除
        """
        held    = self._held_symbols()
        targets = self._scanner.scan(held_symbols=held)

        # 排除已知無效幣種
        targets = [s for s in targets if s not in self._invalid_symbols]

        logger.info(
            f"[Manager] 候選幣種 ({len(targets)}): {sorted(targets)}  "
            f"（持倉中: {sorted(held)}）"
        )

        with self._lock:
            # 停掉不在候選列表中且無持倉的 runner
            for sym in list(self._runners.keys()):
                if sym not in targets and sym not in held:
                    self._stop_symbol(sym)

            # 啟動候選列表中尚未運行的
            for sym in targets:
                if sym not in self._runners:
                    self._start_symbol(sym)

    def _start_symbol(self, symbol: str) -> None:
        """建立並啟動一個 ServiceRunner 執行緒（需持有 _lock）"""
        try:
            qty_precision = self._exchange.get_qty_precision(symbol)
        except Exception as e:
            logger.warning(
                f"[Manager] 無法取得 {symbol} 數量精度，加入黑名單: {e}"
            )
            self._invalid_symbols.add(symbol)
            return

        sizer = PositionSizer(
            leverage=self._sizer.leverage,
            risk_pct=self._sizer.risk_pct,
            qty_precision=qty_precision,
        )
        runner = ServiceRunner(
            exchange=self._exchange,
            symbol=symbol,
            interval=self._interval,
            sizer=sizer,
            max_positions=self._max_positions,
            dry_run=self._dry_run,
        )
        thread = threading.Thread(
            target=runner.run,
            name=f"runner-{symbol}",
            daemon=True,
        )
        self._runners[symbol] = (runner, thread)
        thread.start()
        logger.info(f"[Manager] 已啟動 {symbol} runner（執行緒 {thread.name}）")

    def _stop_symbol(self, symbol: str) -> None:
        """停止並移除一個 runner（需持有 _lock）"""
        entry = self._runners.pop(symbol, None)
        if entry is None:
            return
        runner, thread = entry
        runner.stop()
        logger.info(f"[Manager] 已通知 {symbol} runner 停止")

    def _stop_all(self) -> None:
        with self._lock:
            for sym in list(self._runners.keys()):
                self._stop_symbol(sym)
