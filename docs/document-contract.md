# Document & Resume Product Contract (PERMANENT, frozen 2026-08-18)

Applies to every document/resume capability from Phase 2 onward. Binding on
design, prompts, schemas, tests and documentation. Not reopened per phase.

---

## 1. Primary UX — the user does not orchestrate

**Normal use:** select career profile / resume + provide the JD → Generate →
receive the finished tailored resume.

The system must NOT normally ask the user:

- which model to use, or Quick / Council / Deep
- which keywords to emphasise
- which bullets to replace, or how to order experience
- whether ATS optimisation, recruiter readability or technical review is needed
- whether another model should review, or whether to check again
- whether a bullet should be rewritten

**These are AI Council's decisions.** Analysis, drafting, review, verification
and correction happen internally. Manual controls may exist for advanced use
but must never be required for the normal workflow.

## 2. Objective

Optimise simultaneously for: ATS relevance to the actual JD; recruiter/vendor
5–10 second readability; hiring-manager technical credibility; interview
defensibility; natural experienced-engineer writing; keyword coverage without
stuffing; truthfulness and consistency with the established career; strong use
of limited space.

Goal: maximise probability of shortlist/interview while remaining truthful and
technically defensible. **Do not optimise merely for ATS score.**

## 2A. Role family

The user's legitimate career scope is broader than "Cloud/DevOps Engineer".
These are related role families supported by the same underlying experience,
**not separate invented career paths**:

DevOps Engineer · Azure DevOps Engineer · Cloud DevOps Engineer · Cloud
Engineer · Cloud Operations Engineer · Infrastructure Engineer · Platform
Engineer · Site Reliability Engineer / SRE · Production Operations /
Production SRE · Cloud Infrastructure Engineer · closely related Cloud /
DevOps / Platform / Infrastructure / SRE / Operations roles.

The system identifies the target role family from the JD and **shifts
emphasis, never invents a different career**:

| Target family | Emphasise |
|---|---|
| SRE | production troubleshooting, incident response, observability, reliability, Kubernetes, automation, monitoring, RCA, operational support |
| Platform Engineering | Terraform/IaC, reusable infrastructure patterns, CI/CD, Kubernetes, automation, developer enablement, GitOps, security, platform operations |
| Azure DevOps | Azure, Azure DevOps, Terraform, CI/CD, Harness/Jenkins/GitHub Actions/GitLab where relevant, AKS, scripting, security, deployment automation |
| Cloud Operations | production Azure/AWS operations, monitoring, troubleshooting, incidents, infrastructure changes, access/security, cost management, automation, operational reliability |
| Infrastructure Engineer | cloud infrastructure, Terraform, networking, identity/security, Kubernetes, automation, configuration management, production operations |

## 2B. Confirmed engineering domains

Career baseline: cloud infrastructure; infrastructure as code; CI/CD and
release engineering; containers and orchestration; production operations;
monitoring and observability; incident troubleshooting / RCA; automation and
scripting; security and governance; networking; cost management.

Confirmed technologies include those in the Career Profile and Master Resume:
Azure, AWS, Azure DevOps, Terraform, Terraform Enterprise, Ansible, Docker,
Kubernetes, AKS/EKS, Jenkins, Harness, Argo CD, GitLab, GitHub Actions, Git,
Helm, Splunk, Grafana, Prometheus, Azure Monitor, Log Analytics/KQL,
Application Insights, CloudWatch, PowerShell, Bash, Python — plus supporting
technologies represented in the career sources.

**This list is not permanently exhaustive.** Legitimate experience may be
added later, and the design must accommodate that without rework.

## 3. Career source model

Three distinct sources, with different authority:

- **A. Career Experience Profile** — persistent confirmed information
  accumulated over time: technologies professionally used, engineering
  domains, roles, employers, projects, responsibilities, certifications,
  established achievements/metrics, writing preferences. **This is the
  authority on what the user has done.**
- **B. Master/Career Resume** — broad representation. Strong evidence, not
  necessarily exhaustive.
- **C. Previous tailored resumes** — selective views for particular jobs.

**Critical rule: omission from a tailored resume does not mean lack of
experience.** If Harness is confirmed in the profile but absent from
yesterday's resume because that JD did not need it, a Harness-heavy JD
tomorrow must be able to surface it. Tailored resumes are never treated as
negative evidence.

