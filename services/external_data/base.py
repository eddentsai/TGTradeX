"""
外部數據提供者抽象基類

所有外部數據來源（Coinglass、Binance 等）都需繼承此類並實作抽象方法。

必須實作：
  - get_funding_rate       資金費率
  - get_liquidations       清算量
  - get_long_short_ratio   多空比
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict


class BaseDataProvider(ABC):
    """外部數據提供者基類"""

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> float:
        """
        取得最新資金費率。

        Returns:
            float: 資金費率，例如 0.0001 代表 0.01%
                   正值 = 多頭付給空頭；負值 = 空頭付給多頭
        """

    @abstractmethod
    def get_liquidations(self, symbol: str, period: str = "1h") -> Dict[str, float]:
        """
        取得指定週期內的清算量。

        Returns:
            dict: {
                "long":  float,  # 多頭清算量（USDT）
                "short": float,  # 空頭清算量（USDT）
            }
        """

    @abstractmethod
    def get_long_short_ratio(self, symbol: str) -> float:
        """
        取得多空比（散戶持倉比例）。

        Returns:
            float: 多空比，> 1 代表多頭佔多數
                   例如 1.5 代表多:空 = 1.5:1
        """
