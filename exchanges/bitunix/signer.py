import hashlib
import secrets
import string
import time


_WS_NONCE_CHARS = string.ascii_letters + string.digits


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_nonce_hex() -> str:
    """產生 16 個隨機位元組的十六進位字串（32 字元），用於 HTTP 簽章"""
    return secrets.token_hex(16)


def generate_nonce_alphanumeric(size: int = 32) -> str:
    """產生指定長度的隨機英數字元字串，用於 WebSocket 簽章"""
    return "".join(secrets.choice(_WS_NONCE_CHARS) for _ in range(size))


def sort_params_for_sign(params: dict) -> str:
    """將查詢參數依 key 排序並串接為字串"""
    return "".join(
        f"{k}{v}"
        for k, v in sorted(params.items())
        if v is not None
    )


def generate_http_signature(
    api_key: str,
    secret_key: str,
    nonce: str,
    timestamp: str,
    sorted_params: str = "",
    body: str = "",
) -> str:
    """
    HTTP 請求簽章算法（雙層 SHA256）：
    1. digestInput = nonce + timestamp + apiKey + sortedParams + body
    2. digest = SHA256(digestInput)
    3. sign = SHA256(digest + secretKey)
    """
    digest_input = nonce + timestamp + api_key + sorted_params + body
    digest = sha256_hex(digest_input)
    return sha256_hex(digest + secret_key)


def create_http_auth_headers(
    api_key: str,
    secret_key: str,
    query_params: dict | None = None,
    body: str = "",
) -> dict[str, str]:
    """建立 HTTP 認證 headers"""
    nonce = generate_nonce_hex()
    timestamp = str(int(time.time() * 1000))
    sorted_params = sort_params_for_sign(query_params or {})
    sign = generate_http_signature(api_key, secret_key, nonce, timestamp, sorted_params, body)

    return {
        "api-key": api_key,
        "sign": sign,
        "nonce": nonce,
        "timestamp": timestamp,
    }


def create_ws_auth_payload(api_key: str, secret_key: str) -> dict:
    """建立 WebSocket 登入認證 payload"""
    nonce = generate_nonce_alphanumeric(32)
    timestamp = str(int(time.time()))
    digest = sha256_hex(nonce + timestamp + api_key)
    sign = sha256_hex(digest + secret_key)

    return {
        "apiKey": api_key,
        "timestamp": int(timestamp),
        "nonce": nonce,
        "sign": sign,
    }
