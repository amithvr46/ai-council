"""The two blocking defects the real UI acceptance run exposed.

Both are under-claiming failures: nothing was fabricated, but the resume was
made weaker than the truth allows. That is still a product defect — a resume
that omits real experience costs the user the job just as surely as one that
invents experience costs them the interview.

  1. "I have worked on GCP" was accepted as career truth and then expressed
     only as a Skills entry. No bullet described any GCP work.
  2. Compound JD terms — "Prometheus/Grafana", "Linux-based" — were reported
     as unsupported gaps, so the resume was instructed not to claim three
     technologies the career genuinely establishes.
"""

import pytest

from council.documents.discovery import DiscoveryResult
from council.documents.instructions import parse
from council.documents.profile import (
    AUTHORITY_MASTER_RESUME,
    AUTHORITY_USER_STATEMENT,
    CareerProfile,
    assemble_confirmed,
    decompose_term,
    scan_jd_technologies,
)
from council.documents.schemas import ResumeDraft, ResumeRole
from council.documents.workflow import JDAnalysis, _career_context, _unexpressed_platforms


def _confirmed(statement="I have worked on GCP before.", denials=None, documents=None):
    """The real acceptance-run shape: a master resume plus one user statement."""
    docs = list(documents or [])
    if statement:
        docs.append(
            {
                "authority": AUTHORITY_USER_STATEMENT,
                "title": "Stated by you",
                "text": statement,
            }
        )
    return assemble_confirmed(CareerProfile(), docs, denials=list(denials or []))


def _draft(bullets=None, skills=None):
    return ResumeDraft(
        headline="Cloud / DevOps Engineer",
        summary="Cloud engineer across Azure and AWS infrastructure.",
        skills=skills if skills is not None else {"Cloud": ["Azure", "AWS", "GCP"]},
        roles=[
            ResumeRole(
                title="Cloud Engineer",
                employer="Acme",
                bullets=bullets
                if bullets is not None
                else [
                    "Reviewed Terraform plans before Azure infrastructure changes "
                    "and reconciled drift back to the approved configuration."
                ],
            )
        ],
    )


def _analysis(supported=("gcp", "terraform"), gaps=()):
    return JDAnalysis(
        "infrastructure", [], DiscoveryResult(supported=list(supported), gaps=list(gaps))
    )


# ============================================ 1. the statement establishes it


def test_1_a_first_person_platform_statement_establishes_experience():
    confirmed = _confirmed("I have worked on GCP before.")
    assert confirmed.is_confirmed("gcp")
    # And it is attributed to the user, not to a document that never said it.
    assert confirmed.sources["gcp"] == ["user_statement:Stated by you"]
    assert "gcp" in confirmed.stated_platforms()


def test_1_the_users_actual_acceptance_run_phrasing_works():
    """Verbatim from the run, typos included — it has to work as typed."""
    from council.documents.instructions import technology_terms

    parsed = parse(
        "I have worked on GCP before. If any points are missing add them appropriately."
    )
    assert parsed.career_statements
    named = {t for sentence in parsed.career_statements for t in technology_terms(sentence)}
    assert "gcp" in named


# ================================ 2. it must reach a bullet, not only Skills


def test_2_platform_experience_confined_to_skills_is_flagged():
    """The exact acceptance-run failure: GCP in Skills, no GCP work anywhere."""
    findings = _unexpressed_platforms(_draft(), _analysis(), _confirmed())
    assert len(findings) == 1
    assert findings[0]["class"] == "UNEXPRESSED_PLATFORM_EXPERIENCE"
    assert findings[0]["text"] == "gcp"
    assert "only in the skills list" in findings[0]["reasons"][0]


def test_2_the_writer_is_told_what_it_may_write():
    """A finding after the fact is a patch. The context is the actual fix."""
    context = _career_context(CareerProfile(), _confirmed(), [])
    assert "ESTABLISHED BY THE USER'S OWN STATEMENT" in context
    assert "gcp" in context
    assert "experience section" in context
    # And bounded in the same breath.
    assert "does NOT establish any specific product" in context


