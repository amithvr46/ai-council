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

_NOT_PROFESSIONAL_PATTERNS = (
    # "no professional GCP experience", "no production experience with GCP"
    r"\bno\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial|paid|work|"
    r"on[- ]the[- ]job|enterprise)\s+(?:\w+[- ]){0,3}?experience\b",
    r"\bno\s+(?:\w+[- ]){0,3}?experience\s+(?:\w+[- ]){0,3}?"
    r"(?:professionally|in production|at work|commercially)\b",
    # "never used it professionally", "have not used X in production"
    r"\b(?:never|not|n't)\b[^.]{0,60}?\b(?:professionally|in production|"
    r"at work|commercially|on the job)\b",
    r"\bnot\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial)\s+experience\b",
)

_NEVER_USED_PATTERNS = (
    # "never used", "never worked with", "never touched"
    r"\bnever\s+(?:really\s+|actually\s+|ever\s+)?"
    r"(?:used|use|worked with|work with|touched|ran|run|built|deployed|"
    r"administered|managed|configured|supported|maintained|operated|"
    r"had|have had|been)\b",
    # "have not used", "haven't worked with", "hasn't touched"
    r"\b(?:have|has|had)\s+(?:not|never)\s+(?:\w+\s+){0,2}?"
    r"(?:used|use|worked|work|touched|ran|run|built|deployed|administered|"
    r"managed|configured|supported|maintained|operated)\b",
    r"\b(?:haven't|hasn't|hadn't|didn't|don't|doesn't)\s+(?:\w+\s+){0,2}?"
    r"(?:used|use|worked|work|touched|ran|run|built|deployed|administered|"
    r"managed|configured|supported|maintained|operated)\b",
    # "no experience with", "no hands-on experience in", "zero exposure to"
    r"\bno\s+(?:hands[- ]on\s+|direct\s+|real\s+|prior\s+)?experience\b",
    r"\b(?:zero|nil)\s+(?:hands[- ]on\s+)?(?:experience|exposure)\b",
    r"\bno\s+(?:exposure|background)\s+(?:to|in|with)\b",
    r"\b(?:don't|do not|doesn't|does not)\s+have\s+(?:any\s+)?"
    r"(?:\w+[- ]){0,3}?experience\b",
    r"\bnot\s+(?:something|a technology)\s+i(?:'ve)?\s+(?:have\s+)?used\b",
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
    """
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
        """Technologies named in POSITIVE career statements.

        Used to supersede an earlier denial: the user is the primary source on
        their own career and is allowed to correct themselves. A term denied in
        this same request is excluded by the caller — an explicit denial is a
        hard boundary within the request that states it.
        """
        found: list[str] = []
        for sentence in self.career_statements:
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


def parse(instruction: str | None) -> Instruction:
    """Split an instruction into positive career facts, denials and preferences.

    Negation is checked BEFORE the positive-claim test, and that order is the
    whole fix. "I have never used Harness" matches the first-person experience
    pattern perfectly well — it is a first-person sentence containing "used" —
    so any ordering that tests for a positive claim first classifies a denial
    as a career statement, stores it as positive prose and confirms Harness.

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
        kind = classify_negation(sentence)
        if kind is not None:
            denials.append(
                Denial(kind=kind, terms=technology_terms(sentence), statement=sentence)
            )
        elif _FIRST_PERSON_CLAIM.search(sentence) and not _REQUEST_VERB.search(sentence):
            career.append(sentence)
        else:
            preferences.append(sentence)
    return Instruction(instruction.strip(), career, preferences, denials)
