"""Career Experience Profile — the authority on what the user has done.

The contract's most important structural rule lives here:

    Omission from a tailored resume does not mean lack of experience.

Every career source contributes POSITIVELY to confirmed experience. No source
ever subtracts. A tailored resume that omits Harness is a selective view
created for one JD, not evidence that Harness was never used — so a
Harness-heavy JD tomorrow can still surface it.

That rule is enforced in the assembly function below rather than stated in a
prompt, because a prompt cannot be regression-tested.
"""

import re
from dataclasses import dataclass, field

# Authority levels. All contribute positively; none is negative evidence.
AUTHORITY_PROFILE = "profile"  # authoritative
AUTHORITY_MASTER_RESUME = "master_resume"  # strong evidence
AUTHORITY_SUPPORTING = "supporting"  # notes, project docs, certificates
AUTHORITY_TAILORED_RESUME = "tailored_resume"  # selective view, never negative
AUTHORITY_JD = "jd"  # the target, NOT career evidence

CAREER_AUTHORITIES = (
    AUTHORITY_PROFILE,
    AUTHORITY_MASTER_RESUME,
    AUTHORITY_SUPPORTING,
    AUTHORITY_TAILORED_RESUME,
)

# Role families the career legitimately spans (contract §2A). Emphasis shifts
# between these; the career itself does not change.
ROLE_FAMILIES = {
    "sre": [
        "production troubleshooting", "incident response", "observability",
        "reliability", "kubernetes", "automation", "monitoring", "root cause analysis",
        "operational support",
    ],
    "platform": [
        "terraform", "infrastructure as code", "reusable infrastructure patterns",
        "ci/cd", "kubernetes", "automation", "developer enablement", "gitops",
        "security", "platform operations",
    ],
    "azure_devops": [
        "azure", "azure devops", "terraform", "ci/cd", "harness", "jenkins",
        "github actions", "gitlab", "aks", "scripting", "security",
        "deployment automation",
    ],
    "cloud_operations": [
        "azure", "aws", "monitoring", "troubleshooting", "incident response",
        "infrastructure changes", "access management", "security",
        "cost management", "automation", "operational reliability",
    ],
    "infrastructure": [
        "cloud infrastructure", "terraform", "networking", "identity", "security",
        "kubernetes", "automation", "configuration management",
        "production operations",
    ],
}

# Seeded from the user's stated career baseline (contract §2B). Extensible:
# the user adds legitimate experience over time, and documents contribute too.
DEFAULT_DOMAINS = [
    "cloud infrastructure", "infrastructure as code", "ci/cd",
    "release engineering", "containers and orchestration", "production operations",
    "monitoring and observability", "incident troubleshooting", "root cause analysis",
    "automation and scripting", "security and governance", "networking",
    "cost management",
]

DEFAULT_TECHNOLOGIES = [
    "azure", "aws", "azure devops", "terraform", "terraform enterprise", "ansible",
    "docker", "kubernetes", "aks", "eks", "jenkins", "harness", "argo cd", "gitlab",
    "github actions", "git", "helm", "splunk", "grafana", "prometheus",
    "azure monitor", "log analytics", "kql", "application insights", "cloudwatch",
    "powershell", "bash", "python",
]

# Common ways the same technology is written. Extending this list is routine.
ALIASES: dict[str, str] = {
    "azure kubernetes service": "aks",
    "elastic kubernetes service": "eks",
    "k8s": "kubernetes",
    "argocd": "argo cd",
    "argo-cd": "argo cd",
    "gh actions": "github actions",
    "tfe": "terraform enterprise",
    "iac": "infrastructure as code",
    "adо": "azure devops",
    "azure pipelines": "azure devops",
    "log analytics workspace": "log analytics",
    "app insights": "application insights",
    "rca": "root cause analysis",
}


def normalise(term: str) -> str:
    term = re.sub(r"\s+", " ", term.strip().lower())
    return ALIASES.get(term, term)


