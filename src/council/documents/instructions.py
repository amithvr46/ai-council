"""Splitting one natural-language instruction into its distinct inputs.

A single sentence from the user can carry three things with completely
different authority and completely different lifetimes:

    "I also have professional Harness experience. I have never used Jenkins.
     Emphasise AKS and keep it to 2 pages."

  - "I also have professional Harness experience"  -> a DURABLE POSITIVE career
    fact. The user is the primary source on their own career, so this
    establishes truth and is eligible to persist with user_statement
    provenance.
  - "I have never used Jenkins"                    -> a DURABLE NEGATIVE career
    fact. Same authority, opposite sign. It must never be stored as positive
    prose, because a positive career source is scanned for technology names
    and every name it contains becomes confirmed experience.
  - "Emphasise AKS", "keep it to 2 pages"          -> a REQUEST-ONLY preference.
    True of this run and nothing else.

Conflating them fails in every direction, and every failure is bad:

  - a durable fact treated as request-only means re-stating it for every future
    resume, which is exactly the bookkeeping the product exists to remove
  - a preference treated as a career fact silently corrupts the Career
    Experience Profile. "Target SRE roles" is not a career fact. Neither is
    "keep it to 2 pages".
  - a DENIAL treated as a positive career fact is the worst of the three: it
    turns "I have never used Harness" into confirmed Harness experience and
    puts it on a submitted resume. That is the defect this module's negation
    handling exists to make impossible.

Deterministic, no model call. The positive/preference split is by grammatical
person, which is a reliable signal here: people state their experience in the
first person and give instructions in the imperative. The negation split is by
an explicit list of high-value negative forms — deliberately not general NLP,
because a negation detector that is clever is a negation detector that fails
in ways nobody can predict, and this boundary has to be predictable.
"""

import re
from dataclasses import dataclass, field

from council.documents.profile import denial_vocabulary, normalise_denial_term

# First-person claims about what the user has actually done.
_EXPERIENCE_VERB = (
    r"have|had|has|worked|work|used|use|ran|run|built|build|supported|support|"
    r"managed|manage|configured|configure|deployed|deploy|maintained|maintain|"
    r"administered|administer|operated|operate|wrote|write|automated|automate|"
    r"troubleshot|troubleshoot|implemented|implement|owned|own"
)
_FIRST_PERSON_CLAIM = re.compile(
    rf"\b(?:i|i've|i have|my)\b[^.]{{0,40}}?\b(?:{_EXPERIENCE_VERB})\b"
    rf"|\bi(?:'ve)?\s+(?:also\s+)?(?:{_EXPERIENCE_VERB})\b",
    re.I,
)

# First person, but asking for something rather than reporting experience.
# "I want you to emphasise X" is a preference wearing a first-person costume.
_REQUEST_VERB = re.compile(
    r"\b(?:want|would like|need you|prefer|expect|wish|"
    r"am targeting|'m targeting|am applying|'m applying)\b",
    re.I,
)

# Sentences that TALK ABOUT a claim rather than MAKE one.
#
# "Can you make my resume sound like I used GCP?" contains the substring
# "I used GCP" and matches the first-person experience pattern perfectly. Read
# as a career statement it does two unacceptable things: it becomes a career
# source that confirms GCP off nothing, and — worse — it supersedes an existing
# denial of GCP. The user asked how the document should READ. They did not
# assert a fact about their career, and one of these is a request for
# fabrication.
#
# The distinguishing feature is not the claim, it is the frame around it. These
# patterns detect the frame.
_FRAMING = re.compile(
    r"\?\s*$"  # a question is never an assertion
    r"|\bsounds?\s+like\b"
    r"|\bas\s+if\b"
    r"|\bpretend(?:ing|s)?\b"
    r"|\bimply\b|\bimplies\b|\bsuggest\s+that\b"
    r"|\b(?:can|could|would|will)\s+you\b"
    r"|\b(?:say|says|write|state|put|claim|word|phrase|frame|spin|make)\s+"
    r"(?:it\s+|that\s+)?(?:i|we|my|me)\b"
    r"|\bplease\s+(?:add|include|say|write|put|make|mention|highlight|"
    r"emphasi[sz]e|list)\b"
    r"|\badd\b[^.]{0,40}?\bto\s+my\s+(?:resume|cv)\b",
    re.I,
)

