"""
市場偏向計算器

綜合三個外部數據源，計算市場情緒分數：
  - 資金費率  (Funding Rate)
  - 清算數據  (Liquidations)
  - 多空比    (Long/Short Ratio)

回傳 -100（極度看空）到 +100（極度看多）
各數據源獨立容錯，單一來源失敗不影響其他來源的計算。
"""

from __future__ import annotations

import logging

from .base import BaseDataProvider

logger = logging.getLogger(__name__)

# ── 資金費率門檻 ──────────────────────────────────────────────────────────────
_FUNDING_HIGH = 0.05  # 過度看多（正費率偏高）
_FUNDING_MILD_POS = 0.01  # 輕微看多
_FUNDING_MILD_NEG = -0.01  # 輕微看空
_FUNDING_LOW = -0.05  # 過度看空（負費率偏低）

# ── 清算量門檻（USDT）────────────────────────────────────────────────────────
_LIQ_THRESHOLD = 50_000_000  # 1 小時內清算量 > 5000 萬視為顯著

# ── 多空比門檻 ────────────────────────────────────────────────────────────────
_LS_RATIO_HIGH = 2.0  # 散戶過度看多（反向指標 → 偏空）
_LS_RATIO_LOW = 0.5  # 散戶過度看空（反向指標 → 偏多）


class MarketBiasCalculator:
    """市場偏向計算器"""

    def __init__(self, data_provider: BaseDataProvider) -> None:
        self.provider = data_provider

    def calculate_bias(self, symbol: str) -> int:
        """
        計算市場偏向分數。

        Args:
            symbol: 交易對，例如 "BTCUSDT"

        Returns:
            int: -100（極度看空）到 +100（極度看多）
        """
        score = 0
        score += self._score_funding(symbol)
        score += self._score_liquidations(symbol)
        score += self._score_long_short_ratio(symbol)
        return max(-100, min(100, score))

    # ── 各數據源評分（各自獨立容錯）─────────────────────────────────────────────

    def _score_funding(self, symbol: str) -> int:
        """資金費率評分：費率過高代表多頭擁擠，反向看空"""
        try:
            funding = self.provider.get_funding_rate(symbol)
            if funding > _FUNDING_HIGH:
                return -15  # 過度看多 → 偏空
            if funding > _FUNDING_MILD_POS:
                return -5
            if funding < _FUNDING_LOW:
                return +15  # 過度看空 → 偏多
            if funding < _FUNDING_MILD_NEG:
                return +5
            return 0
        except Exception as e:
            logger.warning(f"[MarketBias] {symbol} 資金費率取得失敗: {e}")
            return 0

    def _score_liquidations(self, symbol: str) -> int:
        """清算數據評分：大量多頭被清算代表已洗盤，偏多"""
        try:
            liquidations = self.provider.get_liquidations(symbol, "1h")
            long_liq = liquidations.get("long", 0)
            short_liq = liquidations.get("short", 0)

            score = 0
            if long_liq > _LIQ_THRESHOLD:
                score += 20  # 多頭已被清洗 → 偏多
            if short_liq > _LIQ_THRESHOLD:
                score -= 20  # 空頭被軋 → 偏空
            return score
        except Exception as e:
            logger.warning(f"[MarketBias] {symbol} 清算數據取得失敗: {e}")
            return 0

    def _score_long_short_ratio(self, symbol: str) -> int:
        """多空比評分：散戶情緒為反向指標"""
        try:
            ls_ratio = self.provider.get_long_short_ratio(symbol)
            if ls_ratio > _LS_RATIO_HIGH:
                return -10  # 散戶過度看多 → 偏空
            if ls_ratio < _LS_RATIO_LOW:
                return +10  # 散戶過度看空 → 偏多
            return 0
        except Exception as e:
            logger.warning(f"[MarketBias] {symbol} 多空比取得失敗: {e}")
            return 0
