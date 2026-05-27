"""
Password hashing with bcrypt.

Uses the bcrypt library directly — work factor 12 (OWASP minimum for 2025).
Never stores raw passwords; hashes are compared in constant time.
"""

import bcrypt as _bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the plaintext password."""
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """
    Constant-time comparison of plain password against bcrypt hash.
    Returns True on match; always False if either argument is empty.
    """
    if not plain or not hashed:
        return False
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """
    Returns True if the stored hash was created with a lower work factor
    and should be upgraded on next login.
    """
    try:
        rounds = _bcrypt.rounds(hashed.encode())
        return rounds < 12
    except Exception:
        return True
