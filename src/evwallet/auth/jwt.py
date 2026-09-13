"""HS256 JWT encode / decode for EV Wallet HK.

The package uses a single symmetric secret (``EVW_JWT_SECRET``) loaded via
:class:`~evwallet.config.Settings`. Tokens carry the user UUID as ``sub``,
``iat`` (issued at, UTC seconds), and ``exp`` (UTC seconds). Refresh-token
support is intentionally out of scope — the architecture contract specifies
a 720h lifetime.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from jwt import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAlgorithmError,
    InvalidSignatureError,
)

from evwallet.config import get_settings
from evwallet.errors import ConfigurationError, SchemaValidationError


def encode_jwt(
    user_id: uuid.UUID | str,
    *,
    extra_claims: dict[str, Any] | None = None,
    expiry_hours: int | None = None,
) -> tuple[str, int]:
    """Sign a JWT for the given user.

    Args:
        user_id: User primary key (UUID). Coerced to ``str`` for JSON.
        extra_claims: Optional additional claims merged into the payload.
        expiry_hours: Override the default lifetime (defaults to
            ``EVW_JWT_EXPIRY_HOURS``).

    Returns:
        A 2-tuple ``(token, expires_at_unix_seconds)``. ``expires_at`` is
        useful for the ``/auth/login`` response body so the client can
        schedule a refresh without re-decoding.

    Raises:
        ConfigurationError: If the JWT secret has not been configured.
    """
    settings = get_settings()
    if not settings.jwt_secret:
        raise ConfigurationError("EVW_JWT_SECRET must be set to issue JWTs")

    now = int(time.time())
    hours = expiry_hours if expiry_hours is not None else settings.jwt_expiry_hours
    if hours < 1:
        raise ConfigurationError(
            f"jwt_expiry_hours must be >= 1, got {hours}"
        )
    exp = now + hours * 3600

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": exp,
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, exp


def decode_jwt(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    """Verify and decode a JWT.

    Args:
        token: The compact JWS string (no ``Bearer `` prefix).
        verify_exp: Whether to enforce the ``exp`` claim. Set to ``False``
            only for explicit diagnostics; default is to verify.

    Returns:
        The decoded payload dict.

    Raises:
        SchemaValidationError: Token is malformed, signature invalid, or
            expired. The error wraps the underlying ``PyJWT`` exception.
        ConfigurationError: JWT secret is not configured.
    """
    settings = get_settings()
    if not settings.jwt_secret:
        raise ConfigurationError("EVW_JWT_SECRET must be set to verify JWTs")
    options: dict[str, Any] = {"verify_exp": verify_exp}
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options=options,  # type: ignore[arg-type]
        )
    except ExpiredSignatureError as exc:
        raise SchemaValidationError(
            "JWT expired",
            details={"reason": "expired", "code": "AUTH_TOKEN_EXPIRED"},
        ) from exc
    except (InvalidSignatureError, InvalidAlgorithmError, DecodeError) as exc:
        raise SchemaValidationError(
            "JWT invalid",
            details={"reason": str(exc), "code": "AUTH_TOKEN_INVALID"},
        ) from exc


__all__ = ["decode_jwt", "encode_jwt"]
