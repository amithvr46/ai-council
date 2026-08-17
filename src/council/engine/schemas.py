"""Structured-output schemas for pipeline stages."""

from typing import Literal

from pydantic import BaseModel, Field


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


class Synthesis(BaseModel):
    final_answer: str = Field(description="The single final answer for the user")
    notes: str = Field(default="", description="Anything dropped or flagged during synthesis")
