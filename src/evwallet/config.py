"""Application configuration for EV Wallet HK.

Settings are loaded from environment variables prefixed ``EVW_`` and validated
at startup. Any missing or out-of-range required variable raises
:class:`~evwallet.errors.ConfigurationError`, crashing the process before the
HTTP server binds.

This module is the ONLY place in the package that reads ``os.environ``
directly (per architecture contract, ``docs/ARCHITECTURE.md``).
"""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from evwallet.errors import ConfigurationError


class Settings(BaseSettings):
    """Validated, frozen-by-convention application settings.

    Attributes:
        env: Deployment environment identifier (``production``/``staging``/
            ``test``). Drives CORS and log verbosity heuristics.
        log_level: Python logging level name (``DEBUG``/``INFO``/...).
        log_format: ``human`` for the local console or ``json`` for the
            production aggregator.
        api_host: Interface for uvicorn to bind.
        api_port: TCP port for uvicorn.
        workers: Number of uvicorn worker processes.
        jwt_secret: HS256 signing key for JWTs. MUST be at least 32 bytes.
        jwt_expiry_hours: Lifetime of issued tokens (default 720h = 30 days).
        trusted_proxies: Comma-separated CIDR list for X-Forwarded-For trust.
        postgres_host / postgres_port / postgres_user / postgres_password /
            postgres_db: Postgres connection parameters.
        redis_host / redis_port / redis_password: Redis connection parameters.
        preauth_max_hkd: Maximum HKD allowed per charging session pre-auth.
        stripe_secret_key / stripe_webhook_secret / apple_pay_merchant_id /
            google_pay_merchant_id: Optional payment integration secrets.
        cors_origins: Comma-separated allow-list of HTTP origins for CORS.
        database_url: Optional full DSN (skips per-field assembly).
            Provided for tests / sandboxed dev only.
    """

    model_config = SettingsConfigDict(
        env_prefix="EVW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = "production"
    log_level: str = "INFO"
    log_format: str = "human"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    workers: int = 3

    jwt_secret: str = Field(default="dev_secret_change_me_min_32_characters_long_xx")
    jwt_expiry_hours: int = 720
    trusted_proxies: str = ""

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "evwallet"
    postgres_password: str = "evwallet"
    postgres_db: str = "evwallet"

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = "evwallet"

    preauth_max_hkd: Decimal = Decimal("500.00")

    # HMAC secret for QR code signatures. In production set this separately
    # from jwt_secret so a JWT compromise doesn't also let an attacker forge
    # valid QR codes. Defaults to jwt_secret in dev for convenience.
    qr_hmac_secret: str | None = None

    # Shared bearer token used by the n8n ingestion workflows (and any
    # other service-to-service caller). REQUIRED in production; if unset,
    # /api/v1/internal/* endpoints return 503 except in 'development' env.
    internal_token: str | None = None

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    apple_pay_merchant_id: str | None = None
    google_pay_merchant_id: str | None = None

    cors_origins: str = ""
    database_url: str | None = None
    redis_url: str | None = None

    # ---- Validators -----------------------------------------------------

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Reject unknown log level names early."""
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(
                f"EVW_LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got {value!r}"
            )
        return normalized

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, value: str) -> str:
        """Reject anything other than ``human`` / ``json``."""
        normalized = value.strip().lower()
        if normalized not in {"human", "json"}:
            raise ConfigurationError(f"EVW_LOG_FORMAT must be 'human' or 'json', got {value!r}")
        return normalized

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, value: str) -> str:
        """Reject weak / placeholder secrets at startup.

        Threshold is 32 bytes (the contract's hard minimum). Common
        placeholders like ``change_me`` are also rejected so a forgotten
        ``.env`` rewrite fails loudly instead of silently signing tokens
        with a guessable key.
        """
        if len(value) < 32:
            raise ConfigurationError(
                f"EVW_JWT_SECRET must be at least 32 bytes long (got len={len(value)})"
            )
        lowered = value.lower()
        for placeholder in ("change_me", "changeme", "secret", "test_secret"):
            if placeholder in lowered:
                raise ConfigurationError(
                    "EVW_JWT_SECRET looks like a placeholder; generate a real "
                    "secret with `python -c 'import secrets; print(secrets.token_urlsafe(64))'`"
                )
        return value

    @field_validator("postgres_user", "postgres_password", "postgres_db")
    @classmethod
    def _validate_required_db(cls, value: str) -> str:
        """Refuse to start with empty Postgres credentials."""
        if not value.strip():
            raise ConfigurationError(
                "EVW_POSTGRES_USER / EVW_POSTGRES_PASSWORD / EVW_POSTGRES_DB must all be set"
            )
        return value

    @field_validator("redis_password")
    @classmethod
    def _validate_redis_password(cls, value: str) -> str:
        """Refuse to start with empty Redis password."""
        if not value.strip():
            raise ConfigurationError(
                "EVW_REDIS_PASSWORD must be set (no default — production must authenticate)"
            )
        return value

    @field_validator("preauth_max_hkd")
    @classmethod
    def _validate_preauth_max(cls, value: Decimal) -> Decimal:
        """Pre-auth cap must be positive and within sane bounds."""
        if value <= 0:
            raise ConfigurationError(f"EVW_PREAUTH_MAX_HKD must be > 0, got {value}")
        if value > Decimal("10000"):
            raise ConfigurationError(
                f"EVW_PREAUTH_MAX_HKD seems unreasonably high ({value}); "
                "cap at 10000 unless you have a specific reason"
            )
        return value

    @field_validator("api_port", "postgres_port", "redis_port")
    @classmethod
    def _validate_ports(cls, value: int) -> int:
        """Reject ports outside the kernel-allocated range."""
        if not 1 <= value <= 65535:
            raise ConfigurationError(f"Port must be in 1..65535, got {value}")
        return value

    @field_validator("workers")
    @classmethod
    def _validate_workers(cls, value: int) -> int:
        """Workers must be at least 1, no more than 32 (sanity)."""
        if value < 1:
            raise ConfigurationError(f"EVW_WORKERS must be >= 1, got {value}")
        if value > 32:
            raise ConfigurationError(f"EVW_WORKERS={value} looks unreasonable; max 32")
        return value

    @field_validator("jwt_expiry_hours")
    @classmethod
    def _validate_jwt_expiry(cls, value: int) -> int:
        """JWT lifetime must be 1..8760 hours (1 year upper bound)."""
        if value < 1:
            raise ConfigurationError(f"EVW_JWT_EXPIRY_HOURS must be >= 1, got {value}")
        if value > 8760:
            raise ConfigurationError(f"EVW_JWT_EXPIRY_HOURS={value} exceeds 1 year — too long")
        return value

    # ---- Derived helpers ---------------------------------------------------

    @property
    def async_database_url(self) -> str:
        """Async DSN for asyncpg.

        Honours ``EVW_DATABASE_URL`` (env or ``database_url=``); otherwise
        builds from the discrete fields. This is the canonical name —
        sibling modules that imported ``settings.async_database_url``
        continue to work.
        """
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Backwards-compat alias so the original ARCHITECTURE.md-named property
    # also resolves.
    @property
    def database_url_str(self) -> str:
        """Alias for :attr:`async_database_url` for callers that expected
        the original ``database_url`` property name.
        """
        return self.async_database_url

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse ``EVW_CORS_ORIGINS`` into a list, dropping empties."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_list(self) -> list[str]:
        """Parse ``EVW_TRUSTED_PROXIES`` into a list of CIDR strings."""
        return [p.strip() for p in self.trusted_proxies.split(",") if p.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` (lazy, cached).

    Reads ``os.environ`` indirectly through Pydantic Settings. Tests can
    call :func:`reset_settings_cache` to force a re-read after monkey-patching
    the environment.
    """
    try:
        return Settings()
    except Exception as exc:
        raise ConfigurationError(f"Configuration load failed: {exc}") from exc


def reset_settings_cache() -> None:
    """Clear the :func:`get_settings` cache (testing only)."""
    get_settings.cache_clear()


def load_settings_from_env(env: dict[str, str] | None = None) -> Settings:
    """Build a Settings from a custom env mapping (no module cache touched).

    Args:
        env: Mapping of env-var names → values. ``None`` means read the
            process environment (same as :func:`get_settings`).

    Returns:
        A validated :class:`Settings`.

    Raises:
        ConfigurationError: If any required field is missing or invalid.
    """
    saved: dict[str, str] = {}
    injected: list[str] = []
    if env is not None:
        for key, value in env.items():
            saved[key] = os.environ.get(key, "")
            os.environ[key] = value
            injected.append(key)
    try:
        try:
            return Settings()
        except Exception as exc:
            raise ConfigurationError(f"Configuration load failed: {exc}") from exc
    finally:
        for key in injected:
            if saved[key] == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved[key]


def settings_as_dict(settings: Settings) -> dict[str, Any]:
    """Serialize a Settings for logging (redacting secrets).

    Used by the FastAPI startup log line — secrets are masked so they
    don't end up in log aggregators.
    """
    data = settings.model_dump()
    for key in (
        "jwt_secret",
        "postgres_password",
        "redis_password",
        "stripe_secret_key",
        "stripe_webhook_secret",
    ):
        if data.get(key):
            data[key] = "***redacted***"
    return data


__all__ = [
    "Settings",
    "get_settings",
    "load_settings_from_env",
    "reset_settings_cache",
    "settings_as_dict",
]
