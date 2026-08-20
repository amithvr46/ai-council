"""The migration chain must actually run on SQLite, not just Postgres.

Local development uses SQLite, so a migration that only works on Postgres is a
migration the user cannot apply. Until this test existed the chain died on
`alembic upgrade head` with a MissingGreenlet error, and the schema stayed in
sync only because create_all quietly papered over it — which is exactly how a
column goes missing in production.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _upgrade(db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO,
        env={
            "PATH": "/usr/bin:/bin",
            "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
            "OPENAI_API_KEY": "test",
            "ANTHROPIC_API_KEY": "test",
        },
        capture_output=True,
        text=True,
    )


def test_full_chain_applies_to_sqlite(tmp_path):
    db = tmp_path / "migrated.db"
    result = _upgrade(db)
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {
        "requests",
        "steps",
        "conversations",
        "evidence_items",
        "claim_assessments",
        "budget_settings",
        "documents",
        "career_profile",
    } <= tables

    def columns(table: str) -> set[str]:
        return {r[1] for r in conn.execute(f"pragma table_info({table})")}

    # The 2B tables the ingestion path depends on.
    assert {"authority", "content_hash", "detected_kind", "text", "truncated"} <= columns(
        "documents"
    )
    assert {"technologies", "domains", "roles", "employers"} <= columns("career_profile")
    # The foreign key added in 0003, which is what broke on SQLite.
    assert "conversation_id" in columns("requests")
    # 0008: intent alongside processing mode.
    assert "outcome_kind" in columns("requests")
    # 0010: durable negative career facts. Without this table a denial only
    # lives for one request, and "I have never used Harness" has to be repeated
    # on every resume or the technology comes back.
    assert "career_denials" in tables
    assert {"term", "kind", "statement", "active", "superseded_by"} <= columns(
        "career_denials"
    )
    # 0009: routing statistics must never mix data populations.
    assert "data_class" in columns("requests")


def test_alembic_resolves_the_url_from_settings_not_raw_environment(monkeypatch, tmp_path):
    """The bug this guards: env.py read os.environ directly, so a DATABASE_URL
    set in .env was ignored. Alembic silently fell back to the Postgres default
    and hung against a database that was never running."""
    import council.config
    from council.db.session import sync_database_url

    council.config.get_settings.cache_clear()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./from-dotenv.db\n")

    resolved = sync_database_url()
    council.config.get_settings.cache_clear()

    assert "from-dotenv.db" in resolved
    # Async drivers are swapped for their sync equivalents; Alembic runs sync.
    assert "+aiosqlite" not in resolved
    assert "+pysqlite" in resolved


def test_the_postgres_driver_is_swapped_too():
    from council.db.session import sync_database_url

    assert sync_database_url("postgresql+asyncpg://u:p@host/db").startswith(
        "postgresql+psycopg://"
    )


def test_credentials_are_masked_before_being_printed():
    from council.db.session import mask_url

    masked = mask_url("postgresql+psycopg://council:secret@localhost:5432/council")
    assert "secret" not in masked
    assert "localhost:5432/council" in masked
