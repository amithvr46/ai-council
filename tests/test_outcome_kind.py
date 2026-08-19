"""outcome_kind — WHAT was wanted, recorded before Auto needs it.

The point of these tests is that intent is captured at the moment it is known.
It cannot be reconstructed later, so a regression here is silent: the column
would still exist, still be queryable and simply contain nothing useful by the
time Phase 3 arrives.
"""

import pytest
from sqlalchemy import select

from council import outcomes
from council.db.models import ArtifactRow, Request
from council.db.session import session_scope
from council.documents.store import save_artifact
from council.engine.schemas import CombinedCheck, Synthesis
from tests.fakes import FakeProvider


def _engine(make_engine):
    return make_engine(
        FakeProvider(
            "openai",
            [
                "a",
                CombinedCheck(agreement="agree", disagreement_type="none", summary="s"),
                Synthesis(final_answer="final"),
            ],
        ),
        FakeProvider("anthropic", ["b"]),
        check_provider="openai",
    )


# ------------------------------------------------------------ vocabulary


def test_vocabulary_is_extensible_without_a_migration():
    """A new workflow adds a kind in code. Nothing touches the schema."""
    assert "deployment_review" not in outcomes.KNOWN_OUTCOME_KINDS
    try:
        kind = outcomes.register("Deployment Review")
        assert kind == "deployment_review"
        assert outcomes.is_known("deployment-review")
    finally:
        outcomes.KNOWN_OUTCOME_KINDS.discard("deployment_review")


def test_unknown_kinds_are_preserved_not_rejected():
    """The known set is a vocabulary, not a gate. A workflow that forgets to
    register still records what it meant — dropping the label would defeat the
    reason for collecting it."""
    assert outcomes.normalise("some_future_workflow") == "some_future_workflow"
    assert outcomes.is_known("some_future_workflow") is False


def test_missing_intent_reads_as_general_not_as_a_guess():
    assert outcomes.normalise(None) == outcomes.GENERAL


def test_normalisation_is_stable_across_spellings():
    for spelling in ("Resume Tailor", "resume-tailor", "  RESUME_TAILOR "):
        assert outcomes.normalise(spelling) == outcomes.RESUME_TAILOR


def test_oversized_and_empty_kinds_are_refused():
    with pytest.raises(ValueError):
        outcomes.normalise("")
    with pytest.raises(ValueError):
        outcomes.normalise("x" * (outcomes.MAX_LENGTH + 1))


# ------------------------------------------------- stamped where it is known


async def test_ask_pipeline_stamps_question_answer(db, make_engine):
    """The ask path is a known workflow, so it stamps deterministically —
    no model call is bought to classify a request."""
    result = await _engine(make_engine).run("q?", "council")
    async with session_scope() as s:
        row = await s.get(Request, result["id"])
        assert row.outcome_kind == outcomes.QUESTION_ANSWER


async def test_create_then_run_keeps_the_kind(db, make_engine):
    """The async API path creates the row before executing; the label must
    survive that split."""
    engine = _engine(make_engine)
    request_id = await engine.create("q?", "council")
    async with session_scope() as s:
        assert (await s.get(Request, request_id)).outcome_kind == outcomes.QUESTION_ANSWER
    await engine.run("q?", "council", request_id=request_id)
    async with session_scope() as s:
        assert (await s.get(Request, request_id)).outcome_kind == outcomes.QUESTION_ANSWER


async def test_a_caller_can_stamp_a_different_outcome(db, make_engine):
    """Future workflows reuse the engine without inheriting question_answer."""
    result = await _engine(make_engine).run(
        "diagnose this", "council", outcome_kind=outcomes.TROUBLESHOOTING
    )
    async with session_scope() as s:
        assert (await s.get(Request, result["id"])).outcome_kind == "troubleshooting"


async def test_the_trace_reports_the_outcome(db, make_engine):
    engine = _engine(make_engine)
    result = await engine.run("q?", "council")
    assert (await engine.get_request(result["id"]))["outcome_kind"] == outcomes.QUESTION_ANSWER


async def test_resume_artifacts_use_the_same_vocabulary(db):
    """Artifacts and requests must be readable as one intent stream; a
    translation layer between them is exactly the rework this avoids."""
    await save_artifact(
        kind="Resume Tailor",
        jd_document_id=None,
        role_family="infrastructure",
        title="t",
        content={},
        trace={},
        cost_usd=0.1,
    )
    async with session_scope() as s:
        row = (await s.execute(select(ArtifactRow))).scalars().one()
        assert row.kind == outcomes.RESUME_TAILOR


async def test_outcomes_are_queryable_as_a_group(db, make_engine):
    """The shape Auto actually needs: cost and behaviour grouped by intent."""
    engine = _engine(make_engine)
    await engine.run("q1?", "council")
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Request).where(Request.outcome_kind == outcomes.QUESTION_ANSWER)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].total_cost_usd >= 0
