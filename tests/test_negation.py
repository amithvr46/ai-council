"""Explicit user contradiction is a hard boundary.

The defect these tests exist to prevent, in full:

    instruction: "I have never used Harness or Jenkins"
    -> the instruction parser matched the first-person experience pattern
       (it IS a first-person sentence containing "used")
    -> the sentence was stored as a user_statement career source
    -> assemble_confirmed scanned that source for technology names
    -> Harness and Jenkins became CONFIRMED PROFESSIONAL EXPERIENCE
    -> both were eligible for a submitted resume

A denial reaching positive career prose confirms exactly the technologies it
denies. That is the failure mode, and it is why several tests below assert on
the shape of the stored data rather than only on `is_confirmed()`: a fix that
returns the right answer from one accessor while leaving the denied term in
`confirmed.terms` would leave every direct reader of that set still wrong.

The invariant:

    positive source mention + explicit user denial
      -> denial wins for career confirmation
      -> the conflict stays visible and auditable
      -> the technology cannot silently become professional experience
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from council.api.main import app
from council.db.models import CareerDenialRow, DocumentRow
from council.db.session import session_scope
from council.documents.conflicts import CONFLICT_EXPERIENCE_DENIED, denial_conflicts
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
    AUTHORITY_USER_STATEMENT,
    NO_DENIALS,
    CareerProfile,
    Denied,
    assemble_confirmed,
    scan_jd_technologies,
)
from council.documents.store import (
    apply_instruction_facts,
    confirmed_experience,
    list_denials,
    load_denials,
    record_denials,
    store_document,
)
from council.documents.workflow import _career_context, _merge_denials
from tests.test_resume_api import (
    GCP_JD,
    _client,
    _upload_master,
)


def _profile(**kwargs) -> CareerProfile:
    """A profile with nothing seeded, so a confirmed term can only have come
    from the document or denial under test."""
    kwargs.setdefault("technologies", [])
    kwargs.setdefault("domains", [])
    return CareerProfile(**kwargs)


# --------------------------------------------------------------------------
# 1. The parser must not read a denial as a career claim
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "I have never used Harness",
        "I never worked with Jenkins",
        "I have not used Harness",
        "I haven't worked with Jenkins",
        "I have no professional GCP experience",
        "I only studied GCP",
        "I have no experience with Datadog",
        "I don't have any Kafka experience",
        "I have never touched OpenShift",
        "I have zero experience with Puppet",
        "I've never really used Chef",
        "I have no hands-on experience with Spinnaker",
        "I have no exposure to Anthos",
        "I only ever studied Terraform Enterprise",
        "I only used Rancher in a home lab",
        "My Istio experience is personal projects only",
        "I have never used Harness professionally",
    ],
)
def test_a_denial_is_never_a_positive_career_statement(sentence):
    """The single most important assertion in the file.

    `career_statements` is what becomes a user_statement career source, and a
    career source is scanned for technology names. Anything that lands here is
    a technology the system will treat as professional experience.
    """
    parsed = parse(sentence)
    assert parsed.career_statements == []
    assert parsed.denials, f"not detected as a denial: {sentence!r}"
    assert parsed.career_text() == ""


@pytest.mark.parametrize(
    ("sentence", "kind"),
    [
        ("I have never used Harness", NEVER_USED),
        ("I never worked with Jenkins", NEVER_USED),
        ("I haven't worked with Jenkins", NEVER_USED),
        ("I have no experience with Datadog", NEVER_USED),
        ("I have no professional GCP experience", NOT_PROFESSIONAL),
        ("I have no production experience with Kafka", NOT_PROFESSIONAL),
        ("I have never used Harness professionally", NOT_PROFESSIONAL),
        ("I only studied GCP", STUDIED_ONLY),
        ("I only used Rancher in a home lab", STUDIED_ONLY),
        ("My Istio experience is personal projects only", STUDIED_ONLY),
    ],
)
def test_the_kind_of_denial_is_recorded(sentence, kind):
    """All three block confirmation; they are not the same statement.

    "I only studied GCP" and "I have never used GCP" are both true reasons GCP
    is not professional experience, and flattening them would throw away the
    user's actual words for no benefit.
    """
    assert classify_negation(sentence) == kind
    assert parse(sentence).denials[0].kind == kind


def test_a_positive_claim_still_parses_as_a_career_statement():
    """The fix must not swing the other way. Negation handling that swallows
    real claims would quietly erase experience the user does have."""
    parsed = parse("I also have professional Harness experience.")
    assert parsed.career_statements == ["I also have professional Harness experience."]
    assert parsed.denials == []


def test_preferences_are_still_preferences():
    parsed = parse("Emphasise AKS and keep it to 2 pages.")
    assert parsed.career_statements == []
    assert parsed.denials == []
    assert parsed.preferences == ["Emphasise AKS and keep it to 2 pages."]


def test_one_instruction_can_carry_all_three():
    parsed = parse(
        "I also have professional Harness experience. I have never used Jenkins. "
        "Emphasise AKS and keep it to 2 pages."
    )
    assert parsed.career_statements == ["I also have professional Harness experience."]
    assert [d.kind for d in parsed.denials] == [NEVER_USED]
    assert parsed.denials[0].terms == ["jenkins"]
    assert parsed.preferences == ["Emphasise AKS and keep it to 2 pages."]
    # And the positive prose that gets persisted contains no denial text.
    assert "never" not in parsed.career_text().lower()


def test_a_denial_naming_several_technologies_covers_all_of_them():
    parsed = parse("I have never used Harness or Jenkins")
    assert parsed.denied_terms() == {"harness": NEVER_USED, "jenkins": NEVER_USED}


def test_denial_terms_are_canonicalised():
    """Aliases from both vocabularies, or "never used AKS" and "never used
    Azure Kubernetes Service" would deny two different things."""
    assert technology_terms("I never used Azure Kubernetes Service") == ["aks"]
    assert technology_terms("I have no experience with Google Cloud Platform") == ["gcp"]
    assert technology_terms("I have never used k8s") == ["kubernetes"]