# The user is UNSURE whether the thing happened.
#
# "I think I might have used GCP once" is not an assertion of professional
# experience. It is someone trying to remember. Treating it as a career fact
# means a hazy recollection becomes a line on a submitted resume, which is the
# same failure as fabrication with a politer origin.
#
# Only genuine epistemic uncertainty belongs here. Words about SCALE or
# DURATION were removed deliberately: "I used Ansible briefly" and "I used it a
# bit" are certain assertions of limited experience, and blocking them would
# force the user to restate legitimate experience — the exact friction the
# Career Experience Profile exists to remove.
_UNCERTAIN = re.compile(
    r"\b(?:i\s+think|i\s+believe|i\s+guess|i\s+suppose|maybe|perhaps|possibly|"
    r"probably|might\s+have|may\s+have|could\s+have|not\s+sure|unsure|"
    r"can'?t\s+remember|don'?t\s+remember|sort\s+of|kind\s+of|"
    r"i\s+may|i\s+might|vaguely|at\s+some\s+point)\b",
    re.I,
)

# Statements about somebody else. "My team used GCP" is true, relevant and
# not a claim that the USER used GCP. A resume is a first-person document, so
# a team's experience becoming the user's is a fabricated career fact even
# though nothing in the sentence is false.
#
# The third party has to be the SUBJECT of a verb, not merely mentioned.
# "I used GCP professionally at my last company" is a first-person claim that
# happens to name an employer, and excluding it would break the product rule
# for one of its most natural phrasings.
_THIRD_PARTY = re.compile(
    r"\b(?:my|our|the|their|his|her)\s+"
    r"(?:(?:current|previous|last|old|former|new)\s+)?"
    r"(?:team|teams|client|clients|company|companies|employer|employers|"
    r"colleague|colleagues|coworkers?|manager|group|org|orgs|organisation|"
    r"organization|department|customer|customers|vendor|vendors|partner|"
    r"partners|contractor|contractors)\s+"
    r"(?:\w+\s+){0,2}?"
    r"(?:used|use|uses|using|ran|run|runs|built|build|builds|deployed|deploy|"
    r"managed|manage|manages|worked|work|works|has|have|had|is|was|were|are|"
    r"adopted|adopt|migrated|standardi[sz]ed)\b",
    re.I,
)

# Words that carry negation. Counted, not just matched — see below.
_NEGATOR = re.compile(
    r"\bnever\b|\bnot\b|n't\b|\bno\b|\bnone\b|\bnothing\b|\bzero\b|\bnil\b|"
    r"\blacks?\b|\blacked\b|\bwithout\b|\bneither\b|\bnor\b",
    re.I,
)

# Contrastive joins, for splitting one sentence into clauses that disagree.
#
# "I have never used Harness but I have used Jenkins extensively" is ONE
# sentence carrying a denial and a claim. Classified whole it is a denial, and
# `technology_terms` would then deny every technology it names — erasing the
# Jenkins experience the same sentence asserts. Splitting on the contrast is
# deterministic tokenisation, not language understanding.
_CONTRAST = re.compile(
    r",?\s+\b(?:but|however|although|though|whereas|while|yet|except\s+that)\b\s+",
    re.I,
)

# ------------------------------------------------------------------ negation

# Three kinds of negative statement. All three block career confirmation; they
# differ in what they additionally record, so the audit trail can answer "why
# is this not confirmed?" with the user's actual words rather than a shrug.
NEVER_USED = "never_used"  # no experience of any kind
NOT_PROFESSIONAL = "not_professional"  # some exposure, but not professionally
STUDIED_ONLY = "studied_only"  # studied / lab / personal only

