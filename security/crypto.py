"""
Cryptographic primitives for EvidentRx.

Signing:    HMAC-SHA256 (deterministic, fast, auditable)
Encryption: AES-256-GCM (authenticated encryption, nonce per message)

All operations are constant-time where applicable to prevent timing attacks.
Keys are sourced from config.settings — never hardcoded.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from typing import Union

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config.settings import settings

_NONCE_BYTES = 12    # GCM recommended nonce size
_KEY_BYTES   = 32    # AES-256


def _signing_key() -> bytes:
    """Derive 32-byte HMAC signing key from settings."""
    raw = settings.secret_signing_key.get_secret_value().encode()
    return hashlib.sha256(raw).digest()


def _encryption_key() -> bytes:
    """Derive 32-byte AES-256 key from settings (separate from signing key)."""
    raw = (settings.secret_signing_key.get_secret_value() + ":enc").encode()
    return hashlib.sha256(raw).digest()


# ─── Signing ──────────────────────────────────────────────────────────────────

def sign_payload(data: Union[str, bytes]) -> str:
    """
    Produce an HMAC-SHA256 signature of data.
    Returns hex-encoded signature string.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    sig = hmac.new(_signing_key(), data, hashlib.sha256).hexdigest()
    return sig


def verify_signature(data: Union[str, bytes], signature: str) -> bool:
    """
    Constant-time verification of an HMAC-SHA256 signature.
    Returns True only if the signature is valid for the given data.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    expected = hmac.new(_signing_key(), data, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ─── Encryption ───────────────────────────────────────────────────────────────

def encrypt(plaintext: Union[str, bytes], associated_data: bytes = b"") -> str:
    """
    AES-256-GCM authenticated encryption.
    Returns base64-encoded string: nonce || ciphertext || tag
    """
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")

    aesgcm = AESGCM(_encryption_key())
    nonce  = os.urandom(_NONCE_BYTES)
    ct     = aesgcm.encrypt(nonce, plaintext, associated_data or None)

    # Prefix with nonce length for extensibility
    packed = struct.pack("!H", _NONCE_BYTES) + nonce + ct
    return base64.b64encode(packed).decode("ascii")


def decrypt(ciphertext_b64: str, associated_data: bytes = b"") -> bytes:
    """
    AES-256-GCM authenticated decryption.
    Raises ValueError on authentication failure (tampered ciphertext).
    """
    packed  = base64.b64decode(ciphertext_b64)
    n_len,  = struct.unpack("!H", packed[:2])
    nonce   = packed[2:2 + n_len]
    ct      = packed[2 + n_len:]

    aesgcm = AESGCM(_encryption_key())
    try:
        return aesgcm.decrypt(nonce, ct, associated_data or None)
    except Exception as e:
        raise ValueError("Decryption failed — ciphertext may be tampered") from e


# ─── Utilities ────────────────────────────────────────────────────────────────

def secure_hash(data: Union[str, bytes]) -> str:
    """SHA-256 hash, hex-encoded. For content-addressable storage."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison (wraps hmac.compare_digest)."""
    return hmac.compare_digest(
        a.encode("utf-8") if isinstance(a, str) else a,
        b.encode("utf-8") if isinstance(b, str) else b,
    )