def test_a_longer_technology_name_does_not_deny_the_names_inside_it():
    """The over-denial trap.

    "Azure Kubernetes Service" contains "Azure" and "Kubernetes" as whole
    words. Denying AKS must not erase two of the strongest things in the
    career — that failure is worse than the one being fixed, because it
    deletes real experience instead of adding false experience.
    """
    assert technology_terms("I have never used Azure Kubernetes Service") == ["aks"]
    assert technology_terms("I have no experience with Google Kubernetes Engine") == [
        "gke"
    ]
    assert technology_terms("I have never used Azure Key Vault") == ["key vault"]


def test_two_separately_named_technologies_are_both_denied():
    """Consuming the matched span must not swallow a genuinely separate name."""
    assert technology_terms("I have never used Harness or Jenkins") == [
        "harness",
        "jenkins",
    ]
    assert technology_terms("I never used Azure Kubernetes Service or Jenkins") == [
        "aks",
        "jenkins",
    ]


def test_a_denial_of_an_unknown_technology_is_still_a_denial():
    """It blocks nothing specific — the name is not in the vocabulary — but it
    must still be kept out of positive career prose, which is the half that
    causes false confirmation."""
    parsed = parse("I have never used Wibbletron")
    assert parsed.career_statements == []
    assert parsed.denials[0].terms == []


def test_an_empty_instruction_produces_no_denials():
    assert parse(None).denials == []
    assert parse("   ").denials == []


# --------------------------------------------------------------------------
# 2. The named cases from the review
# --------------------------------------------------------------------------


