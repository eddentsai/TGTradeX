from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

from .errors import BitunixApiError, BitunixError, BitunixHttpError
from .signer import create_http_auth_headers

DEFAULT_BASE_URL = "https://fapi.bitunix.com"
DEFAULT_TIMEOUT = 10.0
DEFAULT_LANGUAGE = "en-US"

# 保守設為 8 req/s（文件上限 10），多執行緒共用同一個 lock
_RATE_LIMIT_RPS  = 8
_RATE_LIMIT_DELAY = 1.0 / _RATE_LIMIT_RPS   # 0.125 秒
_rate_lock = threading.Lock()


class BitunixHttpTransport:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        language: str = DEFAULT_LANGUAGE,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.language = language
        self._session = session or requests.Session()

    def public_request(self, method: str, path: str, query: dict | None = None) -> Any:
        return self._request(method, path, query=query)

    def private_request(
        self,
        api_key: str,
        secret_key: str,
        method: str,
        path: str,
        query: dict | None = None,
        body: Any = None,
    ) -> Any:
        body_text = ""
        if body is not None:
            body_text = json.dumps(body, separators=(",", ":"))

        # POST 簽章只用 body；GET/DELETE 簽章用 query params
        sign_params = {} if method == "POST" else (query or {})
        auth_headers = create_http_auth_headers(api_key, secret_key, sign_params, body_text)

        return self._request(method, path, query=query, body_text=body_text, extra_headers=auth_headers)

    def _request(
        self,
        method: str,
        path: str,
        query: dict | None = None,
        body_text: str | None = None,
        extra_headers: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "language": self.language,
        }
        if extra_headers:
            headers.update(extra_headers)

        # 過濾掉 None 值的 query 參數
        params = {k: str(v) for k, v in (query or {}).items() if v is not None} or None

        with _rate_lock:
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    data=body_text if method != "GET" else None,
                    timeout=self.timeout,
                )
            except requests.Timeout as e:
                raise BitunixError(f"請求逾時：{path}") from e
            except requests.ConnectionError as e:
                raise BitunixError(f"連線失敗：{path}") from e
            finally:
                time.sleep(_RATE_LIMIT_DELAY)

        if not response.ok:
            raise BitunixHttpError(response.status_code, response.text)

        try:
            data = response.json()
        except ValueError as e:
            raise BitunixError("API 回應不是有效的 JSON") from e

        if not isinstance(data, dict) or "code" not in data:
            raise BitunixError("API 回應格式不符合預期")

        code = data["code"]
        if code != 0:
            msg = data.get("msg", "Unknown error")
            raise BitunixApiError(code, msg)

        return data.get("data")
