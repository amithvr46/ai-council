"""Outcome kinds — what the user wanted accomplished.

`mode` (quick | council | deep) records HOW a request was processed. That is a
model-orchestration label: it says how hard the system tried, not what was
wanted. `outcome_kind` records WHAT was wanted, which is the label Phase 3's
Auto router actually needs.

Recorded now rather than with Auto on purpose. Auto is specified to be built
from measured usage rather than guessed rules, and intent cannot be
reconstructed from historical rows after the fact — so the column starts
collecting real labels before the router that consumes them exists.

Deliberately a plain string in the database, not an enum or a foreign key:
adding a workflow must never require a migration. This module is the single
vocabulary, extensible by editing a list here or by calling `register()`.
"""

# Known outcomes. Extending this list is a one-line change with no migration.
QUESTION_ANSWER = "question_answer"
RESUME_TAILOR = "resume_tailor"
TECHNICAL_DOCUMENT = "technical_document"
TROUBLESHOOTING = "troubleshooting"
CODE_FIX = "code_fix"
RESEARCH = "research"
PROJECT_BUILD = "project_build"

# For requests whose intent is not deterministically known at the call site.
# Phase 3 may classify these; until then an honest "unclassified" beats
# guessing, and beats a model call bought purely to label a row.
GENERAL = "general"

KNOWN_OUTCOME_KINDS: set[str] = {
    QUESTION_ANSWER,
    RESUME_TAILOR,
    TECHNICAL_DOCUMENT,
    TROUBLESHOOTING,
    CODE_FIX,
    RESEARCH,
    PROJECT_BUILD,
    GENERAL,
}

MAX_LENGTH = 32  # matches the column width


def register(kind: str) -> str:
    """Add an outcome kind at runtime (a plugin or future workflow).

    Exists so that extending the vocabulary never becomes a schema change.
    """
    kind = _clean(kind)
    KNOWN_OUTCOME_KINDS.add(kind)
    return kind


def _clean(kind: str) -> str:
    kind = (kind or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not kind:
        raise ValueError("outcome kind cannot be empty")
    if len(kind) > MAX_LENGTH:
        raise ValueError(f"outcome kind {kind!r} exceeds {MAX_LENGTH} characters")
    return kind


def normalise(kind: str | None) -> str:
    """Canonical form. An unrecognised kind is preserved, not rejected.

    A new workflow that forgets to register its kind should still record what
    it meant — losing the label would defeat the point of collecting it. The
    known set is a vocabulary, not a gate.
    """
    if kind is None:
        return GENERAL
    return _clean(kind)


def is_known(kind: str | None) -> bool:
    return kind is not None and _clean(kind) in KNOWN_OUTCOME_KINDS
