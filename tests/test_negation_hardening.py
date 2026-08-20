"""Hardening pass on the denial boundary (2026-08-20, post-GPT review).

The architecture was approved; these are the six items the review asked for
before the fix could be closed. Each section below maps to one of them.

The frozen decisions this file defends, none of which it may quietly change:

  - explicit user contradiction is a hard boundary
  - denial wins over documents
  - a later explicit positive user career statement MAY supersede a denial
  - same-request positive + denial resolves to denial
  - documents may NEVER supersede a denial
  - the three kinds stay: never_used / not_professional / studied_only
  - longest-match-wins on denial extraction, so denying AKS never erases
    Azure or Kubernetes

The governing bias throughout: **predictable under-classification beats an
aggressive matcher**. The original defect ADDED experience the user does not
have. Over-denial DELETES experience they do. The second is worse, so every
ambiguous case in this file resolves towards doing nothing.
"""

import re
from pathlib import Path

import pytest

from council.documents.instructions import (
    NEVER_USED,
    NOT_PROFESSIONAL,
    STUDIED_ONLY,
    classify_negation,
    parse,
    technology_terms,
)
from council.documents.profile import (
    AUTHORITY_MASTER_RESUME,
    NO_DENIALS,
    CareerProfile,
    assemble_confirmed,
    denial_vocabulary,
    normalise_denial_term,
)
from council.documents.store import (
    apply_instruction_facts,
    confirmed_experience,
    list_denials,
    load_denials,
    store_document,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def _profile() -> CareerProfile:
    return CareerProfile(technologies=[], domains=[])


# ==========================================================================
# 1. The residual `assemble_confirmed()` bypass is closed
# ==========================================================================


def test_assembling_confirmed_experience_without_denials_is_impossible():
    """`denials` has no default. Omitting it is a TypeError, not a silent
    truth set with the boundary switched off."""
    with pytest.raises(TypeError):
        assemble_confirmed(_profile(), [])
    with pytest.raises(TypeError):
        assemble_confirmed(_profile())


def test_denials_cannot_be_passed_positionally():
    """Keyword-only, so the word "denials" appears at every call site and a
    reader can see whether the boundary was applied without opening the
    function."""
    with pytest.raises(TypeError):
        assemble_confirmed(_profile(), [], [])


def test_the_explicit_empty_case_still_works():
    confirmed = assemble_confirmed(_profile(), [], denials=NO_DENIALS)
    assert confirmed.terms == set()
    assert confirmed.denied == {}


def test_only_the_sanctioned_call_sites_assemble_confirmed_experience():
    """An architectural fitness function, and the real answer to "is the
    boundary hard to bypass?".

    A required argument stops someone FORGETTING the denials. It cannot stop
    someone writing `denials=NO_DENIALS` in production code and reintroducing
    the bug deliberately-but-thoughtlessly. This test makes that a CI failure:
    the pure assembler may only be called from the workflow (which is handed
    denials) and from the store (which loads them). Anywhere else, use
    `store.confirmed_experience()`.

    If you are here because this test failed: you probably want
    `confirmed_experience()`. If you genuinely need the pure function, add the
    file here and say why in the commit message.
    """
    sanctioned = {"documents/workflow.py", "documents/store.py"}
    callers = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        # The definition itself and docstring mentions are not calls.
        for match in re.finditer(r"(?<!def )\bassemble_confirmed\(", text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line = text[line_start:text.find("\n", match.start())]
            if line.lstrip().startswith("#") or "`" in line:
                continue
            callers.add(str(path.relative_to(SRC / "council")).replace("\\", "/"))
    assert callers == sanctioned, (
        f"unsanctioned assemble_confirmed() caller(s): {sorted(callers - sanctioned)}"
    )


def test_no_production_code_asserts_there_are_no_denials():
    """`NO_DENIALS` is for tests. In production the answer is always "load
    them", so the constant appearing under src/ means someone asserted
    something they could not have known."""
    users = []
    for path in SRC.rglob("*.py"):
        for line in path.read_text().splitlines():
            code = line.split("#")[0]
            if "`" in line or line.strip().startswith(("*", '"""')):
                continue  # a docstring explaining the constant is not a use
            if "denials=NO_DENIALS" in code:
                users.append(f"{path.relative_to(SRC / 'council')}: {line.strip()}")
    assert users == []


# ==========================================================================
# 2. Adversarial supersession
#
# Product rule (Amith's, not up for redesign): a later explicit positive user
# career statement supersedes an earlier denial. The attack surface is what
# counts as "explicit positive user career statement".
# ==========================================================================


MUST_NOT_SUPERSEDE = [
    # --- the review's list -------------------------------------------------
    "Please add GCP to my resume.",
    "The JD wants GCP.",
    "Can you make my resume sound like I used GCP?",
    "I want GCP highlighted.",
    "This role requires GCP.",
    "I am learning GCP.",
    # --- the same fabrication request without the question mark ------------
    "Make my resume sound like I used GCP.",
    "Write it as if I used GCP.",
    "Pretend I used GCP.",
    "Say that I have used GCP.",
    "Word it so I look like a GCP engineer.",
    # --- hedged recollection ------------------------------------------------
    "I think I might have used GCP once.",
    "I may have used GCP briefly.",
    "I possibly used GCP at some point.",
    "I sort of used GCP a bit.",
    # --- third parties and the job, not the user ----------------------------
    "My team used GCP.",
    "The client used GCP.",
]


@pytest.mark.parametrize("sentence", MUST_NOT_SUPERSEDE)
def test_these_must_not_supersede_a_denial(sentence):
    assert parse(sentence).claimed_terms() == [], sentence


@pytest.mark.parametrize("sentence", MUST_NOT_SUPERSEDE)
async def test_an_existing_denial_survives_all_of_them(db, sentence):
    """End to end through the persistence layer, because `claimed_terms()`
    being empty is only half the guarantee — the other half is that nothing
    else in the write path reverses a denial."""
    await apply_instruction_facts(parse("I have never used GCP"))
    await apply_instruction_facts(parse(sentence))
    assert [d.term for d in await load_denials()] == ["gcp"], sentence
    assert (await confirmed_experience()).is_confirmed("gcp") is False


MUST_SUPERSEDE = [
    "I have professional GCP experience.",
    "I used GCP professionally at my last company.",
    "I have used GCP in production for two years.",
    "I ran GCP workloads at my last employer.",
    "I have since built GCP infrastructure professionally.",
]


@pytest.mark.parametrize("sentence", MUST_SUPERSEDE)
async def test_a_genuine_later_claim_does_supersede(db, sentence):
    """The product rule must still work. A hardening pass that quietly
    disabled supersession would be a worse regression than the attack it
    defends against."""
    await apply_instruction_facts(parse("I have never used GCP"))
    facts = await apply_instruction_facts(parse(sentence))
    assert facts["superseded"] == ["gcp"], sentence
    assert await load_denials() == []


def test_a_request_to_fabricate_is_not_even_a_career_source():
    """Stronger than "does not supersede", and worth stating separately.

    "Make my resume sound like I used GCP" previously became a user_statement
    career source, which CONFIRMED GCP from nothing at all — no denial
    required. Not superseding is necessary; not being career prose is the
    complete fix.
    """
    parsed = parse("Make my resume sound like I used GCP.")
    assert parsed.career_statements == []
    assert parsed.career_text() == ""
    assert parsed.preferences == ["Make my resume sound like I used GCP."]


def test_a_hedged_claim_may_be_prose_but_may_not_reverse_a_boundary():
    """Deliberately asymmetric. A hedge is weak evidence, not no evidence, so
    it stays in career prose — but when weak new evidence meets a definite
    earlier denial, the definite statement stands."""
    parsed = parse("I think I might have used GCP once.")
    assert parsed.career_statements != []
    assert parsed.claimed_terms() == []


# --- mixed sentences: positive and negative in one breath -------------------


def test_a_denial_and_a_claim_in_one_sentence_do_not_contaminate_each_other():
    """The over-denial case inside a single sentence.

    Classified whole this reads as a denial, and the denial would then cover
    every technology the sentence names — erasing the Jenkins experience the
    same sentence explicitly asserts.
    """
    parsed = parse("I have never used Harness but I have used Jenkins extensively.")
    assert parsed.denied_terms() == {"harness": NEVER_USED}
    assert parsed.claimed_terms() == ["jenkins"]


def test_a_denying_clause_with_a_pronoun_borrows_the_subject_from_its_sentence():
    """"haven't used it professionally" names no technology. The referent is in
    the other clause, so reading it from there beats recording a denial that
    blocks nothing."""
    parsed = parse("I know about GCP but haven't used it professionally.")
    assert parsed.denied_terms() == {"gcp": NOT_PROFESSIONAL}
    assert parsed.claimed_terms() == []


def test_a_borrowed_referent_never_takes_a_technology_another_clause_claimed():
    parsed = parse(
        "I have used Terraform heavily but I have no professional Harness experience."
    )
    assert "terraform" not in parsed.denied_terms()
    assert parsed.claimed_terms() == ["terraform"]


@pytest.mark.parametrize(
    ("sentence", "denied", "claimed"),
    [
        (
            "I have deep Terraform experience although I have no Harness experience.",
            {"harness": NEVER_USED},
            ["terraform"],
        ),
        (
            "I ran Jenkins for years, however I only studied GCP.",
            {"gcp": STUDIED_ONLY},
            ["jenkins"],
        ),
        (
            "I support AKS in production while I have never touched OpenShift.",
            {"openshift": NEVER_USED},
            ["aks"],
        ),
    ],
)
def test_mixed_sentences_across_contrastive_joins(sentence, denied, claimed):
    parsed = parse(sentence)
    assert parsed.denied_terms() == denied
    assert parsed.claimed_terms() == claimed


# ==========================================================================
# 3. Regex boundary attacks
# ==========================================================================

# Sentences that ASSERT experience while containing negation vocabulary.
# Every one of these was a false positive before the patterns were tightened,
# or is adjacent to one and kept as a guard.
FALSE_POSITIVE_ATTACKS = [
    # The `_NOT_PROFESSIONAL` 60-character window, which used to accept any
    # negator followed by "in production" within 60 chars.
    "I have never had an outage while running Jenkins in production.",
    "I do not just write Terraform, I run it in production.",
    "It is not unusual for me to deploy Harness in production.",
    "I have never lost data while operating Kubernetes in production.",
    "There is not a week where I do not use Terraform at work.",
    # `not only ... but` is an intensifier, not a negation.
    "I have not only used Harness, I built our deployment templates.",
    "I have not only run Jenkins but also migrated it to Azure DevOps.",
    # Bare "had" / "been", which used to sit in the never-used verb list.
    "I have never been on call without Grafana dashboards.",
    "I have never had a failed Terraform apply reach production.",
    # Plain positives with incidental negative words.
    "I use Terraform daily, no exceptions.",
    "Nothing I deploy goes out without a Jenkins pipeline.",
]


@pytest.mark.parametrize("sentence", FALSE_POSITIVE_ATTACKS)
def test_no_false_positive_denials(sentence):
    """A false denial deletes real experience. This is the failure mode the
    review told me to favour avoiding, even at the cost of missing denials."""
    assert classify_negation(sentence) is None, sentence


@pytest.mark.parametrize("sentence", FALSE_POSITIVE_ATTACKS)
def test_no_false_positive_denials_end_to_end(sentence):
    """Not just unclassified — the technologies named must stay confirmed."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "Master resume",
            "text": (
                "Terraform, Jenkins, Harness, Kubernetes, Grafana, Azure DevOps "
                "across production environments."
            ),
        }
    ]
    parsed = parse(sentence)
    confirmed = assemble_confirmed(_profile(), documents, denials=NO_DENIALS)
    assert parsed.denied_terms() == {}, sentence
    for term in ("terraform", "jenkins", "harness", "kubernetes", "grafana"):
        assert confirmed.is_confirmed(term), (sentence, term)


# Real denials that used to escape every pattern in the file.
FALSE_NEGATIVE_ATTACKS = [
    ("I have no Harness experience.", "harness"),
    ("I've got zero Jenkins experience.", "jenkins"),
    ("I lack Harness experience.", "harness"),
    ("I have no real GCP experience.", "gcp"),
    ("I have no prior Datadog experience.", "datadog"),
    ("I have never had any hands-on experience with OpenShift.", "openshift"),
    ("I have no exposure to Anthos.", "anthos"),
    ("I do not have any Kafka experience.", "kafka"),
]


@pytest.mark.parametrize(("sentence", "term"), FALSE_NEGATIVE_ATTACKS)
def test_natural_denials_are_not_missed(sentence, term):
    parsed = parse(sentence)
    assert classify_negation(sentence) is not None, sentence
    assert term in parsed.denied_terms(), sentence


def test_two_negators_in_one_clause_decline_rather_than_guess():
    """A double negative usually asserts the opposite of its parts.

    "There is not a week where I do not use Terraform at work" contains a
    negator directly governing a usage verb — twice — so no amount of pattern
    tightening reads it correctly. Declining is the only honest option.
    """
    assert classify_negation(
        "There is not a week where I do not use Terraform at work."
    ) is None
    assert classify_negation("Nothing I deploy goes out without Jenkins.") is None
    # One negator still classifies normally; the rule is about ambiguity, not
    # about being squeamish.
    assert classify_negation("I have never used Harness.") == NEVER_USED


def test_the_cost_of_the_two_negator_rule_is_a_known_false_negative():
    """Two denials crammed into one clause are declined too. Recoverable by
    saying them as two sentences; a wrongly erased technology is not
    recoverable by anything the user can see."""
    crammed = parse("I never used Harness and I don't have Jenkins experience.")
    assert crammed.denied_terms() == {}
    separated = parse(
        "I never used Harness. I don't have Jenkins experience."
    )
    assert set(separated.denied_terms()) == {"harness", "jenkins"}


def test_a_third_party_must_be_the_subject_not_merely_mentioned():
    """"My team used GCP" is somebody else's experience. "I used GCP at my last
    company" is the user's, and happens to name an employer — excluding it
    would break the product rule for one of its most natural phrasings."""
    assert parse("My team used GCP.").claimed_terms() == []
    assert parse("Our client ran GCP workloads.").claimed_terms() == []
    assert parse("I used GCP professionally at my last company.").claimed_terms() == [
        "gcp"
    ]
    assert parse("I have used GCP at my company in production.").claimed_terms() == [
        "gcp"
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "I have used Terraform professionally but never Harness.",
        "Harness: none.",
        "Jenkins is not something in my background.",
        "I never used Harness and I don't have Jenkins experience.",
    ],
)
def test_deliberate_under_classification_is_recorded_not_pretended(sentence):
    """These read as denials to a human and are NOT detected. That is a known,
    accepted limit, not an oversight — each would need either a verb-less
    negation rule or general NLP, and both risk the false positives above.

    The consequence is bounded: the technology stays in whatever state the
    career sources put it in. Nothing false is asserted. If one of these
    phrasings turns out to be common in real use, add it deliberately with a
    false-positive attack alongside it.
    """
    assert parse(sentence).denied_terms() == {}, sentence


# ==========================================================================
# 4. Over-denial protection across the whole vocabulary
# ==========================================================================


def _composites() -> list[tuple[str, str, list[str]]]:
    """Every vocabulary spelling that contains another as a whole word.

    Generated rather than hand-listed so a new alias added to `profile.py`
    is covered by this regression the day it is added.
    """
    vocab = denial_vocabulary()
    found = []
    for outer in vocab:
        nested = sorted(
            {
                normalise_denial_term(inner)
                for inner in vocab
                if inner != outer
                and re.search(rf"(?<![\w-]){re.escape(inner)}(?![\w-])", outer)
            }
        )
        canonical = normalise_denial_term(outer)
        nested = [n for n in nested if n != canonical]
        if nested:
            found.append((outer, canonical, nested))
    return found


def test_the_vocabulary_actually_contains_nested_names():
    """Guards the guard: if this ever returns nothing, the parametrised test
    below is silently vacuous."""
    composites = _composites()
    assert len(composites) >= 12
    names = {outer for outer, _, _ in composites}
    assert {"azure kubernetes service", "elastic kubernetes service",
            "google kubernetes engine", "azure key vault",
            "terraform enterprise"} <= names


@pytest.mark.parametrize(
    ("spelling", "canonical", "nested"),
    _composites(),
    ids=[c[0].replace(" ", "-") for c in _composites()],
)
def test_denying_a_composite_never_erases_the_names_inside_it(
    spelling, canonical, nested
):
    """Denying AKS must not erase Azure and Kubernetes.

    Run over every nested pair in the vocabulary, not just the pair that
    happened to be found by hand: AKS/Azure, EKS/Kubernetes, GKE/Kubernetes,
    Azure Key Vault/Azure, Terraform Enterprise/Terraform and the rest.
    """
    extracted = technology_terms(f"I have never used {spelling}")
    assert extracted == [canonical], (spelling, extracted)
    for parent in nested:
        assert parent not in extracted, (spelling, parent)


@pytest.mark.parametrize(
    ("spelling", "canonical", "nested"),
    _composites(),
    ids=[c[0].replace(" ", "-") for c in _composites()],
)
def test_the_parent_technologies_stay_confirmed_after_denying_a_composite(
    spelling, canonical, nested
):
    """The consequence that matters, asserted on the assembled truth set
    rather than on the extractor."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "Master resume",
            "text": (
                "Azure, AWS, Kubernetes, Terraform, GitHub, Key Vault, "
                "Log Analytics, JFrog Artifactory, Entra ID, S3, GCP."
            ),
        }
    ]
    parsed = parse(f"I have never used {spelling}")
    denials = [
        type("D", (), {"term": t, "kind": k, "statement": ""})()
        for t, k in parsed.denied_terms().items()
    ]
    confirmed = assemble_confirmed(_profile(), documents, denials=denials)
    for parent in nested:
        if parent in confirmed.sources:
            assert confirmed.is_confirmed(parent), (spelling, parent)