def _confirmed_after(statement: str, document_text: str = "") -> object:
    """Assemble confirmed experience the way the live path does: the denial is
    extracted from the instruction, the positive prose (if any) is stored as a
    career source."""
    parsed = parse(statement)
    documents = []
    if parsed.career_text():
        documents.append(
            {
                "authority": AUTHORITY_USER_STATEMENT,
                "title": "Stated by you",
                "text": parsed.career_text(),
            }
        )
    if document_text:
        documents.append(
            {
                "authority": AUTHORITY_MASTER_RESUME,
                "title": "Master resume",
                "text": document_text,
            }
        )
    denials = [
        Denied(term=t, kind=k, statement=statement)
        for t, k in parsed.denied_terms().items()
    ]
    return assemble_confirmed(_profile(), documents, denials=denials)


def test_never_used_harness_does_not_confirm_harness():
    confirmed = _confirmed_after("I have never used Harness")
    assert confirmed.is_confirmed("harness") is False
    assert "harness" not in confirmed.terms


def test_never_worked_with_jenkins_does_not_confirm_jenkins():
    confirmed = _confirmed_after("I never worked with Jenkins")
    assert confirmed.is_confirmed("jenkins") is False
    assert "jenkins" not in confirmed.terms


def test_no_professional_gcp_experience_is_not_professional_experience():
    confirmed = _confirmed_after("I have no professional GCP experience")
    assert confirmed.is_confirmed("gcp") is False
    assert confirmed.denial_kind("gcp") == NOT_PROFESSIONAL


def test_only_studied_gcp_is_not_professional_experience():
    confirmed = _confirmed_after("I only studied GCP")
    assert confirmed.is_confirmed("gcp") is False
    assert confirmed.denial_kind("gcp") == STUDIED_ONLY


def test_the_original_defect_sentence():
    """Verbatim from the review. Both technologies, one sentence."""
    confirmed = _confirmed_after("I have never used Harness or Jenkins")
    assert confirmed.is_confirmed("harness") is False
    assert confirmed.is_confirmed("jenkins") is False
    assert {"harness", "jenkins"}.isdisjoint(confirmed.terms)


def test_a_document_mention_plus_a_denial_means_the_denial_wins():
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Azure DevOps pipelines with Harness and Terraform.",
    )
    assert confirmed.is_confirmed("harness") is False
    # Terraform came from the same document and is untouched — a denial is
    # surgical, not a reason to distrust the whole source.
    assert confirmed.is_confirmed("terraform") is True


def test_the_document_that_claimed_it_is_still_recorded():
    """`sources` must survive the denial. Erasing it would make the conflict
    invisible and the denial look uncontested when it is not."""
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Azure DevOps pipelines with Harness.",
    )
    assert confirmed.sources["harness"] == ["master_resume:Master resume"]
    assert confirmed.contradicted() == ["harness"]


def test_the_contradiction_is_recorded_as_a_source_conflict():
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Azure DevOps pipelines with Harness.",
    )
    conflicts = denial_conflicts(confirmed)
    assert [c.kind for c in conflicts] == [CONFLICT_EXPERIENCE_DENIED]
    assert conflicts[0].subject == "harness"
    sources = {v["source"] for v in conflicts[0].values}
    assert "master_resume:Master resume" in sources
    assert any("denied by you" in s for s in sources)


def test_no_conflict_when_nothing_claimed_it():
    """A denial of something no source ever asserted is not a disagreement. It
    would be noise in the conflicts list and would train the user to ignore it."""
    confirmed = _confirmed_after("I have never used Harness")
    assert denial_conflicts(confirmed) == []


# --------------------------------------------------------------------------
# 3. Central enforcement — no reader can bypass the boundary
# --------------------------------------------------------------------------


def test_the_denied_term_is_absent_from_the_raw_term_set():
    """The structural half of the fix.

    `terms` is read directly by the prompt's truth set, the document-scanning
    vocabulary and the JD scanner. A boundary living only inside
    `is_confirmed()` would be bypassed by every one of them.
    """
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Harness pipelines.",
    )
    assert "harness" not in confirmed.terms
    assert confirmed.unconfirmed(["harness"]) == ["harness"]


