"""Apple Sign-In identity-token verifier.

Validates the JWT ``identity_token`` Apple returns after a successful
Sign-In. Verification performs:

1. Fetch Apple's published JWKS from
   ``https://appleid.apple.com/auth/keys`` (cached in-process for
   roughly an hour to amortise network across concurrent sign-ins).
2. Resolve the signing key by matching the JWT header ``kid`` against
   the JWKS.
3. Verify the RS256 signature.
4. Verify the standard ``iss`` (``https://appleid.apple.com``) and
   ``aud`` (the configured bundle id) claims.
5. Verify the ``exp`` claim is in the future (PyJWT's default leeway).
6. If the caller supplied ``expected_nonce``, verify the JWT's ``nonce``
   claim matches. When ``Settings.apple_nonce_salt`` is set, the
   comparison is performed via HMAC-SHA256 so plaintext nonces never
   leak to logs or DB rows.

When ``Settings.apple_bundle_id`` (or its fallback
``Settings.apple_pay_merchant_id``) is not configured, the verifier
raises :class:`evwallet.errors.ConfigurationError` so a forgotten
``.env`` rewrite fails loudly instead of silently accepting any
identity. There is no stub mode — Apple tokens in production MUST be
cryptographically verified; a stub would be a security regression.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from evwallet.config import get_settings
from evwallet.errors import AuthTokenInvalid, BackendUnavailableError, ConfigurationError

_log = logging.getLogger(__name__)

_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_ISSUER = "https://appleid.apple.com"
_JWKS_TTL_SECONDS = 3600  # refresh Apple's keys at most once per hour
_NONCE_HMAC_LEN = 32  # length of the HMAC-SHA256 digest we compare against


@dataclass(frozen=True, slots=True)
class AppleIdentityClaims:
    """Verified claims extracted from an Apple identity token.

    Attributes:
        apple_sub: Apple's stable user identifier — the foreign key we
            persist on ``social_accounts.provider_subject``.
        email: The user's email address. Apple returns this only on the
            first sign-in (and only if the user chose to share it), so
            it is ``None`` on most subsequent calls.
        email_verified: Whether Apple asserts the email is verified.
        is_private_email: ``True`` when Apple issued a private relay
            address (``…@privaterelay.appleid.com``) rather than the
            user's real email.
        full_name: Best-effort assembled display name from the
            ``name.firstName`` / ``name.lastName`` claims that Apple
            emits only on the first sign-in.
        nonce: The ``nonce`` claim Apple echoed back, if present.
            Useful for caller-side replay protection — the verifier
            compares this against the client-supplied ``expected_nonce``
            before returning the claims.
    """

    apple_sub: str
    email: str | None
    email_verified: bool
    is_private_email: bool
    full_name: str | None
    nonce: str | None


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a process-wide :class:`httpx.AsyncClient` (lazy)."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    return _http_client


async def _fetch_jwks(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch Apple's JWKS payload.

    Extracted so tests can monkey-patch it without touching the network.

    Args:
        client: The async HTTP client to use.

    Returns:
        The raw JSON dict Apple returns (shape: ``{"keys": [...]}``).

    Raises:
        httpx.HTTPError: Network-level failure (mapped to
            :class:`BackendUnavailableError` by the caller).
    """
    response = await client.get(_APPLE_JWKS_URL)
    response.raise_for_status()
    return response.json()


async def _get_jwks(client: httpx.AsyncClient) -> dict[str, Any]:
    """Return a cached JWKS dict, refreshing it once per TTL window."""
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    if _jwks_cache is None or (now - _jwks_fetched_at) > _JWKS_TTL_SECONDS:
        _jwks_cache = await _fetch_jwks(client)
        _jwks_fetched_at = now
    return _jwks_cache


def reset_jwks_cache() -> None:
    """Clear the in-process JWKS cache (testing only).

    The autouse fixture in :mod:`tests.test_auth` calls this around
    every test so cached keys from one case cannot leak into another.
    """
    global _jwks_cache, _jwks_fetched_at
    _jwks_cache = None
    _jwks_fetched_at = 0.0


# ---------------------------------------------------------------------------
# Settings + audience resolution
# ---------------------------------------------------------------------------


