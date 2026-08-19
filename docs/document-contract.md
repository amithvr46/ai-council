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
