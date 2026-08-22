"""Amendment B2/B3 enforced at the scope they were written for.

The real acceptance run produced a resume that was truthful, ATS-clean, passed
every bullet-level check and still read as a responsibility inventory:

    Support production services...
    Support AKS workloads...
    Support infrastructure delivery...
    Support Azure DevOps, GitHub-based and Harness...

No individual bullet is wrong. The SECTION is. B2 says exactly this — "a set of
individually acceptable bullets can still produce an incoherent section" — and
the deterministic layer could not see it, because every mechanical check in the
workflow runs over one bullet at a time. The repetition rule that would have
caught it had been in the per-bullet rule list since it was written, was fed one
bullet per call, and could therefore never fire on any real draft.

These tests are written at section scope for that reason. A test that hands the
checker a pre-joined markdown block is the test that hid the defect.
"""

from council.documents.discovery import DiscoveryResult
from council.documents.profile import (
    AUTHORITY_USER_STATEMENT,
    CareerProfile,
    assemble_confirmed,
)
from council.documents.schemas import ResumeDraft, ResumeRole
from council.documents.style import check_section
from council.documents.workflow import (
    JDAnalysis,
    _career_context,
    _section_writing,
    _unexpressed_platforms,
)

# ------------------------------------------------------------------ fixtures

WELLS = [
    "Support production services running on Azure across AKS and App Service.",
    "Support AKS workloads including deployments, scaling and configuration.",
    "Support infrastructure delivery using Terraform and Azure DevOps.",
    "Support Azure DevOps, GitHub-based and Harness delivery pipelines.",
]

FIDELITY = [
    "Supported a subset of internal services on GCP, provisioning compute and "
    "storage resources, configuring IAM roles and service accounts and setting "
    "up monitoring alongside the primary Azure delivery work.",
    "Developed Terraform modules for networking and identity resources.",
    "Built and maintained release pipelines in Azure DevOps.",
    "Supported production incidents across application and platform boundaries.",
    "Worked with application teams on deployment configuration.",
    "Worked with security teams on access reviews.",
    "Configured Prometheus and Grafana dashboards.",
    "Configured alerting thresholds for platform services.",
    "Used Python scripting to automate repeatable operational tasks.",
]

STRONG = [
    "Managed AKS application environments through Terraform, supporting "
    "deployment and configuration changes across production Azure subscriptions.",
    "Investigated failed releases by reviewing rollout status, pod events and "
    "networking configuration.",
    "Automated repeatable operational tasks in Python, removing manual steps "
    "from the release path.",
    "Standardised pipeline structure across Azure DevOps and GitHub-based "
    "delivery so teams onboarded the same way.",
]


def _draft(roles):
    return ResumeDraft(
        headline="Cloud Engineer",
        summary="Cloud and platform engineer.",
        skills={"Cloud": ["Azure", "GCP"]},
        roles=[ResumeRole(title=t, employer=e, bullets=b) for t, e, b in roles],
    )


def _analysis(supported=("gcp",)):
    return JDAnalysis(
        role_family="cloud_devops",
        emphasis=[],
        discovery=DiscoveryResult(supported=list(supported)),
    )


def _confirmed(statement="I worked professionally on GCP."):
    return assemble_confirmed(
        CareerProfile(),
        [{"authority": AUTHORITY_USER_STATEMENT, "title": "Stated by you",
          "text": statement}],
        denials=[],
    )


# ------------------------------------------- defect 1: section-scope blindness


def test_the_repetition_rule_fires_on_the_section_that_defeated_it():
    """The exact Wells section from the real run."""
    assert "vary_sentence_structure" in check_section(WELLS)


def test_no_per_bullet_call_could_ever_have_caught_it():
    """The regression that matters. The rule was not weak, it was unreachable —
    so the guard is that the section checks are never fed a single bullet."""
    from council.documents.style import check

    for bullet in WELLS:
        assert check(bullet) == {}


def test_an_inventory_section_is_flagged_even_when_the_verbs_vary():
    """Fidelity used nine bullets and six different verbs. Opener repetition
    alone does not catch it; the framing is what makes it an inventory."""
    hits = check_section(FIDELITY)
    assert "vary_sentence_structure" not in hits
    assert "describe_the_work" in hits


def test_a_section_that_describes_real_work_is_left_alone():
    assert check_section(STRONG) == {}


def test_one_supported_bullet_is_not_a_finding():
    """"Supported" is often the accurate verb. The praised GCP bullet opens
    with it. The rule is a proportion over a section, never a verdict on a
    bullet — otherwise the fix becomes verb-swapping, which is cosmetic."""
    section = [FIDELITY[0], *STRONG[:3]]
    assert "describe_the_work" not in check_section(section)


def test_a_short_section_is_never_flagged():
    """Three bullets that rhyme is a coincidence, not a pattern."""
    assert check_section(WELLS[:3]) == {}


def test_the_finding_is_advisory_and_carries_the_section_as_its_location():
    findings = _section_writing(_draft([("Cloud Engineer", "Wells Fargo", WELLS)]))
    # Wells trips both rules: one opening verb, and framing that reports
    # proximity rather than work. Two findings is right — they are different
    # problems and the corrector needs to see both.
    assert {f["class"] for f in findings} == {"SECTION_WRITING_ADVISORY"}
    assert {f["location"] for f in findings} == {"Wells Fargo — Cloud Engineer"}
    assert {r.split(":")[0] for f in findings for r in f["reasons"]} == {
        "vary_sentence_structure",
        "describe_the_work",
    }


