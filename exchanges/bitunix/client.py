from __future__ import annotations

from .futures_private_http import BitunixFuturesPrivateHttpApi
from .futures_public_http import BitunixFuturesPublicHttpApi
from .http import DEFAULT_BASE_URL, DEFAULT_LANGUAGE, DEFAULT_TIMEOUT, BitunixHttpTransport
from .ws import (
    DEFAULT_AUTO_RECONNECT,
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_RECONNECT_INTERVAL,
    PRIVATE_WS_URL,
    PUBLIC_WS_URL,
    BitunixFuturesPrivateWsApi,
    BitunixFuturesPublicWsApi,
)


class BitunixClient:
    """Bitunix SDK 主客戶端

    Usage::

        # 公開 API（不需要 credentials）
        client = BitunixClient()
        tickers = client.futures_public.get_tickers("BTCUSDT")

        # 私有 API
        client = BitunixClient(api_key="...", secret_key="...")
        account = client.futures_private.get_account()

        # WebSocket
        client.futures_ws_public.on("ticker", lambda data: print(data))
        client.futures_ws_public.start()
        client.futures_ws_public.subscribe_public([{"symbol": "BTCUSDT", "ch": "ticker"}])
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        language: str = DEFAULT_LANGUAGE,
        ws_public_url: str = PUBLIC_WS_URL,
        ws_private_url: str = PRIVATE_WS_URL,
        ws_reconnect_interval: float = DEFAULT_RECONNECT_INTERVAL,
        ws_heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        ws_auto_reconnect: bool = DEFAULT_AUTO_RECONNECT,
    ):
        self._api_key = api_key
        self._secret_key = secret_key

        transport = BitunixHttpTransport(
            base_url=base_url,
            timeout=timeout,
            language=language,
        )

        # 公開 HTTP API（永遠可用）
        self.futures_public = BitunixFuturesPublicHttpApi(transport)

        # 私有 HTTP API（需要 credentials）
        self.futures_private: BitunixFuturesPrivateHttpApi | None = None
        if api_key and secret_key:
            self.futures_private = BitunixFuturesPrivateHttpApi(transport, api_key, secret_key)

        # 公開 WebSocket（永遠可用）
        self.futures_ws_public = BitunixFuturesPublicWsApi(
            url=ws_public_url,
            reconnect_interval=ws_reconnect_interval,
            heartbeat_interval=ws_heartbeat_interval,
            auto_reconnect=ws_auto_reconnect,
        )

        # 私有 WebSocket（需要 credentials）
        self.futures_ws_private: BitunixFuturesPrivateWsApi | None = None
        if api_key and secret_key:
            self.futures_ws_private = BitunixFuturesPrivateWsApi(
                api_key=api_key,
                secret_key=secret_key,
                url=ws_private_url,
                reconnect_interval=ws_reconnect_interval,
                heartbeat_interval=ws_heartbeat_interval,
                auto_reconnect=ws_auto_reconnect,
            )
