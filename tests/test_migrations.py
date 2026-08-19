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