def test_the_jd_scanner_reports_a_denied_technology_as_unsupported():
    confirmed = _confirmed_after(
        "I have never used Jenkins",
        document_text="Ran Jenkins jobs nightly.",
    )
    supported, unsupported = scan_jd_technologies(
        "You will maintain Jenkins pipelines.", confirmed
    )
    assert "jenkins" in unsupported
    assert "jenkins" not in supported


def test_the_prompt_truth_set_excludes_denied_technologies():
    """What the model is allowed to treat as true must not contain it, and the
    model is additionally told outright — a model that simply never sees
    Harness may still reach for it off the JD."""
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Harness pipelines and Terraform.",
    )
    context = _career_context(_profile(), confirmed, [])
    truth_line = next(
        line for line in context.splitlines() if line.startswith("technologies")
    )
    assert "harness" not in truth_line
    assert "terraform" in truth_line
    assert "EXPLICITLY DENIED BY THE USER" in context
    assert "harness" in context


def test_denial_conflicts_do_not_pollute_the_disputed_line():
    """The DISPUTED line means "sources disagree, omit the fact". A denial is
    already decided, so mixing the two would misdescribe both."""
    confirmed = _confirmed_after(
        "I have never used Harness", document_text="Ran Harness pipelines."
    )
    context = _career_context(_profile(), confirmed, denial_conflicts(confirmed))
    assert "DISPUTED" not in context
    assert "EXPLICITLY DENIED BY THE USER" in context


