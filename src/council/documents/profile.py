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

# The user speaking directly about their own career: "I have professional
# Harness experience." Authoritative — the person is the primary source on what
# they have done — but kept DISTINCT from document-derived evidence so the
# sources map can answer "who established this?" honestly.
#
# Recorded now purely so provenance exists when Phase 3 captures such
# statements from natural language. No user-facing workflow writes it yet, and
# deliberately so: a durable career fact and a request-only instruction
# ("emphasise AKS", "keep it to two pages") are different things, and telling
# them apart is intent understanding, which belongs with Auto.
AUTHORITY_USER_STATEMENT = "user_statement"

CAREER_AUTHORITIES = (
    AUTHORITY_PROFILE,
    AUTHORITY_USER_STATEMENT,
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
    "cost management", "identity and access management", "artifact management",
    "configuration management",
]

# Technology names only — no employers, no personal detail. Those come from
# ingested career sources at runtime and stay in the local database, which is
# what keeps this file safe to publish.
DEFAULT_TECHNOLOGIES = [
    # cloud platforms
    "azure", "aws",
    # infrastructure as code and configuration
    "terraform", "terraform enterprise", "arm templates", "cloudformation",
    "ansible", "azure cli", "aws cli",
    # delivery
    "azure devops", "jenkins", "harness", "github actions", "gitlab", "git",
    "github", "jfrog artifactory", "maven", "npm",
    # containers
    "docker", "kubernetes", "aks", "eks", "helm", "argo cd", "gitops",
    # observability
    "azure monitor", "log analytics", "kql", "application insights", "splunk",
    "grafana", "prometheus", "cloudwatch",
    # security, identity and governance
    "entra id", "rbac", "managed identity", "key vault", "hashicorp vault",
    "iam", "azure policy",
    # aws services
    "ec2", "rds", "vpc", "elb", "route 53", "cloudfront", "lambda", "s3",
    "elastic beanstalk", "sns",
    # scripting and tooling
    "powershell", "bash", "python", "linux", "jira",
]

# Common ways the same thing is written. Extending this list is routine.
#
# Two kinds of entry live here and both matter:
#   1. technology spellings — "k8s" is Kubernetes
#   2. domain phrasing — a JD says "incident response" where the profile says
#      "incident troubleshooting". Without the mapping the analyser reports a
#      genuine strength as unsupported, and the resume under-claims real work.
#
# Only true synonyms belong here. Adjacent-but-different terms ("reliability",
# "gitops", "developer enablement") are deliberately absent: reporting them
# unsupported is the honest answer, and the profile is extensible if the user
# decides they belong.
ALIASES: dict[str, str] = {
    "azure kubernetes service": "aks",
    "elastic kubernetes service": "eks",
    "k8s": "kubernetes",
    "argocd": "argo cd",
    "argo-cd": "argo cd",
    "gh actions": "github actions",
    "tfe": "terraform enterprise",
    "iac": "infrastructure as code",
    "ado": "azure devops",
    "azure pipelines": "azure devops",
    "log analytics workspace": "log analytics",
    "app insights": "application insights",
    "rca": "root cause analysis",
    "microsoft entra id": "entra id",
    "azure active directory": "entra id",
    "azure ad": "entra id",
    "aad": "entra id",
    "managed identities": "managed identity",
    # NOT "vault" -> "hashicorp vault": "Key Vault" ends in the word Vault, so
    # a bare alias would confirm HashiCorp Vault off an Azure Key Vault
    # mention. Over-confirmation is exactly the failure mode this file exists
    # to prevent.
    "azure key vault": "key vault",
    "artifactory": "jfrog artifactory",
    "arm template": "arm templates",
    "route53": "route 53",
    "amazon s3": "s3",
    "microsoft azure": "azure",
    "amazon web services": "aws",
    # domain phrasing
    "incident response": "incident troubleshooting",
    "production troubleshooting": "incident troubleshooting",
    "troubleshooting": "incident troubleshooting",
    "observability": "monitoring and observability",
    "monitoring": "monitoring and observability",
    "automation": "automation and scripting",
    "scripting": "automation and scripting",
    "security": "security and governance",
    "reusable infrastructure patterns": "infrastructure as code",
    "identity": "identity and access management",
    "access management": "identity and access management",
    "containers": "containers and orchestration",
    "orchestration": "containers and orchestration",
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


# For callers that have genuinely established there are no denials to apply.
#
# Not a convenience alias for None. The point of the empty tuple having a name
# is that `denials=NO_DENIALS` is a claim the author made and a reviewer can
# challenge, whereas an omitted argument is invisible. Anywhere this appears
# outside a test, the question to ask is "how do you know?" — in production
# code the answer should almost always be that the denials were loaded, and
# this constant should not be there at all.
NO_DENIALS: tuple = ()


@dataclass
class Denied:
    """One technology the user has explicitly said they have not used.

    A plain value object with no behaviour: it is carried unchanged from the
    instruction parser to the audit trail, so the answer to "why is this not
    confirmed?" is always the user's own words rather than an inference.
    """

    term: str
    kind: str  # never_used | not_professional | studied_only
    statement: str = ""

    def as_dict(self) -> dict:
        return {"term": self.term, "kind": self.kind, "statement": self.statement}


@dataclass
class ConfirmedExperience:
    """The assembled confirmed set, plus where each term came from.

    THE DENIAL BOUNDARY LIVES HERE, and it is enforced structurally rather
    than by asking callers to check a second thing:

        a denied term is REMOVED from `terms`.

    That matters because `terms` is read directly in several places — the
    prompt's truth set, the document-scanning vocabulary, the JD scanner — and
    a boundary implemented only inside `is_confirmed()` would be bypassed by
    every one of them. Making the denied term absent from the set means there
    is no read path that can see it as experience. `denied` keeps the record
    for audit; `sources` keeps whatever positively claimed it, so the
    contradiction stays visible instead of being erased.
    """

    terms: set[str] = field(default_factory=set)
    sources: dict[str, list[str]] = field(default_factory=dict)
    denied: dict[str, Denied] = field(default_factory=dict)

    def is_confirmed(self, term: str) -> bool:
        key = normalise(term)
        if key in self.denied:
            return False  # redundant by construction, and deliberately kept
        return key in self.terms

    def unconfirmed(self, terms: list[str]) -> list[str]:
        return [t for t in terms if not self.is_confirmed(t)]

    def denial_kind(self, term: str) -> str | None:
        entry = self.denied.get(normalise(term))
        return entry.kind if entry else None

    def is_denied(self, term: str) -> bool:
        return normalise(term) in self.denied

    def contradicted(self) -> list[str]:
        """Denied terms that a positive career source had also established.

        These are the real conflicts: a document says the technology is there,
        the user says it is not. The denial wins for confirmation, but the
        disagreement is reported rather than quietly dropped.
        """
        return sorted(t for t in self.denied if self.sources.get(t))


def mentions(text: str, term: str) -> bool:
    """Whole-term match, tolerant of punctuation but not of substrings —
    'go' must not match 'going', 'git' must not match 'github'."""
    return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text, re.I) is not None


