"""Critical ledger correctness tests — Agent B scope.

These tests are the financial contract — they exist to catch a class of bugs
that would otherwise lose real money:

    * SUM(amount) over a txn != 0  (INVARIANT I1)
    * available < 0 or reserved < 0 (INVARIANT I2)
    * row drift vs journal (INVARIANT I3)
    * non-idempotent topups (INVARIANT I4)
    * race conditions on concurrent reserves (INVARIANT I5)

The test suite uses SQLite-via-aiosqlite so it runs anywhere — no Postgres
required. ``tests/_wallet_setup.py`` mutates ``sqlalchemy.dialects.postgresql``
so JSONB compiles against SQLite, and drops Postgres-only tables (those with
ARRAY columns) from the metadata. The wallet tables themselves remain.
"""

# Import setup first — it patches globals and sets env vars.
from tests import _wallet_setup  # noqa: F401, E402

import asyncio
import uuid
from decimal import Decimal

import pytest
from hypothesis import (
    HealthCheck,
    given,
    settings as hyp_settings,
    strategies as st,
)
from sqlalchemy import select, text

from evwallet.errors import InsufficientFundsError, WalletLedgerIntegrityError
from evwallet.wallet import ledger as ledger_mod
from evwallet.wallet import reservation, topup
from evwallet.db.models import LedgerEntry, Wallet, WalletTransaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_wallet(db_session, available: Decimal = Decimal("0")):
    user, wallet = _wallet_setup.seed_user_with_wallet(db_session, available=available)
    await db_session.flush()
    return user, wallet


# ---------------------------------------------------------------------------
# 1) SUM(amount) over a transaction MUST equal zero.
# ---------------------------------------------------------------------------


async def test_post_transaction_balances_to_zero(db_session):
    user, wallet = await _make_wallet(db_session)
    txn = await ledger_mod.post_transaction(
        db_session,
        wallet.id,
        kind="topup",
        entries=[
            ("topup_debit", "external", Decimal("-100.00")),
            ("topup_debit", "available", Decimal("100.00")),
        ],
        external_ref="t-zero-1",
    )
    rows = (
        (
            await db_session.execute(
                select(LedgerEntry).where(LedgerEntry.txn_id == txn.id)
            )
        )
        .scalars()
        .all()
    )
    total = sum((r.amount for r in rows), Decimal("0"))
    assert total == Decimal("0"), f"entries sum to {total}, expected 0"
    assert len(rows) == 2


async def test_post_transaction_rejects_imbalanced_entries(db_session):
    user, wallet = await _make_wallet(db_session)
    with pytest.raises(WalletLedgerIntegrityError):
        await ledger_mod.post_transaction(
            db_session,
            wallet.id,
            kind="topup",
            entries=[
                ("topup_debit", "external", Decimal("-100.00")),
                ("topup_debit", "available", Decimal("99.00")),  # not balanced
            ],
            external_ref="t-zero-2",
        )


# ---------------------------------------------------------------------------
# 2) get_balance() reflects the journal
# ---------------------------------------------------------------------------


async def test_get_balance_reflects_journal(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("300.00"), stripe_payment_intent_id="pi_test_1"
    )
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("300.0000"), available
    assert reserved == Decimal("0"), reserved

    await reservation.reserve(
        db_session, wallet.id, Decimal("50.00"), session_id="s1"
    )
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("250.0000"), available
    assert reserved == Decimal("50.0000"), reserved


# ---------------------------------------------------------------------------
# 3) reserve / settle / release
# ---------------------------------------------------------------------------


async def test_reserve_moves_available_to_reserved(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("200.00"), stripe_payment_intent_id="pi_test_r1"
    )
    await reservation.reserve(
        db_session, wallet.id, Decimal("80.00"), session_id="r1"
    )
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("120.0000"), available
    assert reserved == Decimal("80.0000"), reserved


async def test_settle_drains_reserved(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("200.00"), stripe_payment_intent_id="pi_test_s1"
    )
    await reservation.reserve(
        db_session, wallet.id, Decimal("80.00"), session_id="s-sess-1"
    )
    await reservation.settle(
        db_session, wallet.id, Decimal("60.00"), session_id="s-sess-1"
    )
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    # available: 200 - 80 = 120; reserved: 80 - 60 = 20
    assert available == Decimal("120.0000"), available
    assert reserved == Decimal("20.0000"), reserved