def test_2_a_bullet_describing_the_work_clears_the_finding():
    draft = _draft(
        bullets=[
            "Provisioned and maintained GCP infrastructure with Terraform, covering "
            "project layout, IAM bindings and VPC networking."
        ]
    )
    assert _unexpressed_platforms(draft, _analysis(), _confirmed()) == []


def test_2_nothing_is_asked_for_when_the_jd_does_not_want_it():
    """Silence about an irrelevant technology is correct tailoring."""
    analysis = _analysis(supported=("terraform",))
    assert _unexpressed_platforms(_draft(), analysis, _confirmed()) == []


def test_2_document_derived_technologies_are_not_chased():
    """The master resume already narrates these; nothing is missing."""
    confirmed = _confirmed(
        statement=None,
        documents=[{
            "authority": AUTHORITY_MASTER_RESUME, "title": "master",
            "text": "Ran Terraform against Azure and AWS.",
        }],
    )
    assert confirmed.stated_platforms() == set()
    assert _unexpressed_platforms(_draft(), _analysis(), confirmed) == []


# ==================== 3. broad platform experience is not every product


@pytest.mark.parametrize("service", ["gke", "cloud run", "cloud sql", "bigquery", "gce"])
def test_3_a_platform_statement_establishes_no_named_service(service):
    confirmed = _confirmed("I have worked on GCP before.")
    assert confirmed.is_confirmed("gcp")
    assert not confirmed.is_confirmed(service), service


def test_3_named_services_stay_gaps_in_the_jd_scan():
    jd = "GCP required. You will run GKE clusters and Cloud Run services."
    supported, gaps = scan_jd_technologies(jd, _confirmed())
    assert "gcp" in supported
    assert "gke" in gaps and "cloud run" in gaps


# ================= 4. explicitly stated service experience still works


def test_4_a_named_service_can_still_be_established_explicitly():
    confirmed = _confirmed("I have worked on GCP and I have used GKE in production.")
    assert confirmed.is_confirmed("gcp")
    assert confirmed.is_confirmed("gke")
    supported, gaps = scan_jd_technologies("GKE and GCP required.", confirmed)
    assert "gke" in supported and "gke" not in gaps


# ================================== 5-7. compound normalisation


def test_5_a_slash_compound_of_established_components_is_supported():
    confirmed = _confirmed(statement=None)
    assert confirmed.is_confirmed("prometheus") and confirmed.is_confirmed("grafana")
    assert confirmed.is_confirmed("prometheus/grafana")
    _, gaps = scan_jd_technologies("Experience with Prometheus/Grafana required.", confirmed)
    assert "prometheus/grafana" not in gaps


def test_6_a_descriptive_suffix_does_not_hide_the_technology():
    confirmed = _confirmed(statement=None)
    for term in ("linux-based", "linux based", "gcp-native", "kubernetes-native"):
        assert decompose_term(term)[0] in {"linux", "gcp", "kubernetes"}, term
    assert confirmed.is_confirmed("linux-based")
    _, gaps = scan_jd_technologies("Linux-based hosts.", confirmed)
    assert "linux-based" not in gaps


def test_7_a_compound_never_vouches_for_an_unestablished_component():
    """The rule that keeps this from becoming a fabrication route: a slash
    compound is conjunctive. One confirmed half does not carry the other."""
    confirmed = _confirmed(statement=None)
    assert confirmed.is_confirmed("prometheus")
    assert not confirmed.is_confirmed("datadog")
    assert not confirmed.is_confirmed("prometheus/datadog")
    assert not confirmed.is_confirmed("terraform/pulumi")
    # And a compound of two unknowns stays unknown.
    assert not confirmed.is_confirmed("ssl/tls")


def test_7_decomposition_cannot_invent_a_component():
    assert decompose_term("terraform") == ["terraform"]
    assert decompose_term("cloud run") == ["cloud run"]  # a space is not a join


