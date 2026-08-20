"""Phase 2C: the resume workflow and the rules Amendment A adds.

These tests exist because every rule below is one a prompt could be talked out
of. Each is enforced in code, so each gets a regression test.
"""


import pytest

from council.documents.claims import ClaimClass, classify, find_relationship_claims
from council.documents.conflicts import find_conflicts
from council.documents.discovery import (
    DiscoveryCache,
    candidate_terms,
    discover,
    known_vocabulary,
)
from council.documents.profile import NO_DENIALS, CareerProfile, assemble_confirmed
from council.documents.schemas import (
    ExperienceSelection,
    ResumeDraft,
    ResumeRole,
    ReviewFinding,
    ReviewReport,
    RoleEmphasis,
    TechnologyDiscovery,
)
from council.documents.workflow import ResumeWorkflow
from council.engine.prompts import default_registry
from tests.fakes import FakeProvider

GCP_JD = (
    "Infrastructure Engineer. You will work primarily on Google Cloud Platform, "
    "managing GKE clusters and Cloud Run services. You will write Terraform for "
    "all infrastructure, build CI/CD pipelines and support production Kubernetes "
    "workloads with monitoring and incident response. Docker, Helm, Linux and "
    "Python or Bash scripting required."
)


def _confirmed():
    return assemble_confirmed(CareerProfile(), denials=NO_DENIALS)


# ---------------------------------------------- A5: invented relationships


def test_confirmed_nouns_do_not_license_an_invented_relationship():
    """GPT's Amendment A5 example. Every technology is confirmed; the specific
    history binding them is not, and noun-checking alone lets it through."""
    text = (
        "Built Kubernetes clusters on AWS using Terraform and automated the "
        "entire platform through Python."
    )
    terms = ["kubernetes", "aws", "terraform", "python"]
    finding = classify(text, confirmed_terms=set(terms), candidate_terms=terms)
    assert finding.classification is ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM
    assert "multi_technology_relationship" in " ".join(finding.reasons)


@pytest.mark.parametrize(
    "text,terms",
    [
        (
            "Built and supported Azure DevOps, GitHub Actions and Harness delivery "
            "workflows for application and infrastructure releases.",
            ["azure devops", "github actions", "harness"],
        ),
        (
            "Investigated deployment failures by reviewing rollout status, pod events, "
            "configuration changes and application telemetry.",
            ["kubernetes"],
        ),
        (
            "Automated recurring operational checks with Python, PowerShell and Bash "
            "and maintained runbooks with incident timelines.",
            ["python", "powershell", "bash"],
        ),
        (
            "Troubleshot RBAC, Managed Identity and Key Vault issues alongside the "
            "security team.",
            ["rbac", "managed identity", "key vault"],
        ),
        (
            "Implemented Azure Monitor, Log Analytics, Application Insights and Grafana "
            "dashboards used for release validation.",
            ["azure monitor", "log analytics", "application insights", "grafana"],
        ),
    ],
)
def test_ordinary_multi_tool_work_stays_permitted(text, terms):
    """A4/A7: the classifier must not become so strict that real engineering
    wording is rejected. Over-blocking defeats the purpose as surely as
    over-claiming."""
    finding = classify(text, confirmed_terms=set(terms), candidate_terms=terms)
    assert finding.classification is ClaimClass.PERMITTED_EXPANSION, finding.reasons


def test_totalising_scope_is_a_relationship_claim():
    assert "totalising_scope" in find_relationship_claims(
        "Owned the entire Azure infrastructure estate.", ["azure"]
    )


def test_managed_identity_is_a_product_not_an_ownership_claim():
    """Microsoft named a service "Managed Identity". Without the product-name
    scrub a truthful security bullet reads as a leadership claim."""
    text = "Troubleshot Managed Identity and Key Vault access issues with the platform team."
    finding = classify(
        text,
        confirmed_terms={"managed identity", "key vault"},
        candidate_terms=["managed identity", "key vault"],
    )
    assert finding.classification is ClaimClass.PERMITTED_EXPANSION


# ------------------------------------------------- A2: conditional discovery


async def test_discovery_is_mechanical_when_nothing_is_unknown():
    """No leftover terms means no model call. Being conditional is the point."""
    calls = []

    async def ask(candidates):
        calls.append(candidates)
        return {"technologies": []}

    result = await discover(GCP_JD, _confirmed(), ask_model=ask)
    assert result.gaps  # gcp/gke/cloud run found mechanically
    assert "gcp" in result.gaps
    assert "terraform" in result.supported


