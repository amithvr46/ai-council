"""One technology vocabulary for both directions of career truth.

THE DEFECT

Positive confirmation scanned `DEFAULT_TECHNOLOGIES` (56 names). Denial
extraction used `DEFAULT_TECHNOLOGIES | FOREIGN_TECHNOLOGIES` (141 spellings).
So 62 technologies — GCP, Kafka, Datadog, OpenShift, Terraform's competitors,
most of the non-Azure world — could be DENIED but never CONFIRMED:

    "I used GCP professionally."
      -> parsed correctly as an explicit first-person career statement
      -> claimed_terms() == ["gcp"]              the term IS recognised
      -> stored as a user_statement career source
      -> scanned against a vocabulary containing no "gcp"
      -> established nothing, silently

The user states a true fact about their own career and the system drops it,
while confidently accepting the negation of the same fact. That violates the
product rule: the user establishes what is true, and Council must be able to
retain and use it.

THE FIX

`technology_vocabulary()` is now the single list, used by denial extraction and
by confirmation. `canonical_technology()` is the single canonicalisation, used
by both. Domains remain confirmation-only — a career source mentioning
"automation" reasonably establishes the domain, while "no automation
experience" is too broad a claim for a deterministic matcher to act on.

WHAT THIS FILE DOES NOT COVER

Instruction classification (who may establish a fact at all) is
tests/test_positive_confirmation.py. Denial semantics are
tests/test_negation.py and tests/test_negation_hardening.py. This file is
about the vocabulary the two share.
"""

import pytest

from council.documents.extract import Extracted
from council.documents.instructions import parse
from council.documents.profile import (
    AUTHORITY_MASTER_RESUME,
    DEFAULT_TECHNOLOGIES,
    FOREIGN_TECHNOLOGIES,
    canonical_technology,
    technology_vocabulary,
)
from council.documents.store import (
    apply_instruction_facts,
    confirmed_experience,
    store_document,
)


async def _state(claim: str) -> None:
    """Say something about your own career, exactly as the API does it."""
    parsed = parse(claim)
    if parsed.career_text():
        await store_document(
            filename="user-statement.txt",
            title="Stated by you",
            authority="user_statement",
            extracted=Extracted(
                text=parsed.career_text(),
                char_count=len(parsed.career_text()),
                truncated=False,
                detected_kind="text",
            ),
        )
    await apply_instruction_facts(parsed)


# ==========================================================================
# The vocabulary itself
# ==========================================================================


def test_one_vocabulary_covers_both_halves_of_the_career():
    vocabulary = set(technology_vocabulary())
    assert {canonical_technology(t) for t in DEFAULT_TECHNOLOGIES} <= vocabulary
    assert {t.lower() for t in FOREIGN_TECHNOLOGIES} <= vocabulary


def test_the_two_sides_canonicalise_identically():
    """The half of the fix that is easy to miss. If confirmation had kept
    `normalise` while denial used the JD aliases, a document establishing
    "Google Cloud Platform" and a denial of "GCP" would key different entries
    and the denial would silently fail to remove the claim."""
    for spelling in ["GCP", "Google Cloud", "Google Cloud Platform"]:
        assert canonical_technology(spelling) == "gcp"
    assert canonical_technology("Azure Kubernetes Service") == "aks"
    assert canonical_technology("golang") == "go"


# ==========================================================================
# An explicit statement establishes experience — the point of the fix
# ==========================================================================


@pytest.mark.parametrize(
    ("claim", "term"),
    [
        ("I used GCP.", "gcp"),  # plain, no "professionally"
        ("I used GCP professionally.", "gcp"),
        ("I used GCP at my last company.", "gcp"),
        ("I used Argo CD professionally.", "argo cd"),  # already in the baseline
        ("I worked with Harness.", "harness"),  # already in the baseline
        ("I used Kafka in production.", "kafka"),  # outside the baseline
        ("I have professional Datadog experience.", "datadog"),  # outside
        ("I ran OpenShift clusters.", "openshift"),  # outside
        ("I used Google Cloud professionally.", "gcp"),  # alias spelling
    ],
)
async def test_an_explicit_statement_establishes_the_technology(db, claim, term):
    await _state(claim)
    assert (await confirmed_experience()).is_confirmed(term) is True, claim


async def test_the_user_never_has_to_edit_a_static_list_first(db):
    """The product requirement, stated as a test. No profile edit, no resume
    mentioning it, no JD — just the user saying so."""
    assert (await confirmed_experience()).is_confirmed("snowflake") is False
    await _state("I used Snowflake professionally at my last company.")
    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("snowflake") is True
    assert confirmed.sources["snowflake"] == ["user_statement:Stated by you"]


# ==========================================================================
# The wider vocabulary must not become a wider attack surface
# ==========================================================================


