import pytest

from council.db.models import Base
from council.db.session import get_engine, init_engine
from council.engine.pipeline import CouncilEngine
from council.engine.prompts import default_registry
from tests.fakes import FakeProvider


@pytest.fixture
async def db():
    engine = init_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await get_engine().dispose()


@pytest.fixture
def make_engine(db):
    def _make(openai: FakeProvider, anthropic: FakeProvider, **kwargs) -> CouncilEngine:
        return CouncilEngine(
            {"openai": openai, "anthropic": anthropic},
            default_registry(),
            flagship_models={"openai": "fake-gpt", "anthropic": "fake-claude"},
            cheap_models={"openai": "fake-gpt-mini", "anthropic": "fake-haiku"},
            **kwargs,
        )

    return _make