DENIAL_KINDS = (NEVER_USED, NOT_PROFESSIONAL, STUDIED_ONLY)

# Order matters: the most specific reading is tried first. "I only studied GCP"
# and "I have no professional GCP experience" are both denials of professional
# experience, but they are not the same statement and must not be recorded as
# though they were.
_NON_PROFESSIONAL_CONTEXT = (
    r"personal|lab|home lab|homelab|hobby|side project|side projects|"
    r"self[- ]taught|academic|coursework|course|training|tutorial|"
    r"certification|study|studies|sandbox|demo|toy"
)

_STUDIED_ONLY_PATTERNS = (
    # "only studied", "just read about", "only did a course on"
    r"\b(?:only|just|merely)\s+(?:ever\s+)?"
    r"(?:studied|study|studying|learned|learnt|read about|took a course|"
    r"did a course|trained on|practised|practiced|played with|experimented with)\b",
    # "studied it only", "learned X only"
    r"\b(?:studied|learned|learnt|trained)\b[^.]{0,40}?\bonly\b",
    # "only personal experience", "only in a home lab", "lab use only"
    rf"\b(?:only|just)\b[^.]{{0,30}}?\b(?:{_NON_PROFESSIONAL_CONTEXT})\b",
    rf"\b(?:{_NON_PROFESSIONAL_CONTEXT})\s+(?:use|usage|experience|projects?|work)"
    rf"\s+only\b",
    rf"\b(?:{_NON_PROFESSIONAL_CONTEXT})\s+only\b",
)

# Verbs that mean "made use of the technology". Shared by the negative
# patterns so a denial form cannot drift out of sync with its negation.
_USE_VERB = (
    r"used|use|using|worked|work|working|touched|ran|run|running|built|build|"
    r"deployed|deploy|administered|administer|managed|manage|configured|"
    r"configure|supported|support|maintained|maintain|operated|operate|"
    r"implemented|implement|automated|automate|written|wrote"
)

# `not only ... but` is an INTENSIFIER, not a negation: "I have not only used
# Harness, I built our templates" is one of the strongest possible claims. Every
# negation form below refuses to fire when "only" directly follows the negator.
_NOT_ONLY = r"(?!only\b)"

_NOT_PROFESSIONAL_PATTERNS = (
    # "no professional GCP experience", "no production experience with GCP"
    r"\bno\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial|paid|work|"
    r"on[- ]the[- ]job|enterprise)\s+(?:\w+[- ]){0,3}?experience\b",
    r"\bno\s+(?:\w+[- ]){0,3}?experience\s+(?:\w+[- ]){0,3}?"
    r"(?:professionally|in production|at work|commercially)\b",
    # "never used it professionally", "have not used X in production".
    #
    # This pattern used to be `(never|not|n't) ... 60 chars ... professionally`
    # with no constraint on what sat in between, which made it fire on
    # sentences that assert experience:
    #
    #   "I have never had an outage while running Jenkins in production."
    #   "I do not just write Terraform, I run it in production."
    #
    # Both were classified as denials, erasing real experience. The negator now
    # has to directly govern a usage verb, and the professional marker has to
    # follow that verb within a short window.
    rf"(?:\bnever\b|\bnot\b|n't)\s+{_NOT_ONLY}(?:\w+\s+){{0,2}}?(?:{_USE_VERB})\b"
    rf"[^.]{{0,40}}?\b(?:professionally|in production|at work|commercially|"
    rf"on the job|in a professional|in anger)\b",
    r"\bnot\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial)\s+experience\b",
)

