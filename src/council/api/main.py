import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from council import outcomes
from council.db.models import Base, Conversation, DocumentRow, Request
from council.db.session import ensure_engine, get_engine, session_scope
from council.documents.extract import ExtractionError, extract
from council.documents.profile import (
    AUTHORITY_JD,
    CAREER_AUTHORITIES,
    assemble_confirmed,
    detect_role_family,
    scan_jd_technologies,
)
from council.documents.store import (
    career_documents,
    load_profile,
    save_profile,
    store_document,
)
from council.engine.budget import MODE_BUDGETS
from council.engine.events import bus
from council.engine.factory import build_engine
from council.spend import (
    BudgetRefused,
    check_affordable,
    estimate_cost,
    load_settings,
    save_settings,
    spend_snapshot,
)

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
    mode: str = Field(default="council", pattern="^(auto|quick|council|deep)$")
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
    try:
        return await engine.run(body.question, body.mode, history=history)
    except BudgetRefused as e:
        raise HTTPException(status_code=402, detail=e.decision.as_dict()) from None


@app.post("/ask/async")
async def ask_async(body: AskBody):
    """Returns the request + conversation ids immediately; subscribe to
    /requests/{id}/stream for live stage events."""
    # Route once, then reuse the decision for both create() and run() so the
    # request is not routed twice and cannot be routed inconsistently.
    routing = await engine.plan(body.question, body.mode)

    # Check affordability before creating anything, so a refused request
    # leaves no orphan conversation or request row behind.
    decision = await check_affordable(routing.mode)
    if not decision.allowed:
        raise HTTPException(status_code=402, detail=decision.as_dict())

    conversation_id = await _ensure_conversation(body)
    history = await _conversation_history(conversation_id)
    request_id = await engine.create(
        body.question, routing.mode, conversation_id, routing=routing
    )

    async def _run():
        try:
            await engine.run(
                body.question, routing.mode, request_id=request_id, history=history
            )
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
                    "outcome_kind": r.outcome_kind,
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
                    "outcome_kind": r.outcome_kind,
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


# -------------------------------------------------------------- documents


_AUTHORITY_PATTERN = "^(profile|user_statement|master_resume|supporting|tailored_resume|jd)$"

# Module-level singletons: FastAPI needs these as defaults, and ruff's B008
# rightly objects to calling them inline.
_UPLOAD_FILE = File(...)
_FORM_AUTHORITY = Form("supporting")
_FORM_TITLE = Form("")


class DocumentPatch(BaseModel):
    title: str | None = None
    authority: str | None = Field(default=None, pattern=_AUTHORITY_PATTERN)


@app.post("/documents")
async def upload_document(
    file: UploadFile = _UPLOAD_FILE,
    authority: str = _FORM_AUTHORITY,
    title: str = _FORM_TITLE,
):
    """Ingest source material. A file that cannot be parsed is refused with a
    reason — never stored as an empty document that looks like an empty
    resume."""
    if authority not in (*CAREER_AUTHORITIES, AUTHORITY_JD):
        raise HTTPException(status_code=422, detail=f"unknown authority '{authority}'")
    data = await file.read()
    name = file.filename or "upload"
    try:
        extracted = extract(name, data)
    except ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    row, duplicate = await store_document(
        filename=name, title=title, authority=authority, extracted=extracted
    )
    return _document_dict(row, duplicate=duplicate)


def _document_dict(row: DocumentRow, duplicate: bool = False) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "title": row.title,
        "authority": row.authority,
        "detected_kind": row.detected_kind,
        "char_count": row.char_count,
        "truncated": row.truncated,
        "created_at": row.created_at.isoformat(),
        "duplicate": duplicate,
    }


@app.get("/documents")
async def list_documents():
    async with session_scope() as s:
        rows = (
            await s.execute(select(DocumentRow).order_by(DocumentRow.created_at.desc()))
        ).scalars().all()
        return {"items": [_document_dict(r) for r in rows]}


