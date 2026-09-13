"""Alembic environment configuration — async.

Reads the DB URL from :class:`evwallet.config.Settings` (so the same
.env drives both the FastAPI app and Alembic). Runs migrations in
``online`` mode against the async engine via ``run_sync`` because
Alembic itself is sync.

The model metadata for autogenerate is :class:`evwallet.db.Base.metadata`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context

# Ensure ``src/`` is on sys.path so ``import evwallet`` works whether
# Alembic was invoked from the repo root or from anywhere else.
sys.path.insert(0, os.path.abspath("src"))

from sqlalchemy import pool  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from evwallet.config import get_settings  # noqa: E402
from evwallet.db import Base  # noqa: E402
import evwallet.db.models  # noqa: E402,F401 — register all models on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from Settings if available (env-driven).
try:
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database_url)
except Exception:
    # No env configured — fall back to alembic.ini's value.
    pass

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations in 'online' mode (with a live DB connection)."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    """Run migrations in 'online' mode using an async engine."""
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Sync entrypoint that runs the async migrations."""
    asyncio.run(run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