async def test_release_returns_to_available(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("200.00"), stripe_payment_intent_id="pi_test_rel1"
    )
    await reservation.reserve(
        db_session, wallet.id, Decimal("80.00"), session_id="r-rel-1"
    )
    await reservation.release(
        db_session, wallet.id, Decimal("30.00"), session_id="r-rel-1"
    )
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    # available: 200 - 80 + 30 = 150; reserved: 80 - 30 = 50
    assert available == Decimal("150.0000"), available
    assert reserved == Decimal("50.0000"), reserved


# ---------------------------------------------------------------------------
# 4) Insufficient funds
# ---------------------------------------------------------------------------


async def test_insufficient_funds_raises(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("50.00"), stripe_payment_intent_id="pi_if_1"
    )
    with pytest.raises(InsufficientFundsError):
        await reservation.reserve(
            db_session, wallet.id, Decimal("100.00"), session_id="overshoot"
        )

    # No row was written.
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("50.0000"), available
    assert reserved == Decimal("0"), reserved


# ---------------------------------------------------------------------------
# 5) Idempotent topup
# ---------------------------------------------------------------------------


async def test_idempotent_topup_same_external_ref(db_session):
    user, wallet = await _make_wallet(db_session)
    txn1 = await topup.topup_stripe(
        db_session, wallet.id, Decimal("100.00"), stripe_payment_intent_id="pi_idem_1"
    )
    txn2 = await topup.topup_stripe(
        db_session, wallet.id, Decimal("100.00"), stripe_payment_intent_id="pi_idem_1"
    )
    assert txn1.id == txn2.id, "second call must return the first txn (idempotent)"
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("100.0000"), available
    # Only ONE transaction row.
    all_txns = (
        (
            await db_session.execute(
                select(WalletTransaction).where(
                    WalletTransaction.wallet_id == wallet.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(all_txns) == 1, all_txns


# ---------------------------------------------------------------------------
# 6) end_session_settle — remainder + exact + over-preauth
# ---------------------------------------------------------------------------


async def test_end_session_settle_with_remainder_releases(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("500.00"), stripe_payment_intent_id="pi_end_1"
    )
    await reservation.reserve(
        db_session, wallet.id, Decimal("100.00"), session_id="end-1"
    )
    # Final: 8.5 kWh * HKD 9.50/kWh = HKD 80.75 — remainder = 19.25
    settle_txn, release_txn = await reservation.end_session_settle(
        db_session,
        wallet.id,
        final_kwh=Decimal("8.5"),
        rate_hkd_per_kwh=Decimal("9.50"),
        session_id="end-1",
    )
    assert release_txn is not None
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("500.0000") - Decimal("100.0000") + Decimal("19.2500")
    assert reserved == Decimal("0"), reserved


async def test_end_session_settle_exact_amount_no_release(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("500.00"), stripe_payment_intent_id="pi_end_2"
    )
    await reservation.reserve(
        db_session, wallet.id, Decimal("95.00"), session_id="end-2"
    )
    # Final: 10 kWh * HKD 9.50/kWh = 95.00 — exact match
    settle_txn, release_txn = await reservation.end_session_settle(
        db_session,
        wallet.id,
        final_kwh=Decimal("10"),
        rate_hkd_per_kwh=Decimal("9.50"),
        session_id="end-2",
    )
    assert release_txn is None
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("500.0000") - Decimal("95.0000")
    assert reserved == Decimal("0"), reserved


async def test_end_session_settle_over_preauth_raises(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("500.00"), stripe_payment_intent_id="pi_end_3"
    )
    await reservation.reserve(
        db_session, wallet.id, Decimal("100.00"), session_id="end-3"
    )
    # Final: 12 kWh * HKD 9.50 = 114 — over the 100 pre-auth
    with pytest.raises(InsufficientFundsError):
        await reservation.end_session_settle(
            db_session,
            wallet.id,
            final_kwh=Decimal("12"),
            rate_hkd_per_kwh=Decimal("9.50"),
            session_id="end-3",
        )
    # Balances untouched by the failed call.
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available == Decimal("400.0000"), available
    assert reserved == Decimal("100.0000"), reserved


# ---------------------------------------------------------------------------
# 7) Concurrency — both reserves cannot succeed when sum > available.
# ---------------------------------------------------------------------------


async def test_concurrent_reserve_no_double_spend(db_session, test_db_url):
    """Two parallel reserves of 70 each against a wallet with only 100
    available.

    Invariant we test: after concurrent reserves attempt to spend more
    than is available, the wallet balance NEVER goes negative and the
    journal always reconciles to the materialized wallet row. This is
    the load-bearing financial guarantee.

    NOTE: SQLite does NOT honor ``SELECT ... FOR UPDATE`` (it's a no-op),
    so the strict "exactly one succeeds" assertion would require Postgres.
    On Postgres the FOR UPDATE clause serializes the writers; on SQLite
    we verify the post-condition invariant instead. The Postgres test
    suite in the console/validate phase will exercise the strict path.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    SessionLocal = async_sessionmaker(
        create_async_engine(test_db_url, future=True), expire_on_commit=False
    )

    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("100.00"), stripe_payment_intent_id="pi_conc_1"
    )
    await db_session.commit()

    successes: list[bool] = []
    failures: list[Exception] = []

    async def do_reserve(session_id: str):
        async with SessionLocal() as s:
            try:
                await reservation.reserve(
                    s, wallet.id, Decimal("70.00"), session_id=session_id
                )
                await s.commit()
                successes.append(True)
            except Exception as e:  # noqa: BLE001
                await s.rollback()
                failures.append(e)

    await asyncio.gather(
        do_reserve("conc-A"), do_reserve("conc-B"), return_exceptions=False
    )

    # What we assert: at least ONE failed (otherwise double-spend is
    # possible). On Postgres exactly one succeeds; on SQLite the strict
    # count may be 0, 1, or 2 depending on driver scheduling — but the
    # invariant below is what we hold the system to.
    assert len(failures) + len(successes) == 2
    assert (
        len(successes) <= 1
    ), f"both reserves succeeded — the ledger failed its single-write invariant (successes={successes}, failures={failures})"

    # Verify the wallet was NOT double-spent: available >= 0 and the
    # available bucket journal sum matches the wallet row.
    async with SessionLocal() as s2:
        available, reserved = await ledger_mod.get_balance(s2, wallet.id)
        assert available >= Decimal("0"), (
            f"available went negative — DOUBLE-SPEND ({available})"
        )
        assert reserved >= Decimal("0"), f"reserved went negative: {reserved}"

        journal_avail = await s2.scalar(
            text(
                "SELECT COALESCE(SUM(amount), 0) FROM ledger_entries "
                "WHERE wallet_id = :w AND bucket = 'available'"
            ),
            {"w": str(wallet.id)},
        )
        journal_resv = await s2.scalar(
            text(
                "SELECT COALESCE(SUM(amount), 0) FROM ledger_entries "
                "WHERE wallet_id = :w AND bucket = 'reserved'"
            ),
            {"w": str(wallet.id)},
        )
        assert Decimal(journal_avail) == available, (
            f"journal/reconciliation drift (avail): journal={journal_avail}, wallet={available}"
        )
        assert Decimal(journal_resv) == reserved, (
            f"journal/reconciliation drift (resv): journal={journal_resv}, wallet={reserved}"
        )


# ---------------------------------------------------------------------------
# 8) Reconciliation detects a tampered wallet row.
# ---------------------------------------------------------------------------


async def test_reconcile_detects_tampered_wallet_row(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("200.00"), stripe_payment_intent_id="pi_rec_1"
    )
    await db_session.flush()
    # Wallet row should now show 200 available, journal sum should agree.
    deltas_clean = await ledger_mod.reconcile(db_session, wallet.id)
    assert deltas_clean == [], deltas_clean

    # Now tamper with the wallet row directly (simulating an out-of-band
    # write / bad migration).
    await db_session.execute(
        text("UPDATE wallets SET available_credits = :v WHERE id = :id"),
        {"v": "199.99", "id": str(wallet.id)},
    )
    await db_session.flush()
    deltas = await ledger_mod.reconcile(db_session, wallet.id)
    assert len(deltas) == 1, deltas
    assert deltas[0]["bucket"] == "available"
    assert Decimal(deltas[0]["materialized"]) == Decimal("199.99")
    assert Decimal(deltas[0]["journal_sum"]) == Decimal("200.0000")
    assert Decimal(deltas[0]["delta"]) == Decimal("-0.0100")


# ---------------------------------------------------------------------------
# 9) Property-based: invariant holds for any sequence of reserve/settle/release.
# ---------------------------------------------------------------------------


amount_st = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("500.00"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)
op_st = st.sampled_from(["reserve", "settle", "release", "topup"])


@st.composite
def ops_strategy(draw):
    """Sequence of (op, amount) tuples."""
    seq = []
    for _ in range(draw(st.integers(min_value=1, max_value=15))):
        op = draw(op_st)
        amt = draw(amount_st)
        seq.append((op, amt))
    return seq


@given(ops=ops_strategy())
@hyp_settings(
    deadline=None,
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_invariant_holds_for_random_sequences(db_session, ops):
    """For any sequence of (reserve|settle|release|topup), the invariants hold:

        * available >= 0
        * reserved >= 0
        * SUM(journal amounts over available bucket) == available
        * SUM(journal amounts over reserved bucket)  == reserved
    """
    user, wallet = await _make_wallet(db_session)
    # Always seed with a topup so we have something to spend. Use a valid
    # ``pi_<hex>`` payment_intent_id since the Stripe stub verifier
    # requires that prefix.
    await topup.topup_stripe(
        db_session,
        wallet.id,
        Decimal("5000.00"),
        stripe_payment_intent_id=f"pi_{uuid.uuid4().hex}",
    )

    for op, amount in ops:
        try:
            if op == "topup":
                await topup.topup_stripe(
                    db_session,
                    wallet.id,
                    amount,
                    stripe_payment_intent_id=f"pi_{uuid.uuid4().hex}",
                )
            elif op == "reserve":
                await reservation.reserve(
                    db_session, wallet.id, amount, session_id=f"s-{uuid.uuid4().hex}"
                )
            elif op == "settle":
                await reservation.settle(
                    db_session, wallet.id, amount, session_id=f"s-{uuid.uuid4().hex}"
                )
            elif op == "release":
                await reservation.release(
                    db_session, wallet.id, amount, session_id=f"s-{uuid.uuid4().hex}"
                )
        except InsufficientFundsError:
            # Expected when a random op exceeds available/reserved.
            pass
        except WalletLedgerIntegrityError:
            # Should never happen — the ledger code never produces an
            # imbalanced entry on its own.
            raise

    await db_session.flush()
    available, reserved = await ledger_mod.get_balance(db_session, wallet.id)
    assert available >= Decimal("0"), f"available went negative: {available}"
    assert reserved >= Decimal("0"), f"reserved went negative: {reserved}"

    journal_avail = await db_session.scalar(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM ledger_entries "
            "WHERE wallet_id = :w AND bucket = 'available'"
        ),
        {"w": str(wallet.id)},
    )
    journal_resv = await db_session.scalar(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM ledger_entries "
            "WHERE wallet_id = :w AND bucket = 'reserved'"
        ),
        {"w": str(wallet.id)},
    )
    assert Decimal(journal_avail) == available, (
        Decimal(journal_avail),
        available,
    )
    assert Decimal(journal_resv) == reserved, (
        Decimal(journal_resv),
        reserved,
    )


# ---------------------------------------------------------------------------
# 10) External bucket — entries land in the journal even though the wallet
#     row has no external column.
# ---------------------------------------------------------------------------


async def test_external_bucket_entries_recorded(db_session):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("100.00"), stripe_payment_intent_id="pi_ext_1"
    )
    await db_session.flush()
    ext_rows = (
        (
            await db_session.execute(
                text(
                    "SELECT amount, entry_type FROM ledger_entries "
                    "WHERE wallet_id = :w AND bucket = 'external'"
                ),
                {"w": str(wallet.id)},
            )
        )
        .all()
    )
    # One row: -100 (topup_debit) on the external bucket.
    assert len(ext_rows) == 1
    assert ext_rows[0][1] == "topup_debit"
    assert Decimal(ext_rows[0][0]) == Decimal("-100.0000")


# ---------------------------------------------------------------------------
# 11) Reject negative / zero / over-cap amounts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [Decimal("-1"), Decimal("0"), Decimal("1000.00")])
async def test_reserve_rejects_bad_amounts(db_session, bad):
    user, wallet = await _make_wallet(db_session)
    await topup.topup_stripe(
        db_session, wallet.id, Decimal("800.00"), stripe_payment_intent_id="pi_bad_1"
    )
    from evwallet.errors import ValidationError

    with pytest.raises(ValidationError):
        await reservation.reserve(
            db_session, wallet.id, bad, session_id=f"s-{uuid.uuid4().hex}"
        )