@dataclass
class CareerProfile:
    """Structured, user-owned, extensible."""

    technologies: list[str] = field(default_factory=lambda: list(DEFAULT_TECHNOLOGIES))
    domains: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    roles: list[str] = field(default_factory=list)
    employers: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)  # only established ones
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "technologies": self.technologies,
            "domains": self.domains,
            "roles": self.roles,
            "employers": self.employers,
            "certifications": self.certifications,
            "achievements": self.achievements,
            "notes": self.notes,
        }


@dataclass
class ConfirmedExperience:
    """The assembled confirmed set, plus where each term came from."""

    terms: set[str] = field(default_factory=set)
    sources: dict[str, list[str]] = field(default_factory=dict)

    def is_confirmed(self, term: str) -> bool:
        return normalise(term) in self.terms

    def unconfirmed(self, terms: list[str]) -> list[str]:
        return [t for t in terms if not self.is_confirmed(t)]


def _mentions(text: str, term: str) -> bool:
    """Whole-term match, tolerant of punctuation but not of substrings —
    'go' must not match 'going', 'git' must not match 'github'."""
    return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text, re.I) is not None


def assemble_confirmed(
    profile: CareerProfile,
    documents: list[dict] | None = None,
) -> ConfirmedExperience:
    """Union of everything any career source establishes.

    documents: [{"authority": ..., "title": ..., "text": ...}]

    Every career authority contributes positively. A tailored resume adds
    what it mentions and NEVER removes what it omits — the rule that lets a
    technology confirmed in the profile survive its absence from last week's
    resume.
    """
    confirmed = ConfirmedExperience()

    def add(term: str, source: str) -> None:
        key = normalise(term)
        if not key:
            return
        confirmed.terms.add(key)
        confirmed.sources.setdefault(key, [])
        if source not in confirmed.sources[key]:
            confirmed.sources[key].append(source)

    for term in (
        *profile.technologies,
        *profile.domains,
        *profile.roles,
        *profile.employers,
        *profile.certifications,
    ):
        add(term, AUTHORITY_PROFILE)

    # Documents can only ADD, never remove. The searched vocabulary is the
    # baseline technology/domain list UNION whatever the profile adds — not
    # just the profile's own terms. The master resume is itself a career
    # source (contract §3), so it must be able to establish a technology the
    # profile has not listed yet rather than being silently unable to.
    vocabulary = sorted(
        confirmed.terms
        | {normalise(t) for t in DEFAULT_TECHNOLOGIES}
        | {normalise(d) for d in DEFAULT_DOMAINS}
        | set(ALIASES),
        key=len,
        reverse=True,
    )
    for document in documents or []:
        authority = document.get("authority")
        if authority not in CAREER_AUTHORITIES:
            continue  # a JD is the target, never career evidence
        text = document.get("text") or ""
        label = f"{authority}:{document.get('title') or 'document'}"
        for term in vocabulary:
            if _mentions(text, term):
                add(term, label)

    return confirmed


def detect_role_family(jd_text: str) -> tuple[str, list[str]]:
    """Identify the target role family from the JD and return its emphasis.

    Emphasis shifts; the career does not change.
    """
    text = jd_text.lower()
    scores: dict[str, int] = {}
    titles = {
        "sre": ["site reliability", "sre", "production operations", "production support"],
        "platform": ["platform engineer", "platform engineering", "developer platform"],
        "azure_devops": ["azure devops engineer", "azure devops", "devops engineer"],
        "cloud_operations": ["cloud operations", "cloud ops", "operations engineer"],
        "infrastructure": ["infrastructure engineer", "cloud infrastructure"],
    }
    for family, phrases in titles.items():
        # A title match is worth more than a keyword mention.
        scores[family] = sum(3 for p in phrases if p in text)
    for family, keywords in ROLE_FAMILIES.items():
        scores[family] = scores.get(family, 0) + sum(1 for k in keywords if k in text)

    best = max(scores, key=lambda f: scores[f])
    if scores[best] == 0:
        best = "azure_devops"  # the user's default centre of gravity
    return best, ROLE_FAMILIES[best]
