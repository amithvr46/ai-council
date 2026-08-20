"""The resume workflow (contract Amendment A13).

    career sources + JD
      -> JD analysis            (mechanical; one cheap call only if needed)
      -> experience selection   (model)
      -> tailored draft         (model)
      -> multi-lens review      (model, opposite provider)
      -> claim + style checks   (mechanical, no model)
      -> bounded correction     (model, only if anything was found)
      -> DOCX

Two design decisions are worth stating because they are what make the contract
enforceable rather than aspirational.

**The mechanical checks run after the model review, and their verdict is not
advisory.** A model asked "is this bullet truthful?" will usually say yes about
its own house style. Claim classification and style checking are pure functions
over text, so they cannot be talked out of a finding.

**The review runs on the opposite provider from the draft.** A model reviewing
its own writing rates it well. This is the same reason the council pipeline
puts the verifier on the opposite provider.

The user sees the finished resume. Everything below is trace (A12).
"""

import asyncio
from dataclasses import dataclass, field

from council.documents import style
from council.documents.claims import ClaimClass, classify
from council.documents.conflicts import (
    CONFLICT_EXPERIENCE_DENIED,
    Conflict,
    denial_conflicts,
    disputed_subjects,
    find_conflicts,
)
from council.documents.discovery import (
    DISCOVERY_INSTRUCTION,
    DiscoveryCache,
    DiscoveryResult,
    discover,
)
from council.documents.instructions import Instruction
from council.documents.instructions import parse as parse_instruction
from council.documents.mirroring import find_mirroring
from council.documents.profile import (
    CareerProfile,
    ConfirmedExperience,
    Denied,
    assemble_confirmed,
    detect_role_family,
    mentions,
    normalise,
)
from council.documents.schemas import (
    ExperienceSelection,
    ResumeDraft,
    ReviewReport,
    TechnologyDiscovery,
)
from council.documents.support import directly_supported, source_texts
from council.providers.base import ProviderError

# Model calls this workflow may make. Discovery is conditional, correction only
# fires when something was found, so the common path is 3.
MAX_WORKFLOW_CALLS = 5


class GenerationFailed(RuntimeError):
    """The workflow could not produce a usable document.

    Raised rather than returning an empty draft: a resume with no experience
    in it looks like a successful run and is far worse than an error.
    """


@dataclass
class WorkflowTrace:
    stages: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    model_calls: int = 0

    def record(self, stage: str, **detail) -> None:
        self.stages.append({"stage": stage, **detail})

    def as_dict(self) -> dict:
        return {
            "stages": self.stages,
            "cost_usd": round(self.cost_usd, 6),
            "model_calls": self.model_calls,
        }


@dataclass
class JDAnalysis:
    role_family: str
    emphasis: list[str]
    discovery: DiscoveryResult
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def gaps(self) -> list[str]:
        return self.discovery.gaps

    @property
    def match_quality(self) -> str:
        """strong | moderate | weak — computed from what the JD asks for.

        Advisory only (contract §7). A weak match never prevents the artifact
        from being produced; it tells the user what they are walking into. The
        alternative — refusing to generate — would push the system towards
        improving the score, and the only way to improve it is to claim things
        that are not true.
        """
        supported, gaps = len(self.discovery.supported), len(self.gaps)
        if supported + gaps == 0:
            return "moderate"
        ratio = supported / (supported + gaps)
        if ratio >= 0.8:
            return "strong"
        return "moderate" if ratio >= 0.5 else "weak"

    def as_dict(self) -> dict:
        return {
            "role_family": self.role_family,
            "emphasis": self.emphasis,
            "match_quality": self.match_quality,
            **self.discovery.as_dict(),
            "conflicts": [c.as_dict() for c in self.conflicts],
        }