_NEVER_USED_PATTERNS = (
    # "never used", "never worked with", "never touched".
    #
    # Bare "had" and "been" were removed from this alternation. "I have never
    # had an outage", "I have never been on call" are not technology denials,
    # and with "had" present the first of those denied every technology the
    # sentence named. "never had experience" is covered explicitly below.
    rf"\bnever\s+(?:really\s+|actually\s+|ever\s+)?{_NOT_ONLY}(?:{_USE_VERB})\b",
    r"\bnever\s+(?:\w+\s+){0,2}?had\s+(?:any\s+)?(?:\w+[- ]){0,3}?"
    r"(?:experience|exposure|involvement)\b",
    # "have not used", "haven't worked with", "hasn't touched"
    rf"\b(?:have|has|had)\s+(?:not|never)\s+{_NOT_ONLY}(?:\w+\s+){{0,2}}?"
    rf"(?:{_USE_VERB})\b",
    rf"\b(?:haven't|hasn't|hadn't|didn't|don't|doesn't)\s+{_NOT_ONLY}"
    rf"(?:\w+\s+){{0,2}}?(?:{_USE_VERB})\b",
    # "no experience with", "no Harness experience", "zero exposure to".
    #
    # Widened to allow the technology name between "no" and "experience".
    # "I have no Harness experience" is among the most natural ways to say this
    # and previously escaped every pattern in the file.
    r"\bno\s+(?:\w+[- ]){0,3}?experience\b",
    r"\b(?:zero|nil)\s+(?:\w+[- ]){0,3}?(?:experience|exposure)\b",
    r"\bno\s+(?:exposure|background)\s+(?:to|in|with)\b",
    r"\b(?:don't|do not|doesn't|does not)\s+have\s+(?:any\s+)?"
    r"(?:\w+[- ]){0,3}?experience\b",
    r"\black(?:s|ed)?\s+(?:\w+[- ]){0,3}?experience\b",
    r"\bnot\s+(?:something|a technology|a tool)\s+i(?:'ve)?\s+(?:have\s+)?"
    r"(?:ever\s+)?used\b",
)

_STUDIED_ONLY = re.compile("|".join(_STUDIED_ONLY_PATTERNS), re.I)
_NOT_PROFESSIONAL = re.compile("|".join(_NOT_PROFESSIONAL_PATTERNS), re.I)
_NEVER_USED = re.compile("|".join(_NEVER_USED_PATTERNS), re.I)


def classify_negation(sentence: str) -> str | None:
    """Which kind of denial this sentence is, or None if it is not a denial.

    Checked most-specific first. A sentence that matches nothing here is not
    treated as negative, which is the safe default in this direction: a missed
    denial leaves a technology unconfirmed-or-confirmed exactly as it is today,
    whereas a false denial would erase real experience the user has.

    TWO OR MORE NEGATORS MEANS "DO NOT GUESS". A double negative usually
    asserts the opposite of what the individual words suggest:

        "There is not a week where I do not use Terraform at work."

    Every pattern below reads that as a denial of Terraform, and no amount of
    tightening fixes it, because the sentence really does contain a negator
    directly governing a usage verb — twice. Rather than pretend to resolve it,
    the classifier declines. Declining costs a denial the user may have to
    restate; guessing deletes experience they have.

    The cost is bounded and known: "I never used Harness and I don't have
    Jenkins experience" is two denials in one clause and is also declined. Both
    are recoverable by saying them as separate sentences; a wrongly erased
    technology is not recoverable by anything the user can see.
    """
    if len(_NEGATOR.findall(sentence)) > 1:
        return None
    if _STUDIED_ONLY.search(sentence):
        return STUDIED_ONLY
    if _NOT_PROFESSIONAL.search(sentence):
        return NOT_PROFESSIONAL
    if _NEVER_USED.search(sentence):
        return NEVER_USED
    return None