def _resolve_audience() -> str:
    """Return the configured Apple audience (bundle id).

    Raises:
        ConfigurationError: When neither ``EVW_APPLE_BUNDLE_ID`` nor
            ``EVW_APPLE_PAY_MERCHANT_ID`` is set. We surface this BEFORE
            any token parsing so a misconfigured deployment fails fast.
    """
    settings = get_settings()
    bundle_id = settings.apple_bundle_id
    if bundle_id:
        return bundle_id
    if settings.apple_pay_merchant_id:
        return settings.apple_pay_merchant_id
    raise ConfigurationError(
        "Apple Sign-In requires EVW_APPLE_BUNDLE_ID "
        "(or EVW_APPLE_PAY_MERCHANT_ID) so the JWT 'aud' claim "
        "can be verified."
    )


def _find_jwk(jwks: dict[str, Any], kid: str | None) -> dict[str, Any]:
    """Return the JWK dict whose ``kid`` matches *kid*.

    Raises:
        AuthTokenInvalid: When no matching key exists.
    """
    keys = jwks.get("keys") or []
    if not isinstance(keys, list):
        raise AuthTokenInvalid(
            "Apple JWKS payload is malformed",
            details={"code": "AUTH_APPLE_TOKEN_INVALID"},
        )
    for jwk in keys:
        if isinstance(jwk, dict) and jwk.get("kid") == kid:
            return jwk
    raise AuthTokenInvalid(
        "Apple JWT 'kid' not present in JWKS",
        details={"code": "AUTH_APPLE_TOKEN_INVALID", "reason": "unknown_kid"},
    )


# ---------------------------------------------------------------------------
# Nonce helpers
# ---------------------------------------------------------------------------


def _nonces_match(expected: str, actual: str, salt: str | None) -> bool:
    """Constant-time comparison of two nonces, optionally HMAC-salted.

    When *salt* is set we compare ``HMAC(salt, expected)`` against
    ``HMAC(salt, actual)`` so the salt never appears in plaintext in
    log lines and a timing oracle can't reveal the expected nonce
    character-by-character.
    """
    if salt:
        key = salt.encode("utf-8")
        expected_digest = hmac.new(key, expected.encode("utf-8"), hashlib.sha256).digest()
        actual_digest = hmac.new(key, actual.encode("utf-8"), hashlib.sha256).digest()
        return hmac.compare_digest(expected_digest, actual_digest)
    return hmac.compare_digest(expected.encode("utf-8"), actual.encode("utf-8"))


# ---------------------------------------------------------------------------
# Claim shaping
# ---------------------------------------------------------------------------


def _assemble_full_name(raw_name: Any) -> str | None:
    """Build ``"First Last"`` from the ``name`` claim Apple sends on first sign-in."""
    if not isinstance(raw_name, dict):
        return None
    parts: list[str] = []
    for key in ("firstName", "lastName"):
        value = raw_name.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                parts.append(stripped)
    if not parts:
        return None
    return " ".join(parts)


def _coerce_optional_str(value: Any) -> str | None:
    """Return *value* if it's a non-empty ``str``, else ``None``."""
    if isinstance(value, str) and value:
        return value
    return None


