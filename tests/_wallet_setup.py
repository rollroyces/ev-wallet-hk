"""Agent B wallet-test bootstrap.

Imports a SQLite-compatible test schema and patches the production
``evwallet.db.models`` so that the wallet ledger code can run against
SQLite in tests. Specifically:

    * Patch ``sqlalchemy.dialects.postgresql.JSONB`` to plain JSON.
    * Drop Postgres-only tables (those with ARRAY columns) from the
      canonical ``Base.metadata`` so SQLite can compile the rest.

This module is imported by ``tests/test_wallet_ledger.py`` BEFORE the
tests themselves run, so the global state it mutates is in place when
the first test is collected.
"""

from __future__ import annotations

import os
from decimal import Decimal

# Env vars must be set BEFORE importing evwallet.* so Settings() picks
# them up.
os.environ.setdefault("EVW_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("EVW_ENV", "test")
os.environ.setdefault("EVW_JWT_SECRET", "x" * 64)
os.environ.setdefault("EVW_PREAUTH_MAX_HKD", "500.00")

from sqlalchemy import JSON  # noqa: E402

# Make JSONB == JSON so SQLite can compile the wallet tables that
# use JSONB on their JSON-shaped columns.
import sqlalchemy.dialects.postgresql as _pg  # noqa: E402

_pg.JSONB = JSON  # type: ignore[attr-defined]

# SQLite has no gen_random_uuid(); replace it on the SQL functions module
# with a Python uuid4 so the column default works at compile time.
import sqlalchemy.sql.functions as _sqlfuncs  # noqa: E402
import sqlalchemy.sql.operators as _op  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.sql.functions import GenericFunction  # noqa: E402


class gen_random_uuid(GenericFunction):
    """SQLite-compatible replacement for pg's ``gen_random_uuid()``."""

    type = None
    name = "gen_random_uuid"


@compiles(gen_random_uuid, "sqlite")
def _sqlite_gen_random_uuid(element, compiler, **_kw):  # pragma: no cover
    """Emit a SQL fragment that calls a registered Python function."""
    # SQLite stores Uuid as TEXT by default; we use a SQL function registered
    # at connect time (see the connect event below) that returns TEXT.
    return "py_uuid4()"


# Also stub out the attribute on the global functions module so any direct
# ``sqlalchemy.func.gen_random_uuid()`` call is intercepted.
_sqlfuncs.gen_random_uuid = gen_random_uuid  # type: ignore[attr-defined]


# Register the Python function under the name ``gen_random_uuid`` on every
# SQLite DBAPI connection. SQLAlchemy compiles func.gen_random_uuid() to
# the literal SQL ``gen_random_uuid()`` so we just need the SQL function
# to exist. We listen on ``Pool`` so both sync and async engines pick it up.
from sqlalchemy import event as _evt  # noqa: E402
from sqlalchemy.pool import Pool  # noqa: E402


def _on_connect(dbapi_con, _con_record):  # pragma: no cover
    if not hasattr(dbapi_con, "create_function"):
        return
    import uuid as _uuid

    dbapi_con.create_function(
        "gen_random_uuid", 0, lambda: str(_uuid.uuid4())
    )


@_evt.listens_for(Pool, "connect")
def _pool_connect(dbapi_con, con_record):  # pragma: no cover
    _on_connect(dbapi_con, con_record)


# NOTE: BEGIN IMMEDIATE was added to make the concurrency test work on
# SQLite, but it caused test_isolation issues with Agent C's tmp-file
# fixtures (separate engines pointed at the same file collided on the
# write lock). Removed; the Postgres test suite handles the strict
# single-writer assertion.
# See README → "Concurrency on SQLite vs Postgres" for the rationale.

# Drop tables that use ARRAY (Postgres-only) from the production
# metadata so SQLite can compile the rest. The wallet tables don't
# use ARRAY.
from evwallet.db.models import Base  # noqa: E402

_TABLES_TO_DROP = {
    "charging_stations",  # ARRAY(String) on amenities
    "poles",
    "hourly_rates",
    "charging_sessions",
    "session_telemetry",
}
for _tbl in list(Base.metadata.tables):
    if _tbl in _TABLES_TO_DROP:
        Base.metadata.remove(Base.metadata.tables[_tbl])

# Force the cached engine in evwallet.db.session to use SQLite for tests.
import evwallet.db.session as _evdb  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker as _asm  # noqa: E402

_evdb.reset_engine_for_tests()
_evdb._engine = _evdb.create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
_evdb._sessionmaker = _asm(  # type: ignore[attr-defined]
    _evdb._engine, expire_on_commit=False, autoflush=False
)


async def _sqlite_get_db():  # pragma: no cover
    """Replacement for ``get_db`` that uses the SQLite test engine."""
    import contextlib

    from sqlalchemy.ext.asyncio import AsyncSession

    @contextlib.asynccontextmanager
    async def _cm():
        async with _evdb._sessionmaker() as s:  # type: ignore[union-attr]
            yield s

    return _cm


# SQLite only auto-increments columns of the literal type ``INTEGER PRIMARY
# KEY``. Agent A's models use ``BigInteger`` for the journal PK (correct on
# Postgres), but we need to swap that for ``Integer`` when running on SQLite.
try:
    from sqlalchemy import Integer as _Int  # noqa: E402
    from evwallet.db.models import LedgerEntry, HourlyRate, SessionTelemetry  # noqa: E402

    for _model in (LedgerEntry, HourlyRate, SessionTelemetry):
        _col = _model.__table__.columns["id"]
        _col.type = _Int()
except Exception:  # pragma: no cover
    pass


def seed_user_with_wallet(db_session, *, available: Decimal = Decimal("0")):
    """Sync helper: build a (User, Wallet) pair and add them to the session.

    Returns (user, wallet) — caller must flush.
    """
    import uuid as _uuid

    from evwallet.db.models import User, Wallet

    user = User(
        id=_uuid.uuid4(),
        email=f"u-{_uuid.uuid4().hex[:8]}@test.local",
        display_name="Tester",
        is_active=True,
    )
    db_session.add(user)
    wallet = Wallet(
        id=_uuid.uuid4(),
        user_id=user.id,
        available_credits=available,
        reserved_credits=Decimal("0"),
        currency="HKD",
        version=0,
    )
    db_session.add(wallet)
    return user, wallet