@dataclass
class GeneratedResume:
    draft: ResumeDraft
    analysis: JDAnalysis
    review: ReviewReport | None
    findings: list[dict]
    trace: WorkflowTrace

    def as_dict(self) -> dict:
        return {
            "draft": self.draft.model_dump(),
            "analysis": self.analysis.as_dict(),
            "review": self.review.model_dump() if self.review else None,
            "findings": self.findings,
            "trace": self.trace.as_dict(),
        }


def _merge_denials(stored: list | None, instruction: Instruction) -> list[Denied]:
    """Durable denials plus the ones this request states, this request winning.

    A denial has to bite on the run that states it. "Tailor this for the Azure
    DevOps role — I have never used Harness" must not produce a Harness bullet
    and then start behaving next time; that is a defect the user experiences
    once and stops trusting the system over.

    A positive claim in this request does NOT remove a stored denial here.
    Superseding is a durable change to the record and belongs with the code
    that persists it (`store.apply_instruction_facts`), which runs before this
    and hands back an already-updated `stored` list. Doing it in two places
    would be two chances to disagree.
    """
    merged: dict[str, Denied] = {
        d.term: d for d in (stored or []) if getattr(d, "term", "")
    }
    for term, kind in instruction.denied_terms().items():
        merged[term] = Denied(term=term, kind=kind, statement=instruction.denial_text())
    return sorted(merged.values(), key=lambda d: d.term)


def _career_context(
    profile: CareerProfile,
    confirmed: ConfirmedExperience,
    conflicts: list[Conflict],
) -> str:
    """What the model is allowed to treat as true.

    Disputed facts are named as disputed rather than omitted silently — a model
    told nothing about a date will happily infer one, whereas a model told the
    date is disputed leaves it out.
    """
    lines = [
        "CONFIRMED CAREER CONTEXT (the complete truth set — nothing outside this "
        "is established):",
        f"technologies and domains: {', '.join(sorted(confirmed.terms))}",
    ]
    if profile.roles:
        lines.append(f"roles: {', '.join(profile.roles)}")
    if profile.employers:
        lines.append(f"employers: {', '.join(profile.employers)}")
    if profile.certifications:
        lines.append(f"certifications: {', '.join(profile.certifications)}")
    if profile.achievements:
        lines.append(f"established achievements: {'; '.join(profile.achievements)}")
    if profile.notes:
        lines.append(f"notes: {profile.notes}")
    if confirmed.denied:
        # Denied technologies are already absent from the truth set above, so
        # this line is not what enforces the boundary — the assembled set is.
        # It is here because a model that simply never sees "Harness" may still
        # reach for it off the JD, whereas a model told the user has explicitly
        # denied it will not. Naming it is cheaper than hoping.
        denied = "; ".join(
            f"{d.term} ({d.kind.replace('_', ' ')})"
            for d in sorted(confirmed.denied.values(), key=lambda d: d.term)
        )
        lines.append(
            "EXPLICITLY DENIED BY THE USER — these are NOT experience and must "
            "never appear as skills, bullets or summary claims, no matter how "
            f"strongly the job description asks for them: {denied}"
        )
    material = [c for c in conflicts if c.kind != CONFLICT_EXPERIENCE_DENIED]
    if material:
        disputed = "; ".join(
            f"{c.subject} ({' vs '.join(c.distinct_values)})" for c in material
        )
        lines.append(
            "DISPUTED — career sources disagree on these. Do NOT state them. "
            f"Omit rather than choose: {disputed}"
        )
    return "\n".join(lines)


