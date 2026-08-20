"""Migration-state diagnosis.

Written after a real failure: a database created by the app's dev auto-create
has no Alembic stamp, so `alembic current` prints nothing and `upgrade head`
then fails trying to create tables that already exist.
"""

from council.db.status import HEAD, DbStatus, infer_revision, render

ALL_TABLES = [
    "requests", "steps", "conversations", "evidence_items", "claim_assessments",
    "budget_settings", "documents", "career_profile", "technology_cache",
    "source_conflicts", "artifacts", "career_denials",
]
ALL_COLUMNS = [
    "id", "conversation_id", "total_api_attempts", "evidence_used",
    "outcome_kind", "data_class",
]
# Columns on tables other than `requests` that a revision is inferred from.
ALL_TABLE_COLUMNS = {
    "career_denials": [
        "term", "kind", "statement", "active", "superseded_by", "history",
    ],
}


def _status(tables, columns, stamped=None, table_columns=None):
    table_columns = ALL_TABLE_COLUMNS if table_columns is None else table_columns
    return DbStatus(
        url="sqlite:///x", tables=tables, request_columns=columns,
        stamped=stamped, inferred=infer_revision(tables, columns, table_columns),
    )


def test_a_current_schema_is_recognised():
    assert infer_revision(ALL_TABLES, ALL_COLUMNS, ALL_TABLE_COLUMNS) == HEAD


def test_a_schema_stopped_at_0010_is_not_rounded_up_to_0011():
    """0011 adds only `career_denials.history`, so its table evidence is
    identical to 0010's. Without column-level evidence a database missing the
    audit column would be reported as fully migrated and the user would be told
    there is nothing to run."""
    without_history = {
        "career_denials": [c for c in ALL_TABLE_COLUMNS["career_denials"] if c != "history"]
    }
    assert infer_revision(ALL_TABLES, ALL_COLUMNS, without_history) == "0010"
    assert _status(ALL_TABLES, ALL_COLUMNS, stamped="0010",
                   table_columns=without_history).advice() == ["alembic upgrade head"]


def test_unknown_table_columns_stop_inference_conservatively():
    """A caller that cannot report the extra columns must not have 0011
    assumed for it — the same conservative direction as the loop break."""
    assert infer_revision(ALL_TABLES, ALL_COLUMNS) == "0010"


def test_an_empty_database_infers_nothing():
    assert infer_revision([], []) is None


def test_a_pre_phase3_schema_stops_at_0007():
    """The realistic case: a database made before outcome_kind existed."""
    columns = [c for c in ALL_COLUMNS if c not in ("outcome_kind", "data_class")]
    assert infer_revision(ALL_TABLES, columns) == "0007"


def test_inference_stops_at_the_first_incomplete_revision():
    """Conservative on purpose: a partially applied schema reports the last
    COMPLETE revision rather than optimistically rounding up."""
    tables = [t for t in ALL_TABLES if t != "budget_settings"]
    # documents/career_profile exist, but 0005 does not — so 0006 must not be
    # claimed just because its tables happen to be there.
    assert infer_revision(tables, ALL_COLUMNS) == "0004"


def test_an_unstamped_current_schema_is_stamped_not_upgraded():
    """Running upgrade here would try to create tables that already exist."""
    assert _status(ALL_TABLES, ALL_COLUMNS).advice() == ["alembic stamp head"]


def test_an_unstamped_old_schema_is_stamped_then_upgraded():
    columns = [c for c in ALL_COLUMNS if c not in ("outcome_kind", "data_class")]
    assert _status(ALL_TABLES, columns).advice() == [
        "alembic stamp 0007",
        "alembic upgrade head",
    ]


def test_an_empty_database_just_migrates():
    assert _status([], []).advice() == ["alembic upgrade head"]


def test_a_stamped_but_behind_database_upgrades():
    assert _status(ALL_TABLES, ALL_COLUMNS, stamped="0007").advice() == [
        "alembic upgrade head"
    ]


def test_a_current_database_needs_nothing():
    status = _status(ALL_TABLES, ALL_COLUMNS, stamped=HEAD)
    assert status.up_to_date is True
    assert status.advice() == []
    assert "Nothing to run" in render(status)


def test_a_stamp_ahead_of_the_schema_is_called_out():
    """The dangerous case: Alembic believes migrations ran that did not, so
    nothing will fix it automatically and it has to be said out loud."""
    columns = [c for c in ALL_COLUMNS if c != "data_class"]
    advice = _status(ALL_TABLES, columns, stamped=HEAD).advice()
    assert advice[0].startswith("# WARNING")
    assert "alembic stamp 0008" in advice


def test_the_report_explains_an_unstamped_database():
    text = render(_status(ALL_TABLES, ALL_COLUMNS))
    assert "dev auto-create" in text
    assert "existing history is kept" in text
