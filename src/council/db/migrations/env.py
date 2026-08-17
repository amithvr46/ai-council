import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from council.db.models import Base

config = context.config

# Alembic runs sync: swap the asyncpg driver for psycopg.
url = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://council:council@localhost:5432/council"
).replace("+asyncpg", "+psycopg")
config.set_main_option("sqlalchemy.url", url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