def _coerce_bool(value: Any) -> bool:
    """Return *value* as ``bool`` when truthy, ``False`` otherwise.

    Apple sends ``"true"`` / ``"false"`` as JSON booleans; defensive
    coercion here keeps a malformed claim from crashing the upsert.
    """
    return bool(value) if value is not None else False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def verify_apple_identity_token(
    identity_token: str,
    *,
    expected_nonce: str | None = None,
) -> AppleIdentityClaims:
    """Verify an Apple identity token and return the typed claims.

    Args:
        identity_token: The raw JWT string from Apple's Sign-In flow.
        expected_nonce: Optional nonce the client sent to Apple and now
            expects to see echoed back in the JWT ``nonce`` claim.
            When provided, the JWT's ``nonce`` claim must match — the
            comparison is constant-time and, if
            ``Settings.apple_nonce_salt`` is set, performed over
            HMAC-SHA256 digests.

    Returns:
        A populated :class:`AppleIdentityClaims`.

    Raises:
        ConfigurationError: If ``apple_bundle_id`` is not configured.
        BackendUnavailableError: If Apple's JWKS endpoint is unreachable
            or returns a network-level error.
        AuthTokenInvalid: The token is missing, malformed, expired, has
            the wrong audience/issuer, has an unknown ``kid``, or fails
            the optional nonce check. The error's ``details['code']`` is
            one of ``AUTH_APPLE_TOKEN_MISSING``,
            ``AUTH_APPLE_TOKEN_EXPIRED``, or
            ``AUTH_APPLE_TOKEN_INVALID``.
    """
    # Empty/missing tokens are the most common 4xx we see in practice
    # (clients that POST before the user finishes the Apple sheet);
    # surface them with a dedicated code so callers can prompt the user
    # to retry instead of logging them as security failures.
    if not identity_token:
        raise AuthTokenInvalid(
            "identity_token is required",
            details={"code": "AUTH_APPLE_TOKEN_MISSING"},
        )

    audience = _resolve_audience()

    settings = get_settings()
    nonce_salt = settings.apple_nonce_salt

    # Validate the JWT SHAPE before any network call — garbage tokens
    # (no dots, bad base64) fail here with AUTH_APPLE_TOKEN_INVALID so
    # callers get a 401, not a 503. We use the unverified-header helper
    # here purely for structural validation; signature verification
    # happens below once we've resolved the signing key.
    try:
        header = jwt.get_unverified_header(identity_token)
    except jwt.InvalidTokenError as exc:
        raise AuthTokenInvalid(
            "Apple token header is malformed",
            details={"code": "AUTH_APPLE_TOKEN_INVALID", "reason": str(exc)},
        ) from exc

    kid = header.get("kid") if isinstance(header, dict) else None

    client = _get_http_client()
    try:
        jwks = await _get_jwks(client)
    except httpx.HTTPError as exc:
        raise BackendUnavailableError(
            f"Apple JWKS unreachable: {exc}",
            details={"code": "AUTH_APPLE_JWKS_UNREACHABLE"},
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive: any non-HTTP failure
        raise BackendUnavailableError(
            f"Apple JWKS fetch failed: {exc}",
            details={"code": "AUTH_APPLE_JWKS_UNREACHABLE"},
        ) from exc

    # Resolve the signing key AFTER fetching JWKS so we can return a
    # more specific error code (``unknown_kid``) when the header
    # references a key Apple no longer publishes (key rotation during
    # the cache TTL window).
    jwk = _find_jwk(jwks, kid)

    try:
        public_key: RSAPublicKey = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)  # type: ignore[assignment]
    except Exception as exc:
        raise AuthTokenInvalid(
            "Apple JWKS entry is malformed",
            details={"code": "AUTH_APPLE_TOKEN_INVALID", "reason": "bad_jwk"},
        ) from exc

    try:
        claims = jwt.decode(
            identity_token,
            public_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=_APPLE_ISSUER,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthTokenInvalid(
            "Apple token expired",
            details={"code": "AUTH_APPLE_TOKEN_EXPIRED"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        # PyJWT raises InvalidAudienceError, InvalidIssuerError,
        # InvalidSignatureError, DecodeError, etc. — all inherit from
        # InvalidTokenError. We collapse them to a single 401 envelope
        # so callers have one code path to react to, while preserving
        # the library's message in ``reason`` for debugging.
        raise AuthTokenInvalid(
            "Apple token invalid",
            details={"code": "AUTH_APPLE_TOKEN_INVALID", "reason": str(exc)},
        ) from exc

    if not isinstance(claims, dict):
        raise AuthTokenInvalid(
            "Apple token payload is not a JSON object",
            details={"code": "AUTH_APPLE_TOKEN_INVALID"},
        )

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthTokenInvalid(
            "Apple token missing 'sub' claim",
            details={"code": "AUTH_APPLE_TOKEN_INVALID"},
        )

    nonce = _coerce_optional_str(claims.get("nonce"))
    if expected_nonce is not None:
        # When the client supplies an expected nonce, the JWT must echo
        # it back AND they must match. The two checks are independent —
        # a token with no nonce claim at all is a mismatch too.
        if nonce is None or not _nonces_match(expected_nonce, nonce, nonce_salt):
            raise AuthTokenInvalid(
                "Apple token nonce mismatch",
                details={"code": "AUTH_APPLE_TOKEN_INVALID", "reason": "nonce"},
            )

    return AppleIdentityClaims(
        apple_sub=sub,
        email=_coerce_optional_str(claims.get("email")),
        email_verified=_coerce_bool(claims.get("email_verified")),
        is_private_email=_coerce_bool(claims.get("is_private_email")),
        full_name=_assemble_full_name(claims.get("name")),
        nonce=nonce,
    )


__all__ = [
    "AppleIdentityClaims",
    "reset_jwks_cache",
    "verify_apple_identity_token",
]