_mentions = mentions  # internal callers


def assemble_confirmed(
    profile: CareerProfile,
    documents: list[dict] | None = None,
    *,
    denials: list,
) -> ConfirmedExperience:
    """Union of everything any career source establishes, minus what the user denies.

    documents: [{"authority": ..., "title": ..., "text": ...}]
    denials:   [Denied(...)] — explicit negative statements by the user.
               REQUIRED and KEYWORD-ONLY. See below.

    Every career authority contributes positively. A tailored resume adds
    what it mentions and NEVER removes what it omits — the rule that lets a
    technology confirmed in the profile survive its absence from last week's
    resume.

    A DENIAL is the one and only thing that subtracts, and it subtracts only
    because it comes from the user rather than from a document. The two rules
    are not in tension: "omission is not negative evidence" is about what a
    document's SILENCE means, and a denial is not silence.

    WHY `denials` HAS NO DEFAULT
    ----------------------------
    It used to default to None, which meant `assemble_confirmed(profile, docs)`
    silently produced a truth set with the denial boundary switched off. Every
    caller in the tree happened to be correct, so nothing was broken — but the
    boundary depended on developers remembering, and a permanent truth boundary
    should not be one forgotten argument away from disappearing.

    Keyword-only rather than merely positional-required, because the word
    "denials" then has to appear at every call site. A reader of any call can
    see whether the boundary was applied without opening this file, and
    `grep -rn 'denials='` enumerates every place the question was answered.

    Callers with genuinely nothing to pass write `denials=NO_DENIALS`, which is
    an assertion rather than an omission — see that constant.
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

    return _apply_denials(confirmed, denials)


def _apply_denials(
    confirmed: ConfirmedExperience, denials: list | None
) -> ConfirmedExperience:
    """The single chokepoint where an explicit user denial takes effect.

    Every path that produces a ConfirmedExperience ends here, so there is one
    place to read, one place to test and no way for a downstream consumer to
    obtain a ConfirmedExperience whose denials were never applied.

    Two things happen, and the second is as important as the first:

      1. the term is removed from `terms`, so no reader can see it as
         experience — not `is_confirmed`, not the prompt truth set, not the
         JD scanner
      2. `sources` is left INTACT. If a career document had established the
         term, that record survives so the contradiction can be reported
         through the conflict mechanism. Deleting it would make the denial
         look uncontested when it is not.
    """
    for denied in denials or []:
        key = normalise(getattr(denied, "term", "") or "")
        if not key:
            continue
        confirmed.terms.discard(key)
        confirmed.denied[key] = Denied(
            term=key,
            kind=getattr(denied, "kind", "never_used"),
            statement=getattr(denied, "statement", ""),
        )
    return confirmed


# Technologies the JD scanner recognises but the career does not claim.
#
# This list exists because a vocabulary built from confirmed experience can
# only ever find what the user already has. To answer "what is this JD asking
# for that I cannot support?" the scanner needs to recognise names that are
# deliberately absent from DEFAULT_TECHNOLOGIES. Nothing here is ever
# confirmable from this list — it only makes a gap visible.
FOREIGN_TECHNOLOGIES = [
    "gcp", "google cloud", "google cloud platform", "gke",
    "google kubernetes engine", "cloud run", "cloud sql", "bigquery",
    "cloud build", "pub/sub", "firestore", "anthos",
    "openshift", "rancher", "nomad", "consul", "istio", "linkerd",
    "chef", "puppet", "saltstack", "pulumi", "cdk", "crossplane",
    "datadog", "new relic", "dynatrace", "sumo logic", "elk", "elasticsearch",
    "opentelemetry", "pagerduty", "opsgenie",
    "circleci", "travis ci", "teamcity", "bamboo", "spinnaker", "flux",
    "kafka", "rabbitmq", "airflow", "databricks", "snowflake",
    "go", "golang", "java", "ruby", "rust", "scala", "typescript",
    "mongodb", "cassandra", "redis", "postgresql", "mysql", "oracle",
    "vmware", "openstack", "cisco", "f5", "palo alto",
]

_JD_ALIASES = {
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "google kubernetes engine": "gke",
    "golang": "go",
}


def scan_jd_technologies(
    jd_text: str, confirmed: ConfirmedExperience
) -> tuple[list[str], list[str]]:
    """Which technologies the JD names, split by whether the career supports them.

    Returns (supported, unsupported). The unsupported half is the point: it is
    the honest answer to "what does this role want that I cannot claim?", and
    it is what stops the generator quietly writing the JD's requirements into
    the resume as if they were experience.
    """
    known = (
        {normalise(t) for t in DEFAULT_TECHNOLOGIES}
        | set(confirmed.terms)
        | set(ALIASES)
        | set(FOREIGN_TECHNOLOGIES)
        | set(_JD_ALIASES)
    )
    supported: list[str] = []
    unsupported: list[str] = []
    for term in sorted(known, key=len, reverse=True):
        if not _mentions(jd_text, term):
            continue
        canonical = _JD_ALIASES.get(term, normalise(term))
        bucket = supported if confirmed.is_confirmed(canonical) else unsupported
        if canonical not in bucket:
            bucket.append(canonical)
    # Drop anything that landed in both because two spellings disagreed;
    # confirmation wins, since a career source established it.
    unsupported = [t for t in unsupported if t not in supported]
    return sorted(supported), sorted(unsupported)


def denial_vocabulary() -> list[str]:
    """Technology names a denial may name, longest first.

    Technologies only. The user's own stack (DEFAULT_TECHNOLOGIES) plus the
    names the JD scanner recognises but the career does not claim
    (FOREIGN_TECHNOLOGIES) — denying GCP has to work even though GCP is not in
    the career, since that is the common case. Aliases are included so
    "I never used Azure Kubernetes Service" denies the same thing as
    "I never used AKS".

    Domain phrases are deliberately excluded: "no automation experience" is a
    far broader claim than a deterministic matcher should act on, and reading
    it narrowly would be worse than not reading it at all.
    """
    technologies = {normalise(t) for t in DEFAULT_TECHNOLOGIES}
    technologies |= {t.lower() for t in FOREIGN_TECHNOLOGIES}
    spellings = set(technologies)
    spellings |= {a for a, canonical in ALIASES.items() if canonical in technologies}
    spellings |= set(_JD_ALIASES)
    return sorted(spellings, key=len, reverse=True)


def normalise_denial_term(term: str) -> str:
    """Canonical name for a denied technology.

    Runs both alias maps, because a denial can name a technology from either
    vocabulary and the two maps canonicalise different halves of it —
    "azure kubernetes service" through ALIASES, "google cloud" through the
    JD aliases. Without both, "I never used Google Cloud" and "I never used
    GCP" would deny two different things.
    """
    key = normalise(term)
    return _JD_ALIASES.get(key, key)


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
