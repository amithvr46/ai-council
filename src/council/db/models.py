"""Execution history: one row per request, one row per pipeline step.

The steps table is the audit trail the whole system leans on — every model
call and stage transition lands here with prompt version, tokens, cost and
latency. Types are kept portable so tests can run on SQLite while
production runs on Postgres.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    requests: Mapped[list["Request"]] = relationship(back_populates="conversation")


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    question: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))  # quick | council | deep
    status: Mapped[str] = mapped_column(String(32), default="received")
    # received -> routed -> candidates_complete -> checked -> synthesized/
    # disagreement_reported -> complete | failed
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)  # logical generations
    total_api_attempts: Mapped[int] = mapped_column(Integer, default=0)  # physical invocations
    user_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)

    steps: Mapped[list["Step"]] = relationship(
        back_populates="request", order_by="Step.seq", cascade="all, delete-orphan"
    )
    conversation: Mapped[Conversation | None] = relationship(back_populates="requests")


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    stage: Mapped[str] = mapped_column(String(48))
    # candidate_a | candidate_b | combined_check | synthesis | disagreement_report | ...
    provider: Mapped[str | None] = mapped_column(String(24), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(48), nullable=True)

    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error | degraded
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_attempts: Mapped[int] = mapped_column(Integer, default=1)  # 2 when the retry fired

    request: Mapped[Request] = relationship(back_populates="steps")
