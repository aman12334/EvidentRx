"""
security — Production Security Hardening Layer

Provides:
  - Secrets management (environment + AWS Secrets Manager)
  - HMAC signing and AES encryption utilities
  - Signed audit events (tamper-evident)
  - Sliding-window rate limiting
  - Input validation and sanitization
  - Security headers middleware (CSP, HSTS, X-Frame-Options)
"""

from security.audit_signer import sign_audit_event, verify_audit_event
from security.crypto import decrypt, encrypt, sign_payload, verify_signature
from security.rate_limiter import RateLimiter, rate_limiter

__all__ = [
    "sign_payload",
    "verify_signature",
    "encrypt",
    "decrypt",
    "sign_audit_event",
    "verify_audit_event",
    "RateLimiter",
    "rate_limiter",
]
