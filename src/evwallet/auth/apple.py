"""Apple Sign-In identity-token verifier.

Validates the JWT ``identity_token`` that Apple returns after a successful
Sign-In. Verification checks:

1. The token is a valid JWT signed by Apple's JWKS (the keys published at
   ``https://appleid.apple.com/auth/keys``).
2. The ``iss`` is ``https://appleid.apple.com``.
3. The ``aud`` matches ``EVW_APPLE_BUNDLE_ID`` (or
   ``EVW_APPLE_PAY_MERCHANT_ID`` if configured).
4. The ``exp`` claim is in the future.

When ``EVW_APPLE_BUNDLE_ID`` is not set, the verifier raises
:class:`ConfigurationError` so dev environments fail loudly instead of
silently accepting any identity. A "stub mode" is NOT exposed — Apple
tokens in production MUST be cryptographically verified; a stub would be
a security regression.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from evwallet.errors import BackendUnavailableError, ConfigurationError, SchemaValidationError

_log = logging.getLogger(__name__)

_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_ISSUER = "https://appleid.apple.com"

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Return the cached ``PyJWKClient`` for Apple's published keys."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_APPLE_JWKS_URL)
    return _jwks_client


def _get_audience() -> str:
    """Return the configured Apple audience (bundle id)."""
    from evwallet.config import get_settings  # avoid circular at import

    settings = get_settings()
    # Bundle ID is the canonical audience; fall back to the merchant id
    # only as a last resort.
    bundle_id = os.environ.get("EVW_APPLE_BUNDLE_ID", "")
    if bundle_id:
        return bundle_id
    if settings.apple_pay_merchant_id:
        return settings.apple_pay_merchant_id
    raise ConfigurationError(
        "Apple Sign-In requires EVW_APPLE_BUNDLE_ID (or EVW_APPLE_PAY_MERCHANT_ID) "
        "to be set so the JWT 'aud' claim can be verified."
    )


import os  # noqa: E402  (placed after _get_audience to keep the helper tidy)


def verify_apple_identity_token(identity_token: str) -> dict[str, Any]:
    """Verify an Apple identity token and return the decoded claims.

    Args:
        identity_token: The raw JWT string from Apple's Sign-In flow.

    Returns:
        The decoded payload dict, including ``sub`` (Apple user ID),
        ``email`` (optional), ``email_verified`` and ``is_private_email``.

    Raises:
        ConfigurationError: If the Apple audience is not configured.
        BackendUnavailableError: If Apple's JWKS endpoint is unreachable.
        SchemaValidationError: If the token is malformed, expired, or
            signed by an unknown key.
    """
    if not identity_token:
        raise SchemaValidationError(
            "identity_token is required",
            details={"code": "AUTH_APPLE_TOKEN_MISSING"},
        )

    audience = _get_audience()

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(identity_token)
    except httpx.HTTPError as exc:
        raise BackendUnavailableError(
            f"Apple JWKS unreachable: {exc}",
            details={"code": "AUTH_APPLE_JWKS_UNREACHABLE"},
        ) from exc
    except Exception as exc:  # PyJWT raises various subclasses
        raise BackendUnavailableError(
            f"Apple JWKS fetch failed: {exc}",
            details={"code": "AUTH_APPLE_JWKS_UNREACHABLE"},
        ) from exc

    try:
        claims = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=_APPLE_ISSUER,
        )
    except jwt.ExpiredSignatureError as exc:
        raise SchemaValidationError(
            "Apple token expired",
            details={"code": "AUTH_APPLE_TOKEN_EXPIRED"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise SchemaValidationError(
            "Apple token invalid",
            details={"reason": str(exc), "code": "AUTH_APPLE_TOKEN_INVALID"},
        ) from exc

    if "sub" not in claims:
        raise SchemaValidationError(
            "Apple token missing 'sub' claim",
            details={"code": "AUTH_APPLE_TOKEN_INVALID"},
        )

    # Drop non-serializable bits (e.g. decoded header) if any.
    return json.loads(json.dumps(claims, default=str))


__all__ = ["verify_apple_identity_token"]
