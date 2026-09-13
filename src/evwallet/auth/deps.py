"""FastAPI dependencies for authentication / authorization.

Both ``current_user`` and ``current_admin`` decode the
``Authorization: Bearer *** `` header via :func:`evwallet.auth.jwt.decode_jwt`,
look up the user row, and check ``is_active`` (and ``is_admin`` for the
admin variant).

The DB lookup is a single ``SELECT`` against the ``users`` table; we do
NOT cache user objects across requests because ``is_active`` /
``is_admin`` may change between requests.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.auth.jwt import decode_jwt
from evwallet.db.models import User
from evwallet.db.session import get_db
from evwallet.errors import SchemaValidationError


def _extract_bearer(auth_header: str | None) -> str:
    """Pull the raw token out of an ``Authorization`` header.

    Args:
        auth_header: The full header value, e.g. ``"Bearer ey..."``.

    Returns:
        The token string with no prefix.

    Raises:
        SchemaValidationError: If the header is missing or malformed.
    """
    if not auth_header:
        raise SchemaValidationError(
            "Missing Authorization header",
            details={"code": "AUTH_TOKEN_MISSING"},
        )
    parts = auth_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise SchemaValidationError(
            "Authorization header must be 'Bearer <token>'",
            details={"code": "AUTH_TOKEN_MALFORMED"},
        )
    return parts[1]


async def current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: return the authenticated :class:`User`.

    Args:
        authorization: Injected from the ``Authorization`` header.
        db: Injected async DB session.

    Returns:
        The :class:`User` row matching the JWT ``sub`` claim.

    Raises:
        SchemaValidationError: Missing/malformed token or unknown user.
    """
    token = _extract_bearer(authorization)
    payload = decode_jwt(token)
    sub = payload.get("sub")
    if not sub:
        raise SchemaValidationError(
            "JWT missing 'sub' claim",
            details={"code": "AUTH_TOKEN_INVALID"},
        )
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise SchemaValidationError(
            "JWT 'sub' is not a valid UUID",
            details={"code": "AUTH_TOKEN_INVALID"},
        ) from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise SchemaValidationError(
            "Unknown user for token",
            details={"code": "AUTH_USER_NOT_FOUND"},
        )
    if not user.is_active:
        raise SchemaValidationError(
            "User is not active",
            details={"code": "AUTH_USER_DISABLED"},
        )
    return user


async def current_admin(
    user: User = Depends(current_user),
) -> User:
    """FastAPI dependency: return the user if they have ``is_admin=True``.

    Args:
        user: From :func:`current_user`.

    Returns:
        The :class:`User` row, guaranteed ``is_admin=True``.

    Raises:
        SchemaValidationError: If the user is not an admin.
    """
    if not user.is_admin:
        raise SchemaValidationError(
            "Admin privileges required",
            details={"code": "AUTH_FORBIDDEN"},
        )
    return user


__all__ = ["current_admin", "current_user"]
