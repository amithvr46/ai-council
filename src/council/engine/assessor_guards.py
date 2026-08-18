"""Mechanical guardrails around the evidence assessor.

The assessor is an LLM reading sources, so it can misread them — an accepted
V1 tradeoff. What is NOT acceptable is a decisive verdict the pipeline then
enforces mechanically while the verdict rests on nothing checkable. These
guards are plain code applied to the assessor's output before anything
downstream sees it:

  G1  Citations must reference evidence items that actually exist.
      Phantom ordinals are stripped and recorded.
  G2  A decisive verdict (SUPPORTED / CONTRADICTED) must cite at least one
      item that actually succeeded. Verdicts citing only unavailable or
      errored evidence are downgraded to INSUFFICIENT.
  G3  A decisive verdict with no surviving citations at all is downgraded
      to INSUFFICIENT — "trust me" is not evidence.
  G4  Downgrades are recorded, never silent, so a run can be audited.

The assessor is also blinded to which model made each claim (see
blind_claims) so model consensus cannot leak in as a signal.
"""

from dataclasses import dataclass, field

from council.engine.schemas import EvidenceAssessment

DECISIVE = ("SUPPORTED_BY_EVIDENCE", "CONTRADICTED_BY_EVIDENCE")


@dataclass
class GuardReport:
    downgrades: list[dict] = field(default_factory=list)
    dropped_citations: list[dict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.downgrades and not self.dropped_citations

    def as_dict(self) -> dict:
        return {"downgrades": self.downgrades, "dropped_citations": self.dropped_citations}


def blind_claims(claims) -> list[str]:
    """Render claims for the assessor WITHOUT attribution.

    The assessor must judge a claim against sources, not against how many
    models asserted it. `made_by` is re-attached afterwards by matching the
    claim text, so persistence keeps the attribution the UI needs.
    """
    return [c.claim for c in claims]


def sanitize(assessment: EvidenceAssessment, items: list) -> tuple[EvidenceAssessment, GuardReport]:
    """Apply G1–G4. Returns the corrected assessment and what was changed."""
    report = GuardReport()
    valid_ordinals = {i + 1 for i in range(len(items))}
    usable_ordinals = {i + 1 for i, item in enumerate(items) if item.status == "ok"}

    corrected = []
    for claim in assessment.claims:
        citations = list(claim.citations or [])

        # G1: strip ordinals that point at nothing.
        phantom = [c for c in citations if c not in valid_ordinals]
        if phantom:
            report.dropped_citations.append(
                {
                    "claim": claim.claim,
                    "phantom_ordinals": phantom,
                    "reason": "no such evidence item",
                }
            )
            citations = [c for c in citations if c in valid_ordinals]

        # G2: citations pointing only at failed/unavailable evidence cannot
        # support a decisive verdict.
        unusable = [c for c in citations if c not in usable_ordinals]
        if unusable:
            report.dropped_citations.append(
                {
                    "claim": claim.claim,
                    "unusable_ordinals": unusable,
                    "reason": "evidence item was unavailable or errored",
                }
            )
        surviving = [c for c in citations if c in usable_ordinals]

        verdict = claim.verdict
        if verdict in DECISIVE and not surviving:
            # G3: decisive verdict with nothing real behind it.
            report.downgrades.append(
                {
                    "claim": claim.claim,
                    "from": verdict,
                    "to": "INSUFFICIENT_EVIDENCE",
                    "reason": (
                        "decisive verdict cited no usable evidence"
                        if citations or phantom
                        else "decisive verdict cited no evidence at all"
                    ),
                }
            )
            verdict = "INSUFFICIENT_EVIDENCE"

        corrected.append(
            claim.model_copy(update={"verdict": verdict, "citations": surviving})
        )

    # If every decisive verdict was downgraded, the correction text is no
    # longer backed by anything: drop it rather than let it steer the answer.
    still_decisive = any(c.verdict in DECISIVE for c in corrected)
    correction = assessment.correction if still_decisive else ""
    if assessment.correction and not still_decisive:
        report.downgrades.append(
            {
                "claim": "(correction text)",
                "from": "asserted",
                "to": "dropped",
                "reason": "no decisive verdict survived to support it",
            }
        )

    return (
        assessment.model_copy(update={"claims": corrected, "correction": correction}),
        report,
    )
