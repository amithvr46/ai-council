"""What may establish confirmed professional experience — and what may not.

The negation work asked "what stops a technology becoming confirmed?" and
answered it well. This file asks the other half of the same question: **what
counts as the user asserting they have used something?**

The two are one defect shape seen from opposite sides. In both, a sentence that
merely CONTAINS a technology name is read as evidence the user has used it:

    "I have never used Harness."              -> confirmed Harness   (fixed at 3ce07fe)
    "Make my resume sound like I used GCP."   -> confirmed GCP       (fixed at 49af7e6)
    "I think I might have used GCP once."     -> confirmed GCP       (fixed here)
    "My team used GCP."                       -> confirmed GCP       (fixed here)

The last two survived the earlier passes because they were blocked only from
SUPERSEDING a denial. That protects a technology the user has already denied
and does nothing at all for one they have not — which is almost every
technology, almost all of the time.

THE RULE

Only an explicit first-person assertion of the user's own experience may
establish a career fact. Four things that look like one, and are not:

    uncertain    "I think I might have used GCP once"   trying to remember
    third party  "My team used GCP"                     someone else's experience
    request      "Please emphasize GCP" / "The JD wants GCP"   not about the past
    framing      "Make my resume sound like I used GCP" a request to fabricate

Classification is deterministic and conservative. Where a sentence is unclear
it becomes a preference, which is forgotten after the run, rather than a career
fact, which is durable and can reach a submitted resume.
"""

import pytest

from council.documents.extract import Extracted
from council.documents.instructions import parse
from council.documents.profile import AUTHORITY_MASTER_RESUME
from council.documents.store import (
    apply_instruction_facts,
    career_documents,
    confirmed_experience,
    store_document,
)

# ==========================================================================
# Parser level
# ==========================================================================

UNCERTAIN = [
    "I think I might have used GCP once.",
    "I may have used GCP briefly.",
    "I possibly used GCP at some point.",
    "I sort of used GCP a bit.",
    "I believe I used GCP on one project.",
    "I guess I have used GCP.",
    "I can't remember whether I used GCP.",
    "I vaguely remember using GCP.",
    "Maybe I used GCP at that job.",
    "I might have configured GCP once.",
]

THIRD_PARTY = [
    "My team used GCP.",
    "The client used GCP.",
    "Our client ran GCP workloads.",
    "My company standardized on GCP.",
    "The vendor managed GCP for us.",
    "My previous employer used GCP.",
    "Their team deployed GCP infrastructure.",
    "My colleagues worked with GCP.",
]

REQUESTS_AND_PREFERENCES = [
    "The JD wants GCP.",
    "This role requires GCP.",
    "Please emphasize GCP.",
    "Please add GCP to my resume.",
    "I want GCP highlighted.",
    "Highlight GCP near the top.",
]

FABRICATION_FRAMING = [
    "Make my resume sound like I used GCP.",
    "Can you make my resume sound like I used GCP?",
    "Write it as if I used GCP.",
    "Pretend I used GCP.",
    "Say that I have used GCP.",
    "Word it so I look like a GCP engineer.",
]

MUST_NOT_ESTABLISH = [
    *[(s, "uncertain") for s in UNCERTAIN],
    *[(s, "third-party") for s in THIRD_PARTY],
    *[(s, "request") for s in REQUESTS_AND_PREFERENCES],
    *[(s, "framing") for s in FABRICATION_FRAMING],
]


@pytest.mark.parametrize(("sentence", "category"), MUST_NOT_ESTABLISH)
def test_these_are_not_career_prose(sentence, category):
    """Not merely blocked from superseding — not career prose at all.

    `career_text()` is what gets persisted as a user_statement career source,
    and a career source is scanned for technology names that become confirmed
    experience. Anything reaching that string can confirm a technology.
    """
    parsed = parse(sentence)
    assert parsed.career_statements == [], (category, sentence)
    assert parsed.career_text() == "", (category, sentence)
    assert parsed.claimed_terms() == [], (category, sentence)


