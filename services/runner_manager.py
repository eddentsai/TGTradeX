"""
Runner 執行緒管理器

維護一個 {symbol → (ServiceRunner, Thread)} 字典。
定期呼叫 SymbolScanner 取得目標幣種列表，
對所有候選幣種起 runner 監控，
各 runner 開倉前自行查詢交易所持倉數是否達到上限。

支援兩種模式：
  - 一般模式（enable_ensemble=False）：ServiceRunner 自行根據市場狀態選策略（原本行為）
  - Ensemble 模式（enable_ensemble=True）：三策略同時評估，N/3 確認才開倉
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from exchanges.base import BaseExchange
from services.notifier import TelegramNotifier
from services.position_sizer import PositionSizer
from services.risk_guard import RiskGuard
from services.runner import ServiceRunner
from services.trade_journal import TradeJournal
from services.position_store import cleanup_stale as pos_cleanup_stale
from services.strategies.ensemble import EnsembleStrategy
from services.symbol_scanner import SymbolScanner

if TYPE_CHECKING:
    from services.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Redis key：每個交易所各自一個 set，避免 binance/bitunix 黑名單互相污染
def _redis_key(exchange_name: str) -> str:
    return f"tgtraderx:invalid_symbols:{exchange_name}"


class RunnerManager:
    """
    Args:
        exchange:               已初始化的交易所客戶端
        scanner:                SymbolScanner 實例
        sizer:                  PositionSizer 實例
        interval:               K 線週期，例如 "1h"
        max_positions:          最多同時持倉幣種數量（runner 數量不受此限）
        scan_interval:          掃描間隔秒數（預設 4 小時）
        dry_run:                True = 只記錄信號，不實際下單
        enable_ensemble:        True = 啟用 Ensemble 多策略確認模式
        ensemble_strategies:    Ensemble 模式下使用的策略清單（需 enable_ensemble=True）
        ensemble_min_confirm:   Ensemble 開倉所需最少確認策略數（預設 2）
        redis_url:              Redis 連線字串（預設 redis://localhost:6379/0）；
                                None = 停用持久化，黑名單只存記憶體
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
        enable_ensemble: bool = False,
        ensemble_strategies: "list[BaseStrategy] | None" = None,
        ensemble_min_confirm: int = 2,
        redis_url: str | None = "redis://localhost:6379/0",
        notifier: TelegramNotifier | None = None,
        risk_guard: RiskGuard | None = None,
        trade_journal: TradeJournal | None = None,
        trail_activate_roi: float = 0.0,
        trail_distance_roi: float = 0.0,
    ) -> None:
        self._exchange = exchange
        self._scanner = scanner
        self._sizer = sizer
        self._interval = interval
        self._max_positions = max_positions
        self._scan_interval = scan_interval
        self._dry_run = dry_run
        self._enable_ensemble = enable_ensemble
        self._ensemble_strategies = ensemble_strategies or []
        self._ensemble_min_confirm = ensemble_min_confirm
        self._trail_activate_roi = trail_activate_roi
        self._trail_distance_roi = trail_distance_roi

        if enable_ensemble and not self._ensemble_strategies:
            raise ValueError("enable_ensemble=True 時，ensemble_strategies 不可為空")

        self._runners: dict[str, tuple[ServiceRunner, threading.Thread]] = {}
        self._lock = threading.Lock()
        self._stop_ev = threading.Event()

        self._notifier = notifier
        self._risk_guard = risk_guard
        self._trade_journal = trade_journal

        # 黑名單：從 Redis 載入（若可用），否則只用記憶體
        self._redis = self._connect_redis(redis_url)
        self._redis_key = _redis_key(exchange.name)
        self._invalid_symbols: set[str] = self._load_blacklist()

    # ── Redis 輔助 ────────────────────────────────────────────────────────────

    def _connect_redis(self, url: str | None):
        """嘗試連線 Redis；失敗或 url=None 時回傳 None（退回記憶體模式）"""
        if url is None:
            logger.info("[Manager] Redis 停用，黑名單只存記憶體")
            return None
        try:
            import redis
            client = redis.from_url(url, socket_connect_timeout=2, decode_responses=True)
            client.ping()
            logger.info(f"[Manager] Redis 連線成功: {url}")
            return client
        except Exception as e:
            logger.warning(f"[Manager] Redis 連線失敗，退回記憶體模式: {e}")
            return None

    def _load_blacklist(self) -> set[str]:
        """從 Redis 載入黑名單；Redis 不可用時回傳空集合"""
        if self._redis is None:
            return set()
        try:
            symbols = self._redis.smembers(self._redis_key)
            if symbols:
                logger.info(f"[Manager] 從 Redis 載入黑名單 ({len(symbols)} 個): {sorted(symbols)}")
            return set(symbols)
        except Exception as e:
            logger.warning(f"[Manager] 讀取 Redis 黑名單失敗: {e}")
            return set()

    def _persist_ban(self, symbol: str) -> None:
        """將單一幣種寫入 Redis 黑名單"""
        if self._redis is None:
            return
        try:
            self._redis.sadd(self._redis_key, symbol)
        except Exception as e:
            logger.warning(f"[Manager] 寫入 Redis 黑名單失敗 ({symbol}): {e}")

    # ── 公開介面 ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """主迴圈：定期掃描並同步 runner 列表，直到收到停止信號"""
        mode_label = (
            f"Ensemble({self._ensemble_min_confirm}/{len(self._ensemble_strategies)})"
            if self._enable_ensemble
            else "Normal"
        )
        logger.info(
            f"RunnerManager 啟動  exchange={self._exchange.name} "
            f"mode={mode_label} "
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
        held = self._held_symbols()
        pos_cleanup_stale(self._exchange.name, held)
        targets = self._scanner.scan(held_symbols=held)
        targets = [s for s in targets if s not in self._invalid_symbols]

        logger.info(
            f"[Manager] 候選幣種 ({len(targets)}): {sorted(targets)}  "
            f"（持倉中: {sorted(held)}）"
        )

        with self._lock:
            for sym in list(self._runners.keys()):
                if sym not in targets and sym not in held:
                    self._stop_symbol(sym)
            for sym in targets:
                if sym not in self._runners:
                    self._start_symbol(sym)

    def _build_strategy(self) -> "BaseStrategy | None":
        """
        根據模式回傳策略實例：
          - Ensemble 模式 → EnsembleStrategy（包裝所有策略）
          - 一般模式      → None（ServiceRunner 自行選策略）
        """
        if self._enable_ensemble:
            return EnsembleStrategy(
                strategies=self._ensemble_strategies,
                min_confirm=self._ensemble_min_confirm,
            )
        return None

    def _start_symbol(self, symbol: str) -> None:
        """建立並啟動一個 ServiceRunner 執行緒（需持有 _lock）"""
        try:
            qty_precision = self._exchange.get_qty_precision(symbol)
        except Exception as e:
            logger.warning(f"[Manager] 無法取得 {symbol} 數量精度，加入黑名單: {e}")
            self._invalid_symbols.add(symbol)
            self._persist_ban(symbol)
            return

        sizer = PositionSizer(
            leverage=self._sizer.leverage,
            risk_pct=self._sizer.risk_pct,
            qty_precision=qty_precision,
            min_sl_buffer_pct=self._sizer.min_sl_buffer_pct,
        )
        strategy = self._build_strategy()
        runner = ServiceRunner(
            exchange=self._exchange,
            symbol=symbol,
            interval=self._interval,
            sizer=sizer,
            max_positions=self._max_positions,
            dry_run=self._dry_run,
            on_symbol_banned=self._ban_symbol,
            strategy=strategy,
            notifier=self._notifier,
            risk_guard=self._risk_guard,
            trade_journal=self._trade_journal,
            trail_activate_roi=self._trail_activate_roi,
            trail_distance_roi=self._trail_distance_roi,
            leverage=self._sizer.leverage,
        )
        thread = threading.Thread(
            target=runner.run,
            name=f"runner-{symbol}",
            daemon=True,
        )
        self._runners[symbol] = (runner, thread)
        thread.start()
        logger.info(
            f"[Manager] 已啟動 {symbol} runner "
            f"（執行緒 {thread.name}, "
            f"策略: {strategy.name if strategy else 'auto'}）"
        )

    def _stop_symbol(self, symbol: str) -> None:
        """停止並移除一個 runner（需持有 _lock）"""
        entry = self._runners.pop(symbol, None)
        if entry is None:
            return
        runner, thread = entry
        runner.stop()
        logger.info(f"[Manager] 已通知 {symbol} runner 停止")

    def _ban_symbol(self, symbol: str) -> None:
        """runner 回呼：將幣種加入永久黑名單並移除 runner"""
        self._invalid_symbols.add(symbol)
        self._persist_ban(symbol)
        logger.warning(f"[Manager] {symbol} 加入黑名單（不支援 API 交易）")
        if self._notifier is not None:
            self._notifier.notify_ban(symbol, self._exchange.name)
        with self._lock:
            self._stop_symbol(symbol)

    def _stop_all(self) -> None:
        with self._lock:
            for sym in list(self._runners.keys()):
                self._stop_symbol(sym)
