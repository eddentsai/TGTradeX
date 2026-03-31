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

        # 把 runner 傳來的 tpPrice 欄位獨立拆出來，
        # 不使用條件觸發方式掛止盈，而是開倉後補一筆普通限價減倉單（maker 手續費）
        tp_price  = payload.pop("tpPrice",      None)
        payload.pop("tpStopType",  None)
        payload.pop("tpOrderType", None)
        payload.pop("tpOrderPrice", None)

        # SL 欄位對齊交易所價格精度
        sym        = payload["symbol"]
        price_prec = self.get_price_precision(sym)
        if "slPrice" in payload:
            payload["slPrice"] = str(round(float(payload["slPrice"]), price_prec))

        result = self._client.futures_private.place_order(payload)

        # 開倉完成後補掛 TP 限價減倉單
        if payload.get("tradeSide") == "OPEN" and tp_price is not None:
            side       = payload["side"]
            close_side = "BUY" if side == "SELL" else "SELL"
            self._client.futures_private.place_order({
                "symbol":     sym,
                "side":       close_side,
                "orderType":  "LIMIT",
                "qty":        payload["qty"],
                "price":      str(round(float(tp_price), price_prec)),
                "effect":     "GTC",
                "reduceOnly": True,
            })

        return result

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
        close_side  = "BUY" if side == "SELL" else "SELL"
        price_prec  = self.get_price_precision(symbol)
        # 止損：reduceOnly 條件市價單（觸發後市價平倉）
        self._client.futures_private.place_order({
            "symbol":      symbol,
            "side":        close_side,
            "orderType":   "MARKET",
            "qty":         qty,
            "reduceOnly":  True,
            "slPrice":     str(round(sl_price, price_prec)),
            "slStopType":  "MARK_PRICE",
            "slOrderType": "MARKET",
        })
        # 止盈：普通限價減倉（maker 手續費）
        self._client.futures_private.place_order({
            "symbol":     symbol,
            "side":       close_side,
            "orderType":  "LIMIT",
            "qty":        qty,
            "price":      str(round(tp_price, price_prec)),
            "effect":     "GTC",
            "reduceOnly": True,
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

    def _get_pair_info(self, symbol: str) -> dict:
        """取得交易對規格，結果快取於 _pair_cache"""
        if not hasattr(self, "_pair_cache"):
            self._pair_cache: dict[str, dict] = {}
        if symbol not in self._pair_cache:
            pairs = self._client.futures_public.get_trading_pairs(symbol)
            if not pairs:
                raise ValueError(f"找不到交易對: {symbol}")
            self._pair_cache[symbol] = pairs[0]
        return self._pair_cache[symbol]

    def get_qty_precision(self, symbol: str) -> int:
        """從 Bitunix 合約規格取得數量精度（basePrecision 欄位）"""
        return int(self._get_pair_info(symbol).get("basePrecision", 3))

    def get_price_precision(self, symbol: str) -> int:
        """從 Bitunix 合約規格取得價格精度（quotePrecision / pricePrecision 欄位）"""
        info = self._get_pair_info(symbol)
        # Bitunix 欄位名稱依版本不同，依序嘗試
        for key in ("quotePrecision", "pricePrecision", "priceDecimal"):
            if key in info:
                return int(info[key])
        # fallback：從當前價格的小數位數推算
        price_str = str(info.get("lastPrice", "") or "")
        if "." in price_str:
            return len(price_str.rstrip("0").split(".")[-1])
        return 4  # 保守預設

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