@pytest.mark.parametrize(("sentence", "category"), MUST_NOT_ESTABLISH)
def test_nothing_is_silently_dropped(sentence, category):
    """Reclassified as a preference, never discarded. A formatting request the
    user made still has to reach the model as a formatting request."""
    parsed = parse(sentence)
    assert parsed.preferences == [sentence], (category, sentence)


EXPLICIT_FIRST_PERSON = [
    "I used GCP professionally at my last company.",
    "I have professional GCP experience.",
    "I have used GCP in production for two years.",
    "I ran GCP workloads at my last employer.",
    "I built our GCP landing zone.",
    "I have managed GCP projects since 2023.",
    "I support GCP infrastructure day to day.",
]


@pytest.mark.parametrize("sentence", EXPLICIT_FIRST_PERSON)
def test_an_explicit_first_person_assertion_still_establishes_experience(sentence):
    """The product rule must survive the patch. Over-blocking here means the
    user restates legitimate experience over and over, which is exactly the
    friction the Career Experience Profile exists to remove."""
    parsed = parse(sentence)
    assert parsed.career_statements == [sentence], sentence
    assert "gcp" in parsed.claimed_terms(), sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "I used Ansible briefly at work.",
        "I used Terraform a bit on the platform team.",
        "I have used Jenkins on and off for years.",
        "I worked with Kafka on one project.",
    ],
)
def test_limited_experience_is_still_experience(sentence):
    """Scale and duration are NOT uncertainty.

    "briefly", "a bit", "on one project" say the experience was small. They do
    not say the user is unsure it happened. An earlier draft of the uncertainty
    pattern included these words and would have erased real, if limited,
    experience — the failure mode the whole denial boundary is built to avoid,
    arriving through the front door.
    """
    assert parse(sentence).career_statements == [sentence], sentence


def test_a_third_party_mention_does_not_block_the_users_own_claim():
    """The third party must be the SUBJECT. Naming an employer inside a
    first-person claim is one of the most natural ways to say this."""
    for sentence in [
        "I used GCP professionally at my last company.",
        "I have used GCP at my company in production.",
        "I ran the GCP migration for my team.",
    ]:
        assert parse(sentence).career_statements == [sentence], sentence


def test_a_mixed_sentence_keeps_only_the_users_half():
    parsed = parse("My team used GCP but I ran the Terraform pipelines myself.")
    assert parsed.claimed_terms() == ["terraform"]
    assert "gcp" not in parsed.claimed_terms()


# ==========================================================================
# Persistence / end to end — the layer that actually matters
# ==========================================================================


async def test_uncertain_recollection_never_becomes_confirmed_experience(db):
    """The full path: instruction -> stored career source -> confirmed set."""
    await apply_instruction_facts(parse("I think I might have used GCP once."))
    assert await career_documents() == []
    assert (await confirmed_experience()).is_confirmed("gcp") is False


async def test_third_party_experience_never_becomes_the_users(db):
    await apply_instruction_facts(parse("My team used GCP extensively."))
    assert await career_documents() == []
    assert (await confirmed_experience()).is_confirmed("gcp") is False


@pytest.mark.parametrize(("sentence", "category"), MUST_NOT_ESTABLISH)
async def test_none_of_them_confirm_anything_end_to_end(db, sentence, category):
    parsed = parse(sentence)
    if parsed.career_text():  # what the API would persist
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
    assert (await confirmed_experience()).is_confirmed("gcp") is False, (
        category,
        sentence,
    )


