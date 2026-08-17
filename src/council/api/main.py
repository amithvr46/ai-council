from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from council.db.models import Base
from council.db.session import ensure_engine, get_engine
from council.engine.budget import MODE_BUDGETS
from council.engine.factory import build_engine

engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    ensure_engine()
    # Dev convenience; production schema is managed by Alembic.
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    engine = build_engine()
    yield


app = FastAPI(title="AI Council", version="0.1.0", lifespan=lifespan)


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    mode: str = Field(default="council", pattern="^(quick|council|deep)$")


@app.post("/ask")
async def ask(body: AskBody):
    return await engine.run(body.question, body.mode)


@app.get("/requests/{request_id}")
async def get_request(request_id: str):
    try:
        return await engine.get_request(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="request not found") from None


class RateBody(BaseModel):
    rating: int = Field(ge=1, le=5)


@app.post("/requests/{request_id}/rating")
async def rate(request_id: str, body: RateBody):
    from council.db.models import Request
    from council.db.session import session_scope

    async with session_scope() as s:
        req = await s.get(Request, request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        req.user_rating = body.rating
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True, "budgets": MODE_BUDGETS}