def technology_terms(sentence: str) -> list[str]:
    """Technology names this sentence mentions, canonicalised.

    Technologies only — not domains. A denial names a tool ("never used
    Harness"); denying a whole domain ("no automation experience") is a much
    broader claim than the deterministic forms here can safely interpret, and
    reading one narrowly would be worse than not reading it at all. The
    sentence is still kept out of positive career prose either way, which is
    the part that matters for correctness.
    LONGEST MATCH WINS, and the matched span is consumed. Without that,
    "I never used Azure Kubernetes Service" denies Azure and Kubernetes as well
    as AKS, because both names sit as whole words inside the longer one —
    turning one narrow denial into the erasure of the user's two strongest
    technologies. Over-denial is worse than the defect being fixed here: the
    original bug adds experience the user does not have, this one would delete
    experience they do.

    Positive assembly deliberately does NOT work this way. There, matching
    "azure" and "kubernetes" inside "Azure Kubernetes Service" is right,
    because all three are genuinely evidenced by that phrase. Denial is the
    asymmetric case: the user denied one specific thing, and only that thing.
    """
    found: list[str] = []
    remaining = sentence
    for term in denial_vocabulary():  # longest first
        pattern = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
        if not re.search(pattern, remaining, re.I):
            continue
        # Blank the span so a shorter name nested inside it cannot match too.
        remaining = re.sub(
            pattern, lambda m: " " * len(m.group()), remaining, flags=re.I
        )
        canonical = normalise_denial_term(term)
        if canonical and canonical not in found:
            found.append(canonical)
    return sorted(found)


@dataclass
class Denial:
    """One negative career statement and the technologies it covers.

    `terms` may be empty: a denial whose subject is not in the technology
    vocabulary still counts as a denial for classification purposes (it must
    not become positive career prose), it simply blocks nothing specific. That
    is recorded honestly rather than dropped.
    """

    kind: str
    terms: list[str] = field(default_factory=list)
    statement: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "terms": self.terms, "statement": self.statement}


@dataclass
class Instruction:
    """The parsed request. `raw` is kept so nothing is silently discarded."""

    raw: str = ""
    career_statements: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    denials: list[Denial] = field(default_factory=list)

    @property
    def has_career_statements(self) -> bool:
        return bool(self.career_statements)

    @property
    def has_denials(self) -> bool:
        return bool(self.denials)

    def preference_text(self) -> str:
        return " ".join(self.preferences).strip()

    def career_text(self) -> str:
        """POSITIVE career prose only.

        This is what gets persisted as a user_statement career source, and a
        career source is scanned for technology names that then become
        confirmed experience. A denial reaching this string is the negation
        defect itself, so denials are structurally unable to appear here.
        """
        return " ".join(self.career_statements).strip()

    def denial_text(self) -> str:
        return " ".join(d.statement for d in self.denials).strip()

    def denied_terms(self) -> dict[str, str]:
        """term -> denial kind, for every technology this request denies.

        First denial of a term wins within a single request, so a request that
        says both "only studied GCP" and "never used GCP" records the more
        specific reading it stated first rather than flip-flopping.
        """
        denied: dict[str, str] = {}
        for denial in self.denials:
            for term in denial.terms:
                denied.setdefault(term, denial.kind)
        return denied

    def claimed_terms(self) -> list[str]:
        """Technologies named in career statements explicit enough to REVERSE a denial.

        This is a deliberately higher bar than `career_statements`. Superseding
        is the only operation in the system that can undo a hard truth boundary,
        so the statement that does it has to be an unambiguous assertion by the
        user about their own career.

        Framed, uncertain and third-party sentences are excluded by `parse`
        before they can become career statements at all, so none of them reach
        here. The guard below is therefore redundant on every current path, and
        is kept deliberately: this is the function that can undo a hard truth
        boundary, and it should not depend on an upstream filter staying
        correct forever. If the two ever disagree, the stricter one wins.

        A term denied in this same request is excluded by the caller: an
        explicit denial is a hard boundary within the request that states it.
        """
        found: list[str] = []
        for sentence in self.career_statements:
            if (
                _FRAMING.search(sentence)
                or _UNCERTAIN.search(sentence)
                or _THIRD_PARTY.search(sentence)
            ):
                continue
            for term in technology_terms(sentence):
                if term not in found:
                    found.append(term)
        return sorted(found)

    def as_dict(self) -> dict:
        return {
            "career_statements": self.career_statements,
            "preferences": self.preferences,
            "denials": [d.as_dict() for d in self.denials],
        }


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text)
    return [p.strip() for p in parts if p and p.strip()]


