"""Phase 2B — regression tests for the mechanically checkable rules in
docs/document-contract.md.

These are the rules that must not be rediscovered later: the negative-evidence
rule, the three-tier (plus 2B) claim policy, and the permanent style rules.
All deterministic; no model calls.
"""

import pytest

from council.documents.claims import ClaimClass, classify, find_hard_facts
from council.documents.claims import find_implementation_claims as find_impl
from council.documents.extract import ExtractionError, extract
from council.documents.profile import (
    AUTHORITY_JD,
    AUTHORITY_MASTER_RESUME,
    AUTHORITY_PROFILE,
    AUTHORITY_TAILORED_RESUME,
    AUTHORITY_USER_STATEMENT,
    NO_DENIALS,
    CareerProfile,
    assemble_confirmed,
    detect_role_family,
    normalise,
    scan_jd_technologies,
)
from council.documents.style import (
    blocking_violations,
    check,
    check_section,
    prompt_guidance,
)

# --- §3 negative evidence: the rule that must never regress -----------------


def test_tailored_resume_omission_is_not_evidence_of_absence():
    """The Harness case from the contract, stated as a test."""
    profile = CareerProfile(technologies=["azure", "terraform", "harness", "aks"])
    yesterdays_resume = {
        "authority": AUTHORITY_TAILORED_RESUME,
        "title": "resume-for-terraform-role",
        # Deliberately does NOT mention Harness — that JD did not need it.
        "text": "Built Terraform modules for Azure infrastructure and AKS clusters.",
    }
    confirmed = assemble_confirmed(profile, [yesterdays_resume], denials=NO_DENIALS)
    assert confirmed.is_confirmed("harness"), (
        "omission from a tailored resume must not remove confirmed experience"
    )


def test_a_tailored_resume_can_still_add_positively():
    profile = CareerProfile(technologies=["azure"])
    resume = {
        "authority": AUTHORITY_TAILORED_RESUME,
        "title": "old",
        "text": "Managed Azure and Terraform infrastructure.",
    }
    confirmed = assemble_confirmed(profile, [resume], denials=NO_DENIALS)
    assert confirmed.is_confirmed("terraform")
    assert AUTHORITY_PROFILE in confirmed.sources["azure"]


def test_a_jd_is_never_career_evidence():
    """The target job's technologies are not the user's experience."""
    profile = CareerProfile(technologies=["azure"])
    jd = {
        "authority": AUTHORITY_JD,
        "title": "target",
        "text": "Requires deep Kubernetes, Harness and Argo CD experience.",
    }
    confirmed = assemble_confirmed(profile, [jd], denials=NO_DENIALS)
    assert not confirmed.is_confirmed("harness")
    assert not confirmed.is_confirmed("argo cd")


def test_master_resume_contributes_and_is_attributed():
    profile = CareerProfile(technologies=["azure"])
    master = {
        "authority": AUTHORITY_MASTER_RESUME,
        "title": "master",
        "text": "Jenkins pipelines, Splunk dashboards and Ansible playbooks.",
    }
    confirmed = assemble_confirmed(profile, [master], denials=NO_DENIALS)
    assert confirmed.is_confirmed("jenkins")
    assert any("master" in s for s in confirmed.sources["jenkins"])


def test_aliases_resolve_to_one_confirmed_term():
    profile = CareerProfile(technologies=["aks", "kubernetes"])
    confirmed = assemble_confirmed(profile, [], denials=NO_DENIALS)
    assert confirmed.is_confirmed("Azure Kubernetes Service")
    assert confirmed.is_confirmed("K8s")


def test_unconfirmed_terms_are_reported():
    profile = CareerProfile(technologies=["azure", "terraform"])
    confirmed = assemble_confirmed(profile, [], denials=NO_DENIALS)
    assert confirmed.unconfirmed(["Terraform", "Pulumi", "Azure"]) == ["Pulumi"]


def test_normalise_is_whitespace_and_case_insensitive():
    assert normalise("  Azure   DevOps  ") == "azure devops"
    assert normalise("K8s") == "kubernetes"


# --- §2A role families: emphasis shifts, career does not --------------------


@pytest.mark.parametrize(
    "jd,expected",
    [
        ("Site Reliability Engineer — incident response and observability", "sre"),
        ("Platform Engineer building a developer platform with GitOps", "platform"),
        ("Azure DevOps Engineer: Azure Pipelines, Terraform, AKS", "azure_devops"),
        ("Cloud Operations Engineer for production Azure monitoring", "cloud_operations"),
        ("Infrastructure Engineer — networking, identity and Terraform", "infrastructure"),
    ],
)
def test_role_family_detected_from_jd(jd, expected):
    family, emphasis = detect_role_family(jd)
    assert family == expected
    assert emphasis


