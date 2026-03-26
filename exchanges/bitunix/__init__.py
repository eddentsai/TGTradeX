"""
Bitunix SDK for Python

期貨交易 REST API 與 WebSocket 的 Python 客戶端。
"""

from .client import BitunixClient
from .errors import BitunixApiError, BitunixError, BitunixHttpError
from .futures_private_http import BitunixFuturesPrivateHttpApi
from .futures_public_http import BitunixFuturesPublicHttpApi
from .trading_flow import (
    TradingFlowResult,
    run_futures_close_position_flow,
    run_futures_limit_order_flow,
    run_futures_market_order_flow,
    run_futures_open_position_flow,
    run_futures_trading_flow,
)
from .ws import BitunixFuturesPrivateWsApi, BitunixFuturesPublicWsApi

__version__ = "0.1.0"
__all__ = [
    "BitunixClient",
    "BitunixError",
    "BitunixHttpError",
    "BitunixApiError",
    "BitunixFuturesPublicHttpApi",
    "BitunixFuturesPrivateHttpApi",
    "BitunixFuturesPublicWsApi",
    "BitunixFuturesPrivateWsApi",
    "TradingFlowResult",
    "run_futures_trading_flow",
    "run_futures_limit_order_flow",
    "run_futures_market_order_flow",
    "run_futures_open_position_flow",
    "run_futures_close_position_flow",
]
