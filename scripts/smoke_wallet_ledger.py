"""Manual smoke script — exercises the ledger end-to-end.

Run with:
    PYTHONPATH=src .venv/bin/python scripts/smoke_wallet_ledger.py

Prints a numbered trace of every state transition so the ledger's
correctness is auditable by eye. Requires an empty SQLite database at
/tmp/ev_smoke.db (created on the fly).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal

os.environ.setdefault("EVW_DATABASE_URL", "sqlite+aiosqlite:////tmp/ev_smoke.db")
os.environ.setdefault("EVW_ENV", "smoke")
os.environ.setdefault("EVW_JWT_SECRET", "zaq1xsw2cde3vfr4bgt5nhy6ujm7ik8lpq9a0sdfghjklqwertyuiop")
os.environ.setdefault("EVW_PREAUTH_MAX_HKD", "500.00")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # for tests/

import tests._wallet_setup  # noqa: F401, E402  # patches JSONB + drops ARRAY tables

# The _wallet_setup module registers a Pool-level listener for
# create_function("gen_random_uuid"). It uses Pool.dispatch.connect which
# doesn't fire on async engines' pools in some SQLAlchemy versions. As a
# belt-and-braces fix, register a per-engine connect hook on every engine
# we create in this script.
from sqlalchemy import event as _sql_event  # noqa: E402

import contextlib  # noqa: E402

from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from evwallet.db.models import Base, User, Wallet  # noqa: E402
from evwallet.wallet import ledger as ledger_mod  # noqa: E402
from evwallet.wallet import reservation, topup  # noqa: E402


def _section(n: int, title: str) -> None:
    print(f"\n[{n:02d}] {title}")
    print("    " + "─" * 60)


def _line(label: str, *vals: object) -> None:
    parts = [f"{label}="]
    for v in vals:
        parts.append(str(v))
    print("    " + " ".join(parts))


async def main() -> int:
    # 1. Fresh DB
    _section(1, "Create fresh in-memory SQLite + schema")
    if os.path.exists("/tmp/ev_smoke.db"):
        os.unlink("/tmp/ev_smoke.db")
    engine = create_async_engine("sqlite+aiosqlite:////tmp/ev_smoke.db", future=True)

    # Belt-and-braces: also register the gen_random_uuid Python function
    # on every new connection from this engine.
    import uuid as _uuid

    @_sql_event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_con, _record):  # pragma: no cover
        if hasattr(dbapi_con, "create_function"):
            # Register under BOTH names: the compile target (py_uuid4,
            # via the GenericFunction we install in _wallet_setup) and the
            # production target (gen_random_uuid) for belt-and-braces.
            dbapi_con.create_function("py_uuid4", 0, lambda: str(_uuid.uuid4()))
            dbapi_con.create_function("gen_random_uuid", 0, lambda: str(_uuid.uuid4()))

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    _line("db", engine.url)

    async with SessionLocal() as s:
        # 2. Seed a user + wallet
        _section(2, "Create user + wallet")
        u = User(id=uuid.uuid4(), email=f"smoke-{uuid.uuid4().hex[:8]}@test.local", display_name="Smoke")
        s.add(u)
        await s.flush()
        w = Wallet(
            id=uuid.uuid4(),
            user_id=u.id,
            available_credits=Decimal("0"),
            reserved_credits=Decimal("0"),
            currency="HKD",
            version=0,
        )
        s.add(w)
        await s.commit()
        _line("user.id", u.id)
        _line("wallet.id", w.id)
        _line("initial", "available=0 reserved=0")

        # 3. Top-up HKD 500
        _section(3, "Top up HKD 500 via Stripe (stub)")
        txn = await topup.topup_stripe(
            s, w.id, Decimal("500.00"), stripe_payment_intent_id=f"pi_smoke_{uuid.uuid4().hex}"
        )
        await s.commit()
        avail, resv = await ledger_mod.get_balance(s, w.id)
        _line("topup.txn.id", txn.id)
        _line("balances", f"available={avail} reserved={resv}")
        assert avail == Decimal("500.0000")
        assert resv == Decimal("0")

        # 4. Reserve HKD 120 (start a charging session)
        _section(4, "Reserve HKD 120 (charging session pre-auth)")
        rsv, entries = await reservation.reserve(
            s, w.id, Decimal("120.00"), session_id="smoke-sess-1"
        )
        await s.commit()
        avail, resv = await ledger_mod.get_balance(s, w.id)
        _line("reserve.txn.id", rsv.id)
        _line("entries", len(entries))
        _line("balances", f"available={avail} reserved={resv}")
        assert avail == Decimal("380.0000")
        assert resv == Decimal("120.0000")

        # 5. Settle for 12.5 kWh @ HKD 8.40/kWh = HKD 105.00, release remainder HKD 15.00
        _section(5, "Settle 12.5 kWh @ HKD 8.40/kWh (HKD 105); release HKD 15 remainder")
        settle, release = await reservation.end_session_settle(
            s,
            w.id,
            final_kwh=Decimal("12.5"),
            rate_hkd_per_kwh=Decimal("8.40"),
            session_id="smoke-sess-1",
        )
        await s.commit()
        avail, resv = await ledger_mod.get_balance(s, w.id)
        _line("settle.txn.id", settle.id)
        _line("release.txn.id", release.id if release else "None")
        _line("balances", f"available={avail} reserved={resv}")
        assert avail == Decimal("395.0000")
        assert resv == Decimal("0")

        # 6. Idempotent re-top-up
        _section(6, "Idempotent top-up (same external_ref returns same txn)")
        pi_id = f"pi_smoke_idem_{uuid.uuid4().hex}"
        first = await topup.topup_stripe(
            s, w.id, Decimal("100.00"), stripe_payment_intent_id=pi_id
        )
        second = await topup.topup_stripe(
            s, w.id, Decimal("100.00"), stripe_payment_intent_id=pi_id
        )
        await s.commit()
        _line("first.txn.id", first.id)
        _line("second.txn.id", second.id)
        assert first.id == second.id, "idempotency violated"
        avail, resv = await ledger_mod.get_balance(s, w.id)
        _line("balances", f"available={avail} reserved={resv}")
        assert avail == Decimal("495.0000")

        # 7. Insufficient funds (try to reserve more than available, but
        # within the preauth_max_hkd cap of 500)
        _section(7, "Try to reserve HKD 499.99 (insufficient funds)")
        from evwallet.errors import InsufficientFundsError

        try:
            await reservation.reserve(
                s, w.id, Decimal("499.99"), session_id="smoke-overdraft"
            )
            print("    ERROR: expected InsufficientFundsError")
            return 1
        except InsufficientFundsError as e:
            print(f"    OK — raised: {e.code} ({e.details})")
        avail, resv = await ledger_mod.get_balance(s, w.id)
        _line("balances (unchanged)", f"available={avail} reserved={resv}")
        assert avail == Decimal("495.0000")

        # 8. Reconcile (should be empty — wallet matches journal)
        _section(8, "Reconcile wallet row vs journal (must be empty)")
        deltas = await ledger_mod.reconcile(s, w.id)
        _line("deltas", deltas)
        assert deltas == [], deltas
        print("    OK — wallet and journal are in sync")

        # 9. Tamper with the wallet row, reconcile detects it
        _section(9, "Tamper with wallet row; reconcile MUST detect the drift")
        from sqlalchemy import text

        await s.execute(
            text("UPDATE wallets SET available_credits = :v WHERE id = :i"),
            {"v": "494.99", "i": w.id.hex},
        )
        await s.commit()
        deltas = await ledger_mod.reconcile(s, w.id)
        _line("deltas", deltas)
        assert len(deltas) == 1
        assert deltas[0]["bucket"] == "available"
        assert Decimal(deltas[0]["delta"]) == Decimal("-0.0100")
        print(f"    OK — reconcile caught drift: {deltas[0]}")

    await engine.dispose()
    if os.path.exists("/tmp/ev_smoke.db"):
        os.unlink("/tmp/ev_smoke.db")

    print("\n" + "═" * 64)
    print("✓ Smoke test passed: ledger invariants hold across topup/reserve/")
    print("  settle/release/idempotency/reconciliation. Ready for Postgres.")
    print("═" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