async def test_an_explicit_claim_does_confirm_end_to_end(db):
    """The positive control. Without this the tests above could pass because
    confirmation is broken rather than because the filter works.

    Uses Terraform rather than GCP deliberately — see
    `test_a_stated_technology_outside_the_baseline_vocabulary_is_not_confirmed`
    below, which records why GCP could not serve as a positive control here.
    """
    claim = "I used Terraform professionally at my last company."
    parsed = parse(claim)
    assert parsed.career_text() == claim
    await store_document(
        filename="user-statement.txt",
        title="Stated by you",
        authority="user_statement",
        extracted=Extracted(
            text=claim, char_count=len(claim), truncated=False, detected_kind="text"
        ),
    )
    await apply_instruction_facts(parsed)
    assert (await confirmed_experience()).is_confirmed("terraform") is True


async def test_a_document_that_mentions_a_third_partys_technology_is_unaffected(db):
    """Scope check. This patch changes how INSTRUCTIONS are classified and
    must not touch how documents are read — a master resume is still a career
    source, and omission is still not negative evidence."""
    await store_document(
        filename="master.txt",
        title="Master resume",
        authority=AUTHORITY_MASTER_RESUME,
        extracted=Extracted(
            text="Ran Terraform and Jenkins workloads in production.",
            char_count=50,
            truncated=False,
            detected_kind="text",
        ),
    )
    await apply_instruction_facts(parse("My team used Terraform."))
    confirmed = await confirmed_experience()
    # From the document, not the instruction. The instruction contributed
    # nothing, and the document is unaffected by this patch.
    assert confirmed.is_confirmed("terraform") is True
    assert confirmed.is_confirmed("jenkins") is True
    assert "user_statement" not in str(confirmed.sources.get("terraform", []))


async def test_a_stated_technology_outside_the_baseline_vocabulary_is_not_confirmed(db):
    """PRE-EXISTING GAP, recorded rather than fixed. Not caused by this patch.

    "I used GCP professionally at my last company" is parsed correctly as an
    explicit first-person career statement, and `claimed_terms()` returns
    ["gcp"] — the denial vocabulary knows the term. But confirmation scans
    career sources against DEFAULT_TECHNOLOGIES, which does not contain "gcp",
    so the technology never becomes confirmed experience.

    The user therefore cannot establish a technology outside the baseline list
    by stating it plainly. That works against the product goal — provide your
    career information and Council understands it — and it is why GCP could
    not be used as a positive control in this file.

    This test asserts CURRENT behaviour so the gap is visible and a future fix
    has something to flip. It is deliberately not fixed here: it belongs to the
    confirmation-vocabulary / technology-discovery path, not to instruction
    classification, and this patch was scoped to the latter.
    """
    claim = "I used GCP professionally at my last company."
    parsed = parse(claim)
    assert parsed.career_statements == [claim]  # classified correctly
    assert parsed.claimed_terms() == ["gcp"]  # the term is recognised

    await store_document(
        filename="user-statement.txt",
        title="Stated by you",
        authority="user_statement",
        extracted=Extracted(
            text=claim, char_count=len(claim), truncated=False, detected_kind="text"
        ),
    )
    await apply_instruction_facts(parsed)
    assert (await confirmed_experience()).is_confirmed("gcp") is False  # the gap


async def test_the_denial_boundary_is_untouched(db):
    """Regression guard on the frozen rules this patch must not disturb."""
    await apply_instruction_facts(parse("I have never used Harness"))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    # uncertain statement cannot lift a denial
    await apply_instruction_facts(parse("I think I might have used Harness."))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    # third-party statement cannot lift a denial
    await apply_instruction_facts(parse("My team used Harness."))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    # an explicit first-person claim still can
    facts = await apply_instruction_facts(
        parse("I have used Harness professionally since then.")
    )
    assert facts["superseded"] == ["harness"]


def test_preferences_still_reach_the_model():
    """A formatting instruction reclassified out of career prose must still be
    delivered as a preference, or the patch silently drops user intent."""
    parsed = parse(
        "Please emphasize GCP. My team used GCP. I used Terraform professionally."
    )
    assert parsed.preference_text() == "Please emphasize GCP. My team used GCP."
    assert parsed.career_text() == "I used Terraform professionally."
    assert parsed.claimed_terms() == ["terraform"]
