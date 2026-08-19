import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

from council.db.models import Base
from council.db.session import mask_url, sync_database_url

config = context.config

# Resolved through the app's own settings, so a DATABASE_URL in .env is
# honoured. Reading os.environ directly ignored .env, silently fell back to the
# Postgres default, and left `alembic upgrade head` hanging against a database
# that was never running — with no message explaining why.
#
# The sync driver swap matters for both backends: Postgres in production,
# SQLite locally, where an aiosqlite URL otherwise dies with an unhelpful
# MissingGreenlet error.
url = sync_database_url()
config.set_main_option("sqlalchemy.url", url)

# Printed so a misconfigured database is visible immediately rather than as a
# silent hang. Credentials are masked.
print(f"alembic: {mask_url(url)}", file=sys.stderr)

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
