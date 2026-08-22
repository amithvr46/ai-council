"""Conditional technology discovery from a JD (contract Amendment A2).

A hand-maintained list of technologies goes stale as new tools appear, but a
model call per JD is waste. So the flow is mechanical first and only escalates
when it has to:

    mechanical extraction -> classify known terms locally
      -> collect leftover candidate terms
      -> ONLY if meaningful leftovers remain: ONE cheap structured call
      -> compare discovered technologies against career evidence MECHANICALLY

The boundary that matters, and the reason confirmation never touches the model:

    the model may DISCOVER a technology from a JD.
    it may never CONFIRM that the user has experience with it.

`discover()` returns names. It is `assemble_confirmed()` — fed only by career
sources — that decides whether any of them is supported. A discovered term with
no career evidence is a GAP, full stop, and no phrasing of the model's answer
can change that.

Results are cached by normalised term so a second JD using the same vocabulary
costs nothing.
"""

import re
from dataclasses import dataclass, field

from council.documents.profile import (
    ALIASES,
    DEFAULT_DOMAINS,
    DEFAULT_TECHNOLOGIES,
    FOREIGN_TECHNOLOGIES,
    ConfirmedExperience,
    decompose_term,
    mentions,
    normalise,
    scan_jd_technologies,
)

# Words that look technical to a regex but are ordinary JD prose. Without this
# every JD would escalate, which defeats the point of being conditional.
_STOPWORDS = {
    # JD boilerplate
    "we", "you", "your", "our", "the", "and", "or", "with", "for", "this", "that",
    "will", "must", "should", "have", "has", "are", "is", "be", "as", "in", "on",
    "at", "to", "of", "a", "an", "by", "from", "not", "all", "any", "who", "what",
    # role and process vocabulary that is not a technology
    "engineer", "engineering", "engineers", "developer", "developers", "manager",
    "team", "teams", "role", "position", "candidate", "candidates", "experience",
    "years", "year", "required", "requirements", "preferred", "responsibilities",
    "qualifications", "skills", "ability", "strong", "solid", "hands", "plus",
    "bachelor", "master", "degree", "computer", "science", "equivalent",
    "company", "companies", "customer", "customers", "client", "clients",
    "product", "products", "business", "environment", "environments", "systems",
    "system", "software", "hardware", "cloud", "infrastructure", "platform",
    "platforms", "security", "network", "networking", "monitoring", "automation",
    "deployment", "deployments", "production", "development", "operations",
    "support", "design", "designing", "build", "building", "manage", "managing",
    "work", "working", "best", "practices", "practice", "tools",
    "tooling", "technologies", "technology", "stack", "code", "codebase",
    "onsite", "remote", "hybrid", "benefits", "salary", "equity", "insurance",
    "location", "us", "usa", "eeo", "employer", "opportunity", "applicants",
    "monday", "friday", "full", "time", "part",
}

# A candidate looks like a technology name: capitalised, an acronym, a
# dotted/slashed/hyphenated compound, or a version-suffixed word.
_CANDIDATE = re.compile(
    r"\b(?:"
    r"[A-Z][A-Za-z0-9]*(?:[./-][A-Za-z0-9]+)+"     # Argo-CD, CI/CD, Node.js
    r"|[A-Z]{2,6}\b"                                # GKE, IAM, EKS
    r"|[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+"           # CloudFormation, OpenShift
    r"|[A-Z][a-z]{2,}\s+(?:Cloud|Engine|Run|SQL|Hub|Ops|Mesh|Flow|DB|Stack)\b"
    r")"
)

# Plain capitalised words are also candidates — Pulumi, Vercel, Datadog and
# most new products are one ordinary-looking word, and a scanner that only
# recognises CamelCase and acronyms would never surface the very technologies
# this stage exists to catch. Sentence-initial words are excluded, or every
# sentence would nominate its first word.
_PLAIN_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?;:\n•\-]\s*)([A-Z][a-z]{2,})")

# English morphology that product names essentially never have. "Hiring",
# "Reporting" and "Excellence" are JD prose; Pulumi, Vercel and Datadog are
# not shaped like this. Cheap filter, and a miss only costs one cheap call
# that is then cached forever.
_INFLECTED = re.compile(r"(?:ing|ed|ly|tion|sion|ment|ness|ity|ance|ence|ships?)$", re.I)

MAX_CANDIDATES = 25  # a JD with more than this is prose, not a stack list


@dataclass
class DiscoveryResult:
    """What the JD asks for, split by whether career evidence supports it.

    `escalated` records whether a model call was actually made, so the cost
    behaviour is observable rather than assumed.
    """

    supported: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    discovered: list[str] = field(default_factory=list)  # newly learned names
    escalated: bool = False
    # True when discovery was warranted but the provider was unreachable. Gap
    # coverage is narrower for that run, and saying so beats letting an empty
    # discovery read as "nothing unknown was found".
    unavailable: bool = False
    candidates_considered: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "discovery_unavailable": self.unavailable,
            "supported": self.supported,
            "gaps": self.gaps,
            "discovered": self.discovered,
            "escalated": self.escalated,
            "candidates_considered": self.candidates_considered,
        }


def known_vocabulary(confirmed: ConfirmedExperience) -> set[str]:
    return (
        {normalise(t) for t in DEFAULT_TECHNOLOGIES}
        | {normalise(d) for d in DEFAULT_DOMAINS}
        | set(confirmed.terms)
        | set(ALIASES)
        | {normalise(t) for t in FOREIGN_TECHNOLOGIES}
    )