def test_unrecognised_jd_falls_back_without_crashing():
    family, emphasis = detect_role_family("We are hiring a wonderful human being.")
    assert family in ("azure_devops",)
    assert emphasis


# --- §4 Tier 3: hard factual claims -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Reduced deployment time by 40%",
        "Saved $250k in annual cloud spend",
        "Managed 300 servers across three regions",
        "Led a team of 8 engineers",
        "Migrated 45 applications to AKS",
        "Improved pipeline runtime by 30 minutes",
        "Supported 12,000 users",
        "Awarded employee of the year",
    ],
)
def test_hard_facts_are_detected(text):
    assert find_hard_facts(text), f"should flag a hard fact: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "Configured Harness pipelines for AKS deployments",
        "Investigated deployment failures by reviewing rollout status and pod events",
        "Upgraded clusters to Kubernetes 1.29 and validated workloads",
        "Implemented TLS 1.2 enforcement across ingress controllers",
        "Maintained S3 lifecycle policies and EC2 instance configuration",
        "Supported 24/7 on-call rotation duties",
        "Worked with ISO 27001 control requirements",
    ],
)
def test_technology_versions_and_names_are_not_hard_facts(text):
    assert not find_hard_facts(text), f"should NOT flag: {text}"


def test_supported_facts_are_permitted():
    """A real metric present in the source is not a fabrication."""
    finding = classify(
        "Reduced deployment time by 40%",
        confirmed_terms={"azure"},
        candidate_terms=[],
        supported_facts=True,
    )
    assert finding.classification == ClaimClass.PERMITTED_EXPANSION


# --- §4 Tier 2B: implementation / ownership claims --------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Designed a custom Kubernetes operator for workload placement",
        "Built a bespoke internal deployment platform",
        "Architected an enterprise-wide infrastructure framework",
        "Led the migration of the estate to Azure",
        "Managed a team of platform engineers",
        "Owned the entire production architecture",
        "Spearheaded the GitOps adoption programme",
    ],
)
def test_implementation_and_ownership_claims_are_detected(text):
    assert find_impl(text), f"should flag a Tier 2B claim: {text}"


@pytest.mark.parametrize(
    "text",
    [
        "Created Terraform modules for networking and identity resources",
        "Built CI/CD pipelines in Azure DevOps for containerised services",
        "Developed PowerShell scripts to automate access reviews",
        "Authored runbooks for production incident handling",
        "Designed Helm charts for internal service deployment",
    ],
)
def test_routine_engineering_artifacts_are_not_implementation_claims(text):
    """Writing a Terraform module or a pipeline is routine work, not a claim
    to have built a bespoke platform."""
    assert not find_impl(text), f"should NOT flag routine work: {text}"


def test_tier_2b_fires_even_with_confirmed_tech_and_no_numbers():
    """The exact gap Tier 2B exists to close."""
    finding = classify(
        "Designed a custom Kubernetes operator to manage cluster autoscaling",
        confirmed_terms={"kubernetes"},
        candidate_terms=["kubernetes"],
    )
    assert finding.classification == ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM


# --- §4 Tier 2: permitted expansion (the whole point) -----------------------


@pytest.mark.parametrize(
    "text",
    [
        "Configured and maintained Harness pipelines for AKS deployments, "
        "including approval gates and rollback steps",
        "Investigated failed deployments by reviewing rollout status, pod events "
        "and application logs in Azure Monitor",
        "Wrote Terraform to provision Azure networking, storage and identity "
        "resources, and reviewed plans before applying changes",
        "Tuned Prometheus alert rules and Grafana dashboards to reduce noisy "
        "paging for recurring conditions",
    ],
)
def test_realistic_wording_around_confirmed_tech_is_permitted(text):
    """No approval prompts for normal responsibility wording — the contract's
    central UX requirement."""
    confirmed = {
        "harness", "aks", "azure monitor", "terraform", "azure", "prometheus",
        "grafana", "kubernetes",
    }
    finding = classify(text, confirmed_terms=confirmed, candidate_terms=[])
    assert finding.classification == ClaimClass.PERMITTED_EXPANSION
    assert not finding.needs_correction


def test_unconfirmed_technology_is_flagged():
    finding = classify(
        "Managed Pulumi stacks across environments",
        confirmed_terms={"terraform"},
        candidate_terms=["Pulumi"],
    )
    assert finding.classification == ClaimClass.UNSUPPORTED_EXPANSION
    assert finding.unconfirmed_terms == ["Pulumi"]


