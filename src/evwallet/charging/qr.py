"""QR payload validation for EV Wallet HK poles.

Format (canonical, see ``docs/ARCHITECTURE.md`` and the Caddy-pinned spec):

    evwallet://POLE_ID?sig=HMAC_SHA256(secret, POLE_ID)

The HMAC is computed over the pole id bytes (utf-8). The signature is hex-
encoded and appended as the ``sig`` query parameter. The scheme/host prefix
makes the payload directly openable as a deep link in the mobile app.

Usage:

    >>> from evwallet.charging.qr import parse_qr
    >>> pole_id, sig = parse_qr("evwallet://abc-123?sig=deadbeef")
    >>> verify_qr_payload("evwallet://abc-123?sig=deadbeef")
    'abc-123'

The HMAC secret is loaded from :class:`Settings` (``Settings.qr_hmac_secret``,
falling back to ``Settings.jwt_secret`` in dev — see ``Settings.get_qr_hmac_secret``).
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

from ..config import get_settings
from ..errors import ChargingInvalidQR

_PREFIX = "evwallet://"


def _secret() -> bytes:
    """Return the configured HMAC secret as bytes.

    Prefers ``Settings.qr_hmac_secret`` when present (production) and falls
    back to ``Settings.jwt_secret`` for dev — see docs/ARCHITECTURE.md.
    """
    settings = get_settings()
    secret = settings.qr_hmac_secret or settings.jwt_secret
    return secret.encode("utf-8")


def compute_signature(pole_id: str) -> str:
    """Return the hex HMAC-SHA256 signature for ``pole_id``.

    Args:
        pole_id: The pole's canonical id (uuid string).

    Returns:
        Hex-encoded HMAC-SHA256 of ``pole_id`` using the configured secret.
    """
    return hmac.new(_secret(), pole_id.encode("utf-8"), hashlib.sha256).hexdigest()


def parse_qr(payload: str) -> tuple[str, str]:
    """Split a QR payload into ``(pole_id, signature)``.

    Args:
        payload: The raw scanned string. Must begin with ``evwallet://``.

    Returns:
        A 2-tuple ``(pole_id, signature)``.

    Raises:
        ChargingInvalidQR: If the payload does not match the expected scheme
            or is missing the ``sig`` query parameter.
    """
    if not isinstance(payload, str) or not payload.startswith(_PREFIX):
        raise ChargingInvalidQR(
            "QR payload must start with 'evwallet://'",
            details={
                "prefix_seen": payload[:32] if isinstance(payload, str) else type(payload).__name__
            },
        )
    try:
        parts = urlsplit(payload)
    except ValueError as exc:
        raise ChargingInvalidQR("QR payload is not a valid URL") from exc
    pole_id = parts.netloc + parts.path  # netloc is the pole id, path is ''
    pole_id = pole_id.strip("/")
    if not pole_id:
        raise ChargingInvalidQR("QR payload is missing the pole id")
    query = parse_qs(parts.query, keep_blank_values=False)
    sig_values = query.get("sig")
    if not sig_values or not sig_values[0]:
        raise ChargingInvalidQR("QR payload is missing the 'sig' parameter")
    return pole_id, sig_values[0]


def verify_qr_payload(payload: str) -> str:
    """Verify a scanned QR payload and return the pole id.

    Args:
        payload: The raw scanned string from the QR code.

    Returns:
        The pole id (uuid string) on success.

    Raises:
        ChargingInvalidQR: If the payload is malformed or the signature is
            missing/incorrect.
    """
    pole_id, signature = parse_qr(payload)
    expected = compute_signature(pole_id)
    if not hmac.compare_digest(expected.lower(), signature.lower()):
        raise ChargingInvalidQR(
            "QR signature does not match",
            details={"pole_id": pole_id},
        )
    return pole_id


__all__ = ["compute_signature", "parse_qr", "verify_qr_payload"]