async def test_model_may_discover_a_technology_but_never_confirm_it():
    """THE A2 boundary. The model is told the term is a technology; the career
    has no evidence of it; the answer must still be GAP."""
    jd = "You will use Quantumdeploy and Zephyrflow to ship services."
    confirmed = _confirmed()

    async def ask(candidates):
        # Maximally cooperative model: says everything is a technology.
        return {"technologies": [{"term": c, "kind": "tool"} for c in candidates]}

    result = await discover(jd, confirmed, ask_model=ask)
    assert result.escalated is True
    assert "quantumdeploy" in result.gaps
    assert "quantumdeploy" not in result.supported


async def test_discovered_technology_is_supported_only_when_career_evidence_exists():
    jd = "You will use Quantumdeploy daily."
    confirmed = assemble_confirmed(
        CareerProfile(technologies=["quantumdeploy"]),
        [{"authority": "master_resume", "title": "r", "text": "Ran Quantumdeploy releases."}],
        denials=NO_DENIALS,
    )

    async def ask(candidates):
        return {"technologies": [{"term": c, "kind": "tool"} for c in candidates]}

    result = await discover(jd, confirmed, ask_model=ask)
    assert "quantumdeploy" in result.supported
    assert "quantumdeploy" not in result.gaps


async def test_cache_prevents_paying_twice_for_the_same_vocabulary():
    jd = "Experience with Quantumdeploy required."
    cache = DiscoveryCache()
    calls = []

    async def ask(candidates):
        calls.append(list(candidates))
        return {"technologies": [{"term": c, "kind": "tool"} for c in candidates]}

    await discover(jd, _confirmed(), cache=cache, ask_model=ask)
    await discover(jd, _confirmed(), cache=cache, ask_model=ask)
    assert len(calls) == 1, "second identical JD escalated again"


async def test_negative_answers_are_cached_too():
    """Learning a word is NOT a technology has to stick, or the same
    non-technology is re-escalated on every similar JD."""
    jd = "You will partner with Workstream and Peoplehub stakeholders."
    cache = DiscoveryCache()
    calls = []

    async def ask(candidates):
        calls.append(list(candidates))
        return {"technologies": []}  # none of them are technologies

    await discover(jd, _confirmed(), cache=cache, ask_model=ask)
    assert calls, "expected an escalation on the first pass"
    await discover(jd, _confirmed(), cache=cache, ask_model=ask)
    assert len(calls) == 1


def test_ordinary_jd_prose_does_not_become_candidates():
    """If every capitalised word escalated, the conditional design would be
    conditional in name only."""
    jd = (
        "We Are Hiring. You will work with our Engineering team on Cloud "
        "Infrastructure. Bachelor degree required. This is a Full Time role."
    )
    candidates = candidate_terms(jd, known_vocabulary(_confirmed()), set())
    assert candidates == [], candidates


# ------------------------------------------------------------- A3: conflicts


def test_material_date_conflict_is_persisted_not_resolved():
    a = "Cloud DevOps Engineer | Fidelity Investments | Charlotte, NC | Nov 2022 - Oct 2024"
    b = "Cloud DevOps Engineer | Fidelity Investments | Charlotte, NC | Sep 2022 - Oct 2024"
    conflicts = find_conflicts(
        [
            {"authority": "master_resume", "title": "master", "text": a},
            {"authority": "supporting", "title": "notes", "text": b},
        ]
    )
    assert len(conflicts) == 1
    assert conflicts[0].kind == "role_dates"
    assert set(conflicts[0].distinct_values) == {"nov 2022 - oct 2024", "sep 2022 - oct 2024"}


def test_wording_differences_are_never_conflicts():
    """A3 explicitly must not fire on ordinary Tier 2 wording. Two drafts
    describing the same work differently are drafts, not disagreements."""
    dates = "Cloud DevOps Engineer | Fidelity Investments | NC | Nov 2022 - Oct 2024"
    a = f"Built reusable Terraform modules for Azure services. {dates}"
    b = f"Developed Terraform modules used across multiple environments. {dates}"
    assert (
        find_conflicts(
            [
                {"authority": "master_resume", "title": "m", "text": a},
                {"authority": "supporting", "title": "s", "text": b},
            ]
        )
        == []
    )


