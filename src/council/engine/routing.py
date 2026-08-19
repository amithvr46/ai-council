"""Auto routing — Phase 3A. Rungs 0 to 3.

Auto is a decision ladder, not a model that guesses a mode. Buying a model call
to decide how much model to buy is the wrong shape, so **nothing in this module
calls a model**. Every function here is pure or reads rows we already have.

    Rung 0  explicit user mode      -> obey; Auto never overrides a choice
    Rung 1  outcome resolution      -> workflow outcomes skip mode entirely
    Rung 2  hard constraints        -> affordability, provider availability
    Rung 3  deterministic features  -> computed from the text
    Rung 4  historical prior        -> 3C, deliberately not consumed yet
    (Rung 5 escalation lives in the pipeline, 3B)

The governing principle: de-escalation is impossible and escalation is cheap,
so start at the cheapest DEFENSIBLE mode and escalate on observation. Quick is
a real commitment — a single model produces no disagreement signal, so there is
no escalation path out of it. Council is the safe default whenever routing is
uncertain.
"""

import re
from dataclasses import dataclass, field

MODES = ("quick", "council", "deep")
DEFAULT_MODE = "council"  # the safe middle; preserves the escalation option


# --------------------------------------------------------------- freshness
#
# Contract amendment 1: a recency WORD is not a freshness signal. What matters
# is whether the ANSWER depends on fresh external-world information.
#
#   "What is the latest Kubernetes version?"  -> yes, the world moved
#   "Rewrite my current resume."              -> no, 'current' describes the
#                                                user's own attached material
#
# So freshness requires a recency marker AND a question about the external
# world, AND the absence of any signal that the user is pointing at their own
# material. All three, deterministically.

_RECENCY_PHRASES = (
    r"latest|newest|most recent|current(?:ly)?|up[- ]to[- ]date|nowadays|"
    r"these days|right now|as of|so far this|this (?:week|month|quarter|year)|"
    r"today|recently|still (?:supported|maintained|available|the case)"
)
_RECENCY = re.compile(rf"\b(?:{_RECENCY_PHRASES})\b", re.I)

# Asking the world a question, rather than asking for work on a document.
_FACT_QUERY = re.compile(
    # Interrogative forms about the world. The leading yes/no form ("Is X still
    # supported?") matters because support and deprecation status is exactly
    # the kind of fact that goes stale; it is safe here because a question
    # about the user's own material is excluded by the self-reference and
    # transformation checks before this ever runs.
    r"\b(?:what|which|who|when|where|how many|how much|how long|is there|are there|"
    r"has there been|did .* change|what changed|what'?s new)\b"
    r"|^\s*(?:is|are|does|do|has|have|did|was|were|can|will)\b",
    re.I,
)

# The user is pointing at their own material: this is transformation work, and
# no amount of web search makes their resume newer.
_TRANSFORM_VERBS = (
    r"rewrite|reword|rephrase|shorten|lengthen|expand|condense|summari[sz]e|"
    r"review|proofread|edit|revise|polish|tidy|clean up|format|reformat|refactor|"
    r"fix|debug|translate|convert|improve|critique|check over"
)
_TRANSFORM_VERB = re.compile(rf"\b(?:{_TRANSFORM_VERBS})\b", re.I)

# "Shorten this to one sentence: '...'" carries no possessive, but a
# transformation verb pointing at a bare deictic is still work on the user's
# own material. Found by the controlled evaluation, where it cost a legitimate
# quick.
_TRANSFORM_ON_DEICTIC = re.compile(
    rf"\b(?:{_TRANSFORM_VERBS})\b\s+(?:this|that|it|these|the following|the below)\b",
    re.I,
)

# Possessive or deictic reference to material in hand. Recency phrases are
# stripped BEFORE this runs, so "this week" cannot be mistaken for "this file".
_SELF_REFERENCE = re.compile(
    r"\b(?:my|our|mine|ours|the attached|attached|the above|above|below|"
    r"the following|"
    # Up to two intervening words so "this deployment script" and "this Terraform
    # config" are recognised, not just the bare noun. Found by inspecting real
    # routing output: the phrase fell through to council, which was safe but
    # missed a legitimate quick.
    r"th(?:is|e) (?:\w+ ){0,2}(?:code|file|document|resume|text|snippet|function|"
    r"config|script|error|draft|summary|paragraph|section|manifest|template|module)|"
    r"these (?:\w+ ){0,2}(?:files|documents|notes|bullets|scripts|configs))\b",
    re.I,
)

