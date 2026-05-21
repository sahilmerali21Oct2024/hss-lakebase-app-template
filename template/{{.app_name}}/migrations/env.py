"""Alembic env.py — builds a fresh Lakebase connection per run.

Lakebase OAuth tokens are short-lived, so we cannot store a static
sqlalchemy.url. Instead we resolve host + token at runtime from:

    LAKEBASE_INSTANCE   (env)  -> shared instance name
    LAKEBASE_DATABASE   (env)  -> per-app logical DB
    LAKEBASE_BRANCH     (env)  -> 'main' (or 'pr-<n>' for PR builds)

The CI SP must already have privileges on the target database.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from databricks.sdk import WorkspaceClient
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # plug in your SQLAlchemy MetaData if you adopt ORM


def _build_url() -> str:
    instance = os.environ["LAKEBASE_INSTANCE"]
    database = os.environ["LAKEBASE_DATABASE"]
    branch = os.environ.get("LAKEBASE_BRANCH", "main")

    w = WorkspaceClient()
    endpoint = f"projects/{instance}/branches/{branch}/endpoints/primary"
    ep = w.postgres.get_endpoint(name=endpoint)
    cred = w.postgres.generate_database_credential(endpoint=endpoint)

    user = w.current_user.me().user_name
    host = ep.status.hosts.host
    return (
        f"postgresql+psycopg://{user}:{cred.token}@{host}:5432/{database}?sslmode=require"
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_build_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_build_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
