"""The frozen resume contract, one test per named requirement (A–G).

These duplicate some coverage elsewhere on purpose. They are written against
the contract's own wording so that a future change which breaks a *product*
promise fails a test named after that promise, rather than failing something
incidental three modules away.
"""

import pytest

from council.documents.claims import ClaimClass, classify
from council.documents.discovery import DiscoveryResult, discover
from council.documents.mirroring import find_mirroring, mirrors_jd
from council.documents.profile import (
    AUTHORITY_MASTER_RESUME,
    AUTHORITY_TAILORED_RESUME,
    NO_DENIALS,
    CareerProfile,
    assemble_confirmed,
    detect_role_family,
)
from council.documents.schemas import ResumeDraft, ResumeRole, ReviewReport
from council.documents.workflow import JDAnalysis, ResumeWorkflow
from council.engine.prompts import default_registry
from tests.fakes import FakeProvider

GCP_JD = (
    "Infrastructure Engineer. You will work primarily on Google Cloud Platform, "
    "managing GKE clusters and Cloud Run services backed by Cloud SQL. You will "
    "write Terraform for all infrastructure, build CI/CD pipelines and support "
    "production Kubernetes workloads with monitoring and incident response."
)

SRE_JD = (
    "Site Reliability Engineer. Own incident response and production "
    "troubleshooting for Kubernetes workloads. Improve observability, drive "
    "root cause analysis and automate operational toil."
)

PLATFORM_JD = (
    "Platform Engineer. Build reusable infrastructure patterns with Terraform, "
    "run GitOps delivery on Kubernetes and focus on developer enablement and "
    "CI/CD automation."
)


def _confirmed(documents=None):
    return assemble_confirmed(CareerProfile(), documents, denials=NO_DENIALS)


def _workflow(draft: FakeProvider, review: FakeProvider) -> ResumeWorkflow:
    return ResumeWorkflow(
        {"drafter": draft, "reviewer": review},
        default_registry(),
        draft_provider="drafter",
        review_provider="reviewer",
        flagship_models={"drafter": "fake", "reviewer": "fake"},
        cheap_models={"drafter": "fake-cheap", "reviewer": "fake-cheap"},
    )


# =========================================================== A
# JD-only technology cannot become career experience.


def test_A_jd_technology_never_becomes_career_experience():
    """The JD names GCP, GKE, Cloud Run and Cloud SQL. None is in the career.
    Ingesting the JD as a document must not change that."""
    before = _confirmed()
    assert not before.is_confirmed("gcp")

    after = assemble_confirmed(
        CareerProfile(),
        [{"authority": "jd", "title": "target", "text": GCP_JD}],
        denials=NO_DENIALS,
    )
    for term in ("gcp", "gke", "cloud run", "cloud sql"):
        assert not after.is_confirmed(term), term
    assert after.terms == before.terms


async def test_A_even_a_model_calling_it_a_technology_cannot_confirm_it():
    """The discovery stage may name a technology. It can never establish that
    the user has used one — that comes only from career sources."""

    async def maximally_cooperative(candidates):
        return {"technologies": [{"term": c, "kind": "platform"} for c in candidates]}

    result = await discover(
        "You will use Skyforge and Nimbusdeck daily.",
        _confirmed(),
        ask_model=maximally_cooperative,
    )
    assert result.escalated is True
    assert "skyforge" in result.gaps
    assert "skyforge" not in result.supported


async def test_A_a_jd_in_the_document_set_never_reaches_the_writer_as_source():
    drafter = FakeProvider("drafter", [_selection(), _draft()])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=True)])
    workflow = _workflow(drafter, reviewer)
    await workflow.run(
        GCP_JD,
        CareerProfile(),
        [
            {"authority": "master_resume", "title": "master", "text": "Terraform on Azure."},
            {"authority": "jd", "title": "target", "text": GCP_JD},
        ],
    )
    assert "Google Cloud Platform" not in workflow._sources_blob
    assert "Terraform on Azure" in workflow._sources_blob


# =========================================================== B
# A capability omitted from one tailored resume stays available.