def candidate_terms(jd_text: str, known: set[str], cached: set[str]) -> list[str]:
    """Technical-looking terms the local vocabulary cannot account for.

    Anything already known — supported, foreign or previously discovered — is
    excluded, because escalating for it would be paying to learn a word twice.
    """
    sentence_initial = {m.group(1) for m in _SENTENCE_START.finditer(jd_text)}
    # Words that are part of a known multi-word technology are already
    # accounted for: "Google" inside "Google Cloud Platform" is not a new term.
    known_tokens = {token for term in known if " " in term for token in term.split()}
    hits = [m.group(0).strip() for m in _CANDIDATE.finditer(jd_text)]
    hits += [
        m.group(0)
        for m in _PLAIN_WORD.finditer(jd_text)
        if m.group(0) not in sentence_initial
        and normalise(m.group(0)) not in known_tokens
        and not _INFLECTED.search(m.group(0))
    ]

    seen: list[str] = []
    seen_keys: set[str] = set()
    for term in hits:
        key = normalise(term)
        if key in known or key in cached or key in _STOPWORDS or key in seen_keys:
            continue
        # A compound made entirely of names we already know is not a discovery.
        # "Prometheus/Grafana" and "Linux-based" were each escalated to the
        # model as unknown terms and came back classified as technologies the
        # career does not have — paying for the wrong answer twice over.
        parts = decompose_term(key)
        if parts != [key] and all(p in known or p in cached for p in parts):
            continue
        if len(key) < 2 or key.isdigit():
            continue
        if all(part in _STOPWORDS for part in key.split()):
            continue
        # A plain word that is only part of a compound already captured
        # ("Kubernetes" inside "Google Kubernetes Engine") adds nothing.
        if any(key in normalise(other).split() for other in seen if " " in other):
            continue
        seen.append(term)
        seen_keys.add(key)
    return seen[:MAX_CANDIDATES]


def classify_known(jd_text: str, confirmed: ConfirmedExperience) -> tuple[list[str], list[str]]:
    """Mechanical pass: known technologies the JD names, split by support."""
    return scan_jd_technologies(jd_text, confirmed)


DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "technologies": {
            "type": "array",
            "description": (
                "Only those input terms that name a technology, tool, platform, "
                "service, language or framework. Omit company names, role titles, "
                "team names, methodologies and ordinary English words."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "language",
                            "framework",
                            "platform",
                            "service",
                            "tool",
                            "protocol",
                            "other",
                        ],
                    },
                },
                "required": ["term", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["technologies"],
    "additionalProperties": False,
}

DISCOVERY_INSTRUCTION = (
    "You are given terms extracted from a job description. For each, say only "
    "whether it names a technology, tool, platform, service, language or "
    "framework. Do NOT say anything about who has used it, who is qualified, "
    "or what any candidate's experience is — you are identifying vocabulary, "
    "nothing else. Omit company names, role titles, team names, certifications, "
    "methodologies and ordinary English words."
)


class DiscoveryCache:
    """Normalised term -> is-a-technology. Keeps repeat JDs free.

    Negative answers are cached too: learning that "Workday" is not relevant
    vocabulary is worth exactly as much as learning that it is, and without it
    the same non-technology would be re-escalated on every similar JD.
    """

    def __init__(self, entries: dict[str, bool] | None = None):
        self._entries: dict[str, bool] = dict(entries or {})

    def known(self) -> set[str]:
        return set(self._entries)

    def technologies(self) -> set[str]:
        return {k for k, v in self._entries.items() if v}

    def record(self, term: str, is_technology: bool) -> None:
        self._entries[normalise(term)] = is_technology

    def as_dict(self) -> dict[str, bool]:
        return dict(self._entries)


async def discover(
    jd_text: str,
    confirmed: ConfirmedExperience,
    *,
    cache: DiscoveryCache | None = None,
    ask_model=None,
) -> DiscoveryResult:
    """Full A2 flow. `ask_model` is an async callable taking the candidate
    terms and returning the structured discovery payload; None disables
    escalation entirely (the mechanical result still stands).
    """
    cache = cache or DiscoveryCache()
    supported, gaps = classify_known(jd_text, confirmed)
    known = known_vocabulary(confirmed)

    # Terms a previous discovery already established as technologies are known
    # vocabulary now — check them for support without paying again.
    for term in cache.technologies():
        if term in known:
            continue
        if mentions(jd_text, term):
            (supported if confirmed.is_confirmed(term) else gaps).append(term)

    result = DiscoveryResult(
        supported=sorted(set(supported)),
        gaps=sorted(set(gaps)),
    )

    candidates = candidate_terms(jd_text, known, cache.known())
    result.candidates_considered = candidates
    if not candidates or ask_model is None:
        return result  # nothing meaningful left; no call, no cost

    payload = await ask_model(candidates)
    if (payload or {}).get("unavailable"):
        # Reported rather than swallowed: gap coverage is narrower this run,
        # and the caller deserves to know that rather than read an empty
        # discovery as "nothing unknown was found".
        result.unavailable = True
        return result
    result.escalated = True

    discovered = {
        normalise(item.get("term", ""))
        for item in (payload or {}).get("technologies", [])
        if item.get("term")
    }
    discovered.discard("")

    for term in candidates:
        cache.record(term, normalise(term) in discovered)

    for term in sorted(discovered):
        result.discovered.append(term)
        # THE boundary: discovery names it, career evidence decides it.
        if confirmed.is_confirmed(term):
            result.supported.append(term)
        else:
            result.gaps.append(term)

    result.supported = sorted(set(result.supported))
    result.gaps = sorted(set(result.gaps))
    return result