def _clauses(sentence: str) -> list[str]:
    """One sentence split at contrastive joins.

    "I have never used Harness but I have used Jenkins extensively" is a single
    sentence making two opposite statements. Classified whole it reads as a
    denial, and the denial then covers every technology the sentence names —
    erasing the Jenkins experience the sentence explicitly asserts.
    """
    return [c.strip() for c in _CONTRAST.split(sentence) if c and c.strip()]


def _classify_clause(clause: str) -> str:
    """"denial" | "career" | "preference" for one clause.

    Negation is checked BEFORE the positive-claim test, and that order is the
    whole original fix. "I have never used Harness" matches the first-person
    experience pattern perfectly well — it is a first-person sentence containing
    "used" — so any ordering that tests for a positive claim first classifies a
    denial as a career statement, stores it as positive prose and confirms
    Harness.

    Framing, uncertainty and third-party attribution are checked before BOTH.
    Each describes a sentence that is not the user asserting their own
    professional experience:

      - FRAMING     "Make my resume sound like I used GCP"  — a request about
                    wording, and specifically a request to fabricate.
      - UNCERTAIN   "I think I might have used GCP once"    — trying to
                    remember, not asserting.
      - THIRD PARTY "My team used GCP"                      — someone else's
                    experience.

    All three match `_FIRST_PERSON_CLAIM` perfectly well, because all three are
    first-person sentences containing an experience verb. Without these guards
    each becomes positive career prose, gets stored as a user_statement career
    source, and the technology it names becomes CONFIRMED PROFESSIONAL
    EXPERIENCE eligible for a submitted resume — no denial needed anywhere.

    That is the same defect shape as the original negation bug: a sentence that
    merely CONTAINS a technology name is read as evidence the user has used it.
    """
    if _FRAMING.search(clause) or _UNCERTAIN.search(clause) or _THIRD_PARTY.search(clause):
        return "preference"
    if classify_negation(clause) is not None:
        return "denial"
    if _FIRST_PERSON_CLAIM.search(clause) and not _REQUEST_VERB.search(clause):
        return "career"
    return "preference"


def parse(instruction: str | None) -> Instruction:
    """Split an instruction into positive career facts, denials and preferences.

    Each sentence is split at contrastive joins and each clause classified on
    its own, so a sentence that both denies and claims resolves to one of each
    rather than letting whichever reading won apply to every technology named.

    Anything ambiguous that is not negative is treated as a preference,
    deliberately. A misfiled preference is forgotten after this run; a misfiled
    career fact becomes a permanent claim the user never made. The asymmetry
    decides the default.
    """
    if not instruction or not instruction.strip():
        return Instruction()

    career: list[str] = []
    preferences: list[str] = []
    denials: list[Denial] = []

    for sentence in _sentences(instruction):
        clauses = _clauses(sentence)
        kinds = [_classify_clause(c) for c in clauses]

        # Terms the sentence asserts POSITIVELY. A denial clause that names no
        # technology of its own borrows from the rest of the sentence, but must
        # never borrow something another clause just claimed.
        claimed_here: set[str] = set()
        for clause, kind in zip(clauses, kinds, strict=True):
            if kind == "career":
                claimed_here.update(technology_terms(clause))

        for clause, kind in zip(clauses, kinds, strict=True):
            if kind == "denial":
                terms = technology_terms(clause)
                if not terms:
                    # "I know about GCP but haven't used it professionally" —
                    # the denying clause says "it". The referent is in the
                    # sentence, so read it from there rather than recording a
                    # denial that blocks nothing.
                    terms = [
                        t for t in technology_terms(sentence) if t not in claimed_here
                    ]
                denials.append(
                    Denial(
                        kind=classify_negation(clause),
                        terms=sorted(terms),
                        statement=clause,
                    )
                )
            elif kind == "career":
                career.append(clause)
            else:
                preferences.append(clause)

    return Instruction(instruction.strip(), career, preferences, denials)
