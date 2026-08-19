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

    evidence_used: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_override: Mapped[bool] = mapped_column(Boolean, default=False)

    steps: Mapped[list["Step"]] = relationship(
        back_populates="request", order_by="Step.seq", cascade="all, delete-orphan"
    )
    conversation: Mapped[Conversation | None] = relationship(back_populates="requests")
    evidence_items: Mapped[list["EvidenceItemRow"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    claim_assessments: Mapped[list["ClaimAssessmentRow"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


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


class DocumentRow(Base):
    """Uploaded source material. `authority` decides how it may be used:
    career sources contribute POSITIVELY to confirmed experience and never
    negatively (see documents/profile.py); a JD is the target, not evidence."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    filename: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    authority: Mapped[str] = mapped_column(String(32), default="supporting")
    # profile | master_resume | supporting | tailored_resume | jd
    detected_kind: Mapped[str] = mapped_column(String(16), default="text")
    text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, default="")


class CareerProfileRow(Base):
    """Singleton (id=1). The authority on what the user has done — extensible
    by design, since legitimate experience is added over time."""

    __tablename__ = "career_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    technologies: Mapped[list | None] = mapped_column(JSON, nullable=True)
    domains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    employers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    certifications: Mapped[list | None] = mapped_column(JSON, nullable=True)
    achievements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class BudgetSettingsRow(Base):
    """Singleton row (id=1) holding spend ceilings. Runtime-editable so limits
    can change without a restart."""

    __tablename__ = "budget_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    daily_limit_usd: Mapped[float] = mapped_column(Float, default=3.0)
    monthly_limit_usd: Mapped[float] = mapped_column(Float, default=30.0)
    warn_threshold_pct: Mapped[int] = mapped_column(Integer, default=70)
    hard_stop: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EvidenceItemRow(Base):
    """One retrieved piece of evidence — a search result or an execution."""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ordinal: Mapped[int] = mapped_column(Integer)  # the [E<n>] label shown to models

    kind: Mapped[str] = mapped_column(String(16))  # web | code
    query: Mapped[str] = mapped_column(Text)  # search query or executed source
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|unavailable
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    request: Mapped[Request] = relationship(back_populates="evidence_items")


class ClaimAssessmentRow(Base):
    """A checkable claim judged against the evidence bundle — the record that
    makes 'evidence outranked the models' auditable after the fact."""

    __tablename__ = "claim_assessments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    claim: Mapped[str] = mapped_column(Text)
    made_by: Mapped[str] = mapped_column(String(8))  # A | B | both
    verdict: Mapped[str] = mapped_column(String(32))
    # SUPPORTED_BY_EVIDENCE | CONTRADICTED_BY_EVIDENCE | INSUFFICIENT_EVIDENCE
    rationale: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ordinals of items

    request: Mapped[Request] = relationship(back_populates="claim_assessments")


class TechnologyCacheRow(Base):
    """What a discovery call already established about a term.

    Negative answers are stored too: learning that a word is NOT a technology
    is worth exactly as much as learning that it is, and without it the same
    non-technology gets re-escalated on every similar JD (contract A2).
    """

    __tablename__ = "technology_cache"

    term: Mapped[str] = mapped_column(String(120), primary_key=True)  # normalised
    is_technology: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(32), default="other")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceConflictRow(Base):
    """A material factual disagreement between authoritative career sources.

    Contract A3: conflicts are persisted rather than resolved by "latest
    document wins", and the disputed fact is withheld until the user settles
    it. Manufacturing certainty here would put a wrong date on a resume.
    """

    __tablename__ = "source_conflicts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[str] = mapped_column(String(32))  # employer_dates | education | ...
    subject: Mapped[str] = mapped_column(String(255))  # what the fact is about
    values: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{source, value}]
    resolved_value: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class ArtifactRow(Base):
    """A generated document and the trace behind it.

    The user sees the finished result (contract A12); the trace stays so any
    bullet can be traced back to the classification that let it through.
    """

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[str] = mapped_column(String(32), default="resume_tailor")
    jd_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role_family: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # structured resume
    trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="complete")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    file_path: Mapped[str] = mapped_column(Text, default="")
