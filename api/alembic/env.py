import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make 'app' importable: add api/ to sys.path
# (this env.py file is at api/alembic/env.py — go up one level to reach api/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: E402, F401  -- imports register models with Base.metadata

# Alembic Config — provides values from alembic.ini
config = context.config

# Inject the DB URL from our Settings into Alembic's config
config.set_main_option("sqlalchemy.url", settings.app_db_url_sync)

# Configure Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This metadata is what 'alembic revision --autogenerate' compares against the DB
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout, no DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="app",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against an actual database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="app",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()