def test_fabricated_fact_outranks_other_classes():
    """A number without support is the most serious finding."""
    finding = classify(
        "Led a team of 6 engineers using Pulumi",
        confirmed_terms={"terraform"},
        candidate_terms=["Pulumi"],
    )
    assert finding.classification == ClaimClass.FABRICATED_FACT


# --- §9 permanent style rules -----------------------------------------------


def test_comma_before_and_is_a_blocking_violation():
    text = "Managed Terraform, Ansible, and Helm across environments."
    violations = blocking_violations(text)
    assert "no_comma_before_and" in violations


def test_correct_serial_style_passes():
    text = "Managed Terraform, Ansible and Helm across environments."
    assert blocking_violations(text) == {}


def test_comma_rule_exempts_quoted_source_text():
    text = 'The JD states "Terraform, Ansible, and Helm" as requirements.'
    assert blocking_violations(text) == {}


def test_comma_rule_exempts_code_blocks():
    text = "Use this:\n```python\nx = [1, 2, and_flag]\nfoo(a, b, and_more)\n```"
    assert blocking_violations(text) == {}


def test_ai_tells_are_detected_but_not_blocking():
    text = "Leveraged cutting-edge Kubernetes to drive operational excellence."
    findings = check(text)
    assert "no_ai_tells" in findings
    assert blocking_violations(text) == {}  # advisory, not blocking


def test_repetitive_bullet_openers_are_flagged():
    """Bullets as a LIST, which is the only shape the workflow ever has.

    This test previously joined them into a markdown block and passed for
    years while the rule could never fire on a real draft — the workflow calls
    the style checks one bullet at a time, and one bullet has no repetition to
    find. The list is the point of the test, not an implementation detail.
    """
    assert "vary_sentence_structure" in check_section(
        [
            "Managed Azure infrastructure",
            "Managed Kubernetes clusters",
            "Managed CI/CD pipelines",
            "Managed monitoring dashboards",
        ]
    )


def test_varied_bullets_pass():
    assert "vary_sentence_structure" not in check_section(
        [
            "Managed Azure infrastructure across three subscriptions",
            "Investigated recurring deployment failures in AKS",
            "Wrote Terraform for networking and identity resources",
            "Tuned Prometheus alerts to cut noisy paging",
        ]
    )


def test_style_profile_is_renderable_and_extensible():
    guidance = prompt_guidance()
    assert "comma immediately before the word 'and'" in guidance
    from council.documents.style import DEFAULT_RULES, StyleRule

    extended = [*DEFAULT_RULES, StyleRule(id="future", instruction="A later preference.")]
    assert "A later preference." in prompt_guidance(extended)


# --- §2C extraction ---------------------------------------------------------


def test_plain_text_extraction():
    result = extract("notes.md", b"# Notes\n\nTerraform and AKS work.")
    assert "Terraform" in result.text
    assert result.detected_kind == "text"
    assert not result.truncated


def test_unsupported_format_is_refused_clearly():
    with pytest.raises(ExtractionError) as e:
        extract("photo.heic", b"\x00\x01binary")
    assert "unsupported file type" in str(e.value)


def test_empty_file_is_refused():
    with pytest.raises(ExtractionError):
        extract("empty.txt", b"")


def test_oversized_file_is_refused():
    with pytest.raises(ExtractionError) as e:
        extract("huge.txt", b"x" * (11 * 1024 * 1024))
    assert "limit is" in str(e.value)


def test_whitespace_only_file_is_refused_not_silently_empty():
    """A file that yields nothing must error, never become an empty resume."""
    with pytest.raises(ExtractionError) as e:
        extract("blank.txt", b"   \n\n  \t ")
    assert "no text could be extracted" in str(e.value)


def test_truncation_is_reported():
    result = extract("long.txt", b"a" * 200_000)
    assert result.truncated is True
    assert result.char_count == 120_000


# ------------------------------------------------- JD technology gap scanning


def _baseline() -> object:
    return assemble_confirmed(CareerProfile(), denials=NO_DENIALS)