def test_assemble_confirmed_without_denials_is_unchanged():
    """Existing callers keep their behaviour; the parameter is additive."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "m",
            "text": "Terraform and Harness.",
        }
    ]
    confirmed = assemble_confirmed(_profile(), documents, denials=NO_DENIALS)
    assert confirmed.is_confirmed("harness") is True
    assert confirmed.denied == {}


# --------------------------------------------------------------------------
# 4. Adversarial phrasing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "I have never used Harness",
        "I have not used Harness",
        "I haven't worked with Harness",
        "I have no professional experience with Harness",
        "I only studied Harness",
        "I only have personal experience with Harness",
        "My Harness experience is lab only",
        "I have never worked with Harness",
        "I hadn't used Harness at any point",
        "I do not have any Harness experience",
        "I have no hands-on experience with Harness",
        "I have no prior experience with Harness",
        "I never really used Harness",
        "I have zero exposure to Harness",
        "I have no exposure to Harness",
    ],
)
def test_adversarial_negation_phrasing_all_block_confirmation(sentence):
    confirmed = _confirmed_after(
        sentence, document_text="Ran Azure DevOps pipelines with Harness."
    )
    assert confirmed.is_confirmed("harness") is False, sentence
    assert confirmed.denial_kind("harness") is not None, sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "I have used Harness in production",
        "I have professional Harness experience",
        "I ran Harness pipelines for two years",
        "I built our Harness deployment templates",
    ],
)
def test_positive_phrasing_is_not_mistaken_for_a_denial(sentence):
    """The inverse guard. Over-eager negation would erase real experience,
    which is the one failure worse than the one being fixed."""
    assert classify_negation(sentence) is None
    assert parse(sentence).career_statements == [sentence]


# --------------------------------------------------------------------------
# 5. Durability — a denial survives re-ingestion and outlives the request
# --------------------------------------------------------------------------


async def test_a_denial_persists_and_reloads(db):
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="never")])
    assert [d.term for d in await load_denials()] == ["harness"]


async def test_restating_a_denial_does_not_duplicate_it(db):
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="a")])
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="b")])
    denials = await load_denials()
    assert len(denials) == 1
    assert denials[0].statement == "b"


async def test_reingesting_a_document_leaves_the_denial_authoritative(db):
    """The re-ingestion case from the review.

    A document is not the user asserting anything. However many career sources
    mention Harness, and however many times they are re-uploaded, only the user
    can reverse their own denial.
    """
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="never")])

    from council.documents.extract import Extracted

    for i in range(3):
        await store_document(
            filename=f"resume-{i}.txt",
            title=f"Master resume v{i}",
            authority=AUTHORITY_MASTER_RESUME,
            # Distinct text each time so content-hash dedup does not hide the
            # effect being tested.
            extracted=Extracted(
                text=f"Ran Harness pipelines and Terraform in year {2020 + i}.",
                char_count=60,
                truncated=False,
                detected_kind="text",
            ),
        )
        confirmed = await confirmed_experience()
        assert confirmed.is_confirmed("harness") is False
        assert confirmed.is_confirmed("terraform") is True

    # Still one denial, still active, never weakened by the repetition.
    ledger = await list_denials()
    assert len(ledger) == 1
    assert ledger[0]["active"] is True


# --------------------------------------------------------------------------
# 6. A positive statement after an earlier denial
#
# Decided rule: the later explicit user statement SUPERSEDES the denial. The
# user is the primary source on their own career and may have learned the
# technology since. It is never silent — the row survives with both statements
# and both timestamps.
# --------------------------------------------------------------------------


async def test_a_later_positive_statement_supersedes_an_earlier_denial(db):
    await apply_instruction_facts(parse("I have never used Harness"))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    await apply_instruction_facts(parse("I have professional Harness experience now"))
    assert await load_denials() == []


async def test_the_reversal_is_recorded_not_erased(db):
    """"Do not silently resolve the contradiction." The row stays, carrying
    the denial, the statement that overrode it and when that happened."""
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have used Harness in production since then"))

    ledger = await list_denials()
    assert len(ledger) == 1
    row = ledger[0]
    assert row["term"] == "harness"
    assert row["active"] is False
    assert row["statement"] == "I have never used Harness"
    assert "used Harness in production" in row["superseded_by"]
    assert row["superseded_at"] is not None


async def test_a_denial_and_a_claim_in_the_same_request_resolve_to_denied(db):
    """Within one request an explicit denial is a hard boundary, not a race.

    Supersession is for a LATER statement — the user changing their mind over
    time. A single instruction that says both things is contradicting itself,
    and the safe reading of a self-contradiction is the negative one.
    """
    await apply_instruction_facts(
        parse("I have professional Harness experience. I have never used Harness.")
    )
    denials = await load_denials()
    assert [d.term for d in denials] == ["harness"]
    assert (await confirmed_experience()).is_confirmed("harness") is False


async def test_a_positive_claim_about_something_else_reverses_nothing(db):
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have professional Jenkins experience"))
    assert [d.term for d in await load_denials()] == ["harness"]


async def test_a_denial_can_be_restated_after_being_superseded(db):
    """The user changing their mind twice is still the user."""
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have used Harness professionally"))
    await apply_instruction_facts(parse("Actually I have never used Harness"))
    denials = await load_denials()
    assert [d.term for d in denials] == ["harness"]
    assert (await confirmed_experience()).is_confirmed("harness") is False


async def test_superseding_reports_what_changed(db):
    await apply_instruction_facts(parse("I have never used Harness"))
    facts = await apply_instruction_facts(parse("I have used Harness professionally"))
    assert facts["superseded"] == ["harness"]
    assert facts["denied"] == []


# --------------------------------------------------------------------------
# 7. "Only studied" is stored, but never quietly promoted
# --------------------------------------------------------------------------


async def test_studied_only_is_stored_as_structured_career_information(db):
    await apply_instruction_facts(parse("I only studied GCP"))
    ledger = await list_denials()
    assert ledger[0]["term"] == "gcp"
    assert ledger[0]["kind"] == STUDIED_ONLY
    assert ledger[0]["statement"] == "I only studied GCP"


async def test_studied_only_never_becomes_confirmed_experience(db):
    """Contract §4: studied-only must not turn into ATS keyword stuffing. The
    mechanism is simply that it is not in the confirmed set, so nothing that
    writes from confirmed experience can reach it."""
    await apply_instruction_facts(parse("I only studied GCP"))
    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("gcp") is False
    assert "gcp" not in confirmed.terms
    context = _career_context(_profile(), confirmed, [])
    truth_line = next(
        line for line in context.splitlines() if line.startswith("technologies")
    )
    assert "gcp" not in truth_line


def test_studied_only_is_distinguishable_from_never_used():
    """So a later Familiarity/Exposure section can show one and not the other
    without having to re-ask the user."""
    studied = _confirmed_after("I only studied GCP")
    never = _confirmed_after("I have never used GCP")
    assert studied.denial_kind("gcp") == STUDIED_ONLY
    assert never.denial_kind("gcp") == NEVER_USED


# --------------------------------------------------------------------------
# 8. This-request denials bite on this request
# --------------------------------------------------------------------------


def test_a_denial_in_this_request_applies_to_this_run():
    """A denial that only takes effect next time is a defect the user
    experiences once and stops trusting the system over."""
    merged = _merge_denials([], parse("I have never used Harness"))
    assert [d.term for d in merged] == ["harness"]


def test_stored_and_request_denials_merge_with_the_request_winning():
    stored = [Denied(term="gcp", kind=NEVER_USED, statement="old")]
    merged = _merge_denials(stored, parse("I only studied GCP"))
    assert [(d.term, d.kind) for d in merged] == [("gcp", STUDIED_ONLY)]


def test_merging_with_no_denials_anywhere_is_empty():
    assert _merge_denials(None, parse("Emphasise AKS.")) == []


# --------------------------------------------------------------------------
# 9. End to end through the API
# --------------------------------------------------------------------------


async def test_a_denial_in_the_instruction_never_becomes_a_career_source(db, monkeypatch):
    """The live defect, end to end.

    Before the fix this request stored "I have never used Harness or Jenkins"
    as a user_statement career source and confirmed both technologies off it.
    """
    client = _client(monkeypatch)
    try:
        _upload_master(client)
        r = client.post(
            "/artifacts/resume",
            json={
                "jd_text": GCP_JD,
                "instruction": "I have never used Harness or Jenkins. Keep it to 2 pages.",
                "name": "A. Candidate",
            },
        )
        assert r.status_code == 200, r.text
    finally:
        client.__exit__(None, None, None)

    async with session_scope() as s:
        statements = (
            await s.execute(
                select(DocumentRow).where(
                    DocumentRow.authority == AUTHORITY_USER_STATEMENT
                )
            )
        ).scalars().all()
    assert statements == [], "a denial was stored as a positive career source"

    async with session_scope() as s:
        denied = (await s.execute(select(CareerDenialRow))).scalars().all()
    assert {d.term for d in denied} == {"harness", "jenkins"}

    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("harness") is False
    assert confirmed.is_confirmed("jenkins") is False


async def test_the_api_reports_what_the_request_changed(db, monkeypatch):
    client = _client(monkeypatch)
    try:
        _upload_master(client)
        r = client.post(
            "/artifacts/resume",
            json={
                "jd_text": GCP_JD,
                "instruction": "I have never used Harness.",
            },
        )
        body = r.json()
    finally:
        client.__exit__(None, None, None)
    assert body["career_facts"]["denied"] == ["harness"]
    assert body["instruction"]["denials"][0]["kind"] == NEVER_USED


async def test_the_career_profile_endpoint_shows_denials(db, monkeypatch):
    await record_denials([Denied(term="gcp", kind=STUDIED_ONLY, statement="only studied")])
    client = TestClient(app)
    with client:
        body = client.get("/career-profile").json()
    assert body["denied"]["gcp"]["kind"] == STUDIED_ONLY
    assert "gcp" not in body["confirmed"]


async def test_jd_analysis_separates_denied_gaps_from_unknown_ones(db, monkeypatch):
    """A gap the user already ruled out should not be presented the same way as
    one they have never been asked about — that is what makes the later
    confirmation feature able to skip it."""
    await record_denials([Denied(term="gcp", kind=NEVER_USED, statement="never used")])
    client = TestClient(app)
    with client:
        body = client.post("/career-profile/analyze-jd", json={"text": GCP_JD}).json()
    assert "gcp" in body["technologies_unsupported"]
    assert body["technologies_denied"] == ["gcp"]
