"""
Bitunix 交易所 Adapter

將 BitunixClient 包裝為 BaseExchange 介面。
"""
from __future__ import annotations

from typing import Any

from exchanges.base import BaseExchange
from exchanges.bitunix import BitunixClient


class BitunixExchange(BaseExchange):
    """BaseExchange 的 Bitunix 實作"""

    def __init__(self, api_key: str, secret_key: str, **kwargs: Any) -> None:
        self._client = BitunixClient(api_key=api_key, secret_key=secret_key, **kwargs)

    @property
    def name(self) -> str:
        return "bitunix"

    def get_account(self) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_account()

    def get_pending_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_pending_positions(symbol=symbol)

    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_pending_orders(symbol=symbol)

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.place_order(payload)

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.cancel_orders(
            symbol=symbol, order_list=[{"orderId": order_id}]
        )

    def place_sl_tp_orders(
        self,
        symbol: str,
        side: str,
        qty: str,
        sl_price: float,
        tp_price: float,
    ) -> None:
        """補掛 SL/TP 條件單（平倉方向與倉位方向相反）"""
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        close_side = "BUY" if side == "SELL" else "SELL"
        # 止損單
        self._client.futures_private.place_order({
            "symbol":    symbol,
            "side":      close_side,
            "orderType": "MARKET",
            "qty":       qty,
            "tradeSide": "CLOSE",
            "slPrice":    str(round(sl_price, 8)),
            "slStopType": "MARK_PRICE",
            "slOrderType": "MARKET",
        })
        # 止盈單（限價，maker 手續費）
        self._client.futures_private.place_order({
            "symbol":      symbol,
            "side":        close_side,
            "orderType":   "LIMIT",
            "qty":         qty,
            "price":       str(round(tp_price, 8)),
            "effect":      "GTC",
            "tradeSide":   "CLOSE",
            "reduceOnly":  True,
        })

    def cancel_all_orders(self, symbol: str) -> None:
        """取消該交易對所有掛單（含 SL/TP 條件單）"""
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        orders = self._client.futures_private.get_pending_orders(symbol=symbol)
        if not orders:
            return
        order_list = [{"orderId": o["orderId"]} for o in orders if o.get("orderId")]
        if order_list:
            self._client.futures_private.cancel_orders(
                symbol=symbol, order_list=order_list
            )

    def get_klines(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        """
        回傳由舊到新的 K 線列表。
        Bitunix 欄位：time, open, high, low, close, volume
        """
        result = self._client.futures_public.get_kline(
            symbol=symbol, interval=interval, limit=limit
        )
        # 確保由舊到新（有些交易所回傳由新到舊）
        if len(result) >= 2:
            if result[0].get("time", 0) > result[-1].get("time", 0):
                result = list(reversed(result))
        return result

    def get_qty_precision(self, symbol: str) -> int:
        """從 Bitunix 合約規格取得數量精度（basePrecision 欄位）"""
        pairs = self._client.futures_public.get_trading_pairs(symbol)
        if not pairs:
            raise ValueError(f"找不到交易對: {symbol}")
        return int(pairs[0].get("basePrecision", 3))

    def get_tickers(self) -> list[dict]:
        """取得所有合約 ticker，正規化為 BaseExchange 標準格式"""
        raw = self._client.futures_public.get_tickers()
        result = []
        for t in raw:
            try:
                result.append({
                    "symbol":     str(t.get("symbol", "")),
                    "last_price": float(t.get("lastPrice", 0) or 0),
                    "quote_vol":  float(t.get("quoteVol", 0) or 0),
                    "base_vol":   float(t.get("baseVol", 0) or 0),
                    "high":       float(t.get("high", 0) or 0),
                    "low":        float(t.get("low", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
        return result
