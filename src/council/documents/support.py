"""Is a statement directly supported by a career source?

Tier 2B says confirmed tools alone do not authorise a specific implementation
claim — *unless career evidence establishes it*. That second half needs a
mechanism, or the classifier flags the user's own real projects.

A live run demonstrated the failure: the master resume establishes AI Council
as an independent project, the draft described it accurately, and the
classifier called it an unsupported implementation claim because "built ...
platform" matches the bespoke-artifact pattern. True statement, flagged as
invented.

So: before a Tier 2B or Tier 3 finding stands, check whether some sentence in a
career source is saying the same thing. Overlap of distinctive content words,
not fuzzy similarity — a real match rewords the same specifics, and a
manufactured claim does not.

Deliberately strict. A missed support match costs a rewrite of a true bullet; a
false support match lets an invented project through, which is the failure the
whole contract exists to prevent.
"""

import re

# Words that carry no evidential weight: matching on them would let any two
# resume sentences "support" each other.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "for", "with", "using", "used", "use",
    "to", "of", "in", "on", "at", "by", "from", "as", "into", "across", "through",
    "that", "this", "these", "those", "it", "its", "their", "them", "they",
    "was", "were", "is", "are", "be", "been", "being", "has", "have", "had",
    "including", "such", "other", "more", "also", "while", "when", "before",
    "after", "then", "than", "which", "who", "where", "each", "both", "all",
    "work", "worked", "working", "support", "supported", "supporting",
    "team", "teams", "environment", "environments", "production", "development",
}

_MIN_CONTENT_WORDS = 4
_OVERLAP_THRESHOLD = 0.6


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+/.#-]*", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _sentences(text: str) -> list[str]:
    # Bullets and sentences both count; a resume source is mostly bullets.
    parts = re.split(r"(?:[.!?]\s+)|\n+", text)
    return [p.strip(" •-\t") for p in parts if p and p.strip()]


def directly_supported(statement: str, sources: list[str]) -> bool:
    """True when some source sentence asserts substantially this statement.

    Requires most of the statement's distinctive vocabulary to appear in ONE
    source sentence. Spreading the words across a whole document does not
    count — that is how "Terraform" here and "Kubernetes" there would combine
    to support a claim neither of them made.
    """
    target = _content_words(statement)
    if len(target) < _MIN_CONTENT_WORDS:
        return False

    for source in sources:
        for sentence in _sentences(source):
            candidate = _content_words(sentence)
            if not candidate:
                continue
            overlap = len(target & candidate) / len(target)
            if overlap >= _OVERLAP_THRESHOLD:
                return True
    return False


def source_texts(documents: list[dict]) -> list[str]:
    """Career sources only. A JD is the target, never support (A1)."""
    return [
        d.get("text", "")
        for d in documents
        if d.get("authority") not in (None, "jd")
    ]