def test_a_tailored_resume_is_not_a_conflicting_authority():
    """A tailored resume is a selective view (A1). Its re-emphasis is expected
    and must not register as the career sources disagreeing."""
    a = "Cloud DevOps Engineer | Fidelity Investments | NC | Nov 2022 - Oct 2024"
    b = "Cloud DevOps Engineer | Fidelity Investments | NC | Jan 2023 - Oct 2024"
    assert (
        find_conflicts(
            [
                {"authority": "master_resume", "title": "m", "text": a},
                {"authority": "tailored_resume", "title": "t", "text": b},
            ]
        )
        == []
    )


def test_agreeing_sources_produce_no_conflict():
    line = "Cloud DevOps Engineer | Fidelity Investments | NC | Nov 2022 - Oct 2024"
    assert (
        find_conflicts(
            [
                {"authority": "master_resume", "title": "m", "text": line},
                {"authority": "supporting", "title": "s", "text": line},
            ]
        )
        == []
    )


# --------------------------------------------------- workflow-level checks


def _workflow(draft: FakeProvider, review: FakeProvider) -> ResumeWorkflow:
    return ResumeWorkflow(
        {"drafter": draft, "reviewer": review},
        default_registry(),
        draft_provider="drafter",
        review_provider="reviewer",
        flagship_models={"drafter": "fake-flagship", "reviewer": "fake-flagship"},
        cheap_models={"drafter": "fake-cheap", "reviewer": "fake-cheap"},
    )


def _plan() -> ExperienceSelection:
    return ExperienceSelection(
        target_summary="Cloud infrastructure and delivery",
        priority_themes=["terraform", "kubernetes"],
        roles=[RoleEmphasis(employer="Acme", title="Cloud Engineer", bullet_budget=3)],
    )


def _clean_draft() -> ResumeDraft:
    return ResumeDraft(
        headline="Cloud / DevOps Engineer",
        summary="Cloud engineer working across Azure infrastructure and delivery pipelines.",
        skills={"Cloud": ["Azure", "Terraform", "Kubernetes"]},
        roles=[
            ResumeRole(
                title="Cloud Engineer",
                employer="Acme",
                bullets=[
                    "Ran and reviewed Terraform plans for Azure infrastructure changes.",
                    "Supported AKS workloads during releases by reviewing pod health "
                    "and rollout status.",
                ],
            )
        ],
    )


def test_check_flags_a_gap_technology_hiding_in_the_skills_list():
    """The easiest way for a gap to survive: never appear in a bullet."""
    from council.documents.discovery import DiscoveryResult
    from council.documents.workflow import JDAnalysis

    draft = _clean_draft()
    draft.skills["Cloud"].append("GCP")
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult(gaps=["gcp"]))
    workflow = _workflow(FakeProvider("drafter"), FakeProvider("reviewer"))
    findings = workflow.check(draft, analysis, _confirmed())
    assert any(f["class"] == "GAP_TECHNOLOGY" and f["text"] == "GCP" for f in findings)


def test_check_flags_a_gap_technology_in_a_bullet():
    from council.documents.discovery import DiscoveryResult
    from council.documents.workflow import JDAnalysis

    draft = _clean_draft()
    draft.roles[0].bullets.append("Deployed workloads to GKE clusters on GCP.")
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult(gaps=["gcp", "gke"]))
    workflow = _workflow(FakeProvider("drafter"), FakeProvider("reviewer"))
    findings = workflow.check(draft, analysis, _confirmed())
    assert any(f["class"] == "GAP_TECHNOLOGY" for f in findings)


def test_check_flags_the_comma_before_and_rule():
    from council.documents.discovery import DiscoveryResult
    from council.documents.workflow import JDAnalysis

    draft = _clean_draft()
    draft.roles[0].bullets.append("Worked with Terraform, Ansible, and Helm.")
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult())
    workflow = _workflow(FakeProvider("drafter"), FakeProvider("reviewer"))
    findings = workflow.check(draft, analysis, _confirmed())
    assert any(f["class"] == "STYLE_BLOCKING" for f in findings)


def test_clean_draft_produces_no_findings():
    from council.documents.discovery import DiscoveryResult
    from council.documents.workflow import JDAnalysis

    analysis = JDAnalysis("infrastructure", [], DiscoveryResult())
    workflow = _workflow(FakeProvider("drafter"), FakeProvider("reviewer"))
    assert workflow.check(_clean_draft(), analysis, _confirmed()) == []