## 4. Experience generation policy — three tiers

**Tier 1 — CONFIRMED FACT.** Explicitly established in the career profile,
source documents or user-provided information. (Professionally used
Terraform / Harness / AKS; role; employer; known project; certification.)

**Tier 2 — REASONABLE EXPERIENCE EXPANSION (permitted, no approval needed).**
When a technology is confirmed, the system may generate realistic hands-on
engineer-level responsibility wording around it, informed by role, target JD,
confirmed surrounding stack and coherent real-world usage. Must stay
technically coherent and interview-defensible.

*Permitted expansion test (mechanically checkable):* every technology, tool,
platform and role referenced is confirmed in the career profile, AND the
statement contains no hard factual claim (see Tier 3).

**Tier 2B — SPECIFIC IMPLEMENTATION / PROJECT / OWNERSHIP CLAIM (requires
stronger evidence).** Confirmed tools alone do NOT authorise inventing a
specific project. Even with every technology confirmed and no numbers
present, these require direct career support:

- designed a custom Kubernetes operator
- built a bespoke internal platform
- led a major migration
- architected an enterprise-wide framework
- created a named internal system
- led or managed a team
- owned an entire enterprise architecture
- any unusually specific implementation or ownership claim

This is the gap Tier 2 would otherwise leave open: "designed a custom
Kubernetes operator" passes the confirmed-technology test and contains no
number, yet is a fabricated career fact. It is mechanically detectable
(creation/leadership/ownership verbs + singular bespoke artifacts) and is
therefore regression-tested.

**Tier 3 — HARD FACTUAL CLAIM (requires actual support).** Never invent:
percentages; dollar savings; application/server/user counts; team sizes;
production scale; migration quantities; named internal initiatives;
unestablished dates; awards; unestablished certifications; performance
improvement figures; sole architecture/leadership ownership when not
established; any unusually precise career fact.

**Core rule: conservative about facts, proactive about writing.** Do not force
the user to micromanage wording because the system is afraid to formulate a
realistic responsibility. Never manufacture impressive-sounding history to
satisfy a JD.

## 5. JD analysis (internal, before drafting)

Determine: core requirements; preferred requirements; important technologies;
expected responsibilities; likely ATS terminology; what a recruiter scans for
immediately; what a hiring manager cares about; which requirements the career
strongly supports, partially supports, or does not support.

Then select the strongest relevant material. **Do not copy every JD keyword.**

## 6. Space is limited

A tailored resume is not the whole career. For each JD the system decides what
deserves space. Heavy emphasis on Azure DevOps + Harness + Terraform + AKS
means those areas get prominence; less relevant legitimate experience may be
shortened or removed. A different SRE role may give that space to
observability, incident response and reliability instead. Same engineer,
different emphasis.

## 7. Experience section quality

Within each employer, bullets should read as coherent real work, not a keyword
inventory. A logical flow such as scope → infrastructure/platform → CI/CD →
orchestration → production troubleshooting → observability →
security/networking → automation/governance/cost, but not forced when another
order fits the JD better.

