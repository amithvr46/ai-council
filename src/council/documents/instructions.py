"""Splitting one natural-language instruction into its distinct inputs.

A single sentence from the user can carry two things with completely different
authority and completely different lifetimes:

    "I also have professional Harness experience. Emphasise AKS and keep it
     to 2 pages."

  - "I also have professional Harness experience"  -> a DURABLE career fact.
    The user is the primary source on their own career, so this establishes
    truth and is eligible to persist with user_statement provenance.
  - "Emphasise AKS", "keep it to 2 pages"          -> a REQUEST-ONLY preference.
    True of this run and nothing else.

Conflating them fails in both directions, and both failures are bad:

  - a durable fact treated as request-only means re-stating it for every future
    resume, which is exactly the bookkeeping the product exists to remove
  - a preference treated as a career fact silently corrupts the Career
    Experience Profile. "Target SRE roles" is not a career fact. Neither is
    "keep it to 2 pages".

Deterministic, no model call. The split is by grammatical person, which is a
reliable signal here: people state their experience in the first person and
give instructions in the imperative.
"""

import re
from dataclasses import dataclass, field

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


@dataclass
class Instruction:
    """The parsed request. `raw` is kept so nothing is silently discarded."""

    raw: str = ""
    career_statements: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)

    @property
    def has_career_statements(self) -> bool:
        return bool(self.career_statements)

    def preference_text(self) -> str:
        return " ".join(self.preferences).strip()

    def career_text(self) -> str:
        return " ".join(self.career_statements).strip()

    def as_dict(self) -> dict:
        return {
            "career_statements": self.career_statements,
            "preferences": self.preferences,
        }


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text)
    return [p.strip() for p in parts if p and p.strip()]


def parse(instruction: str | None) -> Instruction:
    """Split an instruction into durable career facts and request preferences.

    Anything ambiguous is treated as a preference, deliberately. A misfiled
    preference is forgotten after this run; a misfiled career fact becomes a
    permanent claim the user never made. The asymmetry decides the default.
    """
    if not instruction or not instruction.strip():
        return Instruction()

    career: list[str] = []
    preferences: list[str] = []
    for sentence in _sentences(instruction):
        if _FIRST_PERSON_CLAIM.search(sentence) and not _REQUEST_VERB.search(sentence):
            career.append(sentence)
        else:
            preferences.append(sentence)
    return Instruction(instruction.strip(), career, preferences)