# Self-reference normally suppresses freshness: no search makes the user's own
# resume newer. But it must NOT suppress it when CURRENT EXTERNAL STATE decides
# whether the answer is correct —
#
#   "Is this Terraform code valid with the current provider version?"
#
# points at the user's material AND depends on what the provider ships today.
# Freshness wins there, because being wrong about the external state makes the
# answer wrong regardless of whose code it is.
_EXTERNAL_STATE = (
    r"version|versions|release|releases|sdk|api|provider|runtime|image|package|"
    r"dependency|dependencies|pricing|price|quota|limit|docs|documentation|"
    r"support|deprecat\w+|end[- ]of[- ]life|eol"
)
# ...unless the external-state noun belongs to the user: "the latest version OF
# THIS CODE" is their file, not the world's.
_EXTERNAL_STATE_NEAR_RECENCY = re.compile(
    rf"\b(?:{_RECENCY_PHRASES})\b[\w\s]{{0,20}}?\b(?:{_EXTERNAL_STATE})\b"
    rf"(?!\s+of\s+(?:my|this|these|our|the attached))"
    rf"|\b(?:{_EXTERNAL_STATE})\b[\w\s]{{0,12}}?\b(?:{_RECENCY_PHRASES})\b",
    re.I,
)

_CODE_BLOCK = re.compile(r"```|\bTraceback \(most recent call last\)|"
                         r"^\s*(?:Error|Exception|panic|FATAL)\b", re.M)

_OPINION = re.compile(
    r"\b(?:write me a|compose|brainstorm|come up with|suggest some|"
    r"poem|limerick|haiku|story|joke|tagline|slogan)\b",
    re.I,
)


def _strip_recency(text: str) -> str:
    """Remove recency phrases, then collapse whitespace.

    The collapse matters: without it "these current bullets" becomes
    "these  bullets" with a double space, and the self-reference patterns —
    which allow intervening WORDS, not arbitrary whitespace — stop matching.
    """
    return re.sub(r"\s+", " ", _RECENCY.sub(" ", text)).strip()


def needs_fresh_information(text: str) -> bool:
    """True when the ANSWER depends on fresh external-world information.

    Deterministic on purpose. This is the one defensible *static* deep trigger:
    against a stale-world question, model agreement is worthless — both models
    can confidently agree on training data that has since gone out of date,
    which is exactly the case rule R4 exists for.
    """
    if not _RECENCY.search(text):
        return False

    # Current external state decides correctness: freshness wins even when the
    # request points at the user's own material.
    if _EXTERNAL_STATE_NEAR_RECENCY.search(text):
        return True

    # Remove the recency phrases first so "this week" is never read as a
    # pointer to the user's own material.
    stripped = _strip_recency(text)
    if _TRANSFORM_VERB.search(text) or _SELF_REFERENCE.search(stripped):
        return False
    return bool(_FACT_QUERY.search(text))


def extract_features(text: str) -> dict:
    """Deterministic signals. No model, no network.

    These are SEEDS, not permanent rules: the routing report exists so they can
    be corrected by measurement rather than by argument.
    """
    return {
        "needs_fresh_information": needs_fresh_information(text),
        "has_code": bool(_CODE_BLOCK.search(text)),
        "is_transformation": bool(_TRANSFORM_VERB.search(text))
        and bool(
            _SELF_REFERENCE.search(_strip_recency(text))
            or _TRANSFORM_ON_DEICTIC.search(text)
        ),
        "is_opinion_or_creative": bool(_OPINION.search(text)),
        "length": len(text),
    }


# ----------------------------------------------------- quick eligibility
#
# Contract amendment 2, a PERMANENT principle: historical agreement alone must
# never promote a class to quick.
#
# Agreement measures redundancy between models. It does not establish
# correctness. Two models sharing a blind spot agree enthusiastically, and if
# evidence later overrode them 15% of the time then that class needs MORE
# verification, not less — even at a 98% agreement rate.
#
# Consumed in 3C. Implemented and tested now so the rule cannot be quietly
# skipped when the prior is finally wired in.

MIN_SAMPLES = 8
MIN_AGREEMENT_RATE = 0.95
MAX_OVERRIDE_RATE = 0.02
MIN_MEAN_RATING = 4.0


