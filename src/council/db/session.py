import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from council.config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def sync_database_url(database_url: str | None = None) -> str:
    """The configured database URL with the async driver swapped for its sync
    equivalent — what Alembic needs.

    Resolution goes through get_settings(), NOT os.environ, so a URL set in
    .env is honoured. Reading the raw environment instead silently fell back
    to the Postgres default, and Alembic then hung trying to reach a database
    that was never running.
    """
    url = database_url or get_settings().database_url
    return url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "+pysqlite")


def mask_url(url: str) -> str:
    """Hide credentials so a URL can be printed in a diagnostic."""
    return re.sub(r"//[^/@]*@", "//***@", url)


def init_engine(database_url: str | None = None):
    """Create (or replace) the global engine. Tests pass a sqlite URL."""
    global _engine, _sessionmaker
    url = database_url or get_settings().database_url
    _engine = create_async_engine(url, echo=False)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def ensure_engine():
    """Initialize from settings only if nothing initialized yet (tests may
    have installed their own engine first)."""
    if _engine is None:
        init_engine()
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
