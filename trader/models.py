"""
共用交易資料模型

與交易所無關的通用資料結構，供 parser、dispatcher 共用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TradeSide(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


@dataclass
class OrderRequest:
    """從 TG 指令解析出的開單請求"""

    exchange: str           # 目標交易所，例如 "bitunix"
    symbol: str             # 交易對，例如 "BTCUSDT"
    side: OrderSide
    order_type: OrderType
    qty: str                # 數量（字串，避免浮點精度問題）
    price: str | None = None        # LIMIT 訂單必填
    trade_side: TradeSide | None = None   # OPEN / CLOSE
    position_id: str | None = None        # 平倉時必填
    effect: str | None = None             # GTC / IOC / FOK / POST_ONLY
    extra: dict[str, Any] = field(default_factory=dict)  # 交易所特有參數
