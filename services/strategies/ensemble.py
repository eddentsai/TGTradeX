"""
Ensemble 策略 — 整合多個策略的信號

入場邏輯：
  需要 min_confirm 個策略同時給出 open_long 才開倉
  SL 取所有確認策略中「最高止損價」（最保守）
  TP 取所有確認策略中「最低止盈價」（最保守）

出場邏輯：
  任一策略觸發 close 即出場（最保守原則）
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)


class EnsembleStrategy(BaseStrategy):
    """組合策略 - 整合多個策略的信號"""

    def __init__(
        self,
        strategies: List[BaseStrategy],
        min_confirm: int = 2,
    ) -> None:
        if not strategies:
            raise ValueError("EnsembleStrategy 至少需要一個策略")
        if not (1 <= min_confirm <= len(strategies)):
            raise ValueError(
                f"min_confirm={min_confirm} 超出範圍 [1, {len(strategies)}]"
            )
        self.strategies = strategies
        self.min_confirm = min_confirm

    @property
    def name(self) -> str:
        strategy_names = "/".join(s.name for s in self.strategies)
        return f"Ensemble({strategy_names} min={self.min_confirm})"

    def on_candle(
        self,
        snap,
        position: Optional[ActivePosition] = None,
    ) -> Signal:
        if position is not None:
            return self._evaluate_exit(snap, position)
        return self._evaluate_entry(snap)

    # ── 出場邏輯 ──────────────────────────────────────────────────────────────

    def _evaluate_exit(
        self,
        snap,
        position: ActivePosition,
    ) -> Signal:
        """任一策略觸發出場即出場（最保守原則）"""
        for strategy in self.strategies:
            sig = strategy.on_candle(snap, position)
            if sig.action == "close":
                logger.debug(f"[Ensemble] {strategy.name} 觸發出場: {sig.reason}")
                return Signal(
                    action="close",
                    reason=f"[{strategy.name}] {sig.reason}",
                )

        hold_reasons = " | ".join(f"{s.name}=hold" for s in self.strategies)
        return Signal(action="hold", reason=hold_reasons)

    # ── 入場邏輯 ──────────────────────────────────────────────────────────────

    def _evaluate_entry(self, snap) -> Signal:
        """需 min_confirm 個策略同時確認才開倉（多空分開計票）"""
        long_votes:  list[tuple[str, Signal]] = []
        short_votes: list[tuple[str, Signal]] = []
        hold_reasons: list[str] = []

        for strategy in self.strategies:
            sig = strategy.on_candle(snap, None)
            if sig.action == "open_long":
                long_votes.append((strategy.name, sig))
                logger.debug(f"[Ensemble] {strategy.name} 投票做多: {sig.reason}")
            elif sig.action == "open_short":
                short_votes.append((strategy.name, sig))
                logger.debug(f"[Ensemble] {strategy.name} 投票做空: {sig.reason}")
            else:
                hold_reasons.append(f"{strategy.name}: {sig.reason}")

        # ── 做多優先判斷 ────────────────────────────────────────────────────────
        if len(long_votes) >= self.min_confirm:
            sl_values = [s.stop_loss  for _, s in long_votes if s.stop_loss  is not None]
            tp_values = [s.take_profit for _, s in long_votes if s.take_profit is not None]
            best_sl = max(sl_values) if sl_values else None  # 最保守（最高）
            best_tp = min(tp_values) if tp_values else None  # 最保守（最低）
            confirmed = [n for n, _ in long_votes]
            logger.info(
                f"[Ensemble] 做多確認 {len(long_votes)}/{len(self.strategies)} "
                f"策略: {', '.join(confirmed)}"
            )
            return Signal(
                action="open_long",
                stop_loss=best_sl,
                take_profit=best_tp,
                reason=(
                    f"Ensemble {len(long_votes)}/{len(self.strategies)} 確認: "
                    f"{', '.join(confirmed)}"
                ),
            )

        # ── 做空判斷 ─────────────────────────────────────────────────────────
        if len(short_votes) >= self.min_confirm:
            sl_values = [s.stop_loss  for _, s in short_votes if s.stop_loss  is not None]
            tp_values = [s.take_profit for _, s in short_votes if s.take_profit is not None]
            best_sl = min(sl_values) if sl_values else None  # 做空最保守（最低止損）
            best_tp = max(tp_values) if tp_values else None  # 做空最保守（最高止盈）
            confirmed = [n for n, _ in short_votes]
            logger.info(
                f"[Ensemble] 做空確認 {len(short_votes)}/{len(self.strategies)} "
                f"策略: {', '.join(confirmed)}"
            )
            return Signal(
                action="open_short",
                stop_loss=best_sl,
                take_profit=best_tp,
                reason=(
                    f"Ensemble 做空 {len(short_votes)}/{len(self.strategies)} 確認: "
                    f"{', '.join(confirmed)}"
                ),
            )

        # ── 確認數不足 ────────────────────────────────────────────────────────
        total_votes = len(long_votes) + len(short_votes)
        all_reasons = hold_reasons + [f"{n}: 做多" for n, _ in long_votes] + [f"{n}: 做空" for n, _ in short_votes]
        logger.debug(
            f"[Ensemble] 確認不足 {total_votes}/{self.min_confirm} "
            f"| {' ; '.join(all_reasons)}"
        )
        return Signal(
            action="hold",
            reason=(
                f"Ensemble 確認不足 {total_votes}/{self.min_confirm} "
                f"| {' ; '.join(hold_reasons)}"
            ),
        )
