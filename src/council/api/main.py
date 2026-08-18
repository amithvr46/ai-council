import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from council.db.models import Base, Conversation, Request
from council.db.session import ensure_engine, get_engine, session_scope
from council.engine.budget import MODE_BUDGETS
from council.engine.events import bus
from council.engine.factory import build_engine

engine = None
_background: set[asyncio.Task] = set()
_running: dict[str, asyncio.Task] = {}  # request_id -> task, for cancellation


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


@app.exception_handler(Exception)
async def unhandled_error(request, exc):
    """Return JSON 500s (with CORS headers) instead of bare errors — the
    browser otherwise reports an opaque 'Failed to fetch'."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
        headers={"Access-Control-Allow-Origin": "http://localhost:3000"},
    )


class AskBody(BaseModel):
    question: str = Field(min_length=1)
    mode: str = Field(default="council", pattern="^(quick|council|deep)$")
    conversation_id: str | None = None


MAX_HISTORY_TURNS = 6
MAX_HISTORY_CHARS = 4000  # per message; keeps follow-up context costs sane


async def _conversation_history(conversation_id: str) -> list[dict[str, str]]:
    """Recent completed turns of a conversation as chat messages."""
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Request)
                    .where(
                        Request.conversation_id == conversation_id,
                        Request.status == "complete",
                        Request.final_answer.is_not(None),
                    )
                    .order_by(Request.created_at.desc())
                    .limit(MAX_HISTORY_TURNS)
                )
            )
            .scalars()
            .all()
        )
    history: list[dict[str, str]] = []
    for r in reversed(rows):  # oldest first
        history.append({"role": "user", "content": r.question[:MAX_HISTORY_CHARS]})
        history.append({"role": "assistant", "content": r.final_answer[:MAX_HISTORY_CHARS]})
    return history


async def _ensure_conversation(body: AskBody) -> str:
    from datetime import datetime as _dt

    async with session_scope() as s:
        if body.conversation_id:
            conv = await s.get(Conversation, body.conversation_id)
            if conv is None:
                raise HTTPException(status_code=404, detail="conversation not found")
            conv.updated_at = _dt.now(UTC)
            return conv.id
        title = body.question.strip().splitlines()[0][:80]
        conv = Conversation(title=title)
        s.add(conv)
        await s.flush()
        return conv.id


@app.post("/ask")
async def ask(body: AskBody):
    """Synchronous: runs the full pipeline, returns the completed trace."""
    history = await _conversation_history(body.conversation_id) if body.conversation_id else []
    return await engine.run(body.question, body.mode, history=history)


@app.post("/ask/async")
async def ask_async(body: AskBody):
    """Returns the request + conversation ids immediately; subscribe to
    /requests/{id}/stream for live stage events."""
    conversation_id = await _ensure_conversation(body)
    history = await _conversation_history(conversation_id)
    request_id = await engine.create(body.question, body.mode, conversation_id)

    async def _run():
        try:
            await engine.run(body.question, body.mode, request_id=request_id, history=history)
        except asyncio.CancelledError:
            # User pressed stop: mark the request and tell subscribers.
            await engine.mark_cancelled(request_id)
            raise
        except Exception:
            pass  # failure state is persisted + emitted by the engine itself

    task = asyncio.create_task(_run())
    _background.add(task)
    _running[request_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _background.discard(t)
        _running.pop(request_id, None)

    task.add_done_callback(_cleanup)
    return {"id": request_id, "conversation_id": conversation_id, "status": "running"}


@app.post("/requests/{request_id}/cancel")
async def cancel(request_id: str):
    """Stop an in-flight request: cancels the pipeline task so no further
    model calls are made. Stages already completed stay in the trace."""
    task = _running.get(request_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no running request with that id")
    # Task.cancel() returns False when the task already finished — the done
    # callback may not have cleared _running yet. Report that honestly
    # instead of claiming a cancellation that never happened.
    if not task.cancel():
        raise HTTPException(status_code=409, detail="request already finished")
    return {"ok": True, "id": request_id}


# ----------------------------------------------------------- conversations


class ConversationPatch(BaseModel):
    pinned: bool | None = None
    title: str | None = Field(default=None, max_length=120)


@app.get("/conversations")
async def list_conversations(limit: int = 30):
    limit = max(1, min(limit, 100))
    async with session_scope() as s:
        rows = (
            (
                await s.execute(
                    select(Conversation)
                    .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return {
            "items": [
                {
                    "id": c.id,
                    "title": c.title,
                    "pinned": c.pinned,
                    "updated_at": c.updated_at.isoformat(),
                }
                for c in rows
            ]
        }


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    async with session_scope() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        rows = (
            (
                await s.execute(
                    select(Request)
                    .where(Request.conversation_id == conversation_id)
                    .order_by(Request.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return {
            "id": conv.id,
            "title": conv.title,
            "pinned": conv.pinned,
            "requests": [
                {
                    "id": r.id,
                    "question": r.question,
                    "mode": r.mode,
                    "status": r.status,
                    "degraded": r.degraded,
                    "final_answer": r.final_answer,
                    "cost_usd": round(r.total_cost_usd, 6),
                    "latency_ms": r.latency_ms,
                    "model_calls": r.model_calls,
                    "user_rating": r.user_rating,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ],
        }


@app.patch("/conversations/{conversation_id}")
async def patch_conversation(conversation_id: str, body: ConversationPatch):
    async with session_scope() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if body.pinned is not None:
            conv.pinned = body.pinned
        if body.title is not None:
            conv.title = body.title
    return {"ok": True}


@app.get("/requests/{request_id}/stream")
async def stream(request_id: str):
    """SSE: replays nothing for finished requests (client should fetch the
    trace instead) — emits live stage events until the 'done' event."""
    try:
        current = await engine.get_request(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="request not found") from None

    async def gen():
        if current["status"] in ("complete", "failed", "cancelled"):
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