@app.get("/documents/{document_id}")
async def get_document(document_id: str):
    async with session_scope() as s:
        row = await s.get(DocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        return {**_document_dict(row), "text": row.text}


@app.patch("/documents/{document_id}")
async def patch_document(document_id: str, body: DocumentPatch):
    async with session_scope() as s:
        row = await s.get(DocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        if body.title is not None:
            row.title = body.title
        if body.authority is not None:
            row.authority = body.authority
        return _document_dict(row)


@app.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    async with session_scope() as s:
        row = await s.get(DocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        await s.delete(row)
    return {"ok": True}


# --------------------------------------------------------- career profile


class ProfilePatch(BaseModel):
    technologies: list[str] | None = None
    domains: list[str] | None = None
    roles: list[str] | None = None
    employers: list[str] | None = None
    certifications: list[str] | None = None
    achievements: list[str] | None = None
    notes: str | None = None


@app.get("/career-profile")
async def get_career_profile():
    """The profile plus the assembled confirmed experience, showing which
    source established each term. Additive only — no source subtracts."""
    profile = await load_profile()
    confirmed = assemble_confirmed(profile, await career_documents())
    return {
        "profile": profile.as_dict(),
        "confirmed": sorted(confirmed.terms),
        "sources": {k: v for k, v in sorted(confirmed.sources.items())},
    }


@app.put("/career-profile")
async def put_career_profile(body: ProfilePatch):
    """Extensible by design: adding legitimate experience later is a PUT, not
    a redesign."""
    await save_profile(**body.model_dump())
    return await get_career_profile()


@app.post("/career-profile/analyze-jd")
async def analyze_jd(body: dict):
    """Which role family the JD targets, and how the confirmed career maps
    onto it. The JD is never treated as career evidence."""
    jd_text = body.get("text", "")
    if not jd_text.strip():
        raise HTTPException(status_code=422, detail="jd text required")
    family, emphasis = detect_role_family(jd_text)
    profile = await load_profile()
    confirmed = assemble_confirmed(profile, await career_documents())
    tech_supported, tech_unsupported = scan_jd_technologies(jd_text, confirmed)
    return {
        "role_family": family,
        "emphasis": emphasis,
        "emphasis_supported": [e for e in emphasis if confirmed.is_confirmed(e)],
        "emphasis_unsupported": [e for e in emphasis if not confirmed.is_confirmed(e)],
        # The honest answer to "what does this role want that I can't claim?"
        "technologies_supported": tech_supported,
        "technologies_unsupported": tech_unsupported,
    }


# --------------------------------------------------------------- artifacts
#
# The One-Step resume path: career sources + JD + one optional line of natural
# language in, a downloadable DOCX out. No mode, no model choice, no bullet
# approval — the outcome determines the workflow, so this never touches
# quick/council/deep routing at all.


class ResumeBody(BaseModel):
    jd_document_id: str | None = None
    jd_text: str = ""
    instruction: str = ""
    name: str = ""
    contact: str = ""


@app.post("/artifacts/resume")
async def generate_resume(body: ResumeBody):
    import tempfile
    from pathlib import Path as _Path

    from council.documents.instructions import parse as parse_instruction
    from council.documents.render import render_docx
    from council.documents.store import (
        load_discovery_cache,
        save_artifact,
        save_conflicts,
        save_discovery_cache,
    )
    from council.documents.workflow import GenerationFailed
    from council.engine.factory import build_resume_workflow

    jd_text = body.jd_text.strip()
    if body.jd_document_id:
        async with session_scope() as s:
            row = await s.get(DocumentRow, body.jd_document_id)
            if row is None:
                raise HTTPException(status_code=404, detail="jd document not found")
            jd_text = row.text
    if not jd_text:
        raise HTTPException(status_code=422, detail="a job description is required")

    # One line of natural language carries two different things. The durable
    # career facts become a real career source with user_statement provenance;
    # the request-only preferences never touch the profile.
    instruction = parse_instruction(body.instruction)
    if instruction.has_career_statements:
        await _store_user_statement(instruction.career_text())

    profile = await load_profile()
    documents = await career_documents()
    cache = await load_discovery_cache()
    workflow = build_resume_workflow()
    try:
        result = await workflow.run(
            jd_text, profile, documents, cache=cache, instruction=instruction
        )
    except GenerationFailed as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    await save_discovery_cache(cache)
    await save_conflicts(result.analysis.conflicts)

    # Written to a private temp directory, never into the repository or any
    # user-visible path. Only the artifact id is handed out.
    out_dir = _Path(tempfile.gettempdir()) / "council-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = render_docx(
        result.draft, str(out_dir / "resume.docx"), name=body.name, contact=body.contact
    )

    artifact_id = await save_artifact(
        kind=outcomes.RESUME_TAILOR,
        jd_document_id=body.jd_document_id,
        role_family=result.analysis.role_family,
        title=body.name or "Tailored resume",
        content=result.draft.model_dump(),
        trace={
            "analysis": result.analysis.as_dict(),
            "review": result.review.model_dump() if result.review else None,
            "findings": result.findings,
            "instruction": instruction.as_dict(),
            **result.trace.as_dict(),
        },
        cost_usd=result.trace.cost_usd,
        file_path=str(path),
    )
    final_path = out_dir / f"{artifact_id}.docx"
    _Path(path).rename(final_path)
    await _set_artifact_path(artifact_id, str(final_path))

    return {
        "id": artifact_id,
        "outcome_kind": outcomes.RESUME_TAILOR,
        "role_family": result.analysis.role_family,
        "match_quality": result.analysis.match_quality,
        "gaps": result.analysis.gaps,
        "findings": result.findings,
        "would_submit": result.review.would_submit if result.review else None,
        "cost_usd": round(result.trace.cost_usd, 4),
        "model_calls": result.trace.model_calls,
        "download_url": f"/artifacts/{artifact_id}/download",
        "instruction": instruction.as_dict(),
    }


async def _store_user_statement(text: str) -> None:
    """Persist an explicit career statement as its own career source.

    Kept distinct from document-derived evidence so the sources map can still
    answer "who established this?" honestly.
    """
    from council.documents.extract import Extracted
    from council.documents.profile import AUTHORITY_USER_STATEMENT
    from council.documents.store import store_document

    await store_document(
        filename="user-statement.txt",
        title="Stated by you",
        authority=AUTHORITY_USER_STATEMENT,
        extracted=Extracted(
            text=text, char_count=len(text), truncated=False, detected_kind="text"
        ),
    )


async def _set_artifact_path(artifact_id: str, path: str) -> None:
    from council.db.models import ArtifactRow

    async with session_scope() as s:
        row = await s.get(ArtifactRow, artifact_id)
        if row is not None:
            row.file_path = path


@app.get("/artifacts")
async def list_artifacts_endpoint():
    from council.documents.store import list_artifacts

    return {"items": await list_artifacts()}


@app.get("/artifacts/{artifact_id}")
async def get_artifact_endpoint(artifact_id: str):
    from council.documents.store import get_artifact

    artifact = await get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    # The stored path is a private server detail; the client gets a URL.
    artifact.pop("file_path", None)
    artifact["download_url"] = f"/artifacts/{artifact_id}/download"
    return artifact


@app.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str):
    from pathlib import Path as _Path

    from fastapi.responses import FileResponse

    from council.documents.store import get_artifact

    artifact = await get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = _Path(artifact.get("file_path") or "")
    if not path.is_file():
        raise HTTPException(status_code=410, detail="the generated file is no longer available")
    return FileResponse(
        path,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        filename="resume.docx",
    )


# ---------------------------------------------------------------- budget


class BudgetPatch(BaseModel):
    daily_limit_usd: float | None = Field(default=None, ge=0)
    monthly_limit_usd: float | None = Field(default=None, ge=0)
    warn_threshold_pct: int | None = Field(default=None, ge=1, le=100)
    hard_stop: bool | None = None


@app.get("/budget")
async def get_budget():
    settings = await load_settings()
    snapshot = await spend_snapshot(settings)
    estimates = {mode: round(await estimate_cost(mode), 4) for mode in MODE_BUDGETS}
    return {
        "settings": {
            "daily_limit_usd": settings.daily_limit_usd,
            "monthly_limit_usd": settings.monthly_limit_usd,
            "warn_threshold_pct": settings.warn_threshold_pct,
            "hard_stop": settings.hard_stop,
        },
        "spent_today": round(snapshot.today, 4),
        "spent_month": round(snapshot.month, 4),
        "remaining_today": round(snapshot.remaining_today, 4)
        if snapshot.remaining_today != float("inf")
        else None,
        "remaining_month": round(snapshot.remaining_month, 4)
        if snapshot.remaining_month != float("inf")
        else None,
        "warn_at_today": round(
            settings.daily_limit_usd * settings.warn_threshold_pct / 100, 4
        ),
        "estimates": estimates,
    }


@app.put("/budget")
async def put_budget(body: BudgetPatch):
    await save_settings(**body.model_dump())
    return await get_budget()


@app.get("/health")
async def health():
    return {"ok": True, "call_budgets": MODE_BUDGETS}
