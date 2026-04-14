"""
技術指標計算

所有函式皆為純函式（無副作用、無外部依賴），僅使用標準函式庫。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ── 資料結構 ──────────────────────────────────────────────────────────────────


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class IndicatorSnapshot:
    close: float
    prev_close: float | None = None

    # 幣種識別（由 runner 在建立 snap 後填入）
    symbol: str = ""

    # 新增：原始 K 線數據
    klines: list[Candle] = field(default_factory=list)

    # EMA
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    ema20_prev: float | None = None  # 前一根 K 線的 EMA20（判斷斜率用）

    # ADX
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None

    # RSI / ATR
    rsi: float | None = None
    atr: float | None = None

    # Bollinger Bands
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    bb_width_pct: float | None = None  # (upper-lower)/mid * 100（帶寬百分比）
    bb_position: float | None = None  # 0=下軌, 1=上軌（價格在帶內的相對位置）

    # 線性回歸斜率（每根 K 線的斜率 ÷ 當前收盤價，轉為 %）
    lr_slope_pct: float | None = None

    # 近期波動率（報酬率標準差，%）
    volatility_pct: float | None = None

    # 成交量分佈
    poc: float | None = None  # Point of Control（成交量最大價格）
    val: float | None = None  # Value Area Low（包含 70% 成交量的區間下限）
    vah: float | None = None  # Value Area High（包含 70% 成交量的區間上限）

    # 新增：VWAP 相關
    vwap: float | None = None
    vwap_upper: float | None = None  # VWAP + 1.5σ
    vwap_lower: float | None = None  # VWAP - 1.5σ


# ── 公開介面 ──────────────────────────────────────────────────────────────────


def candles_from_raw(data: list[dict]) -> list[Candle]:
    """將交易所原始 K 線 dict 列表轉換為 Candle 列表（由舊到新）。"""
    result = []
    for d in data:
        if not d:
            continue
        result.append(
            Candle(
                time=int(d.get("time", d.get("t", 0))),
                open=float(d.get("open", d.get("o", 0))),
                high=float(d.get("high", d.get("h", 0))),
                low=float(d.get("low", d.get("l", 0))),
                close=float(d.get("close", d.get("c", 0))),
                volume=float(d.get("volume", d.get("v", d.get("quoteVol", 0)))),
            )
        )
    return result


def compute_indicators(candles: list[Candle]) -> IndicatorSnapshot:
    """計算所有指標，回傳最新一根 K 線的快照。candles 須由舊到新排列。"""
    if not candles:
        return IndicatorSnapshot(close=0.0, klines=[])

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    # EMA
    ema20_s = _ema_series(closes, 20)
    ema50_s = _ema_series(closes, 50)
    ema200_s = _ema_series(closes, 200)

    # 其他指標
    adx_val, plus_di, minus_di = _latest_adx(highs, lows, closes, 14)
    rsi_val = _latest_rsi(closes, 14)
    atr_val = _latest_atr(highs, lows, closes, 14)
    bb_mid, bb_upper, bb_lower = _latest_bb(closes, 20, 2.0)
    lr_slope = _lr_slope(closes, 20)
    vol_pct = _volatility(closes, 20)
    poc, val, vah = _volume_profile(candles, 50)

    # 新增：計算 VWAP（使用最近 24 根 K 線）
    vwap_data = _calculate_vwap(candles, 24)
    vwap = vwap_data[0] if vwap_data else None
    vwap_std = vwap_data[1] if vwap_data else None
    vwap_upper = vwap + 1.5 * vwap_std if vwap and vwap_std else None
    vwap_lower = vwap - 1.5 * vwap_std if vwap and vwap_std else None

    # 衍生 BB 指標
    bb_width_pct = None
    bb_position = None
    if bb_upper is not None and bb_lower is not None and bb_mid and bb_mid != 0:
        bb_width_pct = (bb_upper - bb_lower) / bb_mid * 100
        price_range = bb_upper - bb_lower
        if price_range > 0:
            bb_position = (closes[-1] - bb_lower) / price_range

    lr_slope_pct = None
    if lr_slope is not None and closes[-1] != 0:
        lr_slope_pct = lr_slope / closes[-1] * 100

    return IndicatorSnapshot(
        close=closes[-1],
        prev_close=closes[-2] if len(closes) >= 2 else None,
        klines=candles,  # 保存原始 K 線數據
        ema20=ema20_s[-1],
        ema50=ema50_s[-1],
        ema200=ema200_s[-1],
        ema20_prev=ema20_s[-2] if len(ema20_s) >= 2 else None,
        adx=adx_val,
        plus_di=plus_di,
        minus_di=minus_di,
        rsi=rsi_val,
        atr=atr_val,
        bb_upper=bb_upper,
        bb_mid=bb_mid,
        bb_lower=bb_lower,
        bb_width_pct=bb_width_pct,
        bb_position=bb_position,
        lr_slope_pct=lr_slope_pct,
        volatility_pct=vol_pct,
        poc=poc,
        val=val,
        vah=vah,
        vwap=vwap,
        vwap_upper=vwap_upper,
        vwap_lower=vwap_lower,
    )


# ── 內部實作 ──────────────────────────────────────────────────────────────────


def _ema_series(closes: list[float], period: int) -> list[float | None]:
    """回傳與 closes 等長的 EMA 序列，前 period-1 項為 None。"""
    result: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return result
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    result[period - 1] = ema
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1.0 - k)
        result[i] = ema
    return result


def _latest_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    n = len(closes)
    if n < period + 1:
        return None
    trs = [
        max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        for i in range(1, n)
    ]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _latest_adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float | None, float | None, float | None]:
    """回傳 (ADX, +DI, -DI)，不足資料時回傳 (None, None, None)。"""
    n = len(closes)
    if n < period * 2 + 1:
        return None, None, None

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        trs.append(tr)
        plus_dms.append(up if (up > down and up > 0) else 0.0)
        minus_dms.append(down if (down > up and down > 0) else 0.0)

    m = len(trs)
    if m < period:
        return None, None, None

    # Wilder 初始平滑（直接加總，非平均）
    s_tr = sum(trs[:period])
    s_plus = sum(plus_dms[:period])
    s_minus = sum(minus_dms[:period])

    def _di(s_dm: float) -> float:
        return 100.0 * s_dm / s_tr if s_tr != 0 else 0.0

    di_plus = _di(s_plus)
    di_minus = _di(s_minus)
    di_sum = di_plus + di_minus
    dx_list = [100.0 * abs(di_plus - di_minus) / di_sum if di_sum != 0 else 0.0]

    for i in range(period, m):
        s_tr = s_tr - s_tr / period + trs[i]
        s_plus = s_plus - s_plus / period + plus_dms[i]
        s_minus = s_minus - s_minus / period + minus_dms[i]
        di_plus = _di(s_plus)
        di_minus = _di(s_minus)
        di_sum = di_plus + di_minus
        dx_list.append(100.0 * abs(di_plus - di_minus) / di_sum if di_sum != 0 else 0.0)

    if len(dx_list) < period:
        return None, None, None

    # ADX = DX 的 Wilder 平滑
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period

    return adx, di_plus, di_minus


def _latest_rsi(closes: list[float], period: int = 14) -> float | None:
    n = len(closes)
    if n < period + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _latest_bb(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> tuple[float | None, float | None, float | None]:
    """回傳 (mid, upper, lower)。"""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return mid, mid + std_dev * std, mid - std_dev * std


def _lr_slope(closes: list[float], period: int = 20) -> float | None:
    """線性回歸斜率（每根 K 線的價格變化量）。"""
    if len(closes) < period:
        return None
    y = closes[-period:]
    x_mean = (period - 1) / 2.0
    y_mean = sum(y) / period
    num = sum((i - x_mean) * (y[i] - y_mean) for i in range(period))
    den = sum((i - x_mean) ** 2 for i in range(period))
    return num / den if den != 0 else 0.0


def _volatility(closes: list[float], period: int = 20) -> float | None:
    """近期報酬率標準差（%）。"""
    if len(closes) < period + 1:
        return None
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(len(closes) - period, len(closes))
        if closes[i - 1] != 0
    ]
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance) * 100.0


def _volume_profile(
    candles: list[Candle], num_bins: int = 50
) -> tuple[float, float, float]:
    """
    計算成交量分佈，回傳 (POC, VAL, VAH)。
    VAL / VAH 涵蓋 70% 的總成交量。
    """
    if not candles:
        return 0.0, 0.0, 0.0

    low_p = min(c.low for c in candles)
    high_p = max(c.high for c in candles)
    if high_p == low_p:
        return high_p, high_p, high_p

    bin_size = (high_p - low_p) / num_bins
    bins = [0.0] * num_bins

    for c in candles:
        c_range = c.high - c.low
        if c_range == 0:
            idx = min(int((c.close - low_p) / bin_size), num_bins - 1)
            bins[idx] += c.volume
            continue
        for b in range(num_bins):
            b_low = low_p + b * bin_size
            b_high = b_low + bin_size
            ol = max(b_low, c.low)
            oh = min(b_high, c.high)
            if oh > ol:
                bins[b] += c.volume * (oh - ol) / c_range

    poc_idx = max(range(num_bins), key=lambda i: bins[i])
    poc_price = low_p + (poc_idx + 0.5) * bin_size

    total = sum(bins)
    target = total * 0.70
    val_idx = poc_idx
    vah_idx = poc_idx
    accum = bins[poc_idx]

    while accum < target:
        can_down = val_idx > 0
        can_up = vah_idx < num_bins - 1
        if not (can_down or can_up):
            break
        v_below = bins[val_idx - 1] if can_down else -1.0
        v_above = bins[vah_idx + 1] if can_up else -1.0
        if v_below >= v_above:
            val_idx -= 1
            accum += bins[val_idx]
        else:
            vah_idx += 1
            accum += bins[vah_idx]

    return poc_price, low_p + val_idx * bin_size, low_p + (vah_idx + 1) * bin_size


def _calculate_vwap(
    candles: list[Candle], period: int = 24
) -> tuple[float, float] | None:
    """
    計算 VWAP 及其標準差
    返回 (vwap, std) 或 None
    """
    if len(candles) < period:
        return None

    recent_candles = candles[-period:]

    # 計算 VWAP
    cum_vol = 0.0
    cum_vp = 0.0

    for c in recent_candles:
        typical_price = (c.high + c.low + c.close) / 3
        cum_vol += c.volume
        cum_vp += typical_price * c.volume

    if cum_vol == 0:
        return None

    vwap = cum_vp / cum_vol

    # 計算標準差
    variance_sum = 0.0
    for c in recent_candles:
        typical_price = (c.high + c.low + c.close) / 3
        variance_sum += ((typical_price - vwap) ** 2) * c.volume

    variance = variance_sum / cum_vol
    std = math.sqrt(variance)

    return vwap, std
