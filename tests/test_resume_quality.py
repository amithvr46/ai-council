"""Amendment B — the resume has to be good, not merely true.

The correctness work of August 2026 settled what the system may SAY. This
covers whether what it says reads like an experienced engineer describing real
work, and it adds no new stage to do it: the existing
generate -> review -> at most one correction -> DOCX flow already had the right
shape, and four of the six review lenses.

WHAT WAS MISSING

  - B2, section coherence. Every existing lens was bullet-level. Nothing read a
    whole employment section and asked whether it leaves the reader able to
    picture the work. This is the requirement a bullet-by-bullet review
    structurally cannot meet.
  - B3, seniority expression. The tone lens banned inflated adjectives — the
    negative half. Nothing asked the positive half: does the work described
    read at this engineer's level.

THE RISK THE FIX INTRODUCES

Asking for a more coherent section is the only instruction in this workflow
that pushes the correction pass to ADD material, and the cheapest way to
satisfy it is to invent the missing part. The prompt forbids that. The
deterministic guard below makes it ineffective: a correction that introduces
truth violations the reviewed draft did not have is discarded, and the
reviewed draft is kept.

A5 outranks narrative coherence. These tests are where that is enforced rather
than asserted.
"""


from council.documents.discovery import DiscoveryResult
from council.documents.profile import NO_DENIALS, CareerProfile, assemble_confirmed
from council.documents.schemas import (
    STORY_ELEMENTS,
    ResumeDraft,
    ResumeRole,
    ReviewFinding,
    ReviewReport,
    SectionAssessment,
)
from council.documents.workflow import JDAnalysis, ResumeWorkflow, _truth_violations
from council.engine.prompts import default_registry
from tests.fakes import FakeProvider


def _confirmed():
    return assemble_confirmed(CareerProfile(), denials=NO_DENIALS)


def _workflow(draft: FakeProvider, review: FakeProvider) -> ResumeWorkflow:
    return ResumeWorkflow(
        {"drafter": draft, "reviewer": review},
        default_registry(),
        draft_provider="drafter",
        review_provider="reviewer",
        flagship_models={"drafter": "fake-flagship", "reviewer": "fake-flagship"},
        cheap_models={"drafter": "fake-cheap", "reviewer": "fake-cheap"},
    )


def _draft(bullets=None) -> ResumeDraft:
    return ResumeDraft(
        headline="Cloud / DevOps Engineer",
        summary="Cloud engineer working across Azure infrastructure and delivery.",
        skills={"Cloud": ["Azure", "Terraform", "Kubernetes"]},
        roles=[
            ResumeRole(
                title="Cloud Engineer",
                employer="Acme",
                bullets=bullets
                or [
                    "Ran and reviewed Terraform plans for Azure infrastructure changes.",
                    "Supported AKS workloads during releases by reviewing pod health.",
                ],
            )
        ],
    )


def _analysis(gaps=()) -> JDAnalysis:
    return JDAnalysis("infrastructure", [], DiscoveryResult(gaps=list(gaps)))


class _Trace:
    """Minimal stand-in for WorkflowTrace when calling a stage directly."""

    def __init__(self):
        self.stages = []
        self.cost_usd = 0.0
        self.model_calls = 0

    def record(self, stage, **detail):
        self.stages.append({"stage": stage, **detail})

    def names(self):
        return [s["stage"] for s in self.stages]


# ==========================================================================
# The two readings Amendment B adds
# ==========================================================================


def test_the_review_report_carries_section_and_seniority_readings():
    report = ReviewReport(
        sections=[
            SectionAssessment(section="Acme", tells_the_story=True),
            SectionAssessment(
                section="Globex",
                tells_the_story=False,
                missing=["operations", "troubleshooting"],
                comment="Names tools; never says what was operated.",
            ),
        ],
        seniority_expression="Reads at senior level; ownership is visible.",
    )
    assert [s.section for s in report.incoherent_sections()] == ["Globex"]
    assert set(report.incoherent_sections()[0].missing) <= set(STORY_ELEMENTS)


def test_the_active_review_prompt_asks_for_the_section_reading():
    """The schema alone changes nothing if the prompt never populates it. The
    registry serves the highest version, so this also proves v2 is live."""
    prompt = default_registry().get("resume_review")
    assert prompt.version >= 2
    for element in STORY_ELEMENTS:
        assert element in prompt.text
    assert "sections" in prompt.text and "tells_the_story" in prompt.text


