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
