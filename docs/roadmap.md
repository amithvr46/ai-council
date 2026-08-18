# AI Council — post-V1 roadmap (FROZEN 2026-08-18)

**V1 status:** PASS, frozen at `bbbaf82`. 132 deterministic tests. No further
V1 architecture changes or polishing unless normal usage exposes a real defect.

**This document:** FROZEN sequencing for post-V1 work, reviewed and approved
by GPT with three amendments (budget controls promoted to Phase 2A;
data-readiness threshold replacing calendar time as the Auto prerequisite;
Phase 5 checkpoint granularity clarified). No further roadmap loop — changes
require a real defect or a stated change of goals.

---

## Organising principle

The four goals differ enormously in cost and in urgency, and one has a
deadline the others don't: the job search runs Aug–Oct 2026, which makes
resume work time-sensitive in a way Build Mode is not.

So the sequence is **value-per-effort with the deadline weighted in, and cost
controls landing before the phases that can spend money quickly.**

Four rules hold across every phase:

1. **The V1 reliability engine is the foundation and is not redesigned.**
   Every phase extends it.
2. **Every loop has a hard cap enforced in code, and hitting a cap reports
   honestly** rather than silently degrading or retrying. This is the V1
   principle carried forward, not a new one.
3. **Every capability added from this point inherits spending controls
   before it can spend.** Budget controls precede the capability, never
   follow it.
4. **Affordability is checked forward, not backward.** Before beginning any
   iteration, the system checks whether enough budget remains to *reasonably
   complete that iteration* — not merely whether current spend is under the
   ceiling. Work that cannot fit in the remaining budget is never knowingly
   started; the system stops and says so.

---

## Phase 2A — Budget controls (first, before anything that can spend)

**Goal served:** budget as a first-class requirement.

Ships before document ingestion. Nothing in Phase 2B/2C can spend money
until these exist.

**Scope**

- Daily and monthly spend ceilings, configurable
- Warning threshold (notify) and hard stop (refuse) as separate settings
- Forward affordability check: an estimated cost for the requested mode is
  compared against remaining budget *before* the request starts; a request
  that cannot fit is refused with a clear message, not started and killed
  mid-flight
- Spend visibility in the UI: today, this month, remaining, and what the
  ceiling is
- Hard stop is enforced in code at the engine boundary, not in the UI

**Effort:** ~0.5 build session.

---

## Phase 2B / 2C — Source Material

**Goal served:** #2 (documents and resumes).

Upload source material — resume, job description, technical notes, an
existing document, a config file — and get back a finished artifact that has
been checked *against that source* for invented claims.

**Why this is the smallest possible extension:** uploaded documents become
**evidence**. The verifier already classifies claims as SUPPORTED /
UNSUPPORTED / CONTRADICTED against an evidence bundle. Pointing that
machinery at "the user's actual resume" means a fabricated job title is
caught by exactly the mechanism that already catches a fabricated Kubernetes
version. The V1 spec's resume evidence list (original resume, JD, supported
experience, ATS terminology, interview defensibility) becomes real without
new architecture.

**Scope — 2B (ingestion)**

- File upload: PDF, docx, txt, md, code files
- Text extraction; `documents` table; per-request attachment
- Source material as a first-class evidence kind alongside web and code
- Per-document size/token caps

**Scope — 2C (workflow + output)**

- Artifact output: downloadable .docx / .md
- One workflow preset — the resume flow:
  analyse requirements → identify supported experience → rewrite →
  ATS/recruiter/technical review → detect invented claims → correct → final
- **Auto instrumentation**: capture task category and predicted-vs-actual
  disagreement per request (cheap now, and it is what makes Phase 3 possible
  without guessing)

**Acceptance — concrete, not a universal document platform**

- Primary: current resume + Azure DevOps job description → requirements
  analysis → supported-experience mapping → tailored resume →
  unsupported/invented-claim audit → final downloadable DOCX
- Secondary: source notes/document → accurate, reviewed technical
  documentation

File-format edge cases are explicitly out of scope. PDF/DOCX/TXT/MD support
is useful; unusual formats must not be allowed to expand the phase.

**Effort:** 2–3 build sessions (2A + 2B + 2C).

**After Phase 2, AI Council can:** produce a tailored, hallucination-checked
resume for any job description; turn rough notes into documentation,
READMEs, SOPs, architecture docs and reports that have been reviewed for
invented claims and requirement coverage.

---

## Phase 3 — Auto mode

**Goal served:** the UX requirement (ask → decide → answer) and cost control.

`Auto | Quick | Council | Deep`, Auto default. Auto picks the **cheapest path
that can reliably handle the task** and escalates only when justified.

**Why here and not earlier:** the router must be designed from data, not
guessed thresholds. Phase 2 instruments the data; Phase 3 builds the router
from it.

**Prerequisite is a data-readiness threshold, NOT elapsed calendar time.**
Build Auto as soon as representative data exists; if that is sooner, start
sooner. Proposed threshold (adjustable):

- one full golden-set sweep across quick / council / deep, giving comparative
  quality, cost and latency per task category
- real usage spanning at least 5 distinct task categories
- enough rated requests to distinguish mode quality within a category rather
  than across the whole corpus

