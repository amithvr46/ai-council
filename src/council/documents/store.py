"""Persistence for career sources and the career profile.

Lives here rather than in the API layer so the CLI, the API and (later) the
artifact workflow all read the same career state through one path. A second
implementation is a second place for the "omission is not negative evidence"
rule to be broken.
"""

import hashlib

from sqlalchemy import select

from council.db.models import CareerProfileRow, DocumentRow
from council.db.session import session_scope
from council.documents.extract import Extracted
from council.documents.profile import (
    DEFAULT_DOMAINS,
    DEFAULT_TECHNOLOGIES,
    CareerProfile,
)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def store_document(
    *,
    filename: str,
    title: str,
    authority: str,
    extracted: Extracted,
) -> tuple[DocumentRow, bool]:
    """Store extracted source material. Returns (row, was_duplicate).

    Identical content re-uploaded is the same source, not a second one — two
    copies of the master resume must not double-count as two career sources.
    """
    digest = content_hash(extracted.text)
    async with session_scope() as s:
        existing = (
            await s.execute(select(DocumentRow).where(DocumentRow.content_hash == digest))
        ).scalars().first()
        if existing is not None:
            s.expunge(existing)
            return existing, True
        row = DocumentRow(
            filename=filename,
            title=title or filename,
            authority=authority,
            detected_kind=extracted.detected_kind,
            text=extracted.text,
            char_count=extracted.char_count,
            truncated=extracted.truncated,
            content_hash=digest,
        )
        s.add(row)
        await s.flush()
        s.expunge(row)
        return row, False


async def load_profile() -> CareerProfile:
    """The stored profile, or the seeded baseline if none has been saved.

    A column that was never set falls back to the baseline. Setting a field to
    [] is an explicit choice and is honoured; leaving it untouched must never
    silently erase confirmed experience.
    """
    async with session_scope() as s:
        row = await s.get(CareerProfileRow, 1)
        if row is None:
            return CareerProfile()
        return CareerProfile(
            technologies=(
                row.technologies
                if row.technologies is not None
                else list(DEFAULT_TECHNOLOGIES)
            ),
            domains=row.domains if row.domains is not None else list(DEFAULT_DOMAINS),
            roles=row.roles or [],
            employers=row.employers or [],
            certifications=row.certifications or [],
            achievements=row.achievements or [],
            notes=row.notes or "",
        )


async def save_profile(**fields) -> None:
    async with session_scope() as s:
        row = await s.get(CareerProfileRow, 1)
        if row is None:
            row = CareerProfileRow(id=1)
            s.add(row)
            await s.flush()
        for key, value in fields.items():
            if value is not None:
                setattr(row, key, value)


async def career_documents() -> list[dict]:
    """Every stored document, tagged with its authority.

    JDs are included deliberately: assemble_confirmed filters them out itself,
    so the exclusion is enforced in one tested place rather than depending on
    every caller remembering to filter.
    """
    async with session_scope() as s:
        rows = (await s.execute(select(DocumentRow))).scalars().all()
        return [{"authority": r.authority, "title": r.title, "text": r.text} for r in rows]


# ------------------------------------------------------- 2C persistence


async def load_discovery_cache():
    """Technology-discovery answers already paid for (contract A2)."""
    from council.db.models import TechnologyCacheRow
    from council.documents.discovery import DiscoveryCache

    async with session_scope() as s:
        rows = (await s.execute(select(TechnologyCacheRow))).scalars().all()
        return DiscoveryCache({r.term: r.is_technology for r in rows})


async def save_discovery_cache(cache) -> None:
    from council.db.models import TechnologyCacheRow

    async with session_scope() as s:
        existing = {
            r.term: r for r in (await s.execute(select(TechnologyCacheRow))).scalars().all()
        }
        for term, is_technology in cache.as_dict().items():
            row = existing.get(term)
            if row is None:
                s.add(TechnologyCacheRow(term=term, is_technology=is_technology))
            else:
                row.is_technology = is_technology


async def save_conflicts(conflicts) -> None:
    """Persist material conflicts (contract A3). Already-recorded conflicts on
    the same subject are left alone so a user's resolution is not undone by the
    next generation run."""
    from council.db.models import SourceConflictRow

    async with session_scope() as s:
        known = {
            (r.kind, r.subject)
            for r in (await s.execute(select(SourceConflictRow))).scalars().all()
        }
        for conflict in conflicts:
            if (conflict.kind, conflict.subject) in known:
                continue
            s.add(
                SourceConflictRow(
                    kind=conflict.kind,
                    subject=conflict.subject,
                    values=conflict.values,
                )
            )


async def list_conflicts(unresolved_only: bool = True) -> list[dict]:
    from council.db.models import SourceConflictRow

    async with session_scope() as s:
        query = select(SourceConflictRow)
        if unresolved_only:
            query = query.where(SourceConflictRow.resolved.is_(False))
        rows = (await s.execute(query)).scalars().all()
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "subject": r.subject,
                "values": r.values,
                "resolved": r.resolved,
                "resolved_value": r.resolved_value,
            }
            for r in rows
        ]


async def resolve_conflict(conflict_id: str, value: str) -> bool:
    from council.db.models import SourceConflictRow

    async with session_scope() as s:
        row = await s.get(SourceConflictRow, conflict_id)
        if row is None:
            return False
        row.resolved_value = value
        row.resolved = True
        return True


async def save_artifact(
    *,
    kind: str,
    jd_document_id: str | None,
    role_family: str,
    title: str,
    content: dict,
    trace: dict,
    cost_usd: float,
    file_path: str = "",
) -> str:
    from council.db.models import ArtifactRow

    async with session_scope() as s:
        row = ArtifactRow(
            kind=kind,
            jd_document_id=jd_document_id,
            role_family=role_family,
            title=title,
            content=content,
            trace=trace,
            cost_usd=cost_usd,
            file_path=file_path,
        )
        s.add(row)
        await s.flush()
        return row.id


async def list_artifacts() -> list[dict]:
    from council.db.models import ArtifactRow

    async with session_scope() as s:
        rows = (
            await s.execute(select(ArtifactRow).order_by(ArtifactRow.created_at.desc()))
        ).scalars().all()
        return [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "kind": r.kind,
                "role_family": r.role_family,
                "title": r.title,
                "cost_usd": r.cost_usd,
                "file_path": r.file_path,
            }
            for r in rows
        ]


async def get_artifact(artifact_id: str) -> dict | None:
    from council.db.models import ArtifactRow

    async with session_scope() as s:
        row = await s.get(ArtifactRow, artifact_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "kind": row.kind,
            "role_family": row.role_family,
            "title": row.title,
            "content": row.content,
            "trace": row.trace,
            "cost_usd": row.cost_usd,
            "file_path": row.file_path,
        }
