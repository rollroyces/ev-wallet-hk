"""Manual smoke script — exercises the ledger end-to-end against real Postgres.

Set env vars (see .env.example) and run:

    .venv/bin/python scripts/smoke_wallet_ledger.py

Prints a numbered trace of every state transition so the ledger's
correctness is auditable by eye.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Apply the same SQLite-compat shims conftest does (Postgres doesn't need them
# but the constants in evwallet.errors require Settings to load cleanly).
os.environ.setdefault("EVW_ENV", "smoke")
os.environ.setdefault("EVW_JWT_SECRET", "zaq1xsw2cde3vfr4bgt5nhy6ujm7ik8lpq9a0sdfghjklqwertyuiop")
os.environ.setdefault("EVW_APPLE_BUNDLE_ID", "com.evwallet.hk")
os.environ.setdefault("EVW_GOOGLE_CLIENT_ID", "smoke-test")
os.environ.setdefault("EVW_PREAUTH_MAX_HKD", "5000.00")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from evwallet.config import get_settings  # noqa: E402
from evwallet.db.models import Base, User, Wallet  # noqa: E402
from evwallet.db.session import reset_engine_for_tests  # noqa: E402
from evwallet.errors import InsufficientFundsError  # noqa: E402
from evwallet.wallet import ledger, reservation  # noqa: E402


def hr(title: str) -> None:
    print(f"\n=== {title} ===")


async def main() -> int:
    settings = get_settings()
    url = settings.async_database_url
    print(f"[smoke] DB: {url.split('@')[-1]}")  # hide credentials
    print(f"[smoke] env: {settings.env}")

    reset_engine_for_tests()
    engine = create_async_engine(url, future=True)

    # Fresh schema (drops + recreates all tables)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with SessionLocal() as db:
        hr("[01] Seed user + wallet (HKD 1000.00)")
        user = User(
            id=uuid.uuid4(),
            email=f"smoke-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Smoke",
            is_active=True,
        )
        db.add(user)
        wallet = Wallet(
            id=uuid.uuid4(),
            user_id=user.id,
            available_credits=Decimal("1000.00"),
            reserved_credits=Decimal("0"),
            currency="HKD",
            version=0,
        )
        db.add(wallet)
        await db.commit()

        # Journal the opening balance so reconciliation has a complete ledger.
        await ledger.post_transaction(
            db,
            wallet.id,
            kind="topup",
            entries=[
                ("topup_debit", "external", Decimal("-1000")),
                ("topup_debit", "available", Decimal("1000")),
            ],
            external_ref=f"opening-{wallet.id}",
        )
        await db.commit()

        print(f"  wallet.id = {wallet.id}")
        print(f"  balances = available={wallet.available_credits} reserved={wallet.reserved_credits}")

        hr("[02] Reserve HKD 120 for a charging session (pre-auth)")
        sess_id = uuid.uuid4()
        reserve_txn, _ = await reservation.reserve(
            db, wallet.id, Decimal("120.00"), session_id=sess_id
        )
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == wallet.id))).scalar_one()
        print(f"  txn.id  = {reserve_txn.id}")
        print(f"  balances = available={w.available_credits} reserved={w.reserved_credits}")

        hr("[03] Settle 12.5 kWh @ HKD 8.40/kWh (HKD 105.00) + release HKD 15 remainder")
        settle_txn, release_txn = await reservation.end_session_settle(
            db, wallet.id, Decimal("12.5"), Decimal("8.40"), session_id=sess_id
        )
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == wallet.id))).scalar_one()
        print(f"  settle.id  = {settle_txn.id}")
        print(f"  release.id = {release_txn and release_txn.id}")
        print(f"  balances = available={w.available_credits} reserved={w.reserved_credits}")

        hr("[04] Idempotent topup via post_transaction (same external_ref returns same txn)")
        ext_ref = f"smoke-{uuid.uuid4().hex[:8]}"
        first_txn = await ledger.post_transaction(
            db,
            wallet.id,
            kind="topup",
            entries=[
                ("topup_debit", "external", Decimal("-50")),
                ("topup_debit", "available", Decimal("50")),
            ],
            external_ref=ext_ref,
        )
        await db.commit()
        second_txn = await ledger.post_transaction(
            db,
            wallet.id,
            kind="topup",
            entries=[
                ("topup_debit", "external", Decimal("-50")),
                ("topup_debit", "available", Decimal("50")),
            ],
            external_ref=ext_ref,
        )
        await db.commit()
        w = (await db.execute(select(Wallet).where(Wallet.id == wallet.id))).scalar_one()
        same = first_txn.id == second_txn.id
        print(f"  first.id  = {first_txn.id}")
        print(f"  second.id = {second_txn.id}")
        print(f"  idempotent = {same}")
        print(f"  balances = available={w.available_credits} reserved={w.reserved_credits}")
        assert same, "idempotent topup must return same txn"

        hr("[05] Try to reserve HKD 9999.99 (insufficient funds)")
        try:
            await reservation.reserve(db, wallet.id, Decimal("4999.99"))
            print("  ✗ FAIL — should have raised")
            return 1
        except Exception as exc:
            from evwallet.errors import IDPError
            if isinstance(exc, IDPError):
                print(f"  ✓ raised: {type(exc).__name__} code={exc.code} ({exc.details})")
            else:
                print(f"  ✓ raised: {type(exc).__name__} ({exc})")

        hr("[06] Reconcile wallet row vs journal (must be empty)")
        await db.commit()
        deltas = await ledger.reconcile(db, wallet.id)
        print(f"  deltas = {deltas}")
        assert deltas == [], "reconciliation must show no drift"

        hr("[07] Tamper with wallet row; reconcile MUST detect the drift")
        w.available_credits = Decimal("0.01")
        await db.commit()
        await db.refresh(w)
        deltas = await ledger.reconcile(db, wallet.id)
        print(f"  deltas = {deltas}")
        assert deltas, "reconciliation must detect the tamper"

    await engine.dispose()

    hr("[smoke] ✓ All assertions passed — ledger is correct on this DB")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))