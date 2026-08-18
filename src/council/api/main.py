import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from council.db.models import Base, Request
from council.db.session import ensure_engine, get_engine, session_scope
from council.engine.budget import MODE_BUDGETS
from council.engine.events import bus
from council.engine.factory import build_engine

engine = None
_background: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    ensure_engine()
    # Dev convenience; production schema is managed by Alembic.
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    engine = build_engine()
    yield


app = FastAPI(title="AI Council", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    mode: str = Field(default="council", pattern="^(quick|council|deep)$")


@app.post("/ask")
async def ask(body: AskBody):
    """Synchronous: runs the full pipeline, returns the completed trace."""
    return await engine.run(body.question, body.mode)


@app.post("/ask/async")
async def ask_async(body: AskBody):
    """Returns the request id immediately; subscribe to /requests/{id}/stream
    for live stage events while the pipeline runs in the background."""
    request_id = await engine.create(body.question, body.mode)

    async def _run():
        try:
            await engine.run(body.question, body.mode, request_id=request_id)
        except Exception:
            pass  # failure state is persisted + emitted by the engine itself

    task = asyncio.create_task(_run())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"id": request_id, "status": "running"}


@app.get("/requests/{request_id}/stream")
async def stream(request_id: str):
    """SSE: replays nothing for finished requests (client should fetch the
    trace instead) — emits live stage events until the 'done' event."""
    try:
        current = await engine.get_request(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="request not found") from None

    async def gen():
        if current["status"] in ("complete", "failed"):
            yield _sse({"type": "done", "status": current["status"],
                        "degraded": current["degraded"], "error": current["error"]})
            return
        q = bus.subscribe(request_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=120)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event.get("type") == "done":
                    return
        finally:
            bus.unsubscribe(request_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/requests")
async def list_requests(limit: int = 25, offset: int = 0):
    limit = max(1, min(limit, 100))
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Request))).scalar_one()
        rows = (
            (
                await s.execute(
                    select(Request)
                    .order_by(Request.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {
            "total": total,
            "items": [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat(),
                    "question": r.question[:200],
                    "mode": r.mode,
                    "status": r.status,
                    "degraded": r.degraded,
                    "cost_usd": round(r.total_cost_usd, 6),
                    "model_calls": r.model_calls,
                    "latency_ms": r.latency_ms,
                    "user_rating": r.user_rating,
                }
                for r in rows
            ],
        }


@app.get("/stats")
async def stats():
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    week_start = day_start - timedelta(days=6)

    async with session_scope() as s:
        async def agg(since):
            row = (
                await s.execute(
                    select(
                        func.count(Request.id),
                        func.coalesce(func.sum(Request.total_cost_usd), 0.0),
                        func.avg(Request.latency_ms),
                    ).where(Request.created_at >= since)
                )
            ).one()
            return {
                "requests": row[0],
                "cost_usd": round(float(row[1]), 4),
                "avg_latency_ms": int(row[2]) if row[2] else None,
            }

        by_mode_rows = (
            await s.execute(
                select(
                    Request.mode,
                    func.count(Request.id),
                    func.coalesce(func.sum(Request.total_cost_usd), 0.0),
                )
                .where(Request.created_at >= month_start)
                .group_by(Request.mode)
            )
        ).all()
        degraded = (
            await s.execute(
                select(func.count(Request.id)).where(
                    Request.created_at >= month_start, Request.degraded.is_(True)
                )
            )
        ).scalar_one()

        month = await agg(month_start)
        return {
            "today": await agg(day_start),
            "week": await agg(week_start),
            "month": month,
            "by_mode": {
                m: {"requests": c, "cost_usd": round(float(cost), 4)}
                for m, c, cost in by_mode_rows
            },
            "degraded_this_month": degraded,
            "degraded_rate": round(degraded / month["requests"], 3)
            if month["requests"]
            else 0.0,
        }


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
    async with session_scope() as s:
        req = await s.get(Request, request_id)
        if req is None:
            raise HTTPException(status_code=404, detail="request not found")
        req.user_rating = body.rating
    return {"ok": True}


@app.get("/health")
async def health():
    return {"ok": True, "budgets": MODE_BUDGETS}
