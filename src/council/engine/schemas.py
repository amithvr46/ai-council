"""Structured-output schemas for pipeline stages."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CheckableClaim(BaseModel):
    claim: str = Field(description="The factual claim, stated precisely")
    made_by: Literal["A", "B", "both"]
    why_material: str = Field(description="Why this claim matters to the answer")


class CombinedCheck(BaseModel):
    """Output of the single agreement/disagreement/claims call."""

    agreement: Literal["agree", "partial", "disagree"]
    disagreement_type: Literal["none", "factual", "reasoning", "both"]
    key_disagreements: list[str] = Field(default_factory=list)
    checkable_claims: list[CheckableClaim] = Field(default_factory=list)
    summary: str = Field(description="One-paragraph plain-language comparison")

    @model_validator(mode="after")
    def _invariants(self) -> "CombinedCheck":
        """The pipeline branches on these fields — contradictory combinations
        must fail validation so the provider retry (and, failing that, the
        degraded fallback) handles them instead of the synthesis path.

        Invariants: agree => type none and no key disagreements;
        partial/disagree => type must name what differs (not none);
        disagree => at least one key disagreement listed.
        Checkable claims are allowed at ANY agreement level — two models
        agreeing on a wrong fact is exactly what deep-mode verification
        exists to catch."""
        if self.agreement == "agree":
            if self.disagreement_type != "none":
                raise ValueError("agreement='agree' requires disagreement_type='none'")
            if self.key_disagreements:
                raise ValueError("agreement='agree' cannot list key_disagreements")
        else:
            if self.disagreement_type == "none":
                raise ValueError(
                    f"agreement={self.agreement!r} requires a disagreement_type other than 'none'"
                )
            if self.agreement == "disagree" and not self.key_disagreements:
                raise ValueError("agreement='disagree' requires at least one key_disagreement")
        return self


class Synthesis(BaseModel):
    final_answer: str = Field(description="The single final answer for the user")
    notes: str = Field(default="", description="Anything dropped or flagged during synthesis")


class CritiqueIssue(BaseModel):
    kind: Literal[
        "factual_error",
        "technical_mistake",
        "missing_requirement",
        "bad_assumption",
        "weak_reasoning",
        "better_alternative",
    ]
    severity: Literal["minor", "major", "critical"]
    detail: str = Field(description="The exact claim/part at fault, why, and what would be right")


class Critique(BaseModel):
    """One model's review of the other's answer. Empty issues = sound answer."""

    issues: list[CritiqueIssue] = Field(default_factory=list)
    overall: str = Field(description="One-paragraph verdict on the answer's soundness")


class DimensionVerdict(BaseModel):
    dimension: Literal["accuracy", "completeness", "practical_usefulness", "clarity", "risk"]
    winner: Literal["A", "B", "tie"]
    reason: str


class JudgeVerdict(BaseModel):
    """Dimension-level evaluation — no fake-precision numeric scores."""

    dimensions: list[DimensionVerdict]
    decision: Literal["choose_a", "choose_b", "synthesize", "reject_both", "uncertain"]
    confidence: Literal["low", "medium", "high"]
    rationale: str = Field(description="Why this decision, in plain language")
    final_answer: str = Field(description="The final answer for the user, written by the judge")


class ClaimAudit(BaseModel):
    claim: str
    classification: Literal["SUPPORTED", "INFERRED", "UNSUPPORTED", "CONTRADICTED"]
    note: str = Field(default="", description="Basis for the classification")


class VerifierReport(BaseModel):
    claims: list[ClaimAudit] = Field(default_factory=list)
    verdict: Literal["pass", "revise"]
    reasons: list[str] = Field(
        default_factory=list,
        description="Actionable revision reasons; empty when verdict is pass",
    )


class RevisedAnswer(BaseModel):
    final_answer: str = Field(description="The corrected final answer for the user")
    changes: list[str] = Field(default_factory=list, description="What was changed and why")
