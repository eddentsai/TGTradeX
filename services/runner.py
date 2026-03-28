"""
自動交易服務主迴圈

每根 K 線結束後執行一次完整的決策週期：
  取得 K 線 → 計算指標 → 識別市場狀態 → 選擇策略 → 產生信號 → 執行下單
"""
from __future__ import annotations

import logging
import time

from exchanges.base import BaseExchange
from services.indicators import IndicatorSnapshot, candles_from_raw, compute_indicators
from services.market_state import MarketState, STATE_LABELS, classify_market
from services.position_sizer import PositionSizer, SizeResult
from services.strategies.base import ActivePosition, BaseStrategy, Signal
from services.strategies.conservative import ConservativeStrategy
from services.strategies.trend_following import TrendFollowingStrategy
from services.strategies.volume_profile import VolumeProfileStrategy

logger = logging.getLogger(__name__)


def _is_transient_error(e: Exception) -> bool:
    """
    判斷是否為可重試的暫時性網路錯誤。
    - 訊息含 '逾時' / 'timeout' / 'timed out' / 'connection'
    - 或 cause 是 requests 的 ReadTimeout / ConnectionError
    """
    msg = str(e).lower()
    if any(k in msg for k in ("逾時", "timeout", "timed out", "connection reset")):
        return True
    cause = getattr(e, "__cause__", None)
    if cause is not None:
        try:
            from requests.exceptions import (
                ReadTimeout,
                ConnectionError as ReqConnectionError,
            )
            if isinstance(cause, (ReadTimeout, ReqConnectionError)):
                return True
        except ImportError:
            pass
    return False

_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
    "6h": 21600, "12h": 43200, "1d": 86400,
}


