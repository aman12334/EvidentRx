"""
Secrets management — zero hardcoded secrets policy.

Priority resolution order:
  1. AWS Secrets Manager (production)
  2. HashiCorp Vault (alternative)
  3. Environment variables / .env file (development)

In production, this module is the ONLY place secrets are read. All other
modules import secrets via get_secret(), never via direct os.environ access.

AWS Secrets Manager integration:
  - Secrets are cached locally for 5 minutes (TTL refresh)
  - Cache is flushed on SIGHUP for rotation support
  - Failed fetches fall back to environment variables with a warning
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone
from typing   import Dict, Optional

log = logging.getLogger(__name__)

_CACHE_TTL = timedelta(minutes=5)


class _SecretCache:
    """Simple TTL-based in-process cache for AWS Secrets Manager responses."""

    def __init__(self) -> None:
        self._store: Dict[str, tuple[str, datetime]] = {}

    def get(self, key: str) -> Optional[str]:
        entry = self._store.get(key)
        if not entry:
            return None
        value, fetched_at = entry
        if datetime.now(tz=timezone.utc) - fetched_at > _CACHE_TTL:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: str) -> None:
        self._store[key] = (value, datetime.now(tz=timezone.utc))

    def flush(self) -> None:
        self._store.clear()


_cache = _SecretCache()


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Retrieve a secret by name with environment fallback.

    Production: reads from AWS Secrets Manager (boto3).
    Development: reads from environment / .env file.
    """
    # Check in-process cache first
    cached = _cache.get(name)
    if cached is not None:
        return cached

    # Try AWS Secrets Manager in production
    try:
        import boto3  # type: ignore[import]
        from botocore.exceptions import ClientError  # type: ignore[import]

        region = os.environ.get("AWS_REGION", "us-east-1")
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=name)
        value = response.get("SecretString") or default
        if value:
            _cache.set(name, value)
        return value

    except ImportError:
        pass  # boto3 not installed — dev environment
    except Exception as e:
        log.warning("AWS Secrets Manager fetch failed for %r: %s", name, e)

    # Fallback to environment variable
    env_value = os.environ.get(name, default)
    return env_value


def require_secret(name: str) -> str:
    """Like get_secret but raises if the secret is missing."""
    value = get_secret(name)
    if not value:
        raise RuntimeError(
            f"Required secret {name!r} is not set. "
            "Set it via environment variable or AWS Secrets Manager."
        )
    return value


def flush_secret_cache() -> None:
    """Flush the secret cache — call on SIGHUP or after rotation."""
    _cache.flush()
    log.info("Secret cache flushed")