async def test_full_run_reaches_docx_and_skips_correction_when_clean():
    drafter = FakeProvider("drafter", [_plan(), _clean_draft()])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=True)])
    workflow = _workflow(drafter, reviewer)

    result = await workflow.run(GCP_JD, CareerProfile(), [])
    assert result.findings == []
    assert result.trace.model_calls == 3, "clean draft must not spend a correction call"
    assert "gcp" in result.analysis.gaps
    assert result.review.would_submit is True


async def test_correction_runs_and_is_rechecked():
    dirty = _clean_draft()
    dirty.roles[0].bullets.append("Cut deployment time by 40% across 200 applications.")
    drafter = FakeProvider("drafter", [_plan(), dirty, _clean_draft()])
    reviewer = FakeProvider("reviewer", [
        ReviewReport(
            would_submit=False,
            findings=[
                ReviewFinding(
                    lens="interview_defensibility",
                    severity="blocking",
                    problem="invented metric",
                    fix="remove it",
                )
            ],
        )
    ])
    workflow = _workflow(drafter, reviewer)
    result = await workflow.run(GCP_JD, CareerProfile(), [])
    assert result.trace.model_calls == 4
    assert result.findings == [], "post-correction recheck should be clean"
    stages = [s["stage"] for s in result.trace.stages]
    assert "resume_correction" in stages
    assert stages[-1] == "post_correction_check"


async def test_the_jd_never_enters_the_source_material_blob():
    """A JD inside the career-source blob is a JD the model can mistake for
    evidence — the exact failure A1 forbids."""
    drafter = FakeProvider("drafter", [_plan(), _clean_draft()])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=True)])
    workflow = _workflow(drafter, reviewer)
    documents = [
        {"authority": "master_resume", "title": "master", "text": "Terraform and Azure work."},
        {"authority": "jd", "title": "target", "text": "MUST HAVE GCP AND GKE EXPERIENCE."},
    ]
    await workflow.run(GCP_JD, CareerProfile(), documents)

    blob = workflow._sources_blob
    assert "master" in blob
    assert "MUST HAVE GCP" not in blob


async def test_disputed_facts_are_marked_disputed_for_the_model():
    """Telling the model nothing about a date invites it to infer one. Naming
    the dispute is what makes omission the likely behaviour."""
    a = "Cloud DevOps Engineer | Fidelity Investments | NC | Nov 2022 - Oct 2024"
    b = "Cloud DevOps Engineer | Fidelity Investments | NC | Sep 2022 - Oct 2024"
    drafter = FakeProvider("drafter", [_plan(), _clean_draft()])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=True)])
    workflow = _workflow(drafter, reviewer)
    await workflow.run(
        GCP_JD,
        CareerProfile(),
        [
            {"authority": "master_resume", "title": "m", "text": a},
            {"authority": "supporting", "title": "s", "text": b},
        ],
    )
    select_call = drafter.calls[0]["messages"][-1]["content"]
    assert "DISPUTED" in select_call
    assert "nov 2022" in select_call.lower()


async def test_review_runs_on_the_opposite_provider():
    """A model grading its own writing grades it well."""
    drafter = FakeProvider("drafter", [_plan(), _clean_draft()])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=True)])
    workflow = _workflow(drafter, reviewer)
    await workflow.run(GCP_JD, CareerProfile(), [])
    assert len(reviewer.calls) == 1
    assert len(drafter.calls) == 2


def test_discovery_schema_cannot_express_a_claim_about_the_user():
    """A2 made structural: there is no field the model could use to say the
    user has experience, so the boundary cannot be crossed by a wording
    change in the prompt."""
    fields = set(TechnologyDiscovery.model_json_schema()["$defs"]["DiscoveredTechnology"][
        "properties"
    ])
    assert fields == {"term", "kind"}




# --------------------------------------- deterministic style enforcement


def test_comma_rule_is_enforced_mechanically_not_asked_of_a_model():
    """A live run left a violation in place after the correction call quoted it
    back. This rule has one correct answer, so a regex is both more reliable
    and free."""
    from council.documents.style import blocking_violations, enforce_comma_rule

    text = (
        "Worked with FastAPI, PostgreSQL and Next.js, including migrations "
        "and tests, and shipped it."
    )
    fixed = enforce_comma_rule(text)
    assert blocking_violations(fixed) == {}
    assert "tests and shipped" in fixed