def test_both_prompts_state_that_truth_outranks_coherence():
    """A5 over B2, in the two places a model could trade one for the other."""
    review = default_registry().get("resume_review").text.lower()
    correct = default_registry().get("resume_correct").text.lower()
    # The reviewer must not propose a fix that supplies a missing element.
    assert "outranks narrative coherence" in review
    assert "it stays missing" in review
    # The corrector must be told the same thing where it would act on it.
    assert "leave it missing" in correct
    assert correct.count("invent") >= 2


# ==========================================================================
# When revision happens, and when it does not
# ==========================================================================


async def test_a_clean_draft_is_not_revised_just_because_it_could_be():
    """Amendment B, explicitly: a revision budget existing is not a reason to
    spend it. No findings, no blocking review -> no model call at all."""
    drafter = FakeProvider("drafter")  # empty queue: any call raises
    workflow = _workflow(drafter, FakeProvider("reviewer"))
    trace = _Trace()
    clean = ReviewReport(
        would_submit=True,
        sections=[SectionAssessment(section="Acme", tells_the_story=True)],
    )

    result = await workflow.correct(_draft(), [], clean, trace)

    assert result is not None
    assert drafter.calls == []
    assert "correction_skipped" in trace.names()


async def test_an_incoherent_section_earns_the_one_correction():
    corrected = _draft(
        bullets=[
            "Managed AKS application environments through Terraform, supporting "
            "deployment and configuration changes across production Azure "
            "environments.",
            "Troubleshot pod, networking and dependency issues during releases.",
        ]
    )
    drafter = FakeProvider("drafter", [corrected])
    workflow = _workflow(drafter, FakeProvider("reviewer"))
    trace = _Trace()
    review = ReviewReport(
        findings=[
            ReviewFinding(
                lens="section_coherence",
                severity="major",
                location="Acme",
                problem="Names tools without showing the work.",
                fix="Say what was done with them.",
            )
        ],
        sections=[
            SectionAssessment(
                section="Acme",
                tells_the_story=False,
                missing=["operations", "troubleshooting"],
                comment="Reader cannot tell what was operated.",
            )
        ],
    )

    result = await workflow.correct(_draft(), [], review, trace)

    assert len(drafter.calls) == 1  # exactly one, never a loop
    assert result.roles[0].bullets == corrected.roles[0].bullets


async def test_the_correction_is_told_which_elements_are_missing():
    """"Improve this section" is the instruction that produces an invented
    environment. The named elements are what make the fix bounded."""
    drafter = FakeProvider("drafter", [_draft()])
    workflow = _workflow(drafter, FakeProvider("reviewer"))
    review = ReviewReport(
        sections=[
            SectionAssessment(
                section="Acme",
                tells_the_story=False,
                missing=["operations", "outcome"],
                comment="No operational dimension.",
            )
        ],
        findings=[
            ReviewFinding(lens="section_coherence", severity="major",
                          location="Acme", problem="thin", fix="expand from context")
        ],
    )
    await workflow.correct(_draft(), [], review, _Trace())

    sent = drafter.calls[0]["messages"][-1]["content"]
    assert "Acme: unclear — operations, outcome" in sent
    assert "leave it missing" in sent
    system = drafter.calls[0]["messages"][0]["content"]
    assert "never by" in system.lower()


# ==========================================================================
# The guard: coherence may not be bought with fabrication
# ==========================================================================


def test_truth_violations_counts_only_truth_classes():
    findings = [
        {"class": "GAP_TECHNOLOGY", "location": "role 1 bullet 1"},
        {"class": "FABRICATED_FACT", "location": "role 1 bullet 2"},
        {"class": "STYLE_ADVISORY", "location": "role 1 bullet 3"},
        {"class": "JD_MIRRORING", "location": "role 1 bullet 4"},
    ]
    assert _truth_violations(findings) == {
        ("GAP_TECHNOLOGY", "role 1 bullet 1"),
        ("FABRICATED_FACT", "role 1 bullet 2"),
    }


async def test_a_correction_that_invents_a_gap_technology_is_discarded(monkeypatch):
    """The whole point of the guard. The reviewed draft was thin; the
    correction made it read better by adding a technology the career does not
    establish. Thin and true beats polished and false."""
    fabricated = _draft(
        bullets=[
            "Ran and reviewed Terraform plans for Azure infrastructure changes.",
            "Operated GKE clusters on GCP during production releases.",  # invented
        ]
    )
    drafter = FakeProvider("drafter", [fabricated])
    workflow = _workflow(drafter, FakeProvider("reviewer"))

    original = _draft()
    analysis = _analysis(gaps=["gcp", "gke"])
    confirmed = _confirmed()
    trace = _Trace()

    before = workflow.check(original, analysis, confirmed)
    corrected = await workflow.correct(original, [], _incoherent_review(), trace)
    after = workflow.check(corrected, analysis, confirmed)

    # The correction really did introduce a gap technology...
    assert _truth_violations(after) > _truth_violations(before)
    # ...which is exactly what run() reverts. Proven end to end below.


