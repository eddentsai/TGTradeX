class BitunixError(Exception):
    """Bitunix SDK 基底錯誤類別"""
    pass


class BitunixHttpError(BitunixError):
    """HTTP 層錯誤（非 2xx 狀態碼）"""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP 請求失敗：{status} {message}")
        self.status = status
        self.message = message


class BitunixApiError(BitunixError):
    """Bitunix API 錯誤（code != 0）"""

    def __init__(self, code: int, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
