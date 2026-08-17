from council.engine.schemas import CombinedCheck
from council.providers.base import validate_or_none


def test_valid_json_parses():
    text = (
        '{"agreement": "agree", "disagreement_type": "none", '
        '"key_disagreements": [], "checkable_claims": [], "summary": "same"}'
    )
    parsed = validate_or_none(CombinedCheck, text)
    assert parsed is not None
    assert parsed.agreement == "agree"


def test_fenced_json_parses():
    text = (
        '```json\n{"agreement": "disagree", "disagreement_type": "factual", '
        '"key_disagreements": ["x"], "checkable_claims": [], "summary": "s"}\n```'
    )
    parsed = validate_or_none(CombinedCheck, text)
    assert parsed is not None
    assert parsed.agreement == "disagree"


def test_malformed_json_returns_none():
    assert validate_or_none(CombinedCheck, "not json at all") is None


def test_schema_violation_returns_none():
    text = '{"agreement": "maybe", "disagreement_type": "none", "summary": "s"}'
    assert validate_or_none(CombinedCheck, text) is None
