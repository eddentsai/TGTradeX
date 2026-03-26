"""
Bitunix WebSocket 實作

使用 websocket-client 函式庫（以 threading 執行於背景執行緒）。
事件系統仿照 Node.js EventEmitter，以 on() / off() / emit() 操作。
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any, Callable

import websocket

from .signer import create_ws_auth_payload, generate_nonce_alphanumeric

PUBLIC_WS_URL = "wss://fapi.bitunix.com/public/"
PRIVATE_WS_URL = "wss://fapi.bitunix.com/private/"

DEFAULT_RECONNECT_INTERVAL = 5.0
DEFAULT_HEARTBEAT_INTERVAL = 3.0
DEFAULT_AUTO_RECONNECT = True


class BitunixWsBase:
    """WebSocket 基底類別，提供事件系統、心跳與自動重連功能"""

    def __init__(
        self,
        url: str,
        reconnect_interval: float = DEFAULT_RECONNECT_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        auto_reconnect: bool = DEFAULT_AUTO_RECONNECT,
    ):
        self.url = url
        self.reconnect_interval = reconnect_interval
        self.heartbeat_interval = heartbeat_interval
        self.auto_reconnect = auto_reconnect

        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._lock = threading.Lock()

        # 事件監聽器 {event_name: [callback, ...]}
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    # ── 事件系統 ──────────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """註冊事件監聽器"""
        self._listeners[event].append(callback)

    def off(self, event: str, callback: Callable) -> None:
        """移除事件監聽器"""
        if event in self._listeners:
            try:
                self._listeners[event].remove(callback)
            except ValueError:
                pass

    def emit(self, event: str, *args: Any) -> None:
        """觸發事件（依序呼叫所有監聽器）"""
        for cb in list(self._listeners.get(event, [])):
            try:
                cb(*args)
            except Exception:
                pass

    # ── 連線管理 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """啟動 WebSocket 連線（非阻塞）"""
        with self._lock:
            if self._running:
                return
            self._running = True
        self._connect()

    def stop(self) -> None:
        """停止 WebSocket 連線"""
        with self._lock:
            self._running = False
        self._connected = False
        if self._ws:
            self._ws.close()
            self._ws = None

    def _connect(self) -> None:
        self._ws = websocket.WebSocketApp(
            self.url,
            header={"User-Agent": "bitunix-sdk-python"},
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"ping_interval": 0},
            daemon=True,
        )
        self._ws_thread.start()

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._connected = True
        self._start_heartbeat()
        self.on_connected()
        self.emit("connected")

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        self.emit("raw", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            self.emit("parse-error", e)
            return

        op = data.get("op")
        if op == "pong":
            self.emit("pong", data)
            return
        if op == "connect":
            self.emit("connect-ack", data)
            return

        ch = data.get("ch")
        if ch:
            self.emit(ch, data.get("data"))

        self.emit("message", data)

    def _on_close(self, ws: websocket.WebSocketApp, code: int | None, reason: str | None) -> None:
        self._connected = False
        self._stop_heartbeat()
        self.emit("disconnected", code, reason)
        if self._running and self.auto_reconnect:
            self._schedule_reconnect()

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        self.emit("error", error)

    def _schedule_reconnect(self) -> None:
        def _reconnect():
            time.sleep(self.reconnect_interval)
            if self._running:
                self._connect()

        t = threading.Thread(target=_reconnect, daemon=True)
        t.start()

    # ── 心跳 ──────────────────────────────────────────────────────────────────

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        self._heartbeat_stop = threading.Event()

        def _loop():
            while not self._heartbeat_stop.wait(self.heartbeat_interval):
                if self._connected and self._ws:
                    try:
                        self._send({"op": "ping", "ping": int(time.time() * 1000)})
                    except Exception:
                        pass

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        if hasattr(self, "_heartbeat_stop"):
            self._heartbeat_stop.set()

    # ── 訊息發送 ──────────────────────────────────────────────────────────────

    def _send(self, payload: dict) -> None:
        if not self._ws or not self._connected:
            raise RuntimeError("WebSocket 尚未連線")
        self._ws.send(json.dumps(payload))

    def _subscribe(self, args: list[dict]) -> None:
        self._send({"op": "subscribe", "args": args})

    # ── 子類別 hook ────────────────────────────────────────────────────────────

    def on_connected(self) -> None:
        """連線成功時呼叫，子類別可覆寫"""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Public WebSocket
# ─────────────────────────────────────────────────────────────────────────────

class BitunixFuturesPublicWsApi(BitunixWsBase):
    """期貨公開 WebSocket API"""

    def __init__(
        self,
        url: str = PUBLIC_WS_URL,
        reconnect_interval: float = DEFAULT_RECONNECT_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        auto_reconnect: bool = DEFAULT_AUTO_RECONNECT,
    ):
        super().__init__(url, reconnect_interval, heartbeat_interval, auto_reconnect)

    def subscribe_public(self, channels: list[dict]) -> None:
        """訂閱公開頻道

        channels 範例：
          [{"symbol": "BTCUSDT", "ch": "depth_book5"}]
          [{"symbol": "BTCUSDT", "ch": "ticker"}]
        """
        self._subscribe(channels)


# ─────────────────────────────────────────────────────────────────────────────
# Private WebSocket
# ─────────────────────────────────────────────────────────────────────────────

class BitunixFuturesPrivateWsApi(BitunixWsBase):
    """期貨私有 WebSocket API（需要 API Key）"""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        url: str = PRIVATE_WS_URL,
        reconnect_interval: float = DEFAULT_RECONNECT_INTERVAL,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        auto_reconnect: bool = DEFAULT_AUTO_RECONNECT,
    ):
        super().__init__(url, reconnect_interval, heartbeat_interval, auto_reconnect)
        self._api_key = api_key
        self._secret_key = secret_key

    def on_connected(self) -> None:
        """連線後自動送出登入請求"""
        auth = create_ws_auth_payload(self._api_key, self._secret_key)
        self._send({"op": "login", "args": [auth]})

    def subscribe_private(self, channels: list[dict]) -> None:
        """訂閱私有頻道

        channels 範例：
          [{"ch": "order"}, {"ch": "position"}]
        """
        self._subscribe(channels)

    def subscribe_account_streams(self) -> None:
        """訂閱所有帳戶相關頻道（balance, order, position, tpsl）"""
        self.subscribe_private([
            {"ch": "balance"},
            {"ch": "order"},
            {"ch": "position"},
            {"ch": "tpsl"},
        ])

    def wait_for_order_terminal_state(
        self,
        order_id: str | None = None,
        client_id: str | None = None,
        timeout: float = 30.0,
        terminal_states: list[str] | None = None,
    ) -> dict:
        """等待訂單進入終態（FILLED / CANCELED / PART_FILLED_CANCELED）

        Args:
            order_id: 訂單 ID（與 client_id 至少提供一個）
            client_id: 客戶端訂單 ID
            timeout: 逾時秒數
            terminal_states: 自定義終態列表

        Returns:
            觸發終態的訂單 WebSocket 資料

        Raises:
            ValueError: 未提供 order_id 或 client_id
            TimeoutError: 等待逾時
        """
        if not order_id and not client_id:
            raise ValueError("order_id 或 client_id 必須至少提供一個")

        states = set(terminal_states or ["FILLED", "CANCELED", "PART_FILLED_CANCELED"])
        result_event = threading.Event()
        result_holder: list[dict] = []

        def on_order(data: dict) -> None:
            if data is None:
                return
            id_matched = (
                (order_id and data.get("orderId") == order_id) or
                (client_id and data.get("clientId") == client_id)
            )
            if id_matched and data.get("orderStatus") in states:
                result_holder.append(data)
                result_event.set()

        self.on("order", on_order)
        try:
            if not result_event.wait(timeout):
                raise TimeoutError("等待訂單終態逾時")
            return result_holder[0]
        finally:
            self.off("order", on_order)

    def wait_for_position_update(
        self,
        symbol: str | None = None,
        position_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """等待倉位更新事件

        Args:
            symbol: 過濾特定交易對（可選）
            position_id: 過濾特定倉位 ID（可選）
            timeout: 逾時秒數

        Returns:
            倉位 WebSocket 資料

        Raises:
            TimeoutError: 等待逾時
        """
        result_event = threading.Event()
        result_holder: list[dict] = []

        def on_position(data: dict) -> None:
            if data is None:
                return
            symbol_matched = not symbol or data.get("symbol") == symbol
            pos_id_matched = not position_id or data.get("positionId") == position_id
            if symbol_matched and pos_id_matched:
                result_holder.append(data)
                result_event.set()

        self.on("position", on_position)
        try:
            if not result_event.wait(timeout):
                raise TimeoutError("等待倉位更新逾時")
            return result_holder[0]
        finally:
            self.off("position", on_position)
