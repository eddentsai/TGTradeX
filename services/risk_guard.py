"""
交易風控守衛

監控連續虧損次數與當日累計虧損，超過閾值時暫停開倉。
次日 UTC 00:00 自動重置，不需要手動干預。

注意：
  pnl_pct 以個別交易的進出場百分比計算（非帳戶層面），
  例如 -1.5 代表此筆交易從進場到出場虧損了 1.5%。
  日累計損失 = 當日所有虧損交易 pnl_pct 絕對值之和。
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class RiskGuard:
    """
    Args:
        max_consecutive_losses: 連續虧損達此次數時暫停開倉（預設 3）
        max_daily_loss_pct:     當日所有虧損交易的 pnl_pct 絕對值之和超過此值時暫停開倉
                                （例如 10.0 = 若各筆交易累積虧損達 10%）；預設 10.0
        notifier:               風控觸發時發送 Telegram 通知（可選）
    """

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        max_daily_loss_pct: float = 10.0,
        notifier: "TelegramNotifier | None" = None,
    ) -> None:
        self._max_consec = max_consecutive_losses
        self._max_daily = max_daily_loss_pct
        self._notifier = notifier
        self._lock = threading.Lock()
        self._reset()

    # ── 公開介面 ──────────────────────────────────────────────────────────────

    def is_open_allowed(self) -> bool:
        """返回 True = 允許開新倉；False = 今日已觸發風控"""
        with self._lock:
            self._check_reset()
            return not self._stopped

    def record_trade(self, pnl_pct: float) -> None:
        """
        記錄一筆已平倉交易的損益百分比。

        Args:
            pnl_pct: 正值 = 獲利，負值 = 虧損（以進出場百分比計算）
        """
        with self._lock:
            self._check_reset()
            if pnl_pct >= 0:
                self._consec_losses = 0  # 獲利重置連續虧損計數
                logger.debug(
                    f"[RiskGuard] 記錄獲利 +{pnl_pct:.2f}%，連敗歸零  {self.status}"
                )
                return

            # 虧損
            self._consec_losses += 1
            self._daily_loss_sum += abs(pnl_pct)
            logger.info(
                f"[RiskGuard] 記錄虧損 {pnl_pct:.2f}%  "
                f"連敗={self._consec_losses}  日累損={self._daily_loss_sum:.1f}%"
            )

            # 觸發條件判斷
            if not self._stopped:
                reason = self._check_triggers()
                if reason:
                    self._stop(reason)

    @property
    def status(self) -> str:
        """回傳可讀狀態字串（供 log 使用）"""
        with self._lock:
            self._check_reset()
            if self._stopped:
                return f"STOPPED({self._stop_reason})"
            return (
                f"OK  連敗={self._consec_losses}/{self._max_consec}  "
                f"日損={self._daily_loss_sum:.1f}%/{self._max_daily:.1f}%"
            )

    # ── 內部 ──────────────────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._today = date.today()
        self._consec_losses = 0
        self._daily_loss_sum = 0.0
        self._stopped = False
        self._stop_reason = ""

    def _check_reset(self) -> None:
        """次日 UTC 00:00 後自動重置（需持有 _lock）"""
        today = date.today()
        if today != self._today:
            old_reason = self._stop_reason or "OK"
            self._reset()
            logger.info(f"[RiskGuard] 新的一天，風控狀態重置（舊狀態: {old_reason}）")

    def _check_triggers(self) -> str | None:
        """回傳觸發原因字串，無觸發則回傳 None（需持有 _lock）"""
        if self._consec_losses >= self._max_consec:
            return f"連續虧損 {self._consec_losses} 次（上限 {self._max_consec} 次）"
        if self._daily_loss_sum >= self._max_daily:
            return (
                f"當日累計虧損 {self._daily_loss_sum:.1f}% "
                f"超過上限 {self._max_daily:.1f}%"
            )
        return None

    def _stop(self, reason: str) -> None:
        """觸發風控（需持有 _lock）"""
        self._stopped = True
        self._stop_reason = reason
        logger.warning(f"[RiskGuard] 風控觸發，今日停止開倉: {reason}")
        if self._notifier is not None:
            self._notifier.notify_risk_stop(reason)