def test_7_a_known_compound_costs_no_discovery_call():
    from council.documents.discovery import candidate_terms
    from council.documents.profile import DEFAULT_TECHNOLOGIES, normalise

    known = {normalise(t) for t in DEFAULT_TECHNOLOGIES}
    candidates = candidate_terms(
        "Prometheus/Grafana on Linux-based hosts, plus Pulumi.", known, set()
    )
    assert "Prometheus/Grafana" not in candidates
    assert "Linux-based" not in candidates
    assert "Pulumi" in candidates  # a genuine unknown still escalates


# ============================== 8. denial still outranks everything


def test_8_a_denied_component_denies_the_whole_compound():
    """The side door this could have opened: deny Grafana, then let
    "Prometheus/Grafana" walk back in through decomposition."""
    from council.documents.profile import Denied

    confirmed = _confirmed(
        statement=None,
        denials=[
            Denied(term="grafana", kind="never_used", statement="I never used Grafana")
        ],
    )
    assert not confirmed.is_confirmed("grafana")
    assert not confirmed.is_confirmed("prometheus/grafana")


def test_8_a_denied_platform_is_never_chased_into_a_bullet():
    from council.documents.profile import Denied

    confirmed = _confirmed(
        statement=None,
        denials=[
            Denied(term="gcp", kind="never_used", statement="I have never used GCP")
        ],
    )
    assert not confirmed.is_confirmed("gcp")
    assert "gcp" not in confirmed.stated_platforms()
    assert _unexpressed_platforms(_draft(), _analysis(), confirmed) == []


def test_8_denial_beats_a_later_positive_statement_for_the_same_term():
    from council.documents.profile import Denied

    confirmed = _confirmed(
        "I have worked on GCP before.",
        denials=[
            Denied(term="gcp", kind="never_used", statement="I have never used GCP")
        ],
    )
    assert not confirmed.is_confirmed("gcp")


# ================= 9-10. A5 truth protections and the revision bound


def test_9_relationship_claims_are_still_caught_for_a_stated_platform():
    """Establishing GCP does not license an invented GCP project."""
    from council.documents.claims import ClaimClass, classify

    finding = classify(
        "Architected a custom multi-region GCP platform and migrated the entire "
        "estate onto it.",
        confirmed_terms={"gcp", "terraform", "kubernetes"},
        candidate_terms=["gcp", "terraform"],
    )
    assert finding.classification is ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM


def test_9_hard_facts_are_still_caught_for_a_stated_platform():
    from council.documents.claims import ClaimClass, classify

    finding = classify(
        "Ran 40 GCP projects and cut cloud spend by 35%.",
        confirmed_terms={"gcp"},
        candidate_terms=["gcp"],
    )
    assert finding.classification is ClaimClass.FABRICATED_FACT


def test_10_the_new_finding_uses_the_existing_single_correction_pass():
    """No new stage and no second revision: it is an ordinary finding on the
    same list every other check contributes to."""
    import inspect

    from council.documents.workflow import ResumeWorkflow

    source = inspect.getsource(ResumeWorkflow.run)
    assert source.count("await self.correct(") == 1
    check_source = inspect.getsource(ResumeWorkflow.check)
    assert "_unexpressed_platforms" in check_source


def test_6_a_descriptive_suffix_still_surfaces_the_technology_as_supported():
    """Not creating a false gap is half the fix. The other half is that
    "GCP-native services" must actually READ as GCP, or established experience
    is merely hidden more quietly."""
    confirmed = _confirmed("I have worked on GCP before.")
    supported, gaps = scan_jd_technologies("GCP-native services on Linux-based hosts.", confirmed)
    assert "gcp" in supported
    assert "linux" in supported
    assert gaps == []


def test_6_the_modifier_match_does_not_break_the_github_guard():
    """The word boundary that allows "GCP-native" is the same one that stops
    "git" matching "github". Only the closed modifier list gets through."""
    from council.documents.profile import mentions_with_modifier

    assert mentions_with_modifier("GCP-native tooling", "gcp")
    assert not mentions_with_modifier("We use GitHub heavily.", "git")
    assert not mentions_with_modifier("cloud-agnostic design", "cloud")