Learning does not stop when Auto ships — routing decisions and their outcomes
continue feeding the same tables, and the router is retunable.

**Scope**

- Golden-set sweep across quick/council/deep for comparative baselines
- Router using task category, question shape, historical disagreement rate
  per category, user ratings, and cost/latency per mode
- **Budget remaining as a routing input** — a near-limit day biases cheaper
- Manual override preserved; routing decision recorded in the trace like
  every other stage, so a bad route is auditable

**Effort:** 1–2 build sessions.

**After Phase 3:** ask → send → answer. No model-level decisions. Average
cost per request falls because easy questions stop paying council prices.

---

## Phase 4 — Code Workspace

**Goal served:** #3 (code generation and debugging).

A request or bug enters an isolated per-request workspace; code is generated
or inspected, executed, tested, errors read, fixed, retested — under a hard
repair cap — then independently reviewed before the user sees it.

**Relationship to V1:** the evidence layer already executes code as a
one-shot. This extends it to a bounded loop with a small workspace.

**Scope**

- Per-request isolated workspace, multiple files
- Generate/inspect → execute → read errors → fix → retest
- **Hard cap on repair iterations (proposed: 3)**
- Tool-execution cap; per-request cost ceiling checked **before** each
  iteration
- Independent review pass before the result is returned
- Mandatory honest stop on cap: report what was tried and what still fails

**Effort:** 3–4 build sessions.

**After Phase 4:** paste a failing function, a broken Terraform file or an
error log and get back a fix that has actually been run and tested — not one
that merely looks right.

---

## Phase 5 — Build Mode

**Goal served:** #4 (build complete products).

Describe a project; get a plan, a repository, an implementation, tests,
debugging, review and documentation.

**Honest framing:** this is a different product from a verification engine —
an agent harness with filesystem, git, dependency and process management.
Done naively it is a re-implementation of an existing coding agent, and it is
where uncontrolled spend genuinely lives.

**Therefore scoped hard:**

- requirements → plan → **user approves the plan (always mandatory)** →
  bounded implementation → tests/review → **major checkpoint** →
  continue or stop
- Work isolated in a git worktree
- Per-phase iteration caps; per-build cost ceiling; projected cost shown
  before a build starts; forward affordability checked before each increment
- Kill switch
- No autonomous "keep trying until it works" behaviour, ever

**Checkpoint granularity.** Approval of the initial plan is permanently
mandatory. Checkpoints during implementation are *major* — at meaningful
boundaries, not after every small increment. Requiring approval of every
increment would make the user the orchestrator, which defeats the purpose.
Checkpoint frequency becomes policy-controlled as the system earns trust;
cost ceilings, bounded iterations and the kill switch never do.

**Why after Phase 4:** the repair loop is the hard and dangerous part. Phase 4
proves it on one file for pennies; Phase 5 runs it dozens of times across a
project. Scaling an unproven loop is how budgets die.

**Effort:** 5+ build sessions.

**After Phase 5:** describe a small application and get a working, tested,
documented repository — with the user approving each stage and a hard ceiling
on spend.

---

## Budget controls by phase

| Phase | Added |
|---|---|
| V1 (done) | Per-mode call budgets in code; physical API attempt accounting; per-request cost visible; cost stats endpoint |
| **2A (first)** | Daily/monthly ceilings, warn threshold + hard stop; forward affordability check before a request starts; spend visibility |
| 2B/2C | Per-document size/token caps; artifact generation capped |
| 3 | Cost-aware routing (cheapest sufficient path); budget-remaining as routing input; per-category cost targets |
| 4 | Repair-iteration cap; tool-execution cap; per-task ceiling checked before each iteration |
| 5 | Per-build ceiling; per-phase caps; checkpoint gates; kill switch; projected cost before start |

---

## Not to be built yet

- **Specialised councils** (DevOps / Coding / Resume / Research as separate
  councils) — they are prompt presets on the same engine, cheap to add later,
  and multiply maintenance now for no capability gain
- **Additional providers** (Gemini, local models) — pointless until Auto has
  data on which model is better at what
- **Vector DB / RAG** — the evidence layer already covers retrieval
- **Image input, mobile, multi-user auth, fine-tuning, Kubernetes deployment
  of AI Council itself, performance/scaling work** — one user
- **Any redesign of the V1 engine** to accommodate later phases — extend it

---

## Decisions (settled at freeze)

1. **Phase 2 leads with documents rather than Auto** — the Aug–Oct job search
   deadline. Confirmed by review.
2. **Phase 4 is a real standalone phase, not a thin stepping stone** — it is
   independently useful for daily debugging, and the repair loop must earn
   trust at small scale before Build Mode scales it. Confirmed by review.
3. **Budget controls are Phase 2A** — they ship before any capability that
   can spend. Added by review.
4. **Auto's prerequisite is data readiness, not elapsed time.** Added by
   review.
5. **Phase 5 requires mandatory plan approval and major checkpoints**, not
   per-increment approval. Added by review.

## Concurrent with Phase 2

**Use V1 daily.** Not homework — it is what makes Phase 3 real. Every request
is a labelled data point for the Auto router and every star rating is the
quality signal it will be tuned against. A router built from real usage beats
one built from invented thresholds.
