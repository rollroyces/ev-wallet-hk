"""Authentication router — ``/api/v1/auth/*``.

Endpoints (per ``docs/ARCHITECTURE.md``):

* ``POST /api/v1/auth/login`` — email + password (stub for now; the
  full password flow is wired in Agent A's eventual phase; here we
  raise a 501-ish schema error so callers know the gap is intentional).
* ``POST /api/v1/auth/apple`` — Apple identity token.
* ``POST /api/v1/auth/google`` — Google ID token.
* ``GET  /api/v1/auth/me`` — current user + wallet summary.

The Apple and Google handlers perform real verification; on success they
upsert the user, social_account, and wallet rows in a single transaction
and return ``{access_token, expires_at, user}`` — the canonical envelope
from the architecture contract.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.auth.apple import verify_apple_identity_token
from evwallet.auth.deps import current_user
from evwallet.auth.google import verify_google_id_token
from evwallet.auth.jwt import encode_jwt
from evwallet.config import get_settings
from evwallet.db.models import SocialAccount, User, Wallet
from evwallet.db.session import get_db
from evwallet.errors import SchemaValidationError

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Email + password login request body."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class AppleLoginRequest(BaseModel):
    """Apple Sign-In request body."""

    identity_token: str = Field(min_length=10, max_length=4096)
    authorization_code: str | None = Field(default=None, max_length=1024)
    full_name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None


class GoogleLoginRequest(BaseModel):
    """Google Sign-In request body."""

    id_token: str = Field(min_length=10, max_length=4096)


class UserResponse(BaseModel):
    """Public user shape."""

    id: uuid.UUID
    email: str | None
    phone_e164: str | None
    display_name: str
    locale: str
    is_admin: bool
    created_at: datetime


class WalletSummaryResponse(BaseModel):
    """Subset of wallet fields exposed by ``/auth/me`` and the mobile/web
    ``me()`` API."""

    available_hkd: Decimal
    reserved_hkd: Decimal
    currency: str


class SessionResponse(BaseModel):
    """Canonical response from ``/auth/login``, ``/auth/apple``,
    ``/auth/google``."""

    access_token: str
    expires_at: int  # unix seconds
    user: UserResponse


class MeResponse(BaseModel):
    """Response from ``GET /api/v1/auth/me``."""

    user: UserResponse
    wallet: WalletSummaryResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_to_response(user: User) -> UserResponse:
    """Build a :class:`UserResponse` from a :class:`User` row."""
    return UserResponse(
        id=user.id,
        email=user.email,
        phone_e164=user.phone_e164,
        display_name=user.display_name,
        locale=user.locale,
        is_admin=user.is_admin,
        created_at=user.created_at,
    )


def _wallet_to_summary(wallet: Wallet) -> WalletSummaryResponse:
    """Build a :class:`WalletSummaryResponse` from a :class:`Wallet` row."""
    return WalletSummaryResponse(
        available_hkd=wallet.available_credits,
        reserved_hkd=wallet.reserved_credits,
        currency=wallet.currency,
    )


async def _upsert_oauth_user(
    db: AsyncSession,
    *,
    provider: str,
    provider_subject: str,
    email: str | None,
    display_name: str,
) -> User:
    """Find or create the user + social_account + wallet triple.

    Idempotent on ``(provider, provider_subject)`` — the unique constraint
    on ``social_accounts`` means concurrent first-login collisions are
    serialised by Postgres; we re-read on ``IntegrityError`` and return
    the existing user.

    Args:
        db: An open async session.
        provider: ``"apple"`` / ``"google"`` / ``"email"``.
        provider_subject: The provider's stable user id (or the email
            itself for the ``email`` provider).
        email: Email to attach to the user row (may be ``None``).
        display_name: Human-readable name to seed the user with.

    Returns:
        The :class:`User` row (existing or newly-created), with the wallet
        eagerly loaded.
    """
    from sqlalchemy.exc import IntegrityError  # local import keeps top tidy

    result = await db.execute(
        select(SocialAccount, User)
        .join(User, SocialAccount.user_id == User.id)
        .where(
            SocialAccount.provider == provider,
            SocialAccount.provider_subject == provider_subject,
        )
    )
    row = result.first()
    if row is not None:
        _existing_social, existing_user = row
        _log.info("auth upsert hit provider=%s sub=%s", provider, provider_subject[:8])
        # Backfill email / display_name if the provider later reveals more
        # than we recorded on first sign-in. Apple in particular returns
        # the email only on the first sign-in for some flows, and only
        # echoes a name the very first time; subsequent sign-ins may
        # carry an email we hadn't recorded yet.
        new_email: str | None = None
        new_display: str | None = None
        if email and not existing_user.email:
            existing_user.email = email
            existing_user.updated_at = datetime.now(tz=UTC)
            new_email = email
        if display_name and not existing_user.display_name:
            existing_user.display_name = display_name
            existing_user.updated_at = datetime.now(tz=UTC)
            new_display = display_name
        if new_email or new_display:
            await db.commit()
            await db.refresh(existing_user)
        return existing_user

    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name=display_name or "",
        locale="zh-Hant",
        is_active=True,
        is_admin=False,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # Concurrent insert — re-read.
        result = await db.execute(
            select(User)
            .join(SocialAccount, SocialAccount.user_id == User.id)
            .where(
                SocialAccount.provider == provider,
                SocialAccount.provider_subject == provider_subject,
            )
        )
        return result.scalar_one()

    social = SocialAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        provider=provider,
        provider_subject=provider_subject,
        email_at_provider=email,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    db.add(social)

    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=user.id,
        available_credits=Decimal("0"),
        reserved_credits=Decimal("0"),
        currency="HKD",
        version=0,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    db.add(wallet)
    await db.commit()
    _log.info("auth upsert created user_id=%s provider=%s", user.id, provider)
    return user


async def _load_wallet(db: AsyncSession, user_id: uuid.UUID) -> Wallet:
    """Return the user's wallet, creating an empty one if missing."""
    result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
    wallet = result.scalar_one_or_none()
    if wallet is None:
        wallet = Wallet(
            id=uuid.uuid4(),
            user_id=user_id,
            available_credits=Decimal("0"),
            reserved_credits=Decimal("0"),
            currency="HKD",
            version=0,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        db.add(wallet)
        await db.commit()
    return wallet


def _make_session(user: User) -> SessionResponse:
    """Mint a JWT and return the canonical session envelope."""
    token, exp = encode_jwt(user.id)
    return SessionResponse(access_token=token, expires_at=exp, user=_user_to_response(user))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Email + password login (DEV-ONLY shortcut when EVW_DEV_LOGIN=true)",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """DEV-ONLY login shortcut for local web UI testing.

    The production credential flow (bcrypt verify, lockout policy, password
    reset, etc.) is a separate phase. Until that's built, this endpoint:
      - When Settings.dev_login is True (env: EVW_DEV_LOGIN=true), looks
        up a user by email (auto-creating if missing) and returns a
        session. The ``password`` field is ignored — it's accepted only
        to match the API contract.
      - When dev_login is False, raises AUTH_LOGIN_NOT_IMPLEMENTED as
        before (production-safe default).

    DELETE THIS HANDLER before deploying to production. The flag check
    exists for defence-in-depth, but the endpoint should not ship.
    """
    settings = get_settings()
    if not settings.dev_login:
        raise SchemaValidationError(
            "email/password login is not yet implemented",
            details={"code": "AUTH_LOGIN_NOT_IMPLEMENTED"},
        )

    # Auto-create the user on first login (test environment only).
    user = (
        await db.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if user is None:
        from evwallet.db.models import Wallet  # local import to avoid cycle

        user = User(
            email=body.email.lower(),
            display_name=body.email.split("@", 1)[0],
        )
        db.add(user)
        await db.flush()
        wallet = Wallet(user_id=user.id)
        db.add(wallet)
        await db.commit()
        await db.refresh(user)

    return _make_session(user)


@router.post(
    "/apple",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in with Apple",
)
async def login_apple(
    body: AppleLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Verify an Apple identity token, upsert the user, return a session.

    Apple only returns the user's display name on the FIRST sign-in; clients
    are expected to forward that name in ``full_name`` on the very first
    /auth/apple call so the row can be populated. The verifier emits a
    typed :class:`~evwallet.auth.apple.AppleIdentityClaims` and the
    handler maps verifier failures to a canonical 401
    ``AUTH_APPLE_TOKEN_INVALID`` envelope.
    """
    claims = await verify_apple_identity_token(body.identity_token)
    provider_subject: str = claims.apple_sub
    email_claim: str | None = claims.email
    # Apple only returns the user's name on the FIRST sign-in; clients
    # pass it through so we can populate the row.
    display_name = body.full_name or email_claim or ""
    user = await _upsert_oauth_user(
        db,
        provider="apple",
        provider_subject=provider_subject,
        email=body.email or email_claim,
        display_name=display_name,
    )
    return _make_session(user)


@router.post(
    "/google",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in with Google",
)
async def login_google(
    body: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Verify a Google ID token, upsert the user, return a session.

    The verifier emits a typed :class:`~evwallet.auth.google.GoogleIdentityClaims`
    and the handler maps verifier failures to a canonical 401
    ``AUTH_GOOGLE_TOKEN_INVALID`` envelope.
    """
    claims = verify_google_id_token(body.id_token)
    provider_subject: str = claims.google_sub
    email_claim: str | None = claims.email
    display_name: str = claims.name or email_claim or ""
    user = await _upsert_oauth_user(
        db,
        provider="google",
        provider_subject=provider_subject,
        email=email_claim,
        display_name=display_name,
    )
    return _make_session(user)


@router.get(
    "/me",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    summary="Current user + wallet summary",
)
async def me(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """Return the authenticated user + a wallet summary."""
    wallet = await _load_wallet(db, user.id)
    return MeResponse(user=_user_to_response(user), wallet=_wallet_to_summary(wallet))


__all__ = ["router"]