@pytest.mark.parametrize(
    "instruction",
    [
        "My team used GCP.",
        "The JD requires GCP.",
        "Please add GCP.",
        "Make my resume sound like I used GCP.",
        "I think I might have used GCP once.",
    ],
)
async def test_the_non_establishing_categories_are_unaffected(db, instruction):
    """The fix widens WHICH technologies can be established. It must not widen
    WHO can establish them — that boundary is the previous two patches."""
    await _state(instruction)
    assert (await confirmed_experience()).is_confirmed("gcp") is False, instruction


async def test_a_jd_still_establishes_nothing_however_wide_the_vocabulary(db):
    """A JD is the target, never career evidence. Worth asserting on the
    document path specifically, because the JD is the single richest source of
    foreign technology names and they are all now in the vocabulary."""
    await store_document(
        filename="jd.txt",
        title="Senior Platform Engineer",
        authority="jd",
        extracted=Extracted(
            text="Required: GCP, Kafka, Datadog, OpenShift, Spinnaker.",
            char_count=52,
            truncated=False,
            detected_kind="text",
        ),
    )
    confirmed = await confirmed_experience()
    for term in ("gcp", "kafka", "datadog", "openshift", "spinnaker"):
        assert confirmed.is_confirmed(term) is False, term


# ==========================================================================
# Denial still wins, across the newly reachable half of the vocabulary
# ==========================================================================


async def test_a_denial_still_blocks_a_now_establishable_technology(db):
    await _state("I have never used GCP.")
    await store_document(
        filename="master.txt",
        title="Master resume",
        authority=AUTHORITY_MASTER_RESUME,
        extracted=Extracted(
            text="Ran GCP and Terraform workloads.",
            char_count=32,
            truncated=False,
            detected_kind="text",
        ),
    )
    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("gcp") is False  # denial beats the document
    assert confirmed.is_confirmed("terraform") is True  # and only GCP
    assert "gcp" in confirmed.sources  # the contradiction stays auditable


async def test_a_denial_written_one_way_removes_a_claim_written_another(db):
    """The canonicalisation half of the fix, end to end. Before it, these two
    spellings were two different technologies."""
    await _state("I used Google Cloud Platform professionally.")
    assert (await confirmed_experience()).is_confirmed("gcp") is True
    await _state("Actually I have never used GCP.")
    assert (await confirmed_experience()).is_confirmed("gcp") is False


async def test_supersession_still_works_on_a_foreign_technology(db):
    """The frozen rule, verified on a term that could not be confirmed at all
    before this fix — so it had never actually been exercised end to end."""
    await _state("I have never used Kafka.")
    assert (await confirmed_experience()).is_confirmed("kafka") is False
    await _state("I have used Kafka professionally since then.")
    assert (await confirmed_experience()).is_confirmed("kafka") is True


# ==========================================================================
# Document-only recognition still behaves as intended
# ==========================================================================


async def test_a_career_document_can_now_establish_a_foreign_technology(db):
    """Consistent with the frozen rule that every career source adds. A master
    resume mentioning Kafka is evidence of Kafka, exactly as it is for the
    baseline technologies — the baseline was never the reason to believe it."""
    await store_document(
        filename="master.txt",
        title="Master resume",
        authority=AUTHORITY_MASTER_RESUME,
        extracted=Extracted(
            text="Built streaming pipelines on Kafka and Terraform.",
            char_count=49,
            truncated=False,
            detected_kind="text",
        ),
    )
    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("kafka") is True
    assert confirmed.sources["kafka"] == ["master_resume:Master resume"]


async def test_omission_from_a_tailored_resume_is_still_not_negative_evidence(db):
    """The oldest rule in the file, re-checked because the vocabulary moved."""
    await store_document(
        filename="master.txt",
        title="Master resume",
        authority=AUTHORITY_MASTER_RESUME,
        extracted=Extracted(
            text="Kafka, Terraform, Jenkins.",
            char_count=26,
            truncated=False,
            detected_kind="text",
        ),
    )
    await store_document(
        filename="tailored.txt",
        title="Azure resume",
        authority="tailored_resume",
        extracted=Extracted(
            text="Terraform only.", char_count=15, truncated=False, detected_kind="text"
        ),
    )
    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("kafka") is True
    assert confirmed.is_confirmed("jenkins") is True


def test_domains_stay_confirmation_only():
    """Asymmetric on purpose, and the asymmetry survives the unification: a
    document mentioning "automation" establishes the domain, but
    "no automation experience" is too broad for a deterministic denial."""
    vocabulary = set(technology_vocabulary())
    assert "automation and scripting" not in vocabulary
    assert "monitoring and observability" not in vocabulary
    assert parse("I have no automation experience.").denied_terms() == {}