class ResumeWorkflow:
    """Orchestrates the stages. Providers are injected; nothing here knows
    which vendor is behind `draft_provider` or `review_provider`."""

    _instruction: Instruction = Instruction()

    def _with_preferences(self, context: str) -> str:
        """Append this run's preferences, clearly labelled as preferences.

        They shape presentation only. Naming them as request-only in the prompt
        is what keeps a model from reading "target SRE" as a career fact."""
        prefs = self._instruction.preference_text()
        if not prefs:
            return context
        return (
            f"{context}\n\nREQUEST-ONLY PREFERENCES for this resume (presentation "
            f"guidance, NOT career facts, and never evidence of experience):\n{prefs}"
        )

    def __init__(
        self,
        providers: dict,
        registry,
        *,
        draft_provider: str,
        review_provider: str,
        flagship_models: dict[str, str],
        cheap_models: dict[str, str],
    ):
        self.providers = providers
        self.registry = registry
        self.draft_provider = draft_provider
        self.review_provider = review_provider
        self.flagship_models = flagship_models
        self.cheap_models = cheap_models

    # ---------------------------------------------------------------- calls

    # A full resume draft is a large structured payload — four roles of
    # bullets plus skills, projects and education runs well past the 4096
    # default, and a truncated payload is invalid JSON that fails both
    # attempts. Sized per stage rather than globally so the cheap discovery
    # call stays cheap.
    STAGE_MAX_TOKENS = {
        "technology_discovery": 1024,
        "experience_selection": 4096,
        "resume_draft": 12000,
        "resume_review": 6000,
        "resume_correction": 12000,
    }

    async def _call(self, trace: WorkflowTrace, stage: str, provider_name: str,
                    model: str, messages, schema):
        """Returns the parsed payload, or None when the stage could not run.

        A provider outage degrades the STAGE rather than destroying the
        outcome. Every caller already handles None: discovery falls back to
        mechanical classification, review is skipped, correction keeps the
        reviewed draft. Only the draft itself is essential, and its own None
        path raises GenerationFailed — so the one stage that must not silently
        vanish still cannot.

        Found by a real run: an outage in the optional discovery stage returned
        HTTP 500 and produced no resume at all, which is the worst possible
        trade for an enhancement.
        """
        provider = self.providers[provider_name]
        try:
            response = await provider.generate(
                messages,
                model=model,
                schema=schema,
                max_tokens=self.STAGE_MAX_TOKENS.get(stage, 4096),
            )
        except ProviderError as e:
            trace.record(stage, provider=provider_name, model=model, ok=False, error=str(e))
            return None
        trace.model_calls += 1
        trace.cost_usd += getattr(response, "cost_usd", 0.0) or 0.0
        trace.record(
            stage,
            provider=provider_name,
            model=model,
            ok=response.parsed is not None,
        )
        return response.parsed

    def _prompt(self, name: str) -> str:
        return self.registry.get(name).text

    # -------------------------------------------------------------- stage 1

    async def analyse(
        self,
        jd_text: str,
        profile: CareerProfile,
        documents: list[dict],
        *,
        cache: DiscoveryCache | None = None,
        trace: WorkflowTrace | None = None,
        denials: list | None = None,
    ) -> tuple[JDAnalysis, ConfirmedExperience]:
        """Mechanical, except for one conditional cheap call (A2)."""
        trace = trace or WorkflowTrace()
        confirmed = assemble_confirmed(profile, documents, denials=denials)
        family, emphasis = detect_role_family(jd_text)
        # A denial that contradicts a career source is a real disagreement and
        # goes through the same conflict channel as a disputed date. Unlike a
        # date, its outcome is already decided — the user outranks a document
        # about their own career — so it is recorded to be seen, not resolved.
        conflicts = find_conflicts(documents) + denial_conflicts(confirmed)

        async def ask_model(candidates: list[str]) -> dict:
            # Discovery is an ENHANCEMENT: it widens gap reporting to terms the
            # local vocabulary has never seen. When it cannot run, the resume
            # is still fully writable from mechanical classification, and
            # failing open is safe in the one direction that matters — a
            # missing discovery can only UNDER-report a gap, never manufacture
            # a claim, because only career evidence confirms anything.
            payload = await self._call(
                trace,
                "technology_discovery",
                self.review_provider,
                self.cheap_models[self.review_provider],
                [
                    {"role": "system", "content": DISCOVERY_INSTRUCTION},
                    {"role": "user", "content": "Terms:\n" + "\n".join(candidates)},
                ],
                TechnologyDiscovery,
            )
            if payload is None:
                return {"technologies": [], "unavailable": True}
            return payload.model_dump()

        discovery = await discover(jd_text, confirmed, cache=cache, ask_model=ask_model)
        trace.record(
            "jd_analysis",
            role_family=family,
            supported=len(discovery.supported),
            gaps=discovery.gaps,
            escalated=discovery.escalated,
            conflicts=len(conflicts),
            denied=sorted(confirmed.denied),
        )
        return JDAnalysis(family, emphasis, discovery, conflicts), confirmed

    # ------------------------------------------------------- stages 2 and 3

    async def select(self, jd_text, analysis, profile, confirmed, trace) -> ExperienceSelection:
        context = _career_context(profile, confirmed, analysis.conflicts)
        context = self._with_preferences(context)
        user = (
            f"{context}\n\n"
            f"ROLE FAMILY: {analysis.role_family}\n"
            f"EMPHASIS FOR THIS FAMILY: {', '.join(analysis.emphasis)}\n"
            f"SUPPORTED TECHNOLOGIES THE JD ASKS FOR: "
            f"{', '.join(analysis.discovery.supported) or 'none'}\n"
            f"GAP TECHNOLOGIES — the career does NOT have these, never plan around "
            f"them: {', '.join(analysis.gaps) or 'none'}\n\n"
            f"SOURCE MATERIAL:\n{self._sources_blob}\n\n"
            f"JOB DESCRIPTION:\n{jd_text}"
        )
        plan = await self._call(
            trace,
            "experience_selection",
            self.draft_provider,
            self.flagship_models[self.draft_provider],
            [
                {"role": "system", "content": self._prompt("resume_select")},
                {"role": "user", "content": user},
            ],
            ExperienceSelection,
        )
        return plan or ExperienceSelection()

    async def draft(self, jd_text, analysis, plan, profile, confirmed, trace) -> ResumeDraft:
        context = _career_context(profile, confirmed, analysis.conflicts)
        context = self._with_preferences(context)
        user = (
            f"{context}\n\n"
            f"GAP TECHNOLOGIES — must not appear anywhere, including skills: "
            f"{', '.join(analysis.gaps) or 'none'}\n\n"
            f"SELECTION PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
            f"SOURCE MATERIAL (facts come from here; wording does not have to):\n"
            f"{self._sources_blob}\n\n"
            f"JOB DESCRIPTION:\n{jd_text}"
        )
        draft = await self._call(
            trace,
            "resume_draft",
            self.draft_provider,
            self.flagship_models[self.draft_provider],
            [
                {"role": "system", "content": self._prompt("resume_generate")},
                {"role": "user", "content": user},
            ],
            ResumeDraft,
        )
        # No silent empty resume. A draft that failed to parse must surface as
        # a failure, not as a document with no experience in it — the same rule
        # ingestion follows for an unreadable file.
        if draft is None or not draft.roles:
            raise GenerationFailed(
                "the draft stage returned no usable resume; nothing was written"
            )
        return draft

    # -------------------------------------------------------------- stage 4

    async def review(self, jd_text, analysis, draft, confirmed, trace) -> ReviewReport | None:
        user = (
            f"CONFIRMED CAREER CONTEXT: {', '.join(sorted(confirmed.terms))}\n"
            f"GAP TECHNOLOGIES (must not appear): {', '.join(analysis.gaps) or 'none'}\n\n"
            f"RESUME UNDER REVIEW:\n{draft.model_dump_json(indent=2)}\n\n"
            f"JOB DESCRIPTION:\n{jd_text}"
        )
        return await self._call(
            trace,
            "resume_review",
            self.review_provider,  # opposite provider: no model grades its own work
            self.flagship_models[self.review_provider],
            [
                {"role": "system", "content": self._prompt("resume_review")},
                {"role": "user", "content": user},
            ],
            ReviewReport,
        )

    # ------------------------------------------- stage 5: mechanical checks

    def check(self, draft: ResumeDraft, analysis: JDAnalysis,
              confirmed: ConfirmedExperience,
              sources: list[str] | None = None,
              jd_text: str = "") -> list[dict]:
        """Pure functions over the draft. A model cannot argue with these.

        `sources` lets a Tier 2B or Tier 3 finding be cleared when a career
        source actually says the same thing — without it the user's own real
        projects get flagged as invented.
        """
        findings: list[dict] = []
        sources = sources or []
        gap_terms = {normalise(g) for g in analysis.gaps}

        for location, text in draft.bullets():
            terms = [t for t in confirmed.terms if mentions(text, t)]
            present_gaps = sorted(g for g in gap_terms if mentions(text, g))
            if present_gaps:
                findings.append({
                    "location": location,
                    "text": text,
                    "class": "GAP_TECHNOLOGY",
                    "reasons": [f"uses unsupported technology: {', '.join(present_gaps)}"],
                })
                continue
            finding = classify(
                text,
                confirmed_terms=confirmed.terms,
                candidate_terms=terms,
                supported_facts=directly_supported(text, sources),
            )
            if finding.classification in (
                ClaimClass.UNSUPPORTED_IMPLEMENTATION_CLAIM,
                ClaimClass.FABRICATED_FACT,
            ) and directly_supported(text, sources):
                # A career source asserts substantially this statement, which
                # is exactly the "established by career evidence" exception
                # Tier 2B carries.
                finding = None
            if finding is not None and finding.classification is not (
                ClaimClass.PERMITTED_EXPANSION
            ):
                findings.append({
                    "location": location,
                    "text": text,
                    "class": finding.classification.value,
                    "reasons": finding.reasons,
                })
            violations = style.check(text)
            blocking = set(style.blocking_violations(text))
            for rule_id, hits in violations.items():
                findings.append({
                    "location": location,
                    "text": text,
                    "class": "STYLE_BLOCKING" if rule_id in blocking else "STYLE_ADVISORY",
                    "reasons": [f"{rule_id}: {', '.join(hits)}"],
                })

        # A gap technology hiding in the skills list is the easiest way for one
        # to survive: it is never in a bullet, so bullet-level checks miss it.
        for group, terms in (draft.skills or {}).items():
            for term in terms:
                if normalise(term) in gap_terms:
                    findings.append({
                        "location": f"skills: {group}",
                        "text": term,
                        "class": "GAP_TECHNOLOGY",
                        "reasons": ["unsupported technology listed as a skill"],
                    })

        # Contract §6: emphasising what the JD wants is the job; reproducing
        # its sentences is the JD rewritten in first person.
        if jd_text:
            findings.extend(find_mirroring(draft.bullets(), jd_text))
        return findings

    # -------------------------------------------------------------- stage 6

    async def correct(self, draft, findings, review, trace) -> ResumeDraft:
        blocking = review.blocking() if review else []
        if not findings and not blocking:
            return draft
        user = (
            f"DRAFT:\n{draft.model_dump_json(indent=2)}\n\n"
            f"MECHANICAL VIOLATIONS (not negotiable):\n"
            + "\n".join(
                f"- [{f['class']}] {f['location']}: {f['text']}\n    {'; '.join(f['reasons'])}"
                for f in findings
            )
            + "\n\nREVIEW FINDINGS:\n"
            + "\n".join(
                f"- [{f.severity}/{f.lens}] {f.location}: {f.problem} -> {f.fix}"
                for f in blocking
            )
        )
        corrected = await self._call(
            trace,
            "resume_correction",
            self.draft_provider,
            self.flagship_models[self.draft_provider],
            [
                {"role": "system", "content": self._prompt("resume_correct")},
                {"role": "user", "content": user},
            ],
            ResumeDraft,
        )
        # A correction that fails to parse leaves the reviewed draft in place.
        # Reverting is safe; the surviving findings are reported either way.
        return corrected if corrected is not None and corrected.roles else draft

    # ----------------------------------------------------------------- run

    async def run(
        self,
        jd_text: str,
        profile: CareerProfile,
        documents: list[dict],
        *,
        cache: DiscoveryCache | None = None,
        instruction: str | Instruction | None = None,
        denials: list | None = None,
    ) -> GeneratedResume:
        """`instruction` is one natural-language line from the user. Its positive
        career statements were already turned into a user_statement career source
        by the caller; only the request-only preferences reach the writing stages,
        which is what stops "keep it to 2 pages" becoming a career fact.

        `denials` is the durable set of technologies the user has explicitly
        said they have not used. It is passed in rather than read here so the
        workflow stays free of persistence, but a caller that omits it gets a
        run without the boundary — which is why the API and CLI both go through
        `store.confirmed_experience()`/`store.load_denials()` and never
        assemble the set themselves.

        Denials stated in THIS request are folded in below, so a denial takes
        effect on the very run that states it rather than only the next one.
        """
        trace = WorkflowTrace()
        self._sources_blob = _sources_blob(documents)
        self._instruction = (
            instruction if isinstance(instruction, Instruction)
            else parse_instruction(instruction)
        )
        if self._instruction.preferences:
            trace.record("user_preferences", count=len(self._instruction.preferences))

        effective_denials = _merge_denials(denials, self._instruction)
        if effective_denials:
            trace.record(
                "denials_applied",
                terms=sorted({d.term for d in effective_denials}),
            )

        analysis, confirmed = await self.analyse(
            jd_text, profile, documents, cache=cache, trace=trace,
            denials=effective_denials,
        )
        plan = await self.select(jd_text, analysis, profile, confirmed, trace)
        draft = await self.draft(jd_text, analysis, plan, profile, confirmed, trace)

        # Review and mechanical checks are independent; run them together.
        review_task = asyncio.create_task(
            self.review(jd_text, analysis, draft, confirmed, trace)
        )
        sources = source_texts(documents)
        findings = self.check(draft, analysis, confirmed, sources, jd_text)
        review = await review_task
        trace.record("mechanical_check", findings=len(findings))

        corrected = await self.correct(draft, findings, review, trace)
        # The comma rule is deterministic, so enforce it deterministically
        # rather than hoping the correction pass obeyed it.
        corrected = enforce_style(corrected)
        remaining = self.check(corrected, analysis, confirmed, sources, jd_text)
        trace.record(
            "post_correction_check",
            findings=len(remaining),
            corrected=corrected is not draft,
        )
        return GeneratedResume(corrected, analysis, review, remaining, trace)