def test_style_enforcement_preserves_quoted_source_text():
    from council.documents.style import enforce_comma_rule

    quoted = 'The JD says "Terraform, Ansible, and Helm" exactly, and we quote it.'
    fixed = enforce_comma_rule(quoted)
    assert '"Terraform, Ansible, and Helm"' in fixed
    assert "exactly and we quote it" in fixed


def test_enforce_style_covers_every_generated_string():
    from council.documents.workflow import enforce_style

    draft = _clean_draft()
    draft.headline = "Cloud, DevOps, and Platform Engineer"
    draft.summary = "Azure, AWS, and Terraform work."
    draft.roles[0].bullets.append("Used Helm, Argo CD, and Kubernetes.")
    draft.education = ["BSc, MSc, and certifications"]
    fixed = enforce_style(draft)
    for text in [fixed.headline, fixed.summary, *fixed.roles[0].bullets, *fixed.education]:
        assert ", and" not in text, text


async def test_empty_draft_is_refused_never_rendered():
    """An empty resume that looks like a successful run is worse than an error
    — the same rule ingestion follows for an unreadable file."""
    from council.documents.workflow import GenerationFailed

    drafter = FakeProvider("drafter", [_plan(), None])
    reviewer = FakeProvider("reviewer", [])
    workflow = _workflow(drafter, reviewer)
    with pytest.raises(GenerationFailed):
        await workflow.run(GCP_JD, CareerProfile(), [])


def test_resume_draft_schema_rejects_an_empty_object():
    """With every field optional a model emitting {} validated, producing a
    silently empty resume. The schema now makes that a parse failure."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ResumeDraft.model_validate({})


# ------------------------------------------ Tier 2B "established by evidence"

MASTER = (
    "Designed and built a multi-model AI orchestration platform that runs "
    "independent model responses, detects disagreement and applies "
    "evidence-based review before producing a final answer.\n"
    "Ran and reviewed Terraform plans for Azure infrastructure changes."
)


def test_a_real_project_the_master_resume_establishes_is_not_flagged():
    """A live run flagged the user's own real project as invented: "built ...
    platform" matches the bespoke-artifact pattern, and the classifier had no
    way to see the master resume says the same thing."""
    from council.documents.discovery import DiscoveryResult
    from council.documents.workflow import JDAnalysis

    draft = _clean_draft()
    draft.roles[0].bullets.append(
        "Built a platform that runs independent model responses, compares them for "
        "disagreement and applies an evidence-based review step before producing a "
        "final answer."
    )
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult())
    workflow = _workflow(FakeProvider("d"), FakeProvider("r"))
    assert workflow.check(draft, analysis, _confirmed(), [MASTER]) == []


def test_an_invented_project_is_still_flagged_with_sources_present():
    """The exception must not become a hole. A source that does not say this
    cannot clear it."""
    from council.documents.discovery import DiscoveryResult
    from council.documents.workflow import JDAnalysis

    draft = _clean_draft()
    draft.roles[0].bullets.append(
        "Designed a custom Kubernetes operator and a bespoke internal deployment "
        "framework adopted company-wide."
    )
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult())
    workflow = _workflow(FakeProvider("d"), FakeProvider("r"))
    findings = workflow.check(draft, analysis, _confirmed(), [MASTER])
    assert any(f["class"] == "UNSUPPORTED_IMPLEMENTATION_CLAIM" for f in findings)


def test_support_requires_one_sentence_not_a_whole_document():
    """Words scattered across a document must not combine into support for a
    claim no sentence made."""
    from council.documents.support import directly_supported

    scattered = ["Used Terraform.\nUsed Kubernetes.\nUsed AWS.\nUsed Python."]
    claim = (
        "Built Kubernetes clusters on AWS using Terraform and automated the "
        "entire platform through Python."
    )
    assert directly_supported(claim, scattered) is False


def test_a_jd_can_never_provide_support():
    from council.documents.support import source_texts

    documents = [
        {"authority": "master_resume", "title": "m", "text": "Terraform work."},
        {"authority": "jd", "title": "t", "text": "Built a custom GCP platform."},
    ]
    assert source_texts(documents) == ["Terraform work."]