def test_B_omission_from_a_tailored_resume_is_not_absence():
    """Absence from one resume != absence from the career. The profile and
    every other authoritative source still establish it."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "master",
            "text": "Built Harness delivery workflows, Ansible playbooks and Splunk dashboards.",
        },
        {
            "authority": AUTHORITY_TAILORED_RESUME,
            "title": "aws tailored",
            "text": "AWS, EKS and CloudWatch only.",  # mentions none of the above
        },
    ]
    confirmed = assemble_confirmed(CareerProfile(), documents, denials=NO_DENIALS)
    for term in ("harness", "ansible", "splunk"):
        assert confirmed.is_confirmed(term), term
    # And the tailored resume still contributed positively.
    assert confirmed.is_confirmed("eks")


def test_B_profile_alone_survives_every_resume_omitting_it():
    """Even with no document mentioning it, a profile capability holds."""
    confirmed = assemble_confirmed(
        CareerProfile(technologies=["harness"]),
        [{"authority": AUTHORITY_TAILORED_RESUME, "title": "t", "text": "AWS only."}],
        denials=NO_DENIALS,
    )
    assert confirmed.is_confirmed("harness")


# =========================================================== C
# A new realistic bullet needs no identical source sentence.


def test_C_a_new_bullet_from_established_capabilities_is_permitted():
    """The contract's own example. A weak source line must not cap the system
    at paraphrasing it."""
    source_was = "Worked with Terraform and Kubernetes."
    generated = (
        "Developed and maintained reusable Terraform configurations for cloud "
        "infrastructure and supported Kubernetes workloads through deployment, "
        "configuration and production troubleshooting."
    )
    finding = classify(
        generated,
        confirmed_terms={"terraform", "kubernetes"},
        candidate_terms=["terraform", "kubernetes"],
    )
    assert finding.classification is ClaimClass.PERMITTED_EXPANSION, finding.reasons
    assert generated != source_was  # not a paraphrase of the source line


def test_C_the_workflow_does_not_require_source_support_to_pass_a_bullet():
    """Support clears Tier 2B/3 findings; it is never a precondition for an
    ordinary bullet. Passing NO sources must still yield a clean check."""
    draft = _draft()
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult())
    workflow = _workflow(FakeProvider("d"), FakeProvider("r"))
    assert workflow.check(draft, analysis, _confirmed(), sources=[]) == []


@pytest.mark.parametrize(
    "bullet",
    [
        "Investigated failed deployments by reviewing rollout status, pod events and "
        "recent configuration changes before coordinating a fix.",
        "Reviewed Terraform plans ahead of infrastructure changes and reconciled drift "
        "back to the approved configuration.",
        "Tuned alert thresholds after recurring false positives and folded the findings "
        "into the on-call runbook.",
        "Scripted the recurring access review in Python so it stopped eating a morning "
        "every month.",
    ],
)
def test_C_varied_engineer_voiced_bullets_are_permitted(bullet):
    """Different shapes — troubleshooting, implementation, operations,
    automation — none of which exist verbatim in any source."""
    finding = classify(
        bullet,
        confirmed_terms={"terraform", "kubernetes", "python", "monitoring and observability"},
        candidate_terms=["terraform", "python"],
    )
    assert finding.classification is ClaimClass.PERMITTED_EXPANSION, finding.reasons


# =========================================================== D
# Confirmed technologies alone cannot manufacture a relationship.


def test_D_confirmed_nouns_do_not_authorise_an_invented_implementation():
    text = (
        "Built Kubernetes clusters on AWS using Terraform and automated the "
        "entire platform through Python."
    )
    terms = ["kubernetes", "aws", "terraform", "python"]
    finding = classify(text, confirmed_terms=set(terms), candidate_terms=terms)
    assert finding.classification is ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM


@pytest.mark.parametrize(
    "text",
    [
        "Designed a custom Kubernetes operator for internal workloads.",
        "Led the migration of the entire Azure estate.",
        "Architected an enterprise-wide delivery framework adopted company-wide.",
        "Managed the platform team and owned the infrastructure roadmap.",
    ],
)
def test_D_ownership_and_bespoke_claims_still_require_evidence(text):
    finding = classify(text, confirmed_terms={"azure", "kubernetes"}, candidate_terms=["azure"])
    assert finding.classification is ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM


# =========================================================== E
# Same career evidence, different emphasis per role family.


def test_E_role_family_changes_emphasis_not_facts():
    confirmed = _confirmed()
    sre_family, sre_emphasis = detect_role_family(SRE_JD)
    platform_family, platform_emphasis = detect_role_family(PLATFORM_JD)

    assert sre_family == "sre"
    assert platform_family == "platform"
    assert set(sre_emphasis) != set(platform_emphasis)

    # The career itself is identical in both cases — only presentation moves.
    assert assemble_confirmed(CareerProfile(), denials=NO_DENIALS).terms == confirmed.terms
    assert "incident response" in sre_emphasis
    assert "reusable infrastructure patterns" in platform_emphasis


@pytest.mark.parametrize(
    "jd,expected",
    [
        (SRE_JD, "sre"),
        (PLATFORM_JD, "platform"),
        (GCP_JD, "infrastructure"),
        (
            "Azure DevOps Engineer building AKS release pipelines with Terraform "
            "and Harness.",
            "azure_devops",
        ),
        (
            "Cloud Operations Engineer handling monitoring, access management and "
            "cost management for production cloud workloads.",
            "cloud_operations",
        ),
    ],
)
def test_E_each_role_family_is_detected_from_its_jd(jd, expected):
    assert detect_role_family(jd)[0] == expected


# =========================================================== F
# A major gap is reported but never blocks generation.


async def test_F_a_weak_match_still_produces_the_requested_artifact():
    """GCP-primary JD, no GCP in the career. The contract says: report the gap,
    produce the resume anyway, let the user decide."""
    drafter = FakeProvider("drafter", [_selection(), _draft()])
    reviewer = FakeProvider("reviewer", [ReviewReport(would_submit=False)])
    workflow = _workflow(drafter, reviewer)

    result = await workflow.run(GCP_JD, CareerProfile(), [])

    assert result.draft.roles, "artifact must be produced despite the gap"
    assert "gcp" in result.analysis.gaps
    assert result.review.would_submit is False  # advice, not a veto
    assert result.analysis.match_quality in ("weak", "moderate")


def test_F_match_quality_is_computed_without_a_model_call():
    weak = JDAnalysis("infrastructure", [], DiscoveryResult(supported=["terraform"],
                                                           gaps=["gcp", "gke", "cloud run"]))
    strong = JDAnalysis("azure_devops", [], DiscoveryResult(
        supported=["azure", "terraform", "aks", "harness", "jenkins"], gaps=[]))
    assert weak.match_quality == "weak"
    assert strong.match_quality == "strong"


async def test_F_no_gap_technology_survives_into_the_artifact():
    """Reporting the gap and refusing to claim it are two different promises;
    this is the second one."""
    draft = _draft()
    draft.skills["Cloud"].append("GCP")
    draft.roles[0].bullets.append("Ran workloads on GKE clusters.")
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult(gaps=["gcp", "gke"]))
    workflow = _workflow(FakeProvider("d"), FakeProvider("r"))
    findings = workflow.check(draft, analysis, _confirmed())
    classes = {f["class"] for f in findings}
    assert classes == {"GAP_TECHNOLOGY"}
    assert len([f for f in findings if f["class"] == "GAP_TECHNOLOGY"]) == 2


# =========================================================== G
# The resume must not mechanically copy JD terminology.


def test_G_a_bullet_lifted_from_the_jd_is_flagged():
    lifted = (
        "Write Terraform for all infrastructure, build CI/CD pipelines and support "
        "production Kubernetes workloads with monitoring and incident response."
    )
    mirrored, reason = mirrors_jd(lifted, GCP_JD)
    assert mirrored is True
    assert reason


def test_G_honest_tailoring_that_shares_technology_nouns_is_not_mirroring():
    """The whole point of tailoring is to talk about what the JD cares about.
    Shared nouns must not be mistaken for copied sentences."""
    honest = (
        "Reviewed Terraform plans before Azure infrastructure changes and reconciled "
        "drift back to the approved configuration, then watched rollout status and pod "
        "events through the release."
    )
    mirrored, _ = mirrors_jd(honest, GCP_JD)
    assert mirrored is False


def test_G_short_bullets_cannot_trip_the_mirroring_check():
    assert mirrors_jd("Terraform and Kubernetes.", GCP_JD)[0] is False


def test_G_the_workflow_reports_mirroring_as_a_correctable_finding():
    draft = _draft()
    draft.roles[0].bullets.append(
        "Write Terraform for all infrastructure, build CI/CD pipelines and support "
        "production Kubernetes workloads with monitoring and incident response."
    )
    analysis = JDAnalysis("infrastructure", [], DiscoveryResult())
    workflow = _workflow(FakeProvider("d"), FakeProvider("r"))
    findings = workflow.check(draft, analysis, _confirmed(), jd_text=GCP_JD)
    assert any(f["class"] == "JD_MIRRORING" for f in findings)


def test_G_find_mirroring_takes_the_drafts_own_bullet_shape():
    draft = _draft()
    assert find_mirroring(draft.bullets(), GCP_JD) == []


# ------------------------------------------------------------------ helpers


def _selection():
    from council.documents.schemas import ExperienceSelection, RoleEmphasis

    return ExperienceSelection(
        target_summary="Cloud infrastructure and delivery",
        priority_themes=["terraform", "kubernetes"],
        roles=[RoleEmphasis(employer="Acme", title="Cloud Engineer", bullet_budget=3)],
    )


def _draft() -> ResumeDraft:
    return ResumeDraft(
        headline="Cloud / DevOps Engineer",
        summary="Cloud engineer working across Azure infrastructure and delivery pipelines.",
        skills={"Cloud": ["Azure", "Terraform", "Kubernetes"]},
        roles=[
            ResumeRole(
                title="Cloud Engineer",
                employer="Acme",
                bullets=[
                    "Reviewed Terraform plans ahead of Azure infrastructure changes and "
                    "reconciled drift back to the approved configuration.",
                    "Supported AKS workloads during releases by checking pod health, "
                    "restart patterns and rollout status.",
                ],
            )
        ],
    )
