"""Google Sign-In ID-token verifier.

Verifies the ``id_token`` returned by Google Sign-In using the official
``google-auth`` library. The verifier accepts tokens issued by either
``accounts.google.com`` or ``https://accounts.google.com`` (the library
recognises both prefixes) and enforces the audience matches the
configured OAuth client id.

The library handles JWKS fetching, signature verification, and the
canonical ``iss`` / ``aud`` / ``exp`` claim checks. We additionally
re-verify ``iss`` here as belt-and-braces and raise a domain-typed
:class:`evwallet.errors.AuthTokenInvalid` (HTTP 401, ``code``
``AUTH_GOOGLE_TOKEN_INVALID``) on any failure so callers get a
consistent error envelope across providers.

There is no stub mode — Google tokens must be cryptographically verified;
a stub would be a security regression.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from google.auth.transport.requests import Request as _GoogleRequest

from evwallet.config import get_settings
from evwallet.errors import (
    AuthTokenInvalid,
    BackendUnavailableError,
    ConfigurationError,
)

_log = logging.getLogger(__name__)

_GOOGLE_VALID_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


@dataclass(frozen=True, slots=True)
class GoogleIdentityClaims:
    """Verified claims extracted from a Google Sign-In ID token.

    Attributes:
        google_sub: Google's stable user identifier — the foreign key we
            persist on ``social_accounts.provider_subject``.
        email: The user's email address. ``None`` only if the user
            explicitly chose not to share one (rare in sign-in flows).
        email_verified: Whether Google asserts the email is verified.
            Always present in ID tokens; ``False`` is treated as
            unverified by downstream code.
        name: The user's full display name, if granted.
        picture: URL to the user's profile picture, if granted.
    """

    google_sub: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None


def _resolve_audience() -> str:
    """Return the configured Google OAuth client id.

    Raises:
        ConfigurationError: When neither ``google_client_id`` nor
            ``google_pay_merchant_id`` is set.
    """
    settings = get_settings()
    audience = settings.google_client_id or settings.google_pay_merchant_id
    if not audience:
        raise ConfigurationError(
            "Google Sign-In requires EVW_GOOGLE_CLIENT_ID "
            "(or EVW_GOOGLE_PAY_MERCHANT_ID) so the JWT 'aud' claim "
            "can be verified."
        )
    return audience


def verify_google_id_token(id_token: str) -> GoogleIdentityClaims:
    """Verify a Google ID token and return the typed claims.

    Args:
        id_token: The raw JWT string from Google's Sign-In flow.

    Returns:
        A populated :class:`GoogleIdentityClaims`.

    Raises:
        ConfigurationError: If the Google audience is not configured.
        BackendUnavailableError: If the ``google-auth`` library is missing
            or Google returns a network-level error during JWKS fetch.
        AuthTokenInvalid: The token is malformed, expired, has the wrong
            audience/issuer, or fails signature verification. The error's
            ``details['code']`` is one of ``AUTH_GOOGLE_TOKEN_MISSING``
            or ``AUTH_GOOGLE_TOKEN_INVALID``.
    """
    if not id_token:
        raise AuthTokenInvalid(
            "id_token is required",
            details={"code": "AUTH_GOOGLE_TOKEN_MISSING"},
        )

    audience = _resolve_audience()

    try:
        # Imported lazily so the rest of the module loads even if the
        # google-auth package is uninstalled (e.g. minimal CI images).
        from google.oauth2 import id_token as google_id_token
    except ImportError as exc:  # pragma: no cover - exercised via the BackendUnavailable branch
        raise BackendUnavailableError(
            "google-auth library not installed",
            details={"code": "AUTH_GOOGLE_LIB_MISSING"},
        ) from exc

    # google-auth wants a fresh Request per call so its internal cache
    # does not get poisoned by a stale socket. The library treats this
    # object as fire-and-forget; we instantiate a new one each call.
    request = _GoogleRequest()

    try:
        claims = google_id_token.verify_oauth2_token(
            id_token,
            request,
            audience=audience,
        )
    except ValueError as exc:
        # google-auth raises ValueError for: bad audience, expired token,
        # untrusted issuer, malformed JWT, signature mismatch, missing
        # required claim. We collapse them to a single 401 envelope so
        # callers have one error code to react to.
        raise AuthTokenInvalid(
            "Google token invalid",
            details={"code": "AUTH_GOOGLE_TOKEN_INVALID", "reason": str(exc)},
        ) from exc
    except Exception as exc:  # network errors raised as google.auth.exceptions.TransportError
        raise BackendUnavailableError(
            f"Google token verification failed: {exc}",
            details={"code": "AUTH_GOOGLE_VERIFY_FAILED"},
        ) from exc

    if not isinstance(claims, dict):
        raise AuthTokenInvalid(
            "Google token returned an unexpected payload shape",
            details={"code": "AUTH_GOOGLE_TOKEN_INVALID"},
        )

    # Belt-and-braces: the library already enforces iss, but verify it
    # ourselves so a library bug or future change can't widen the set.
    issuer = claims.get("iss")
    if issuer not in _GOOGLE_VALID_ISSUERS:
        raise AuthTokenInvalid(
            "Google token issuer mismatch",
            details={"code": "AUTH_GOOGLE_TOKEN_INVALID", "reason": "iss"},
        )

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthTokenInvalid(
            "Google token missing 'sub' claim",
            details={"code": "AUTH_GOOGLE_TOKEN_INVALID"},
        )

    return GoogleIdentityClaims(
        google_sub=sub,
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified", False)),
        name=_coerce_str(claims.get("name")),
        picture=_coerce_str(claims.get("picture")),
    )


def _coerce_str(value: Any) -> str | None:
    """Return *value* as ``str`` if it is one, else ``None``."""
    return value if isinstance(value, str) and value else None


__all__ = [
    "GoogleIdentityClaims",
    "verify_google_id_token",
]