def _incoherent_review() -> ReviewReport:
    return ReviewReport(
        findings=[
            ReviewFinding(lens="section_coherence", severity="major",
                          location="Acme", problem="thin", fix="show the work")
        ],
        sections=[
            SectionAssessment(section="Acme", tells_the_story=False,
                              missing=["operations"], comment="thin"),
        ],
    )


async def test_run_reverts_a_correction_that_introduced_a_truth_violation():
    """End to end through run(), which is where the guard actually lives."""
    from council.documents.schemas import ExperienceSelection, RoleEmphasis

    clean = _draft()
    fabricated = _draft(
        bullets=[
            "Ran and reviewed Terraform plans for Azure infrastructure changes.",
            "Operated GKE clusters on GCP during production releases.",
        ]
    )
    plan = ExperienceSelection(
        target_summary="Cloud infrastructure",
        priority_themes=["terraform"],
        roles=[RoleEmphasis(employer="Acme", title="Cloud Engineer", bullet_budget=2)],
    )
    drafter = FakeProvider("drafter", [plan, clean, fabricated])
    reviewer = FakeProvider("reviewer", [_incoherent_review()])
    workflow = _workflow(drafter, reviewer)

    result = await workflow.run(
        "Infrastructure Engineer working on GCP and GKE with Terraform.",
        CareerProfile(),
        [],
    )

    stages = [s["stage"] for s in result.trace.stages]
    assert "correction_reverted" in stages
    # The shipped resume is the reviewed draft, not the fabricated one.
    bullets = result.draft.roles[0].bullets
    assert not any("GCP" in b or "GKE" in b for b in bullets)


async def test_a_correction_that_only_fixes_things_is_kept():
    """The guard must not revert good corrections. Trading a style advisory
    for a fixed fabrication is a good trade, and a raw finding count would
    get it wrong."""
    from council.documents.schemas import ExperienceSelection, RoleEmphasis

    # The draft invents a number; the correction removes it and leaves a
    # harmless advisory-level phrasing behind.
    bad = _draft(
        bullets=[
            "Reduced deployment time by 47% across the Azure estate.",
            "Ran and reviewed Terraform plans for Azure infrastructure changes.",
        ]
    )
    fixed = _draft(
        bullets=[
            "Shortened deployment cycles by automating Azure release steps.",
            "Ran and reviewed Terraform plans for Azure infrastructure changes.",
        ]
    )
    plan = ExperienceSelection(
        target_summary="Cloud infrastructure",
        priority_themes=["terraform"],
        roles=[RoleEmphasis(employer="Acme", title="Cloud Engineer", bullet_budget=2)],
    )
    drafter = FakeProvider("drafter", [plan, bad, fixed])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=False)])
    workflow = _workflow(drafter, reviewer)

    result = await workflow.run("Cloud Engineer with Terraform and Azure.", CareerProfile(), [])

    stages = [s["stage"] for s in result.trace.stages]
    assert "correction_reverted" not in stages
    assert "47%" not in " ".join(result.draft.roles[0].bullets)


async def test_the_workflow_still_makes_no_extra_model_call():
    """Amendment B adds quality dimensions to an existing review call and an
    existing correction call. It must not add a stage — the cost delta is
    output tokens, not another round trip."""
    from council.documents.schemas import ExperienceSelection, RoleEmphasis

    plan = ExperienceSelection(
        target_summary="Cloud infrastructure",
        priority_themes=["terraform"],
        roles=[RoleEmphasis(employer="Acme", title="Cloud Engineer", bullet_budget=2)],
    )
    drafter = FakeProvider("drafter", [plan, _draft()])
    reviewer = FakeProvider(
        "reviewer",
        [ReviewReport(would_submit=True,
                      sections=[SectionAssessment(section="Acme", tells_the_story=True)])],
    )
    workflow = _workflow(drafter, reviewer)

    result = await workflow.run("Cloud Engineer with Terraform and Azure.", CareerProfile(), [])

    # select + draft on the drafter, review on the reviewer. No correction.
    assert len(drafter.calls) == 2
    assert len(reviewer.calls) == 1
    assert "correction_skipped" in [s["stage"] for s in result.trace.stages]
