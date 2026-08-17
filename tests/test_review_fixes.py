"""Fixes from GPT's Milestone 1 adversarial review:

1. Physical API attempts are tracked separately from logical generations —
   retries can never silently bypass the advertised accounting.
2. CombinedCheck rejects contradictory field combinations at the schema level.
3. Quick mode fails over to the healthy provider, visibly degraded.
"""

import pytest
from pydantic import ValidationError

from council.engine.schemas import CombinedCheck, Synthesis
from council.providers.base import ProviderError
from tests.fakes import FakeProvider, Retried

AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")
SYNTH = Synthesis(final_answer="Final.")


# ---------------------------------------------------------------- 1. retries


async def test_retry_accounting_visible_in_trace(make_engine):
    # The combined check needs its malformed-output retry: 4 logical
    # generations but 5 physical API attempts — both must be recorded.
    openai = FakeProvider("openai", ["GPT answer", Retried(AGREE), SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")

    result = await engine.run("q?", "council")

    assert result["totals"]["model_calls"] == 4
    assert result["totals"]["api_attempts"] == 5
    check_step = next(s for s in result["steps"] if s["stage"] == "combined_check")
    assert check_step["api_attempts"] == 2
    other_steps = [s for s in result["steps"] if s["stage"] != "combined_check"]
    assert all(s["api_attempts"] == 1 for s in other_steps)


# --------------------------------------------------- 2. schema invariants


def test_agree_with_disagreement_type_rejected():
    with pytest.raises(ValidationError):
        CombinedCheck(agreement="agree", disagreement_type="factual", summary="s")


def test_agree_with_key_disagreements_rejected():
    with pytest.raises(ValidationError):
        CombinedCheck(
            agreement="agree",
            disagreement_type="none",
            key_disagreements=["but they differ on X"],
            summary="s",
        )


def test_disagree_with_type_none_rejected():
    with pytest.raises(ValidationError):
        CombinedCheck(
            agreement="disagree",
            disagreement_type="none",
            key_disagreements=["x"],
            summary="s",
        )


def test_disagree_without_key_disagreements_rejected():
    with pytest.raises(ValidationError):
        CombinedCheck(agreement="disagree", disagreement_type="factual", summary="s")


def test_partial_with_type_none_rejected():
    with pytest.raises(ValidationError):
        CombinedCheck(agreement="partial", disagreement_type="none", summary="s")


def test_partial_with_type_is_valid():
    c = CombinedCheck(agreement="partial", disagreement_type="reasoning", summary="s")
    assert c.agreement == "partial"


def test_agreement_may_still_carry_checkable_claims():
    # Two models agreeing on a wrong fact is what deep verification catches —
    # claims must be allowed at any agreement level.
    c = CombinedCheck(
        agreement="agree",
        disagreement_type="none",
        checkable_claims=[
            {"claim": "Default port is 5432", "made_by": "both", "why_material": "core answer"}
        ],
        summary="s",
    )
    assert len(c.checkable_claims) == 1


# ---------------------------------------------------- 3. quick failover


async def test_quick_fails_over_to_healthy_provider(make_engine):
    openai = FakeProvider("openai", [ProviderError("openai down")])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, quick_mode_strategy="openai")

    result = await engine.run("q?", "quick")

    assert result["status"] == "complete"
    assert result["final_answer"] == "Claude answer"
    assert result["degraded"] is True
    stages = {s["stage"]: s for s in result["steps"]}
    assert stages["candidate_a"]["status"] == "error"
    assert stages["candidate_fallback"]["provider"] == "anthropic"


async def test_quick_fails_when_both_providers_fail(make_engine):
    openai = FakeProvider("openai", [ProviderError("down")])
    anthropic = FakeProvider("anthropic", [ProviderError("also down")])
    engine = make_engine(openai, anthropic, quick_mode_strategy="openai")

    with pytest.raises(ProviderError):
        await engine.run("q?", "quick")
