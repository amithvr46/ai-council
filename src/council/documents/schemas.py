"""Structured outputs for the resume workflow (contract Amendment A13).

Every model stage returns a validated object rather than prose. That is not
tidiness: the claim classifier and style checker have to run over individual
bullets mechanically, and you cannot reliably split a free-text resume back
into the bullets that produced it.
"""

from pydantic import BaseModel, Field


class DiscoveredTechnology(BaseModel):
    term: str
    kind: str = "other"  # language | framework | platform | service | tool | protocol


class TechnologyDiscovery(BaseModel):
    """A2 escalation. Note what is absent: nothing about the user.

    The model is asked to identify vocabulary, never to judge experience.
    There is deliberately no field here that could carry "the candidate has
    used this" — the schema itself makes the boundary unrepresentable.
    """

    technologies: list[DiscoveredTechnology] = Field(default_factory=list)


class RoleEmphasis(BaseModel):
    employer: str
    title: str
    dates: str = ""
    keep: bool = True
    bullet_budget: int = Field(default=5, ge=0, le=10)
    emphasise: list[str] = Field(default_factory=list)  # confirmed themes for this role
    rationale: str = ""


class ExperienceSelection(BaseModel):
    """Which parts of the career earn the limited space (A10)."""

    target_summary: str = ""  # what this employer actually cares about
    priority_themes: list[str] = Field(default_factory=list)
    skills_to_foreground: list[str] = Field(default_factory=list)
    deprioritise: list[str] = Field(default_factory=list)
    roles: list[RoleEmphasis] = Field(default_factory=list)


class ResumeRole(BaseModel):
    title: str
    employer: str
    location: str = ""
    dates: str = ""
    bullets: list[str] = Field(min_length=1)


class ResumeProject(BaseModel):
    name: str
    context: str = ""
    bullets: list[str] = Field(default_factory=list)


class ResumeDraft(BaseModel):
    """Required fields are required on purpose.

    With every field optional, an empty object validates — and a model that
    emits `{}` then produces a silently empty resume that looks like a
    successful run. Making the substance mandatory turns that into a parse
    failure the provider retries and the workflow can refuse.
    """

    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    skills: dict[str, list[str]] = Field(min_length=1)  # group -> terms
    roles: list[ResumeRole] = Field(min_length=1)
    projects: list[ResumeProject] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)

    def bullets(self) -> list[tuple[str, str]]:
        """(location, text) for every generated statement, for classification."""
        items: list[tuple[str, str]] = []
        if self.summary:
            items.append(("summary", self.summary))
        for role in self.roles:
            for bullet in role.bullets:
                items.append((f"{role.employer} — {role.title}", bullet))
        for project in self.projects:
            for bullet in project.bullets:
                items.append((f"project: {project.name}", bullet))
        return items


class ReviewFinding(BaseModel):
    lens: str  # ats | recruiter_scan | technical_credibility | interview_defensibility
    #          | truthfulness | tone
    severity: str = "minor"  # blocking | major | minor
    location: str = ""
    problem: str
    fix: str = ""


class ReviewReport(BaseModel):
    """A11. One report, several lenses — not one score.

    A single number would let a strong ATS result mask a bullet that cannot be
    defended in an interview, which is the failure the contract cares most
    about.
    """

    findings: list[ReviewFinding] = Field(default_factory=list)
    ats_relevance: str = ""
    recruiter_scan: str = ""
    technical_credibility: str = ""
    interview_defensibility: str = ""
    would_submit: bool = False

    def blocking(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity in ("blocking", "major")]