def test_denying_the_parent_explicitly_still_works():
    """The protection is against ACCIDENTAL erasure, not against the user
    denying a broad platform on purpose."""
    parsed = parse("I have never used Azure.")
    assert parsed.denied_terms() == {"azure": NEVER_USED}


def test_both_can_be_denied_when_both_are_named():
    parsed = parse("I have never used Azure Kubernetes Service or Kubernetes.")
    assert set(parsed.denied_terms()) == {"aks", "kubernetes"}


# ==========================================================================
# 5. Persistence and audit semantics after supersession
# ==========================================================================


async def test_the_full_forward_sequence(db):
    """never used X -> used X professionally -> X is confirmed, denial
    inactive, both statements auditable, timestamps establish ordering."""
    await apply_instruction_facts(parse("I have never used Harness"))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    claim = "I have used Harness professionally since then"
    await apply_instruction_facts(parse(claim))
    # The positive statement becomes a career source, exactly as the API does.
    from council.documents.extract import Extracted
    from council.documents.profile import AUTHORITY_USER_STATEMENT

    await store_document(
        filename="user-statement.txt",
        title="Stated by you",
        authority=AUTHORITY_USER_STATEMENT,
        extracted=Extracted(
            text=claim, char_count=len(claim), truncated=False, detected_kind="text"
        ),
    )

    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("harness") is True
    assert confirmed.denied == {}

    ledger = await list_denials()
    assert len(ledger) == 1
    row = ledger[0]
    assert row["active"] is False
    assert row["statement"] == "I have never used Harness"
    assert row["superseded_by"] == claim
    assert row["superseded_at"] is not None
    assert row["created_at"] < row["superseded_at"]


