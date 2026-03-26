"""
簽章邏輯單元測試

執行：pytest tests/test_signer.py -v
"""
import hashlib

from exchanges.bitunix.signer import (
    generate_nonce_alphanumeric,
    generate_nonce_hex,
    generate_http_signature,
    sort_params_for_sign,
    sha256_hex,
)


def test_sha256_hex():
    result = sha256_hex("hello")
    assert result == hashlib.sha256(b"hello").hexdigest()


def test_generate_nonce_hex_length():
    nonce = generate_nonce_hex()
    assert len(nonce) == 32
    assert all(c in "0123456789abcdef" for c in nonce)


def test_generate_nonce_alphanumeric_length():
    nonce = generate_nonce_alphanumeric(32)
    assert len(nonce) == 32
    import string
    valid = set(string.ascii_letters + string.digits)
    assert all(c in valid for c in nonce)


def test_sort_params_for_sign():
    params = {"b": "2", "a": "1", "c": "3"}
    result = sort_params_for_sign(params)
    assert result == "a1b2c3"


def test_sort_params_for_sign_ignores_none():
    params = {"b": "2", "a": None, "c": "3"}
    result = sort_params_for_sign(params)
    assert result == "b2c3"


def test_generate_http_signature_deterministic():
    """相同輸入應產生相同簽章"""
    sig1 = generate_http_signature("key", "secret", "nonce123", "1700000000000", "symbol=BTCUSDT", "")
    sig2 = generate_http_signature("key", "secret", "nonce123", "1700000000000", "symbol=BTCUSDT", "")
    assert sig1 == sig2
    assert len(sig1) == 64  # SHA256 hex = 64 chars


def test_generate_http_signature_different_inputs():
    """不同輸入應產生不同簽章"""
    sig1 = generate_http_signature("key", "secret", "nonce1", "1700000000000", "", "")
    sig2 = generate_http_signature("key", "secret", "nonce2", "1700000000000", "", "")
    assert sig1 != sig2
