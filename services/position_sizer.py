"""
安全倉位計算器

根據帳戶餘額、槓桿、風險比例與止損價格，自動計算每次開倉的安全數量。

計算邏輯：
  risk_amount    = account_balance × risk_pct
  position_value = risk_amount ÷ sl_distance_pct
  qty            = position_value ÷ entry_price
  required_margin = position_value ÷ leverage

安全保護：
  1. position_value 上限 = account_balance × leverage × max_position_pct
  2. 止損必須在清算價的 min_sl_buffer_pct 安全距離之外
  3. qty 不低於精度最小值
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_MM_RATE = 0.005  # 維持保證金率預設值（0.5%，各交易所合約可能不同）


@dataclass
class SizeResult:
    qty: str                      # 下單數量（格式化字串）
    qty_float: float              # 下單數量（浮點）
    position_value: float         # 倉位市值（USDT）
    required_margin: float        # 所需保證金（USDT）
    liquidation_price: float      # 預估強平價（孤立保證金模式）
    risk_amount: float            # 本次承擔的最大虧損（USDT）
    risk_pct_actual: float        # 實際風險佔帳戶比例（0–1）
    sl_distance_pct: float        # 止損距入場的距離百分比（0–1）
    sl_to_liq_buffer_pct: float   # 止損距強平的安全緩衝百分比（0–1）

    def summary(self) -> str:
        return (
            f"qty={self.qty} "
            f"市值={self.position_value:.2f}U "
            f"保證金={self.required_margin:.2f}U "
            f"清算={self.liquidation_price:.2f} "
            f"風險={self.risk_amount:.2f}U({self.risk_pct_actual*100:.2f}%) "
            f"SL距離={self.sl_distance_pct*100:.2f}% "
            f"SL-清算緩衝={self.sl_to_liq_buffer_pct*100:.1f}%"
        )


class PositionSizer:
    """
    固定風險比例（Fixed Fractional）倉位計算器。

    Args:
        leverage:          槓桿倍數（預設 4）
        risk_pct:          每次最大風險比例（預設 0.01 = 1%）
        max_position_pct:  帳戶最大動用比例（預設 0.60 = 60%，為交易所費用與資金費率留緩衝）
        qty_precision:     數量小數位數（預設 3，BTC 為 0.001）
        mm_rate:           維持保證金率（預設 0.5%）
        min_sl_buffer_pct: 止損距清算價最低緩衝（預設 15%）
    """

    def __init__(
        self,
        leverage: int = 4,
        risk_pct: float = 0.01,
        max_position_pct: float = 0.60,
        qty_precision: int = 3,
        mm_rate: float = _DEFAULT_MM_RATE,
        min_sl_buffer_pct: float = 0.15,
    ) -> None:
        if leverage <= 0:
            raise ValueError("leverage 必須 > 0")
        if not (0 < risk_pct < 1):
            raise ValueError("risk_pct 必須在 (0, 1) 之間")
        if not (0 < max_position_pct <= 1):
            raise ValueError("max_position_pct 必須在 (0, 1]")

        self.leverage          = leverage
        self.risk_pct          = risk_pct
        self.max_position_pct  = max_position_pct
        self.qty_precision     = qty_precision
        self.mm_rate           = mm_rate
        self.min_sl_buffer_pct = min_sl_buffer_pct

    # ── 公開 API ──────────────────────────────────────────────────────────────

    def calculate(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
        side: str,          # "BUY" | "SELL"
    ) -> SizeResult | None:
        """
        計算安全開倉數量。

        Returns:
            SizeResult，或 None（條件不滿足時）。
            None 時已記錄 warning/error，呼叫端不需額外處理。
        """
        # ── 基本驗證 ────────────────────────────────────────────────────────
        if account_balance <= 0:
            logger.error(f"帳戶餘額無效: {account_balance}")
            return None
        if entry_price <= 0:
            logger.error(f"入場價格無效: {entry_price}")
            return None

        sl_distance     = abs(entry_price - stop_loss)
        sl_distance_pct = sl_distance / entry_price
        if sl_distance_pct == 0:
            logger.error("止損價格等於入場價格，無法計算倉位")
            return None

        # ── 清算價與安全緩衝 ─────────────────────────────────────────────────
        liq_price      = self._liquidation_price(entry_price, side)
        sl_buffer_pct  = self._sl_to_liq_buffer(stop_loss, liq_price, side)

        logger.info(
            f"清算價估算: {liq_price:.4f}  "
            f"止損={stop_loss:.4f}  "
            f"SL-清算緩衝={sl_buffer_pct*100:.1f}%"
        )

        if sl_buffer_pct < self.min_sl_buffer_pct:
            logger.warning(
                f"⚠️  止損距清算價太近！"
                f"緩衝={sl_buffer_pct*100:.1f}% < 最低{self.min_sl_buffer_pct*100:.0f}%，"
                f"建議將 SL 上移到 {self._safe_sl(entry_price, liq_price, side):.4f} 以上"
            )
            return None

        # ── 倉位計算 ─────────────────────────────────────────────────────────
        risk_amount    = account_balance * self.risk_pct
        position_value = risk_amount / sl_distance_pct

        # 上限：帳戶 × 槓桿 × max_position_pct
        cap            = account_balance * self.leverage * self.max_position_pct
        if position_value > cap:
            logger.info(
                f"倉位市值 {position_value:.2f} 超過上限 {cap:.2f}，已截斷"
            )
            position_value = cap

        qty_float = position_value / entry_price
        min_qty   = 10 ** (-self.qty_precision)
        if qty_float < min_qty:
            logger.warning(
                f"計算數量 {qty_float} 低於最小值 {min_qty}，"
                f"帳戶餘額可能不足（balance={account_balance:.2f}）"
            )
            return None

        # 四捨五入
        qty_float       = round(qty_float, self.qty_precision)
        position_value  = qty_float * entry_price
        required_margin = position_value / self.leverage
        actual_risk     = qty_float * sl_distance

        result = SizeResult(
            qty=str(qty_float),
            qty_float=qty_float,
            position_value=position_value,
            required_margin=required_margin,
            liquidation_price=liq_price,
            risk_amount=actual_risk,
            risk_pct_actual=actual_risk / account_balance,
            sl_distance_pct=sl_distance_pct,
            sl_to_liq_buffer_pct=sl_buffer_pct,
        )
        logger.info(f"倉位計算結果: {result.summary()}")
        return result

    def liquidation_price(self, entry_price: float, side: str) -> float:
        """公開方法：計算預估清算價（孤立保證金模式）"""
        return self._liquidation_price(entry_price, side)

    # ── 內部方法 ──────────────────────────────────────────────────────────────

    def _liquidation_price(self, entry: float, side: str) -> float:
        """
        孤立保證金模式估算：
          多單清算 = entry × (1 - 1/leverage + mm_rate)
          空單清算 = entry × (1 + 1/leverage - mm_rate)
        """
        if side == "BUY":
            return entry * (1.0 - 1.0 / self.leverage + self.mm_rate)
        return entry * (1.0 + 1.0 / self.leverage - self.mm_rate)

    def _sl_to_liq_buffer(self, sl: float, liq: float, side: str) -> float:
        """止損距清算價的百分比緩衝（越大越安全）"""
        if liq <= 0:
            return 1.0
        if side == "BUY":
            # 多單：SL 應在 LIQ 上方，緩衝 = (SL - LIQ) / LIQ
            return max((sl - liq) / liq, 0.0)
        else:
            # 空單：SL 應在 LIQ 下方，緩衝 = (LIQ - SL) / LIQ
            return max((liq - sl) / liq, 0.0)

    def _safe_sl(self, entry: float, liq: float, side: str) -> float:
        """計算符合最低緩衝要求的 SL 價格建議"""
        buffer = self.min_sl_buffer_pct
        if side == "BUY":
            return liq * (1.0 + buffer)
        return liq * (1.0 - buffer)
