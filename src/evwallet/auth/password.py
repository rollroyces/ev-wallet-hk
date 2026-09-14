"""Password hashing + verification.

Uses Argon2id (OWASP 2024 recommended) via argon2-cffi. Argon2 is the
modern replacement for bcrypt/scrypt; it's memory-hard (resists GPU/ASIC
attacks) and the default parameters shipped by argon2-cffi are tuned for
the OWASP cheat-sheet (time_cost=3, memory=64MB, parallelism=4).

Why not bcrypt: bcrypt is older and has a 72-byte input cap that forces
workarounds (pre-hashing with SHA-256, which nullifies bcrypt's main
defence against GPU cracking). Argon2 has no such limit.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHash,
    VerifyMismatchError,
)

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password with Argon2id.

    The returned string includes the algorithm parameters, salt, and
    digest — store it verbatim. Re-hashing on each call (unique salt)
    means the same plaintext produces different hashes each time.
    """
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, stored_hash: str | None) -> bool:
    """Verify a plaintext against a stored Argon2 hash.

    Returns False on any mismatch (wrong password, malformed hash,
    missing hash). Never raises — the caller treats both
    "wrong password" and "user not found" identically to avoid leaking
    which is which.
    """
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, plaintext)
    except (VerifyMismatchError, InvalidHash):
        return False


__all__ = ["hash_password", "verify_password"]