def enforce_style(draft: ResumeDraft) -> ResumeDraft:
    """Apply the mechanically-decidable style rules to every generated string."""
    fix = style.enforce_comma_rule
    draft.headline = fix(draft.headline)
    draft.summary = fix(draft.summary)
    draft.skills = {fix(group): [fix(t) for t in terms] for group, terms in draft.skills.items()}
    for role in draft.roles:
        role.bullets = [fix(b) for b in role.bullets]
    for project in draft.projects:
        project.bullets = [fix(b) for b in project.bullets]
    draft.education = [fix(e) for e in draft.education]
    return draft


def _sources_blob(documents: list[dict], limit: int = 24_000) -> str:
    """Career sources only. The JD is passed separately and never here — a JD
    inside the source blob is a JD the model can mistake for evidence."""
    parts = []
    for document in documents:
        if document.get("authority") in (None, "jd"):
            continue
        parts.append(
            f"--- {document.get('authority')}: {document.get('title')} ---\n"
            f"{document.get('text', '')}"
        )
    blob = "\n\n".join(parts)
    return blob[:limit]


__all__ = [
    "MAX_WORKFLOW_CALLS",
    "GenerationFailed",
    "GeneratedResume",
    "enforce_style",
    "JDAnalysis",
    "ResumeWorkflow",
    "WorkflowTrace",
    "disputed_subjects",
]
