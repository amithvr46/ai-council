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

    def sections(self) -> list[tuple[str, list[str]]]:
        """(label, bullets) per employment and project section, most recent first.

        The unit Amendment B2 and B3 are about. Repetition and inventory framing
        are properties of a section; a check handed one bullet at a time cannot
        see either, however well it is written.

        Roles come first and in the order the draft carries them, which is the
        order the document is written in — most recent first.
        """
        return [
            *((f"{r.employer} — {r.title}", list(r.bullets)) for r in self.roles),
            *((f"project: {p.name}", list(p.bullets)) for p in self.projects),
        ]

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
    #          | truthfulness | tone | section_coherence | seniority
    severity: str = "minor"  # blocking | major | minor
    location: str = ""
    problem: str
    fix: str = ""


# The arc a recent employment or project section should convey BETWEEN its
# bullets (Amendment B2). Not a template every bullet must follow — a checklist
# for what the section as a whole leaves the reader knowing.
STORY_ELEMENTS = (
    "environment",
    "responsibility",
    "implementation",
    "operations",
    "troubleshooting",
    "outcome",
)


class SectionAssessment(BaseModel):
    """One employment or project section judged as a whole (Amendment B2).

    Structured rather than prose, because this is precisely the requirement a
    bullet-by-bullet review cannot meet: a set of individually acceptable
    bullets can still leave a reader unable to picture the work.

    Naming WHICH elements are missing is what makes the finding actionable, and
    what keeps "improve this section" out of the correction pass — a vague
    instruction there is an invitation to invent.
    """

    section: str = ""  # the employer or project this covers
    tells_the_story: bool = True
    missing: list[str] = Field(
        default_factory=list,
        description=f"Which of {', '.join(STORY_ELEMENTS)} the section leaves unclear",
    )
    comment: str = ""


class ReviewReport(BaseModel):
    """A11 + Amendment B. One report, several lenses — not one score.

    A single number would let a strong ATS result mask a bullet that cannot be
    defended in an interview, which is the failure the contract cares most
    about.

    Amendment B adds two readings the original four missed. `sections` judges
    each recent section as a whole. `seniority_expression` asks whether the work
    described reads at the engineer's actual level — the positive half of a rule
    whose negative half, no inflated adjectives, the tone lens already enforced.
    """

    findings: list[ReviewFinding] = Field(default_factory=list)
    ats_relevance: str = ""
    recruiter_scan: str = ""
    technical_credibility: str = ""
    interview_defensibility: str = ""
    # --- Amendment B ---
    sections: list[SectionAssessment] = Field(default_factory=list)
    seniority_expression: str = ""
    would_submit: bool = False

    def blocking(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity in ("blocking", "major")]

    def incoherent_sections(self) -> list[SectionAssessment]:
        return [s for s in self.sections if not s.tells_the_story]
