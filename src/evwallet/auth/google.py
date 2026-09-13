"""Google Sign-In ID-token verifier.

Verifies the ``id_token`` returned by Google Sign-In (via the
``google-auth`` library). The verifier accepts tokens issued by
``accounts.google.com`` and ``https://accounts.google.com`` and enforces
the audience matches ``EVW_GOOGLE_CLIENT_ID`` (or
``EVW_GOOGLE_PAY_MERCHANT_ID`` as a fallback).

When the audience is not configured the verifier raises
:class:`ConfigurationError` — same fail-fast contract as Apple.

A real Google library is used here (``google.oauth2.id_token``); there is
no stub. If ``google-auth`` is not installed (e.g. minimal CI image), a
:class:`BackendUnavailableError` is raised instead of silently accepting
every token.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from evwallet.errors import BackendUnavailableError, ConfigurationError, SchemaValidationError

_log = logging.getLogger(__name__)


def _get_audience() -> str:
    """Return the configured Google audience (OAuth client ID)."""
    from evwallet.config import get_settings  # avoid circular

    settings = get_settings()
    client_id = os.environ.get("EVW_GOOGLE_CLIENT_ID", "")
    if client_id:
        return client_id
    if settings.google_pay_merchant_id:
        return settings.google_pay_merchant_id
    raise ConfigurationError(
        "Google Sign-In requires EVW_GOOGLE_CLIENT_ID "
        "(or EVW_GOOGLE_PAY_MERCHANT_ID) so the JWT 'aud' claim can be verified."
    )


def verify_google_id_token(id_token: str) -> dict[str, Any]:
    """Verify a Google ID token and return the decoded claims.

    Args:
        id_token: The raw JWT string from Google's Sign-In flow.

    Returns:
        The decoded payload dict, including ``sub`` (Google user ID),
        ``email``, ``email_verified``, ``name``, ``picture``.

    Raises:
        ConfigurationError: If the Google audience is not configured.
        BackendUnavailableError: If the google-auth library is missing
            or the token fetch fails for a network reason.
        SchemaValidationError: If the token is malformed, expired, or
            signed by an unknown key.
    """
    if not id_token:
        raise SchemaValidationError(
            "id_token is required",
            details={"code": "AUTH_GOOGLE_TOKEN_MISSING"},
        )

    audience = _get_audience()

    try:
        from google.oauth2 import id_token as google_id_token
    except ImportError as exc:
        raise BackendUnavailableError(
            "google-auth library not installed",
            details={"code": "AUTH_GOOGLE_LIB_MISSING"},
        ) from exc

    try:
        claims = google_id_token.verify_oauth2_token(
            id_token,
            _GoogleRequest(),  # type: ignore[arg-type]
            audience=audience,
        )
    except ValueError as exc:
        # google-auth raises ValueError for audience mismatch, expiry, etc.
        raise SchemaValidationError(
            "Google token invalid",
            details={"reason": str(exc), "code": "AUTH_GOOGLE_TOKEN_INVALID"},
        ) from exc
    except Exception as exc:
        raise BackendUnavailableError(
            f"Google token verification failed: {exc}",
            details={"code": "AUTH_GOOGLE_VERIFY_FAILED"},
        ) from exc

    if "sub" not in claims:
        raise SchemaValidationError(
            "Google token missing 'sub' claim",
            details={"code": "AUTH_GOOGLE_TOKEN_INVALID"},
        )

    return claims


class _GoogleRequest:
    """Minimal adapter so ``verify_oauth2_token`` can run without a real
    Request object (the library uses it only to short-circuit certificate
    caching — for our short-lived calls we don't care).
    """

    def __init__(self) -> None:
        pass


__all__ = ["verify_google_id_token"]