class ServiceRunner:
    """
    自動交易服務

    Args:
        exchange:   已初始化的交易所客戶端（實作 BaseExchange）
        symbol:     交易對，例如 "BTCUSDT"
        interval:   K 線週期，例如 "15m"
        sizer:      倉位計算器（建議使用）；為 None 時必須提供 fixed_qty
        fixed_qty:  固定開倉數量（覆蓋 sizer，手動指定時使用）
        dry_run:    True = 只記錄信號，不實際下單
    """

    def __init__(
        self,
        exchange: BaseExchange,
        symbol: str,
        interval: str = "15m",
        sizer: PositionSizer | None = None,
        fixed_qty: str | None = None,
        dry_run: bool = False,
    ) -> None:
        if sizer is None and fixed_qty is None:
            raise ValueError("必須提供 sizer 或 fixed_qty 其中之一")

        self._exchange  = exchange
        self._symbol    = symbol.upper()
        self._interval  = interval
        self._sizer     = sizer
        self._fixed_qty = fixed_qty
        self._dry_run   = dry_run
        self._sleep_sec = _INTERVAL_SECONDS.get(interval, 900)

        self._active_pos: ActivePosition | None = None

        self._strategies: dict[MarketState, BaseStrategy] = {
            MarketState.UPTREND:          TrendFollowingStrategy(),
            MarketState.DOWNTREND:        ConservativeStrategy(),
            MarketState.RANGING:          VolumeProfileStrategy(),
            MarketState.HIGH_VOLATILITY:  ConservativeStrategy(),
        }

    # ── 主迴圈 ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        mode = "[DRY-RUN] " if self._dry_run else ""
        qty_desc = (
            f"auto-sizer leverage={self._sizer.leverage}x "
            f"risk={self._sizer.risk_pct*100:.1f}%"
            if self._sizer else f"fixed_qty={self._fixed_qty}"
        )
        logger.info(
            f"{mode}TGTradeX 啟動 "
            f"exchange={self._exchange.name} symbol={self._symbol} "
            f"interval={self._interval} {qty_desc}"
        )
        while True:
            try:
                self._run_cycle_with_retry()
            except KeyboardInterrupt:
                logger.info("收到中斷信號，服務停止")
                break
            except Exception as e:
                logger.exception(f"週期執行錯誤（非暫時性，跳過本週期）: {e}")
            logger.debug(f"睡眠 {self._sleep_sec} 秒，等待下一根 K 線")
            time.sleep(self._sleep_sec)

    # ── 暫時性錯誤重試 ────────────────────────────────────────────────────────

    def _run_cycle_with_retry(self, max_retries: int = 3, retry_delay: int = 20) -> None:
        """
        執行一次週期，對暫時性網路錯誤（timeout / connection）自動重試。
        非網路錯誤（邏輯錯誤、API 業務錯誤）直接拋出，不重試。
        """
        for attempt in range(1, max_retries + 1):
            try:
                self._run_cycle()
                return
            except Exception as e:
                if not _is_transient_error(e):
                    raise
                if attempt == max_retries:
                    logger.error(
                        f"[{self._symbol}] 網路錯誤，已重試 {max_retries} 次仍失敗，"
                        f"跳過本週期: {e}"
                    )
                    return   # 不 raise，讓主迴圈繼續等下一根 K 線
                logger.warning(
                    f"[{self._symbol}] 網路錯誤（第 {attempt}/{max_retries} 次），"
                    f"{retry_delay}s 後重試: {e}"
                )
                time.sleep(retry_delay)

    # ── 單次決策週期 ──────────────────────────────────────────────────────────

    def _run_cycle(self) -> None:
        # 1. 取得 K 線
        raw     = self._exchange.get_klines(self._symbol, self._interval, limit=250)
        candles = candles_from_raw(raw)
        if len(candles) < 30:
            logger.warning(f"K 線數量不足 ({len(candles)} 根)，跳過")
            return

        # 2. 計算指標
        snap = compute_indicators(candles)

        # 3. 識別市場狀態
        state = classify_market(snap)
        logger.info(
            f"[{self._symbol}] 市場={STATE_LABELS[state]}  "
            f"close={snap.close:.4f}  "
            f"ADX={f'{snap.adx:.1f}' if snap.adx is not None else 'N/A'}  "
            f"RSI={f'{snap.rsi:.1f}' if snap.rsi is not None else 'N/A'}  "
            f"Vol%={f'{snap.volatility_pct:.2f}' if snap.volatility_pct is not None else 'N/A'}"
        )

        # 4. 核對倉位
        positions  = self._exchange.get_pending_positions(self._symbol)
        active_pos = self._reconcile_position(positions, snap)

        # 5. 策略切換保護（開倉策略 ≠ 當前策略時，依盈虧分流）
        strategy   = self._strategies[state]
        active_pos = self._handle_strategy_switch(snap, active_pos, strategy.name)
        if active_pos is None and self._active_pos is None:
            # 剛被平倉，本週期不再做其他動作
            return

        # 6. 選策略 → 產生信號
        signal = strategy.on_candle(snap, active_pos)
        logger.info(
            f"[{self._symbol}] 策略={strategy.name}  "
            f"信號={signal.action}  {signal.reason}"
        )

        # 7. 執行
        self._execute(signal, snap, active_pos, strategy.name)

    # ── 策略切換保護 ──────────────────────────────────────────────────────────

    def _handle_strategy_switch(
        self,
        snap: IndicatorSnapshot,
        active_pos: ActivePosition | None,
        current_strategy_name: str,
    ) -> ActivePosition | None:
        """
        當市場狀態改變導致策略切換時，依持倉盈虧決定處置：

        - 無持倉 / recovered / 策略未切換 → 不做任何事
        - 虧損中 → 立即平倉（開倉的市場條件已消失，避免繼續虧損）
        - 獲利中 → 止損上移至保本（入場價），讓新策略接管出場
        """
        if active_pos is None:
            return None

        # 重啟重建的倉位無法判斷原始策略，跳過
        if active_pos.strategy_name == "recovered":
            return active_pos

        # 策略未切換，不做任何事
        if active_pos.strategy_name == current_strategy_name:
            return active_pos

        # ── 策略已切換 ────────────────────────────────────────────────────────
        is_profitable = snap.close > active_pos.entry_price

        if is_profitable:
            # 止損移到保本：鎖住不虧，讓新策略繼續管理出場
            old_sl = active_pos.stop_loss
            active_pos.stop_loss = active_pos.entry_price
            logger.info(
                f"[{self._symbol}] 策略切換 {active_pos.strategy_name} → {current_strategy_name}  "
                f"持倉獲利，止損移至保本 {active_pos.entry_price:.4f}  "
                f"（原 SL={old_sl:.4f}）"
            )
        else:
            # 虧損中：立即平倉，開倉條件不再成立
            logger.info(
                f"[{self._symbol}] 策略切換 {active_pos.strategy_name} → {current_strategy_name}  "
                f"持倉虧損，立即平倉  "
                f"entry={active_pos.entry_price:.4f} close={snap.close:.4f}"
            )
            self._close_position(
                Signal(
                    action="close",
                    reason=(
                        f"策略切換（{active_pos.strategy_name} → {current_strategy_name}），"
                        f"開倉條件消失"
                    ),
                ),
                active_pos,
            )
            return None

        return active_pos

    # ── 倉位核對 ──────────────────────────────────────────────────────────────

    def _reconcile_position(
        self,
        positions: list[dict],
        snap: IndicatorSnapshot,
    ) -> ActivePosition | None:
        pos_dict = next(
            (p for p in positions if p.get("symbol") == self._symbol), None
        )

        if pos_dict is None:
            if self._active_pos is not None:
                logger.info(f"[{self._symbol}] 交易所無倉位，清除本地狀態")
                self._active_pos = None
            return None

        if self._active_pos is None:
            # 服務重啟後發現有倉位：保守重建
            entry = float(pos_dict.get("openPrice", snap.close))
            self._active_pos = ActivePosition(
                position_id=pos_dict.get("positionId", ""),
                side=pos_dict.get("side", "BUY"),
                entry_price=entry,
                qty=str(pos_dict.get("qty", self._fixed_qty or "0")),
                stop_loss=entry * 0.95,
                take_profit=entry * 1.05,
                strategy_name="recovered",
            )
            logger.warning(
                f"[{self._symbol}] 發現未追蹤倉位，已重建（保守 SL/TP）"
                f" positionId={self._active_pos.position_id}"
            )
        else:
            if not self._active_pos.position_id:
                self._active_pos.position_id = pos_dict.get("positionId", "")

        return self._active_pos

    # ── 執行信號 ──────────────────────────────────────────────────────────────

    def _execute(
        self,
        signal: Signal,
        snap: IndicatorSnapshot,
        active_pos: ActivePosition | None,
        strategy_name: str,
    ) -> None:
        if signal.action == "hold":
            return
        if signal.action in ("open_long", "open_short"):
            self._open_position(signal, snap, strategy_name)
        elif signal.action == "close":
            self._close_position(signal, active_pos)

    def _open_position(
        self, signal: Signal, snap: IndicatorSnapshot, strategy_name: str
    ) -> None:
        if self._active_pos is not None:
            logger.warning(f"[{self._symbol}] 已有持倉，忽略開倉信號")
            return

        side = "BUY" if signal.action == "open_long" else "SELL"

        # ── 計算開倉數量 ──────────────────────────────────────────────────────
        qty, size_result = self._resolve_qty(snap, signal, side)
        if qty is None:
            logger.error(f"[{self._symbol}] 無法確定開倉數量，跳過")
            return

        payload: dict = {
            "symbol":    self._symbol,
            "side":      side,
            "orderType": signal.order_type,
            "qty":       qty,
            "tradeSide": "OPEN",
        }
        if signal.order_type == "LIMIT" and signal.price:
            payload["price"]  = signal.price
            payload["effect"] = "GTC"

        if self._dry_run:
            logger.info(
                f"[DRY-RUN] 開倉 {payload}  "
                f"理由={signal.reason}"
                + (f"  {size_result.summary()}" if size_result else "")
            )
            return

        result = self._exchange.place_order(payload)
        logger.info(f"[{self._symbol}] 開倉送出 orderId={result.get('orderId')}")

        entry  = snap.close
        sl     = signal.stop_loss  or (size_result.liquidation_price * 1.05 if size_result else entry * 0.95)
        tp     = signal.take_profit or entry * 1.05

        self._active_pos = ActivePosition(
            position_id="",
            side=side,
            entry_price=entry,
            qty=qty,
            stop_loss=sl,
            take_profit=tp,
            strategy_name=strategy_name,
        )
        logger.info(
            f"[{self._symbol}] 倉位建立 side={side} entry={entry:.4f} "
            f"SL={sl:.4f} TP={tp:.4f} qty={qty}"
        )

    def _resolve_qty(
        self,
        snap: IndicatorSnapshot,
        signal: Signal,
        side: str,
    ) -> tuple[str | None, SizeResult | None]:
        """
        決定開倉數量。
        優先使用 sizer 動態計算；若未設定 sizer 則使用 fixed_qty。
        """
        if self._sizer is not None:
            if signal.stop_loss is None:
                logger.error("策略未提供止損價，無法使用倉位計算器")
                return None, None

            account = self._exchange.get_account()
            balance = float(account.get("available", 0))
            if balance <= 0:
                logger.error(f"帳戶可用餘額為 0 或無法取得: {account}")
                return None, None

            result = self._sizer.calculate(
                account_balance=balance,
                entry_price=snap.close,
                stop_loss=signal.stop_loss,
                side=side,
            )
            if result is None:
                return None, None
            return result.qty, result

        # 使用固定數量
        return self._fixed_qty, None

    def _close_position(
        self, signal: Signal, active_pos: ActivePosition | None
    ) -> None:
        if active_pos is None:
            return

        if not active_pos.position_id:
            positions = self._exchange.get_pending_positions(self._symbol)
            pos_dict  = next(
                (p for p in positions if p.get("symbol") == self._symbol), None
            )
            if pos_dict:
                active_pos.position_id = pos_dict.get("positionId", "")
            if not active_pos.position_id:
                logger.error(f"[{self._symbol}] 無法取得 positionId，跳過平倉")
                return

        close_side = "SELL" if active_pos.side == "BUY" else "BUY"
        payload = {
            "symbol":     self._symbol,
            "side":       close_side,
            "orderType":  "MARKET",
            "qty":        active_pos.qty,
            "tradeSide":  "CLOSE",
            "positionId": active_pos.position_id,
        }

        if self._dry_run:
            logger.info(f"[DRY-RUN] 平倉 {payload}  理由={signal.reason}")
            return

        result = self._exchange.place_order(payload)
        logger.info(
            f"[{self._symbol}] 平倉成功 orderId={result.get('orderId')}  "
            f"理由={signal.reason}"
        )
        self._active_pos = None
