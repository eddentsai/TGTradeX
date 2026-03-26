"""
TG 訊息解析器

將純文字指令轉換為 OrderRequest。

支援格式：
  /buy  <exchange> <symbol> <qty> [price]
  /sell <exchange> <symbol> <qty> [price]
  /open  <exchange> <symbol> <side> <qty> [price]
  /close <exchange> <symbol> <side> <qty> <position_id> [price]
  /account <exchange>
  /positions <exchange> [symbol]
  /orders <exchange> [symbol]

範例：
  /buy bitunix BTCUSDT 0.001              # 市價買入
  /buy bitunix BTCUSDT 0.001 50000        # 限價買入
  /sell bitunix BTCUSDT 0.001
  /close bitunix BTCUSDT SELL 0.001 pos123
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trader.models import OrderRequest, OrderSide, OrderType, TradeSide


class ParseError(ValueError):
    """無法解析指令時拋出"""


@dataclass
class QueryCommand:
    """查詢類指令（非開單）"""
    action: str          # "account" / "positions" / "orders"
    exchange: str
    symbol: str | None = None


def parse(text: str) -> OrderRequest | QueryCommand:
    """解析 TG 訊息，回傳對應的指令物件"""
    text = text.strip()
    if not text.startswith("/"):
        raise ParseError("指令必須以 / 開頭")

    parts = text.split()
    cmd = parts[0].lower().lstrip("/")

    if cmd == "account":
        return _parse_account(parts)
    if cmd == "positions":
        return _parse_query(parts, "positions")
    if cmd == "orders":
        return _parse_query(parts, "orders")
    if cmd in ("buy", "sell"):
        return _parse_buy_sell(parts, cmd)
    if cmd == "open":
        return _parse_open(parts)
    if cmd == "close":
        return _parse_close(parts)

    raise ParseError(f"未知指令: /{cmd}")


# ── 內部解析函式 ──────────────────────────────────────────────────────────────

def _require(parts: list[str], idx: int, name: str) -> str:
    if idx >= len(parts):
        raise ParseError(f"缺少參數: {name}")
    return parts[idx]


def _parse_account(parts: list[str]) -> QueryCommand:
    exchange = _require(parts, 1, "exchange")
    return QueryCommand(action="account", exchange=exchange)


def _parse_query(parts: list[str], action: str) -> QueryCommand:
    exchange = _require(parts, 1, "exchange")
    symbol = parts[2] if len(parts) > 2 else None
    return QueryCommand(action=action, exchange=exchange, symbol=symbol)


def _parse_buy_sell(parts: list[str], cmd: str) -> OrderRequest:
    # /buy|/sell <exchange> <symbol> <qty> [price]
    exchange = _require(parts, 1, "exchange")
    symbol = _require(parts, 2, "symbol")
    qty = _require(parts, 3, "qty")
    price = parts[4] if len(parts) > 4 else None

    side = OrderSide.BUY if cmd == "buy" else OrderSide.SELL
    order_type = OrderType.LIMIT if price else OrderType.MARKET

    return OrderRequest(
        exchange=exchange,
        symbol=symbol.upper(),
        side=side,
        order_type=order_type,
        qty=qty,
        price=price,
    )


def _parse_open(parts: list[str]) -> OrderRequest:
    # /open <exchange> <symbol> <side:BUY|SELL> <qty> [price]
    exchange = _require(parts, 1, "exchange")
    symbol = _require(parts, 2, "symbol")
    side_str = _require(parts, 3, "side").upper()
    qty = _require(parts, 4, "qty")
    price = parts[5] if len(parts) > 5 else None

    try:
        side = OrderSide(side_str)
    except ValueError:
        raise ParseError(f"side 必須是 BUY 或 SELL，收到: {side_str}")

    return OrderRequest(
        exchange=exchange,
        symbol=symbol.upper(),
        side=side,
        order_type=OrderType.LIMIT if price else OrderType.MARKET,
        qty=qty,
        price=price,
        trade_side=TradeSide.OPEN,
    )


def _parse_close(parts: list[str]) -> OrderRequest:
    # /close <exchange> <symbol> <side:BUY|SELL> <qty> <position_id> [price]
    exchange = _require(parts, 1, "exchange")
    symbol = _require(parts, 2, "symbol")
    side_str = _require(parts, 3, "side").upper()
    qty = _require(parts, 4, "qty")
    position_id = _require(parts, 5, "position_id")
    price = parts[6] if len(parts) > 6 else None

    try:
        side = OrderSide(side_str)
    except ValueError:
        raise ParseError(f"side 必須是 BUY 或 SELL，收到: {side_str}")

    return OrderRequest(
        exchange=exchange,
        symbol=symbol.upper(),
        side=side,
        order_type=OrderType.LIMIT if price else OrderType.MARKET,
        qty=qty,
        price=price,
        trade_side=TradeSide.CLOSE,
        position_id=position_id,
    )
