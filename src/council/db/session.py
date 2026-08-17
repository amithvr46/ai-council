from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from council.config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


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
