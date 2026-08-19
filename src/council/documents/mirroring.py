"""JD mirroring detection (contract §6).

Emphasising what a JD asks for is the job. Reproducing the JD's own sentences
is not — a resume whose bullets echo the posting's phrasing reads to a hiring
manager as the JD pasted into a chatbot and rewritten in first person, which is
exactly the impression the contract forbids.

Detection is over *consecutive runs of content words*, not bag-of-words
overlap. A truthful bullet about a JD's stack will naturally share the
technology nouns; what it will not share is a six-word sequence in the same
order. That distinction is what keeps this from firing on honest tailoring.

Nothing here calls a model, and nothing here blocks generation. Mirroring is a
finding the bounded correction pass fixes (§7: match quality and style are
advisory, the artifact still gets produced).
"""

import re

# Words too common to make a run distinctive.
_FILLER = {
    "and", "or", "the", "a", "an", "of", "to", "in", "on", "for", "with", "by",
    "as", "at", "from", "that", "this", "is", "are", "be", "will", "you", "we",
    "our", "your", "their", "its", "it", "they", "have", "has", "had", "was",
    "were", "been", "such", "including", "other", "across", "into", "through",
}

MIN_RUN = 6  # consecutive content words shared, in order
HIGH_OVERLAP = 0.75  # of a bullet's vocabulary found in ONE JD sentence
MIN_BULLET_WORDS = 8  # shorter bullets cannot mirror meaningfully


def _words(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9+/.#-]*", text.lower())
    return [t for t in tokens if t not in _FILLER and len(t) > 1]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?:[.!?;]\s+)|\n+", text)
    return [p.strip(" •-\t") for p in parts if p and p.strip()]


def _runs(a: list[str], b: list[str]) -> int:
    """Longest common consecutive subsequence length."""
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def mirrors_jd(bullet: str, jd_text: str) -> tuple[bool, str]:
    """(is_mirroring, reason). Reason is empty when clean."""
    bullet_words = _words(bullet)
    if len(bullet_words) < MIN_BULLET_WORDS:
        return False, ""

    bullet_vocab = set(bullet_words)
    for sentence in _sentences(jd_text):
        sentence_words = _words(sentence)
        if not sentence_words:
            continue

        run = _runs(bullet_words, sentence_words)
        if run >= MIN_RUN:
            return True, f"shares a {run}-word sequence with the job description"

        overlap = len(bullet_vocab & set(sentence_words)) / len(bullet_vocab)
        if overlap >= HIGH_OVERLAP and len(sentence_words) >= MIN_BULLET_WORDS:
            return True, (
                f"{int(overlap * 100)}% of this bullet's vocabulary comes from one "
                "job-description sentence"
            )
    return False, ""


def find_mirroring(bullets: list[tuple[str, str]], jd_text: str) -> list[dict]:
    """bullets: [(location, text)]. Returns findings in check()'s shape."""
    findings = []
    for location, text in bullets:
        mirrored, reason = mirrors_jd(text, jd_text)
        if mirrored:
            findings.append(
                {
                    "location": location,
                    "text": text,
                    "class": "JD_MIRRORING",
                    "reasons": [reason],
                }
            )
    return findings
