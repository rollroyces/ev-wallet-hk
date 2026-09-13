"""Async SQLAlchemy engine + FastAPI dependency.

Single process-wide :class:`AsyncEngine` created lazily on first use. The
session dependency yields a fresh :class:`AsyncSession` per request and
ensures it's closed on exit. ``init_db`` runs ``Base.metadata.create_all``
on the bound metadata — used by tests and ``make init-db``; production
uses Alembic migrations instead.

The :class:`Base` is re-exported here so ``from evwallet.db.session
import Base`` continues to work for sibling modules.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from evwallet.config import get_settings
from evwallet.errors import BackendUnavailableError, StorageError

_log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all EV Wallet HK models.

    Uses the SQLAlchemy 2.0 typed ``Mapped[...]`` style. Subclasses should
    import this base and annotate every column.
    """


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call.

    The engine is built from :func:`get_settings().async_database_url`.
    Calling this function without a configured :class:`Settings` raises
    :class:`ConfigurationError` via the settings loader.

    Returns:
        The shared :class:`AsyncEngine`.
    """
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.async_database_url,
            pool_size=10,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False,
            future=True,
        )
        _sessionmaker = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        _log.info(
            "db engine initialised host=%s db=%s",
            settings.postgres_host,
            settings.postgres_db,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, building the engine if needed."""
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an ``AsyncSession`` for one request.

    The session is rolled back on exception, committed on successful exit
    if the caller explicitly commits. Yields the session so FastAPI can
    handle cleanup via the ``finally`` block.

    Yields:
        An :class:`AsyncSession` bound to the request.
    """
    Session = get_sessionmaker()
    session = Session()
    try:
        yield session
    except SQLAlchemyError:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Create all tables (dev/test only — production uses Alembic).

    Issues ``CREATE EXTENSION IF NOT EXISTS pgcrypto`` so ``gen_random_uuid``
    is available, then ``Base.metadata.create_all``. Safe to call multiple
    times.
    """
    from sqlalchemy import text

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await conn.run_sync(Base.metadata.create_all)
    except SQLAlchemyError as exc:
        raise StorageError(f"init_db failed: {exc}") from exc


async def dispose_engine() -> None:
    """Close all pooled connections — call from FastAPI lifespan shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        _log.info("db engine disposed")


async def healthcheck_db() -> bool:
    """Return ``True`` if Postgres responds to ``SELECT 1``.

    Used by ``GET /readyz``; returns ``False`` (without raising) on any
    driver error so the ready probe can serve 503 cleanly.
    """
    from sqlalchemy import text

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        _log.warning("db healthcheck failed: %s", exc)
        raise BackendUnavailableError(f"db unreachable: {exc}") from exc


def reset_engine_for_tests() -> None:
    """Drop the cached engine (testing only)."""
    global _engine, _sessionmaker
    if _engine is not None:
        with contextlib.suppress(Exception):
            _engine.sync_engine.dispose()
    _engine = None
    _sessionmaker = None


__all__ = [
    "Base",
    "dispose_engine",
    "get_db",
    "get_engine",
    "get_sessionmaker",
    "healthcheck_db",
    "init_db",
    "reset_engine_for_tests",
]
