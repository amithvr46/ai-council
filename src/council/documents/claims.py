"""Mechanical claim classification for document-grounded generation.

The product contract permits AI Council to write realistic responsibility
wording around confirmed technologies WITHOUT asking the user to approve every
bullet, while never manufacturing career facts. Those two requirements pull in
opposite directions, so the boundary between them is drawn here in code —
mechanically, and therefore testable — rather than left to prompt adherence.

Four classes (see docs/document-contract.md §4):

  PERMITTED_EXPANSION             realistic wording, confirmed stack, no facts
  UNSUPPORTED_EXPANSION           references unconfirmed technology/role
  UNSUPPORTED_IMPLEMENTATION_CLAIM  Tier 2B: bespoke project / leadership /
                                  sole ownership without direct support
  FABRICATED_FACT                 Tier 3: a hard fact with no source support

Nothing here calls a model. These are pure functions over text.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum


class ClaimClass(StrEnum):
    PERMITTED_EXPANSION = "PERMITTED_EXPANSION"
    UNSUPPORTED_EXPANSION = "UNSUPPORTED_EXPANSION"
    UNSUPPORTED_IMPLEMENTATION_CLAIM = "UNSUPPORTED_IMPLEMENTATION_CLAIM"
    FABRICATED_FACT = "FABRICATED_FACT"


# --- Tier 3: hard factual claims --------------------------------------------
# Precise historical facts that cannot be inferred from a technology list.

_HARD_FACT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("percentage", re.compile(r"\b\d+(?:\.\d+)?\s*%|\bpercent\b", re.I)),
    ("currency", re.compile(r"[$£€]\s?\d|\b\d+\s*(?:k|m|million|billion)\b(?!\w)", re.I)),
    (
        "count",
        re.compile(
            r"\b\d[\d,]*\+?\s*(?:servers?|vms?|nodes?|clusters?|applications?|apps?|"
            r"services?|microservices?|users?|customers?|tenants?|subscriptions?|"
            r"pipelines?|repos?|repositories?|environments?|regions?|databases?|"
            r"workloads?|deployments?|incidents?|tickets?|engineers?|developers?|"
            r"people|members?|teams?)\b",
            re.I,
        ),
    ),
    (
        "team_size",
        re.compile(r"\bteam of \d+|\b\d+[- ]person\b|\bmanaged \d+\b", re.I),
    ),
    (
        "duration_or_date",
        re.compile(
            r"\b(?:19|20)\d{2}\b|\bwithin \d+\s*(?:days?|weeks?|months?|hours?|minutes?)\b",
            re.I,
        ),
    ),
    (
        "improvement_metric",
        re.compile(
            r"\b(?:reduc\w+|improv\w+|increas\w+|decreas\w+|cut|saved?|sped up|"
            r"accelerat\w+)\b[^.]{0,40}?\b\d",
            re.I,
        ),
    ),
    (
        "exact_scale",
        re.compile(r"\b\d[\d,]*\s*(?:tb|gb|pb|rps|qps|tps|req/s|transactions)\b", re.I),
    ),
    ("award", re.compile(r"\baward(?:ed)?\b|\brecogni[sz]ed as\b|\bwinner\b", re.I)),
]

# Numbers that are part of a technology name are not factual claims about the
# user's career (Kubernetes 1.29, Python 3, TLS 1.2, S3, EC2, ISO 27001...).
_BENIGN_NUMBER = re.compile(
    r"\b(?:v?\d+\.\d+(?:\.\d+)?|s3|ec2|ec3|oauth ?2|http/?2|tls ?1\.\d|ipv[46]|"
    r"iso ?\d+|soc ?2|pci ?dss|24/7|ci/cd|p1|p2|p3|sev ?\d)\b",
    re.I,
)


# --- Tier 2B: specific implementation / ownership claims ---------------------
# Confirmed tools do not authorise inventing a specific project.

_CREATION_VERBS = (
    r"design(?:ed|ing)?|architect(?:ed|ing)?|built|build(?:ing)?|creat(?:ed|ing)|"
    r"develop(?:ed|ing)|author(?:ed|ing)|invent(?:ed|ing)|establish(?:ed|ing)|"
    r"found(?:ed|ing)|pioneer(?:ed|ing)|introduc(?:ed|ing)"
)
_BESPOKE_ARTIFACTS = (
    r"custom|bespoke|in-house|internal|proprietary|greenfield|from scratch|novel|"
    r"enterprise-wide|company-wide|organi[sz]ation-wide|framework|platform|operator|"
    r"controller|toolchain|product|system"
)
_LEADERSHIP = (
    r"\bled\b|\bleading\b|\bmanag(?:ed|ing)\b|\bmentor(?:ed|ing)\b|\bsupervis(?:ed|ing)\b|"
    r"\bheaded\b|\bdirect(?:ed|ing)\b|\bown(?:ed|ing)\b|\bsole(?:ly)?\b|"
    r"\bsingle-handedly\b|\bspearhead(?:ed|ing)\b|\bdrove\b|\bdriving\b"
)

_IMPLEMENTATION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "bespoke_artifact_creation",
        re.compile(rf"\b(?:{_CREATION_VERBS})\b[^.]{{0,60}}?\b(?:{_BESPOKE_ARTIFACTS})\b", re.I),
    ),
    (
        "leadership_or_ownership",
        re.compile(
            rf"(?:{_LEADERSHIP})[^.]{{0,60}}?"
            r"\b(?:team|migration|initiative|programme|program|project|effort|"
            r"architecture|strategy|roadmap|transformation|adoption|rollout)\b",
            re.I,
        ),
    ),
    (
        "major_migration",
        re.compile(r"\b(?:major|large-scale|full|complete|end-to-end)\b[^.]{0,30}?"
                   r"\bmigrat\w+", re.I),
    ),
]

# Ordinary engineering work that happens to use a creation verb is NOT a Tier
# 2B claim: writing a Terraform module or a pipeline is routine, not a bespoke
# platform. These take precedence over the patterns above.
_ROUTINE_WORK = re.compile(
    r"\b(?:terraform (?:module|plan|config\w*)|pipeline|playbook|helm chart|"
    r"dashboard|alert|runbook|script|manifest|workflow|job|template|role|policy)s?\b",
    re.I,
)


# --- Tier 2B, second half: invented relationships ---------------------------
# Contract Amendment A5. Confirmed AWS + Terraform + Kubernetes + Python does
# NOT establish "Built Kubernetes clusters on AWS using Terraform and automated
# the entire platform through Python". Every noun is confirmed; the specific
# historical relationship between them may be invented. Classification must ask
# two questions, not one — hence this check, which looks at how technologies are
# bound together rather than at whether each is confirmed.

_BINDING_VERBS = re.compile(
    rf"\b(?:{_CREATION_VERBS}|integrat(?:ed|ing)|migrat(?:ed|ing)|"
    r"consolidat(?:ed|ing)|re-?platform(?:ed|ing)|standardi[sz](?:ed|ing)|"
    r"automat(?:ed|ing))\b",
    re.I,
)

# Totalising scope: the difference between describing work and claiming an
# entire estate. "the entire platform" is a bigger claim than "the platform".
_TOTALISING_SCOPE = re.compile(
    r"\b(?:entire|whole|all(?: of)?|complete|end-to-end|full|every)\b[^.]{0,30}?"
    r"\b(?:platform|infrastructure|environment|estate|stack|architecture|"
    r"landscape|footprint|ecosystem|system|pipeline|deployment|migration|"
    r"organi[sz]ation|company|enterprise)\b",
    re.I,
)

# How many distinct technologies one sentence may bind under a creation verb
# before it stops being a description of routine work and becomes a claim about
# a specific thing that was built. Three is the threshold: "Built pipelines in
# Azure DevOps" is ordinary, "Built X on Y using Z and automated W" is a story.
_RELATIONSHIP_TECH_THRESHOLD = 3


def find_relationship_claims(text: str, technologies: list[str]) -> list[str]:
    """Tier 2B detections based on how technologies are bound together.

    technologies: the technical terms this statement mentions, confirmed or
    not. Confirmation status is deliberately irrelevant here — the whole point
    is that confirmed nouns do not license an invented relationship.
    """
    findings: list[str] = []
    distinct = {t.lower() for t in technologies if t}

    if _TOTALISING_SCOPE.search(text):
        findings.append("totalising_scope")

    if (
        len(distinct) >= _RELATIONSHIP_TECH_THRESHOLD
        and _BINDING_VERBS.search(text)
        and not _ROUTINE_WORK.search(text)
    ):
        findings.append("multi_technology_relationship")

    return findings


@dataclass
class ClaimFinding:
    text: str
    classification: ClaimClass
    reasons: list[str] = field(default_factory=list)
    unconfirmed_terms: list[str] = field(default_factory=list)

    @property
    def needs_correction(self) -> bool:
        return self.classification != ClaimClass.PERMITTED_EXPANSION


def find_hard_facts(text: str) -> list[str]:
    """Tier 3 detections. Version numbers and product names are not facts."""
    scrubbed = _BENIGN_NUMBER.sub(" ", text)
    return [name for name, pattern in _HARD_FACT_PATTERNS if pattern.search(scrubbed)]


# Product names that collide with leadership/creation vocabulary. "Managed
# Identity" is an Azure service, not a claim to have managed anything —
# without this scrub, a truthful bullet about RBAC and Key Vault gets flagged
# as an ownership claim purely because Microsoft named a product "Managed".
_PRODUCT_NAME_NOISE = re.compile(
    r"\bmanaged (?:identit(?:y|ies)|instances?|grafana|prometheus|disks?|"
    r"clusters?|service identity)\b|\bazure managed\b|\bled display\b",
    re.I,
)


def find_implementation_claims(text: str) -> list[str]:
    """Tier 2B detections, excluding routine engineering artifacts."""
    text = _PRODUCT_NAME_NOISE.sub(" ", text)
    if _ROUTINE_WORK.search(text):
        # Routine artifact present: only flag if leadership/ownership language
        # is ALSO present, which routine work does not require.
        return [
            name
            for name, pattern in _IMPLEMENTATION_PATTERNS
            if name != "bespoke_artifact_creation" and pattern.search(text)
        ]
    return [name for name, pattern in _IMPLEMENTATION_PATTERNS if pattern.search(text)]


def classify(
    text: str,
    *,
    confirmed_terms: set[str],
    candidate_terms: list[str] | None = None,
    supported_facts: bool = False,
) -> ClaimFinding:
    """Classify one generated statement.

    confirmed_terms: lowercase technologies/roles/employers established by the
        career sources.
    candidate_terms: technical terms detected in this statement; when omitted
        they are extracted from the known vocabulary.
    supported_facts: True when the statement's hard facts are directly present
        in the source material (so a real metric is not flagged).
    """
    reasons: list[str] = []

    unconfirmed = [
        term
        for term in (candidate_terms if candidate_terms is not None else [])
        if term.lower() not in confirmed_terms
    ]

    facts = find_hard_facts(text)
    implementation = find_implementation_claims(text)
    # Amendment A5: asked independently of confirmation, because confirmed
    # nouns are exactly what makes an invented relationship look safe.
    implementation += find_relationship_claims(text, candidate_terms or [])

    # Precedence: a fabricated number is the most serious, then an invented
    # project, then an unconfirmed technology.
    if facts and not supported_facts:
        reasons.append(f"hard factual claim(s) without support: {', '.join(facts)}")
        return ClaimFinding(text, ClaimClass.FABRICATED_FACT, reasons)

    if implementation:
        reasons.append(
            "specific implementation/ownership claim requiring direct career "
            f"evidence: {', '.join(implementation)}"
        )
        return ClaimFinding(text, ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM, reasons)

    if unconfirmed:
        reasons.append(f"references unconfirmed experience: {', '.join(unconfirmed)}")
        return ClaimFinding(text, ClaimClass.UNSUPPORTED_EXPANSION, reasons, unconfirmed)

    return ClaimFinding(text, ClaimClass.PERMITTED_EXPANSION)
