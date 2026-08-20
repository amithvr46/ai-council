"""Database / migration status.

Exists because of a real failure: a local database created by the app's dev
auto-create carries no Alembic version stamp, so `alembic current` prints
nothing and `alembic upgrade head` then tries to create tables that already
exist. Diagnosing that by hand meant shell one-liners, which is exactly the
kind of thing that breaks differently on every shell.

So the diagnosis lives in the tool: report what the database actually contains,
work out which revision that corresponds to, and say what to run.
"""

from dataclasses import dataclass

from sqlalchemy import inspect, text

from council.db.session import get_engine, mask_url, sync_database_url

# What each revision leaves behind, oldest first. Matching is by the LAST
# revision whose evidence is fully present.
REVISION_EVIDENCE: list[tuple[str, list[str], list[str]]] = [
    # (revision, required tables, required columns on `requests`)
    ("0001", ["requests", "steps"], []),
    ("0002", ["requests", "steps"], ["total_api_attempts"]),
    ("0003", ["conversations"], ["conversation_id"]),
    ("0004", ["evidence_items", "claim_assessments"], ["evidence_used"]),
    ("0005", ["budget_settings"], []),
    ("0006", ["documents", "career_profile"], []),
    ("0007", ["technology_cache", "source_conflicts", "artifacts"], []),
    ("0008", [], ["outcome_kind"]),
    ("0009", [], ["data_class"]),
    ("0010", ["career_denials"], []),
]

HEAD = REVISION_EVIDENCE[-1][0]


@dataclass
class DbStatus:
    url: str
    tables: list[str]
    request_columns: list[str]
    stamped: str | None  # what alembic_version says, if anything
    inferred: str | None  # what the schema actually looks like

    @property
    def up_to_date(self) -> bool:
        return self.stamped == HEAD and self.inferred == HEAD

    def advice(self) -> list[str]:
        """Exact commands to run, or an empty list when nothing is needed."""
        if not self.tables:
            return ["alembic upgrade head"]  # empty database: just migrate
        if self.stamped is None:
            # Schema exists but was never stamped — the dev auto-create path.
            # Stamping records where it already is; upgrading then applies only
            # what is genuinely missing, without touching existing data.
            if self.inferred == HEAD:
                return ["alembic stamp head"]
            return [f"alembic stamp {self.inferred}", "alembic upgrade head"]
        if self.stamped != HEAD:
            return ["alembic upgrade head"]
        if self.inferred != HEAD:
            # Stamped ahead of the real schema: the dangerous case, because
            # nothing will fix it automatically.
            return [
                f"# WARNING: stamped {self.stamped} but the schema looks like "
                f"{self.inferred}",
                f"alembic stamp {self.inferred}",
                "alembic upgrade head",
            ]
        return []


def infer_revision(tables: list[str], request_columns: list[str]) -> str | None:
    """The newest revision whose evidence is fully present.

    Conservative by design: it stops at the first revision that is not fully
    satisfied, so a partially-applied schema is reported as the last COMPLETE
    revision rather than optimistically rounded up.
    """
    have_tables, have_columns = set(tables), set(request_columns)
    inferred = None
    for revision, needed_tables, needed_columns in REVISION_EVIDENCE:
        if set(needed_tables) <= have_tables and set(needed_columns) <= have_columns:
            inferred = revision
        else:
            break
    return inferred


async def collect() -> DbStatus:
    engine = get_engine()
    async with engine.connect() as conn:
        tables = sorted(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        columns = []
        if "requests" in tables:
            columns = await conn.run_sync(
                lambda c: [col["name"] for col in inspect(c).get_columns("requests")]
            )
        stamped = None
        if "alembic_version" in tables:
            row = (await conn.execute(text("select version_num from alembic_version"))).first()
            stamped = row[0] if row else None

    return DbStatus(
        url=mask_url(sync_database_url()),
        tables=[t for t in tables if t != "alembic_version"],
        request_columns=columns,
        stamped=stamped,
        inferred=infer_revision(tables, columns),
    )


def render(status: DbStatus) -> str:
    lines = [
        f"database:        {status.url}",
        f"tables:          {len(status.tables)} ({', '.join(status.tables) or 'none'})",
        f"alembic stamp:   {status.stamped or 'NONE — never migrated'}",
        f"schema looks like: {status.inferred or 'empty'}   (head is {HEAD})",
        "",
    ]
    if status.up_to_date:
        lines.append("Up to date. Nothing to run.")
        return "\n".join(lines)

    advice = status.advice()
    if status.stamped is None and status.tables:
        lines.append(
            "This database was created by the app's dev auto-create, so it has "
            "no version stamp.\nStamping records where it already is; upgrading "
            "then applies only what is missing.\nYour existing history is kept."
        )
        lines.append("")
    lines.append("Run:")
    lines.extend(f"  {c}" for c in advice)
    return "\n".join(lines)