def test_an_advisory_never_counts_as_a_truth_violation():
    """It must not be able to revert a correction. Nothing here is about truth,
    and a writing observation that can discard a real fix is a worse defect
    than the one it reports."""
    from council.documents.workflow import _truth_violations

    findings = _section_writing(_draft([("Cloud Engineer", "Wells Fargo", WELLS)]))
    assert _truth_violations(findings) == set()


def test_advisories_are_rendered_apart_from_non_negotiable_violations():
    """"Not negotiable" behind a writing observation is pressure to invent."""
    import inspect

    from council.documents.workflow import ResumeWorkflow

    source = inspect.getsource(ResumeWorkflow.correct)
    assert "_ADVISORY" in source
    assert "WRITING ADVISORIES" in source


# ------------------------- defect 2: platform coverage across recent sections


def test_a_platform_expressed_only_in_an_older_role_still_flags_the_current_one():
    """The demonstrated defect: GCP landed under Fidelity and Wells was never
    reconsidered, because the gate asked whether the platform appeared ANYWHERE
    in the draft."""
    draft = _draft([
        ("Cloud Engineer", "Wells Fargo", WELLS),
        ("Cloud Engineer", "Fidelity", FIDELITY),
    ])
    findings = _unexpressed_platforms(draft, _analysis(), _confirmed())
    assert [f["location"] for f in findings] == ["Wells Fargo — Cloud Engineer"]
    assert findings[0]["text"] == "gcp"


def test_the_second_finding_asks_for_a_different_aspect_not_a_copy():
    draft = _draft([
        ("Cloud Engineer", "Wells Fargo", WELLS),
        ("Cloud Engineer", "Fidelity", FIDELITY),
    ])
    reason = _unexpressed_platforms(draft, _analysis(), _confirmed())[0]["reasons"][0]
    assert "Fidelity" in reason
    assert "DIFFERENT aspect" in reason
    assert "not a restatement" in reason


def test_a_platform_present_in_both_recent_sections_is_not_flagged():
    """Satisfied is satisfied. The gate must stop, or the correction pass is
    told to keep adding GCP to a resume that already describes it twice."""
    draft = _draft([
        ("Cloud Engineer", "Wells Fargo", [*WELLS, "Ran GCP networking changes."]),
        ("Cloud Engineer", "Fidelity", FIDELITY),
    ])
    assert _unexpressed_platforms(draft, _analysis(), _confirmed()) == []


def test_older_roles_beyond_the_recent_window_are_never_flagged():
    """A platform is not retro-fitted into every job the engineer ever had."""
    draft = _draft([
        ("Cloud Engineer", "Wells Fargo", [*WELLS, "Ran GCP networking changes."]),
        ("Cloud Engineer", "Fidelity", FIDELITY),
        ("Systems Engineer", "First Job", STRONG),
        ("Intern", "Older Still", STRONG),
    ])
    assert _unexpressed_platforms(draft, _analysis(), _confirmed()) == []


def test_a_platform_the_jd_does_not_emphasise_is_never_flagged():
    """Silence about an irrelevant platform is correct tailoring, not a defect.
    Unchanged from the original gate and asserted so it stays that way."""
    draft = _draft([("Cloud Engineer", "Wells Fargo", WELLS)])
    assert _unexpressed_platforms(draft, _analysis(supported=()), _confirmed()) == []


def test_a_platform_no_user_statement_established_is_never_flagged():
    """The authorisation comes from the user having said it. Without that this
    gate would push the resume to claim whatever the JD asks for."""
    draft = _draft([("Cloud Engineer", "Wells Fargo", WELLS)])
    confirmed = _confirmed(statement="I want a cloud role.")
    assert "gcp" not in confirmed.stated_platforms()
    assert _unexpressed_platforms(draft, _analysis(), confirmed) == []


def test_a_denied_platform_is_never_flagged():
    """Denial outranks everything, including a JD that wants it badly."""
    from council.documents.profile import Denied

    draft = _draft([("Cloud Engineer", "Wells Fargo", WELLS)])
    confirmed = assemble_confirmed(
        CareerProfile(),
        [{"authority": AUTHORITY_USER_STATEMENT, "title": "Stated by you",
          "text": "I worked professionally on GCP."}],
        denials=[Denied(term="gcp", kind="never_used", statement="")],
    )
    assert _unexpressed_platforms(draft, _analysis(), confirmed) == []


def test_every_finding_permits_leaving_it_out():
    """The escape hatch is the truth boundary. A finding the corrector cannot
    decline is a finding that gets satisfied by invention."""
    draft = _draft([("Cloud Engineer", "Wells Fargo", WELLS)])
    for finding in _unexpressed_platforms(draft, _analysis(), _confirmed()):
        assert "leave it out" in finding["reasons"][0]


def test_no_named_service_is_ever_authorised():
    draft = _draft([("Cloud Engineer", "Wells Fargo", WELLS)])
    reason = _unexpressed_platforms(draft, _analysis(), _confirmed())[0]["reasons"][0]
    assert "never a named service" in reason


def test_the_career_context_orders_current_before_recent():
    context = _career_context(CareerProfile(), _confirmed(), [])
    assert "CURRENT role first" in context
    assert "immediately previous" in context
    assert "DIFFERENT aspects" in context


def test_the_career_context_still_forbids_naming_a_service():
    """Preserved from the behaviour the user approved. Widening WHERE the
    platform may appear must not widen WHAT may be said about it."""
    context = _career_context(CareerProfile(), _confirmed(), [])
    assert "does NOT establish any specific product of that platform" in context
