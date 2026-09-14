"""IDPError hierarchy and canonical error envelope.

STUB: agent A owns this file in production.
"""

from __future__ import annotations

from typing import Any


class IDPError(Exception):
    """Base for all EV Wallet domain errors.

    Attributes:
        code: SCREAMING_SNAKE_CASE error code (per ARCHITECTURE.md).
        status: HTTP status code (default 400).
        details: Optional dict for debugging context (never PII).
    """

    code: str = "INTERNAL_ERROR"
    status: int = 400

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details or {}

    def to_envelope(self, trace_id: str | None = None) -> dict[str, Any]:
        """Return canonical error envelope."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "trace_id": trace_id,
            }
        }


class WalletError(IDPError):
    """Base for wallet domain errors."""

    code: str = "WALLET_ERROR"
    status: int = 400


class InsufficientFundsError(WalletError):
    """Raised when a reserve would drive available < 0 or reserved > available."""

    code: str = "WALLET_INSUFFICIENT_FUNDS"
    status: int = 409

    def __init__(
        self,
        message: str = "Insufficient funds for this operation",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class WalletLedgerIntegrityError(WalletError):
    """Raised when SUM(amount) over a txn != 0."""

    code: str = "WALLET_LEDGER_INTEGRITY_ERROR"
    status: int = 500


class WalletReconciliationError(WalletError):
    """Raised when wallet row diverges from the journal."""

    code: str = "WALLET_RECONCILIATION_ERROR"
    status: int = 500


class WalletNotFoundError(WalletError):
    code: str = "WALLET_NOT_FOUND"
    status: int = 404


class ConflictError(IDPError):
    """Resource already exists (e.g. duplicate email on signup)."""

    code: str = "CONFLICT"
    status: int = 409


class PaymentError(IDPError):
    """Base for payments domain errors."""

    code: str = "PAYMENT_ERROR"
    status: int = 400


class StripeSignatureError(PaymentError):
    code: str = "PAYMENT_STRIPE_SIGNATURE_INVALID"
    status: int = 400


class StripeIntentError(PaymentError):
    code: str = "PAYMENT_STRIPE_INTENT_INVALID"
    status: int = 400


class ApplePayValidationError(PaymentError):
    code: str = "PAYMENT_APPLE_PAY_INVALID"
    status: int = 400


class GooglePayValidationError(PaymentError):
    code: str = "PAYMENT_GOOGLE_PAY_INVALID"
    status: int = 400


class AuthError(IDPError):
    code: str = "AUTH_ERROR"
    status: int = 401


class NotAuthenticatedError(AuthError):
    code: str = "AUTH_NOT_AUTHENTICATED"
    status: int = 401


class ValidationError(IDPError):
    code = "VALIDATION_ERROR"
    status: int = 422  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Domain errors — appended by Agent C (charging + stations scope) so that
# the canonical CHARGING_*/STATION_* exception classes live alongside the
# other agents' domains in a single import path. They follow the IDPError
# contract above: ``code`` (SCREAMING_SNAKE_CASE), ``status`` (HTTP int),
# ``details`` (debug context), and ``to_envelope(trace_id)``.
# ---------------------------------------------------------------------------


class StationError(IDPError):
    code = "STATION_ERROR"
    status: int = 400  # type: ignore[assignment]


class StationNotFound(StationError):
    code = "STATION_NOT_FOUND"
    status: int = 404  # type: ignore[assignment]


class PoleNotFound(StationError):
    code = "POLE_NOT_FOUND"
    status: int = 404  # type: ignore[assignment]


class PoleUnavailable(StationError):
    code = "POLE_UNAVAILABLE"
    status: int = 409  # type: ignore[assignment]


class ChargingError(IDPError):
    code = "CHARGING_ERROR"
    status: int = 400  # type: ignore[assignment]


class ChargingSessionNotFound(ChargingError):
    code = "CHARGING_SESSION_NOT_FOUND"
    status: int = 404  # type: ignore[assignment]


class ChargingSessionForbidden(ChargingError):
    code = "CHARGING_SESSION_FORBIDDEN"
    status: int = 403  # type: ignore[assignment]


class ChargingInvalidQR(ChargingError):
    code = "CHARGING_INVALID_QR"
    status: int = 400  # type: ignore[assignment]


class ChargingPreauthExceeded(ChargingError):
    code = "CHARGING_PREAUTH_EXCEEDED"
    status: int = 400  # type: ignore[assignment]


class ChargingInvalidState(ChargingError):
    code = "CHARGING_INVALID_STATE"
    status: int = 409  # type: ignore[assignment]


class AuthTokenInvalid(AuthError):
    code = "AUTH_TOKEN_INVALID"
    status: int = 401  # type: ignore[assignment]


class AuthForbidden(AuthError):
    code = "AUTH_FORBIDDEN"
    status: int = 403  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Agent A core errors — the canonical 5 the production-readiness-hardening
# skill mandates. They live at the top of the hierarchy alongside the domain
# errors appended by Agents B/C above. All inherit from IDPError so
# ``except IDPError`` catches them but ``except ValueError`` does not.
# ---------------------------------------------------------------------------


class ConfigurationError(IDPError):
    """Bad or missing configuration at startup.

    Raised from :class:`evwallet.config.Settings` validators when an env
    var is missing or out-of-range. Crashes the process at boot.
    """

    code: str = "CONFIGURATION_ERROR"
    status: int = 500


class RateLimitedError(IDPError):
    """Caller exceeded a rate limit.

    Surfaces as HTTP 429.
    """

    code: str = "RATE_LIMITED"
    status: int = 429


class BackendUnavailableError(IDPError):
    """External dependency (DB, Redis, JWKS) is unreachable.

    Surfaces as HTTP 503.
    """

    code: str = "BACKEND_UNAVAILABLE"
    status: int = 503


class StorageError(IDPError):
    """Persistence layer rejected a write (constraint, corruption, etc.).

    Surfaces as HTTP 500 by default.
    """

    code: str = "STORAGE_ERROR"
    status: int = 500


class SchemaValidationError(IDPError):
    """Input shape does not match the expected schema.

    Surfaces as HTTP 422.
    """

    code: str = "SCHEMA_VALIDATION_ERROR"
    status: int = 422


def is_idp_error(exc: object) -> bool:
    """Return ``True`` if *exc* is an :class:`IDPError` instance.

    Identity-based check, NOT ``isinstance(exc, IDPError)`` would still work
    here, but the helper exists to give callers an explicit, well-typed
    predicate that is easy to import without dragging the class into their
    namespace.

    Args:
        exc: Any Python object (typically an exception).

    Returns:
        ``True`` only when *exc* is an ``IDPError`` subclass instance.
        Python builtins like ``ValueError`` return ``False``.
    """
    return isinstance(exc, IDPError)