@dataclass(frozen=True)
class ClassStats:
    """Measured behaviour of one (outcome_kind, feature bucket) group."""

    samples: int = 0
    agreement_rate: float = 0.0
    evidence_override_rate: float = 1.0
    mean_rating: float | None = None  # None when unrated
    low_risk: bool = False


def quick_eligible(stats: ClassStats, features: dict) -> tuple[bool, str]:
    """Every condition must hold. Returns (eligible, reason)."""
    if stats.samples < MIN_SAMPLES:
        return False, f"only {stats.samples} samples, need {MIN_SAMPLES}"
    if stats.agreement_rate < MIN_AGREEMENT_RATE:
        return False, f"agreement {stats.agreement_rate:.0%} below {MIN_AGREEMENT_RATE:.0%}"
    if stats.evidence_override_rate > MAX_OVERRIDE_RATE:
        # The case worth naming: models agreeing while evidence keeps
        # contradicting them is a reason to verify more, not less.
        return False, (
            f"evidence overrode this class {stats.evidence_override_rate:.0%} of the "
            "time; agreement is redundancy, not correctness"
        )
    if not stats.low_risk:
        return False, "class is not marked low risk"
    if stats.mean_rating is not None and stats.mean_rating < MIN_MEAN_RATING:
        return False, f"mean rating {stats.mean_rating:.1f} below {MIN_MEAN_RATING}"
    if features.get("needs_fresh_information"):
        return False, "answer depends on fresh external information"
    if features.get("has_code"):
        return False, "code benefits from a second opinion"
    return True, "sustained agreement, no evidence overrides, low-risk class"


# ------------------------------------------------------------- the decision


@dataclass(frozen=True)
class RoutingDecision:
    mode: str
    rung: str  # explicit | constraint | features | prior | default | fallback
    reason: str
    features: dict = field(default_factory=dict)
    considered: list[str] = field(default_factory=list)
    prior: dict | None = None

    @property
    def was_routed(self) -> bool:
        return self.rung != "explicit"

    def as_dict(self) -> dict:
        return {
            "chosen": self.mode,
            "deciding_rung": self.rung,
            "reason": self.reason,
            "features": self.features,
            "candidates_considered": self.considered,
            "prior": self.prior,
        }


def decide(
    text: str,
    *,
    affordable: list[str] | None = None,
    degraded_providers: bool = False,
    stats: ClassStats | None = None,
) -> RoutingDecision:
    """Rungs 2 and 3. Pure — affordability is resolved by the caller.

    `stats` is accepted so 3C can pass a measured prior without reshaping this
    signature. In 3A it is ignored unless quick_eligible clears it outright,
    and callers do not supply it yet.
    """
    affordable = list(affordable) if affordable is not None else list(MODES)
    features = extract_features(text)

    # --- Rung 2: hard constraints -------------------------------------
    if not affordable:
        # The caller refuses before reaching here; belt and braces.
        return RoutingDecision(
            "quick", "constraint", "no mode is affordable; cheapest attempted",
            features, affordable,
        )

    if degraded_providers and "quick" in affordable:
        return RoutingDecision(
            "quick", "constraint",
            "a provider is unavailable, so council would be council in name only",
            features, affordable,
        )

    # --- Rung 3: deterministic features -------------------------------
    if features["needs_fresh_information"] and "deep" in affordable:
        return RoutingDecision(
            "deep", "features",
            "the answer depends on fresh external information, where model "
            "agreement proves nothing",
            features, affordable,
        )

    if (features["is_transformation"] or features["is_opinion_or_creative"]) and (
        "quick" in affordable
    ):
        why = (
            "transformation of the user's own material"
            if features["is_transformation"]
            else "opinion or creative request"
        )
        return RoutingDecision(
            "quick", "features", f"{why}: no external fact to verify", features, affordable
        )

    # --- Rung 4: historical prior (3C) --------------------------------
    if stats is not None:
        eligible, why = quick_eligible(stats, features)
        if eligible and "quick" in affordable:
            return RoutingDecision(
                "quick", "prior", why, features, affordable, stats.__dict__
            )

    # --- Default ------------------------------------------------------
    if DEFAULT_MODE in affordable:
        return RoutingDecision(
            DEFAULT_MODE, "default",
            "no signal favours a cheaper or more thorough path; council keeps "
            "the escalation option open",
            features, affordable,
        )

    cheapest = next(m for m in MODES if m in affordable)
    return RoutingDecision(
        cheapest, "constraint",
        f"council is not affordable; using {cheapest}", features, affordable,
    )