Every bullet needs a reason to exist. Prefer concrete engineering activity
("Investigated deployment failures by reviewing rollout status, pod events,
configuration changes and application telemetry") over generic language
("Leveraged Kubernetes to drive operational excellence").

## 8. Human writing is a hard requirement

Applies to ALL professional artifacts: resumes, technical documentation,
architecture documents, READMEs, SOPs, reports, notes.

**Never intentionally produce obvious AI-style writing.** Avoid: generic
assistant phrasing; inflated corporate language; unnecessary adjectives
("leveraged cutting-edge", "spearheaded transformative"); meaningless
"operational excellence" language; repetitive bullet structures; unnatural
keyword stuffing; vague responsibility statements; every bullet polished the
same way; invented impact metrics; excessive buzzwords.

**Prefer:** practical engineering language; real implementation detail;
natural sentence variation; concise explanation; correct terminology;
believable day-to-day work; technically defensible descriptions.

Do not optimise for sounding impressive at the expense of sounding real.

## 9. Permanent style rule

**Never place a comma immediately before the word "and".** Applies to
generated resumes, documentation, notes and other professional writing, unless
preserving quoted or source text requires otherwise.

The writing-preference profile is **extensible** — more preferences will be
added over time and the design must accommodate that without rework.

## 10. Internal council workflow (resume)

```
Career Profile + Master Resume / supporting sources + Target JD
  → JD requirements analysis
  → relevant career experience selection
  → reasonable experience expansion where appropriate
  → draft tailored resume
  → ATS review
  → recruiter 5–10 second review
  → technical hiring-manager review
  → interview-defensibility review
  → claim audit
  → correction if needed
  → final artifact
```

The user normally sees the RESULT, not these stages.

## 11. Output

Normal resume use produces the finished downloadable DOCX plus an optional
concise summary of major tailoring decisions. **No internal model debates,
evidence classifications or orchestration traces in the artifact.** Those
remain available in the advanced trace view.

## 12. Documentation workflow

Same philosophy: source material → determine document type and audience →
organise → draft → technical review → factual/source review → human-tone
review → final artifact. Normal use produces finished work, not an
orchestration exercise.

## 13. Budget behaviour

Operate inside the approved budget architecture. **Do not run every possible
review model because more calls feel safer.** Use the minimum processing
necessary for a reliable result; future Auto routing chooses the cheapest
reliable path. Document/resume workflows have bounded review and correction
passes and must never enter open-ended rewriting loops.

## 14. User questions

Do not interrupt normal generation for low-value clarification. Use career
evidence plus reasonable expansion wherever possible. Ask only when the
missing information is BOTH (a) material to the quality or truthfulness of the
deliverable AND (b) impossible to resolve safely from existing evidence —
e.g. a precise unsupported metric, or a required technology with no
established professional experience. **Never ask the user to approve normal
wording choices.**

## 15. Acceptance standard

Phase 2 is not complete because a DOCX can be produced. The standard is:

> Given the career sources and a real target JD, without micromanaging,
> the user receives a polished tailored resume they would be comfortable
> submitting to a recruiter, vendor, client or hiring manager.

It must pass: ATS relevance; 5–10 second recruiter scan; hiring-manager
credibility; technical defensibility; truthfulness; human experienced-engineer
tone; formatting/readability; no obvious AI-writing patterns.

The system cannot guarantee an interview — hiring decisions are external. The
engineering goal is the strongest truthful and relevant submission possible.

---

## Implementation notes (how this binds on the V1 engine)

**Resolved tension — expansion vs the V1 verifier.** V1's verifier treats any
claim absent from source material as UNSUPPORTED. Applied unmodified to
resumes it would flag every Tier 2 expansion and either strip the resume or
generate approval prompts — violating §1 and §4. Resolution: document-grounded
verification uses the three-tier model, not V1's two-tier one.

- **PERMITTED_EXPANSION** — all referenced technologies/roles confirmed in the
  career profile, no hard factual claim, no specific implementation/ownership
  claim. Not a defect. No correction, no user prompt.
- **UNSUPPORTED_EXPANSION** — references a technology, tool, employer or role
  not confirmed anywhere in the career sources. Correction required.
- **UNSUPPORTED_IMPLEMENTATION_CLAIM** — Tier 2B. Specific project, bespoke
  artifact, leadership or ownership claim without direct career support, even
  when all technologies are confirmed and no number appears. Correction
  required.
- **FABRICATED_FACT** — contains a hard factual claim (Tier 3) without source
  support. Correction required, always.

Both detectors are mechanical and therefore regression-testable rather than
left to prompt adherence: the hard-fact detector matches numerals,
percentages, currency, counts, team sizes, durations, dates and exact-scale
phrasing; the implementation-claim detector matches creation, leadership and
sole-ownership verbs applied to bespoke or named artifacts.

**Negative-evidence rule is structural.** Career sources carry an
`authority` attribute: `profile` (authoritative), `master_resume`
(strong evidence), `tailored_resume` (selective view — **never** usable as
evidence of absence). Enforced in code where the confirmed-technology set is
assembled, not merely stated in a prompt.

**Style rules are a testable profile.** The writing-preference profile is
data, not prose baked into a prompt: an extensible list of rules, each with a
mechanical check where one exists. The comma-before-"and" rule ships as a
regression test over generated artifacts on day one.

**Mechanically testable rules → regression tests.** Comma rule; hard-fact
detection; tailored-resume-is-not-negative-evidence; no orchestration
artifacts in output; bounded review/correction passes; no clarification
prompt on a resolvable question; expansion permitted when technologies are
confirmed; fabrication caught when a number is invented.

---

# Amendment A — Phase 2C requirements (frozen 2026-08-19)

Refinements to the contract above, approved after GPT's Phase 2B adversarial
review returned PASS. These bind on Phase 2C prompts, classifier, workflow and
acceptance tests.

## A1. Phase 2B structural rules — approved, unchanged

- Career sources contribute positively. A tailored resume omitting Harness,
  Jenkins or any established technology never removes it from the Career
  Experience Profile. A tailored resume is a selective representation, not a
  biography.
- A JD is the target, never evidence. A JD requiring GCP and GKE with no
  supporting career evidence yields `GCP → GAP`, `GKE → GAP`. Never
  "JD mentions GCP, therefore the user has GCP".
- Parse failures stay explicit. An unreadable document never silently becomes
  an empty career source.

## A2. Technology discovery — conditional, never confirming

A hand-maintained foreign-technology list goes stale as new tools appear, but
a model call per JD is waste. The flow is:

    JD
      → mechanical known-technology extraction
      → classify known supported/gap technologies locally
      → inspect remaining unclassified technical terms
      → ONLY if meaningful unclassified terms remain: ONE cheap
        structured-output call asking whether they are technologies
      → compare discovered candidates against career evidence mechanically

**The boundary:** the model may DISCOVER a technology from a JD. It may never
CONFIRM that the user has experience with it. Confirmation comes only from the
Career Experience Profile, the master resume, authoritative supporting career
documents, or information the user explicitly provides.

Discovered terms are cached in normalised form so repeat JDs do not pay to
rediscover the same vocabulary.

## A3. Conflicting career sources

Authoritative career sources must not silently overwrite one another on
material hard facts: employer dates, role dates, certification status,
education, exact achievements, specific historical facts.

On a genuine conflict: persist it, manufacture no certainty, and avoid using
the disputed fact until resolved. "Latest document wins" is NOT the policy
unless a future explicit policy establishes it.

This never fires on ordinary Tier 2 wording differences. Only materially
conflicting facts trigger it.

## A4. Facts are grounded; writing is not copied

The single most important 2C principle:

> AI Council has freedom to formulate realistic professional experience. It
> does not have freedom to fabricate career facts.

The system is not a sentence extractor that rearranges bullets already present
in old resumes. Where a technology or domain is established and the target JD
makes it relevant, the Council formulates natural hands-on wording from the
confirmed technologies, confirmed domains, confirmed role context, surrounding
confirmed stack, the JD, ordinary real-world usage of those technologies, and
its own engineering knowledge. The resulting sentence does not need to exist in
any prior resume.

The user must not have to say "yes, I configured this" or "yes, I troubleshot
that" for every normal responsibility. Requiring that would defeat the purpose
of the system.

## A5. Confirmed tools do not confirm every relationship

The other side of A4. Independently confirmed AWS, Terraform, Kubernetes and
Python do NOT establish:

> "Built Kubernetes clusters on AWS using Terraform and automated the entire
> platform through Python."

Every noun is confirmed; the specific historical relationship may be invented.
Classification must therefore ask two questions, not one:

1. Are the technologies confirmed?
2. Is the relationship an ordinary reasonable expansion consistent with the
   role and context, or an invented specific historical implementation?

A bullet is never validated by noun-presence alone.

## A6. Four-level claim policy — interpretation

- **Tier 1 — confirmed fact.** Directly supported: professionally used
  Terraform, Harness, Azure, Kubernetes; employer; role; established
  responsibility, certification, project.
- **Tier 2 — permitted reasonable expansion.** Generated proactively without
  per-sentence approval: CI/CD configuration and support, deployment and
  release support, troubleshooting failed deployments, Terraform plans,
  modules and infrastructure changes, Kubernetes deployment and operations,
  monitoring and alert investigation, production troubleshooting, scripting
  and automation, cloud infrastructure changes, security and governance work,
  observability, operational support, release validation, configuration
  management. These need not exist verbatim in an old resume.
- **Tier 2B — specific implementation / project / relationship claim.**
  Confirmed tools alone do not authorise: a custom Kubernetes operator, a
  bespoke internal platform, leading a major migration, a custom enterprise
  framework, enterprise-wide architecture, a named internal product, leading
  or managing a team, owning an entire architecture, an invented integration
  between several tools, or any unusual implementation not established by
  career evidence. These are historical claims even with no numbers in them.
- **Tier 3 — hard factual claim.** Requires direct support. Never invent
  percentages, dollar savings, application/server/user counts, team sizes,
  dates, migration quantities, production scale, performance improvements,
  named internal initiatives, awards, certifications, business outcomes or
  unusually specific ownership.

## A7. Core resume principle

> Be conservative about facts but proactive about writing.

Ground career facts strictly. Generate professional wording intelligently.
Keep ordinary experience expansion flexible. Keep specific historical and
project claims controlled. Never invent hard facts. Do not become so
conservative that useful sentences are rejected merely because those exact
words are absent from an old resume.

## A8. Real-experience writing is a first-class quality target

Not a grammar pass at the end. The generation stage itself targets the voice of
an experienced engineer describing actual work. Every bullet must pass:

> "Would this sound natural if an experienced Cloud/DevOps/Platform/SRE
> engineer explained this work to a technical hiring manager?"

Avoid: "leveraged cutting-edge", "spearheaded transformative", "drove
operational excellence", corporate impact language, generic assistant phrasing,
keyword stuffing, repetitive structure, every bullet on one formula,
suspiciously polished wording, vague responsibilities, invented impact.

Prefer: practical implementation language, realistic troubleshooting detail,
natural sentence variation, accurate terminology, concise human wording, real
operational context, believable engineer-level responsibility.

Not this:

> "Leveraged Kubernetes to enhance scalability and drive operational
> excellence."

This:

> "Supported Kubernetes deployments by reviewing rollout status, pod events,
> resource usage and configuration changes when releases failed or workloads
> became unhealthy."

The final resume must not look like the JD was pasted into a chatbot.

## A9. Role family drives the writing

Legitimate scope: DevOps Engineering, Azure DevOps, Cloud DevOps, Cloud
Engineering, Infrastructure Engineering, Cloud Infrastructure, Platform
Engineering, SRE, Cloud Operations, Production Operations, and closely related
roles. Same career, different emphasis. The Council identifies the target
family from the JD and shifts emphasis accordingly (see §2A).

## A10. Optimise limited resume space

Do not simply rewrite the master resume. Per JD: what does this employer care
about → which parts of the career prove it → what deserves the space → what to
emphasise, rewrite, combine, shorten or remove → which Tier 2 expansions
improve the match → is every resulting statement technically defensible.

Omission from the tailored output never alters the Career Experience Profile.

## A11. Internal review standard

Before finalising, evaluate: ATS relevance; recruiter 5–10 second readability;
hiring-manager technical credibility; interview defensibility; truthfulness;
experienced-engineer tone; keyword coverage without stuffing; use of limited
space; realistic phrasing; absence of AI-writing patterns.

Do not optimise for ATS score alone. The resume must survive machine screening
and a technical human reading it.

## A12. Minimal user involvement

Normal experience stays: career sources + JD → Generate → finished resume. The
user never orchestrates model choice, mode, keyword selection, bullet
selection, review stages, verifier choice, ATS review, technical review or
rewrite decisions. Ask only when missing information is materially necessary
for truthfulness or quality and cannot be resolved safely from existing career
evidence. Never ask the user to approve ordinary Tier 2 wording.

## A13. Phase 2C workflow

    Career Profile + master resume / supporting sources + JD
      → JD analysis
      → relevant experience selection
      → intelligent Tier 2 experience formulation
      → tailored draft
      → ATS review
      → recruiter scan review
      → technical / hiring-manager review
      → interview-defensibility review
      → claim and relationship classification
      → human-writing / style review
      → bounded correction
      → final submission-ready DOCX

Internal traces stay available for debugging; the normal path exposes the
finished result.

## A14. Acceptance test

The real Caveonix Infrastructure Engineer JD is the first adversarial case. It
is GCP-primary, which makes it a useful hard test. The system must identify
GCP, GKE, Cloud Run and Cloud SQL as gaps, never manufacture GCP experience,
still recognise substantial transferable cloud/infrastructure/DevOps
experience, emphasise confirmed Terraform, infrastructure, Kubernetes, CI/CD,
automation, production operations, security and networking work, generate
realistic engineer-level wording, avoid mirroring the JD, and produce a resume
the user would realistically consider submitting.

The goal is not "match every JD keyword". The goal is the strongest truthful
and technically defensible representation of this career for this opportunity.