async def test_reversal_again_keeps_the_whole_history(db):
    """denial -> positive -> denial.

    The newest explicit statement controls current state. Before the history
    column, re-denying cleared `superseded_by`, so the middle statement
    vanished and the record claimed the user had never contradicted
    themselves.
    """
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have used Harness professionally"))
    await apply_instruction_facts(parse("Actually I have never used Harness"))

    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("harness") is False
    assert confirmed.denial_kind("harness") == NEVER_USED

    ledger = await list_denials()
    assert len(ledger) == 1
    history = ledger[0]["history"]
    assert [h["action"] for h in history] == ["denied", "superseded", "denied"]
    assert history[0]["statement"] == "I have never used Harness"
    assert history[1]["statement"] == "I have used Harness professionally"
    assert history[2]["statement"] == "Actually I have never used Harness"
    # Ordering is legible from the record itself, not from row order.
    assert [h["at"] for h in history] == sorted(h["at"] for h in history)


async def test_history_survives_a_third_reversal(db):
    for statement in [
        "I have never used Harness",
        "I have used Harness professionally",
        "Actually I have never used Harness",
        "I have used Harness professionally again",
    ]:
        await apply_instruction_facts(parse(statement))
    history = (await list_denials())[0]["history"]
    assert [h["action"] for h in history] == [
        "denied", "superseded", "denied", "superseded",
    ]
    assert (await load_denials()) == []