def test_jd_technology_the_career_lacks_is_reported_unsupported():
    """The signal that matters most on a GCP-primary JD for an Azure engineer:
    say plainly what cannot be claimed, instead of quietly claiming it."""
    jd = (
        "Infrastructure Engineer working primarily on Google Cloud Platform. "
        "You will manage GKE clusters, Cloud Run services and Cloud SQL, write "
        "Terraform, run Kubernetes workloads and support production on Linux."
    )
    supported, unsupported = scan_jd_technologies(jd, _baseline())
    assert "gcp" in unsupported
    assert "gke" in unsupported
    assert "cloud run" in unsupported
    assert "terraform" in supported
    assert "kubernetes" in supported
    # Nothing may appear on both sides.
    assert not set(supported) & set(unsupported)


def test_jd_scanner_never_confirms_from_the_jd():
    """Scanning a JD must not mutate confirmed experience — the JD is the
    target, never evidence."""
    confirmed = _baseline()
    before = set(confirmed.terms)
    scan_jd_technologies("Deep GCP and BigQuery experience required.", confirmed)
    assert confirmed.terms == before


def test_jd_scanner_credits_a_technology_a_career_source_established():
    """A technology absent from the seeded baseline still counts once a career
    source establishes it — otherwise ingestion would be pointless."""
    jd = "We use Datadog for observability."
    _, unsupported = scan_jd_technologies(jd, _baseline())
    assert "datadog" in unsupported

    confirmed = assemble_confirmed(
        CareerProfile(technologies=["datadog"]),
        [{"authority": AUTHORITY_MASTER_RESUME, "title": "r", "text": "Datadog monitors."}],
        denials=NO_DENIALS,
    )
    supported, unsupported = scan_jd_technologies(jd, confirmed)
    assert "datadog" in supported
    assert "datadog" not in unsupported


def test_jd_scanner_matches_spelling_variants_to_one_canonical_name():
    supported, unsupported = scan_jd_technologies(
        "Experience with Google Cloud and GCP; Azure Kubernetes Service a plus.",
        _baseline(),
    )
    assert unsupported.count("gcp") == 1
    assert "google cloud" not in unsupported
    assert "aks" in supported


def test_key_vault_does_not_confirm_hashicorp_vault():
    """Word-boundary aliasing must not over-confirm: 'Key Vault' ends in the
    word Vault. Over-claiming is the failure this module exists to prevent."""
    confirmed = assemble_confirmed(
        CareerProfile(technologies=[], domains=[]),
        [{"authority": AUTHORITY_MASTER_RESUME, "title": "r", "text": "Used Azure Key Vault."}],
        denials=NO_DENIALS,
    )
    assert confirmed.is_confirmed("key vault")
    assert not confirmed.is_confirmed("hashicorp vault")


# ------------------------------------------- user statements as provenance


def test_user_statement_is_a_career_authority_distinct_from_documents():
    """The user is the primary source on their own career. Their statement
    confirms exactly like a document does — but the sources map must still say
    which one established the term, because 'you told us' and 'your resume says
    so' are different provenance."""
    confirmed = assemble_confirmed(
        CareerProfile(technologies=[], domains=[]),
        [
            {
                "authority": AUTHORITY_USER_STATEMENT,
                "title": "stated",
                "text": "I have professional Harness and Argo CD experience.",
            },
            {
                "authority": AUTHORITY_MASTER_RESUME,
                "title": "master",
                "text": "Terraform across Azure environments.",
            },
        ],
        denials=NO_DENIALS,
    )
    assert confirmed.is_confirmed("harness")
    assert confirmed.is_confirmed("argo cd")
    assert any(s.startswith("user_statement:") for s in confirmed.sources["harness"])
    assert any(s.startswith("master_resume:") for s in confirmed.sources["terraform"])
    # Provenance is not collapsed: a user statement never masquerades as a
    # document, and vice versa.
    assert not any(s.startswith("master_resume:") for s in confirmed.sources["harness"])


def test_a_user_statement_never_subtracts_either():
    """Every career authority is additive. Saying one thing does not retract
    what another source established."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "master",
            "text": "Jenkins pipelines and Splunk dashboards.",
        },
        {"authority": AUTHORITY_USER_STATEMENT, "title": "stated", "text": "I use Harness."},
    ]
    confirmed = assemble_confirmed(
        CareerProfile(technologies=[], domains=[]), documents, denials=NO_DENIALS
    )
    assert {"jenkins", "splunk", "harness"} <= confirmed.terms


def test_a_jd_is_still_not_a_user_statement():
    """Adding a new career authority must not weaken the one rule that keeps
    the JD out."""
    confirmed = assemble_confirmed(
        CareerProfile(technologies=[], domains=[]),
        [{"authority": AUTHORITY_JD, "title": "target", "text": "Requires Harness."}],
        denials=NO_DENIALS,
    )
    assert not confirmed.is_confirmed("harness")
