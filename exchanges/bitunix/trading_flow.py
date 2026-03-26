"""
高階交易流程輔助函式

整合 HTTP 下單與 WebSocket 事件等待，提供完整的交易生命週期管理。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .signer import generate_nonce_alphanumeric

if TYPE_CHECKING:
    from .client import BitunixClient


@dataclass
class TradingFlowResult:
    order_id: str
    client_id: str
    last_book5: Any
    terminal_order: dict
    position_update: dict


def run_futures_trading_flow(
    client: "BitunixClient",
    symbol: str,
    qty: str,
    side: str,
    order_type: str,
    trade_side: str | None = None,
    position_id: str | None = None,
    price: str | None = None,
    effect: str | None = None,
    timeout: float = 30.0,
    client_id: str | None = None,
) -> TradingFlowResult:
    """執行完整的交易流程

    流程：
    1. 啟動公開 WS 訂閱 depth_book5
    2. 啟動私有 WS 訂閱帳戶串流
    3. 透過 HTTP 下單
    4. 等待訂單終態
    5. 等待倉位更新
    6. 回傳結果

    Args:
        client: BitunixClient 實例（需設定 credentials）
        symbol: 交易對，例如 "BTCUSDT"
        qty: 數量字串
        side: "BUY" 或 "SELL"
        order_type: "LIMIT" 或 "MARKET"
        trade_side: "OPEN" 或 "CLOSE"（可選）
        position_id: 平倉時必填
        price: LIMIT 訂單必填
        effect: LIMIT 訂單必填，"GTC"|"IOC"|"FOK"|"POST_ONLY"
        timeout: 等待逾時秒數（預設 30）
        client_id: 自定義訂單 ID（不填則自動產生）

    Returns:
        TradingFlowResult 包含訂單資訊與倉位更新

    Raises:
        RuntimeError: 未設定 credentials
        TimeoutError: 等待訂單或倉位更新逾時
    """
    if not client.futures_private or not client.futures_ws_private:
        raise RuntimeError("私有 HTTP/WS 客戶端不可用，請檢查 credentials 設定")

    public_ws = client.futures_ws_public
    private_ws = client.futures_ws_private

    last_book5: list[Any] = [None]

    def on_book5(data: Any) -> None:
        last_book5[0] = data

    public_ws.on("depth_book5", on_book5)

    try:
        public_ws.start()
        public_ws.subscribe_public([{"symbol": symbol, "ch": "depth_book5"}])

        private_ws.start()
        private_ws.subscribe_account_streams()

        used_client_id = client_id or generate_nonce_alphanumeric(24)

        # 在下單前先設定等待器（避免競爭條件）
        import threading
        order_event = threading.Event()
        position_event = threading.Event()
        order_result: list[dict] = []
        position_result: list[dict] = []

        terminal_states = {"FILLED", "CANCELED", "PART_FILLED_CANCELED"}

        def on_order(data: dict) -> None:
            if data is None:
                return
            if data.get("clientId") == used_client_id and data.get("orderStatus") in terminal_states:
                order_result.append(data)
                order_event.set()

        def on_position(data: dict) -> None:
            if data is None:
                return
            if data.get("symbol") == symbol:
                position_result.append(data)
                position_event.set()

        private_ws.on("order", on_order)
        private_ws.on("position", on_position)

        try:
            order_payload: dict = {
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "qty": qty,
                "clientId": used_client_id,
            }
            if trade_side is not None:
                order_payload["tradeSide"] = trade_side
            if position_id is not None:
                order_payload["positionId"] = position_id
            if price is not None:
                order_payload["price"] = price
            if effect is not None:
                order_payload["effect"] = effect

            placed = client.futures_private.place_order(order_payload)

            if not order_event.wait(timeout):
                raise TimeoutError("等待訂單終態逾時")
            if not position_event.wait(timeout):
                raise TimeoutError("等待倉位更新逾時")

            return TradingFlowResult(
                order_id=placed.get("orderId", ""),
                client_id=used_client_id,
                last_book5=last_book5[0],
                terminal_order=order_result[0],
                position_update=position_result[0],
            )
        finally:
            private_ws.off("order", on_order)
            private_ws.off("position", on_position)

    finally:
        public_ws.off("depth_book5", on_book5)
        public_ws.stop()
        private_ws.stop()


def run_futures_limit_order_flow(
    client: "BitunixClient",
    symbol: str,
    qty: str,
    side: str,
    price: str,
    effect: str = "GTC",
    timeout: float = 30.0,
    client_id: str | None = None,
) -> TradingFlowResult:
    """下限價單並等待成交"""
    return run_futures_trading_flow(
        client=client,
        symbol=symbol,
        qty=qty,
        side=side,
        order_type="LIMIT",
        price=price,
        effect=effect,
        timeout=timeout,
        client_id=client_id,
    )


def run_futures_market_order_flow(
    client: "BitunixClient",
    symbol: str,
    qty: str,
    side: str,
    timeout: float = 30.0,
    client_id: str | None = None,
) -> TradingFlowResult:
    """下市價單並等待成交"""
    return run_futures_trading_flow(
        client=client,
        symbol=symbol,
        qty=qty,
        side=side,
        order_type="MARKET",
        timeout=timeout,
        client_id=client_id,
    )


def run_futures_open_position_flow(
    client: "BitunixClient",
    symbol: str,
    qty: str,
    side: str,
    order_type: str,
    price: str | None = None,
    effect: str | None = None,
    timeout: float = 30.0,
    client_id: str | None = None,
) -> TradingFlowResult:
    """開倉並等待成交"""
    return run_futures_trading_flow(
        client=client,
        symbol=symbol,
        qty=qty,
        side=side,
        order_type=order_type,
        trade_side="OPEN",
        price=price,
        effect=effect,
        timeout=timeout,
        client_id=client_id,
    )


def run_futures_close_position_flow(
    client: "BitunixClient",
    symbol: str,
    qty: str,
    side: str,
    position_id: str,
    order_type: str,
    price: str | None = None,
    effect: str | None = None,
    timeout: float = 30.0,
    client_id: str | None = None,
) -> TradingFlowResult:
    """平倉並等待成交"""
    return run_futures_trading_flow(
        client=client,
        symbol=symbol,
        qty=qty,
        side=side,
        order_type=order_type,
        trade_side="CLOSE",
        position_id=position_id,
        price=price,
        effect=effect,
        timeout=timeout,
        client_id=client_id,
    )
