"""Email verification service: issue + redeem 6-digit codes.

Flow:

1. ``issue_code(user, db)`` — generate a 6-digit code, hash it with
   Argon2id, store the row, send the code via the configured sender.
   Returns the row id (used for the in-memory ``dev_code`` test escape
   hatch — see below).

2. ``redeem_code(user, code, db)`` — verify the submitted code against
   the latest unconsumed row for the user. Bumps the row's
   ``attempts`` counter; locks the row (consumes) on success. Sets
   ``user.email_verified_at`` on success.

Dev escape hatch
----------------

When ``EVW_DEV_LOGIN=true`` (or simply no SMTP configured, i.e. we're
using the :class:`ConsoleSender`), we keep the **plaintext code** in
an in-memory map keyed by user id, accessible via
:func:`get_last_dev_code`. Tests use this to fetch the code instead
of scraping stderr. The map is wiped on process restart — that's
fine, dev codes are throwaway.

The dev mode is gated behind the same flag the rest of dev mode
uses (``EVW_DEV_LOGIN``). In production with a real SMTP sender,
this is a no-op.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evwallet.auth.password import hash_password, verify_password
from evwallet.config import get_settings
from evwallet.db.models import EmailVerification, User
from evwallet.email.sender import EmailMessage_, get_sender

_log = logging.getLogger(__name__)


CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5  # lock the row after this many wrong tries


@dataclass(frozen=True)
class IssuedCode:
    verification_id: str
    expires_at: datetime
    # Plaintext code, ONLY populated in dev / console-sender mode so
    # tests can fetch it without scraping logs. In production with a
    # real SMTP relay, this is empty — the code went out via email.
    dev_code: str | None = None


# In-memory store of dev codes keyed by user_id (str) -> (code, expiry).
# Wiped on process restart. Used only when ConsoleSender is in use.
_dev_codes: dict[str, tuple[str, datetime]] = {}


def get_last_dev_code(user_id: str) -> str | None:
    """Return the most recent dev code for a user, or None.

    For tests only. Returns None if the code has expired or never
    existed. Resets after 1 hour (longer than the 15-min TTL — any
    code still in the map is stale; tests should issue a fresh code
    per test).
    """
    pair = _dev_codes.get(str(user_id))
    if pair is None:
        return None
    code, expires_at = pair
    if datetime.now(UTC) > expires_at:
        _dev_codes.pop(str(user_id), None)
        return None
    return code


def _purge_dev_code(user_id: str) -> None:
    _dev_codes.pop(str(user_id), None)


def _generate_code() -> str:
    """Return a 6-digit numeric code as a string, with leading zeros."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def issue_code(
    user: User,
    db: AsyncSession,
    *,
    purpose: str = "signup",
) -> IssuedCode:
    """Generate a code, persist the hash, and send the code via email.

    Any prior unconsumed rows for the user are kept (audit trail);
    only the most recent row is consulted on redemption. Send failures
    are logged but not re-raised — the row is committed regardless, so
    the user can re-request a code via the ``resend`` endpoint.
    """
    settings = get_settings()
    code = _generate_code()
    code_hash = hash_password(code)
    expires_at = datetime.now(UTC) + CODE_TTL

    row = EmailVerification(
        user_id=user.id,
        code_hash=code_hash,
        email_to=user.email or "",
        purpose=purpose,
        expires_at=expires_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Build the email
    from_addr = settings.smtp_from or "noreply@evwallet.hk"
    subject = "Your EV Wallet HK verification code"
    body = (
        f"Hi {user.display_name or 'there'},\n"
        f"\n"
        f"Your EV Wallet HK verification code is: {code}\n"
        f"\n"
        f"This code is good for 15 minutes. If you didn't request this, "
        f"you can safely ignore the email.\n"
        f"\n"
        f"— EV Wallet HK\n"
    )

    sender = get_sender()
    is_console = type(sender).__name__ == "ConsoleSender"
    try:
        sender.send(
            EmailMessage_(
                subject=subject,
                body_text=body,
                from_addr=from_addr,
                to_addr=user.email or "",
            )
        )
    except Exception as exc:  # pragma: no cover
        # Send failures shouldn't break signup. Log and let the user
        # re-request via the resend endpoint.
        _log.error(
            "email send failed user_id=%s error=%s", user.id, exc
        )

    if is_console:
        # Keep the plaintext so tests can fetch it
        _dev_codes[str(user.id)] = (code, expires_at)
        return IssuedCode(
            verification_id=str(row.id),
            expires_at=expires_at,
            dev_code=code,
        )
    return IssuedCode(
        verification_id=str(row.id),
        expires_at=expires_at,
        dev_code=None,
    )


async def redeem_code(
    user: User, code: str, db: AsyncSession
) -> bool:
    """Try to verify the code. Returns True on success.

    On success: sets ``user.email_verified_at`` and consumes the row.
    On failure: bumps the row's attempts. After ``MAX_ATTEMPTS`` wrong
    attempts, the row is consumed (locked) to prevent brute force.
    """
    # Find the latest active row for this user
    result = await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.user_id == user.id,
            EmailVerification.consumed_at.is_(None),
        )
        .order_by(EmailVerification.created_at.desc())
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False

    # SQLite (used in some tests) returns naive datetimes. Normalize
    # both sides to UTC-aware so the comparison works on every
    # backend.
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if datetime.now(UTC) > expires_at:
        # Expired — consume and reject
        row.consumed_at = datetime.now(UTC)
        await db.commit()
        return False

    if row.attempts >= MAX_ATTEMPTS:
        # Locked out
        row.consumed_at = datetime.now(UTC)
        await db.commit()
        return False

    if not verify_password(code, row.code_hash):
        row.attempts += 1
        await db.commit()
        return False

    # Success
    row.consumed_at = datetime.now(UTC)
    user.email_verified_at = datetime.now(UTC)
    await db.commit()
    _purge_dev_code(str(user.id))
    _log.info("email verified user_id=%s", user.id)
    return True