async def test_the_denial_kind_is_carried_through_history(db):
    await apply_instruction_facts(parse("I only studied GCP"))
    await apply_instruction_facts(parse("I have used GCP professionally"))
    await apply_instruction_facts(parse("I have no professional GCP experience"))

    ledger = await list_denials()
    assert ledger[0]["kind"] == NOT_PROFESSIONAL  # current state
    kinds = [h.get("kind") for h in ledger[0]["history"] if h["action"] == "denied"]
    assert kinds == [STUDIED_ONLY, NOT_PROFESSIONAL]  # how it got there


async def test_documents_still_cannot_supersede_after_all_this(db):
    """The frozen rule, re-asserted at the end of the reversal machinery: no
    number of documents is a statement by the user."""
    await apply_instruction_facts(parse("I have never used Harness"))
    from council.documents.extract import Extracted

    for i in range(3):
        await store_document(
            filename=f"r{i}.txt",
            title=f"Master resume v{i}",
            authority=AUTHORITY_MASTER_RESUME,
            extracted=Extracted(
                text=f"Ran Harness pipelines in year {2020 + i}.",
                char_count=40,
                truncated=False,
                detected_kind="text",
            ),
        )
    assert (await confirmed_experience()).is_confirmed("harness") is False
    ledger = await list_denials()
    assert ledger[0]["active"] is True
    assert [h["action"] for h in ledger[0]["history"]] == ["denied"]
