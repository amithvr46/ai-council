# AI Council — post-V1 roadmap (PROPOSAL, not yet frozen)

**V1 status:** PASS, frozen at `bbbaf82`. 132 deterministic tests. No further
V1 architecture changes or polishing unless normal usage exposes a real defect.

**This document:** proposed sequencing for post-V1 work, prioritised around
Amith's four stated product goals. To be reviewed (Amith + GPT) and frozen
before implementation starts.

---

## Organising principle

The four goals differ enormously in cost and in urgency, and one has a
deadline the others don't: the job search runs Aug–Oct 2026, which makes
resume work time-sensitive in a way Build Mode is not.

So the sequence is **value-per-effort with the deadline weighted in, and cost
controls landing before the phases that can spend money quickly.**

Two rules hold across every phase:

1. **The V1 reliability engine is the foundation and is not redesigned.**
   Every phase extends it.
2. **Every loop has a hard cap enforced in code, and hitting a cap reports
   honestly** rather than silently degrading or retrying. This is the V1
   principle carried forward, not a new one.

---

## Phase 2 — Source Material

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

**Scope**

- File upload: PDF, docx, txt, md, code files
- Text extraction; `documents` table; per-request attachment
- Source material as a first-class evidence kind alongside web and code
- Artifact output: downloadable .md / .docx
- One workflow preset — the resume flow:
  analyse requirements → identify supported experience → rewrite →
  ATS/recruiter/technical review → detect invented claims → correct → final
- **Budget ceilings**: daily/monthly limits, configurable warn threshold and
  hard stop, per-document size/token caps
- **Auto instrumentation**: capture task category and predicted-vs-actual
  disagreement per request (cheap now, and it is what makes Phase 3 possible
  without guessing)

**Effort:** 2–3 build sessions.

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
guessed thresholds — Amith was explicit about this and it is correct. But
"wait for data" only works if the data is deliberately collected. Phase 2
instruments it; Phase 3 builds the router from a few weeks of real usage plus
a golden-set sweep across all three modes (giving comparative quality, cost
and latency per task category).

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

- plan → **user approves** → scaffold → implement in bounded increments →
  test → review
- Work isolated in a git worktree
- Per-phase iteration caps; per-build cost ceiling; projected cost shown
  before a build starts
- **Mandatory human checkpoints between stages** rather than end-to-end
  autonomy — the user approves the plan before implementation and each
  increment before the next
- Kill switch
- No autonomous "keep trying until it works" behaviour, ever

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
| 2 | Daily/monthly ceilings, warn threshold + hard stop; per-document size/token caps; artifact generation capped |
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

## Open decisions (defaults taken; overrule freely)

1. **Phase 2 leads with documents rather than Auto.** Rationale: the Aug–Oct
   job search deadline. If mode-picking friction proves more painful in daily
   use than the resume deadline is valuable, swap Phases 2 and 3 — Auto is
   the smaller build.
2. **Phase 4 is a real phase, not a thin stepping stone.** Rationale: it is
   independently useful for weeks of daily debugging, and the repair loop
   deserves to earn trust at small scale before Build Mode scales it.

## Concurrent with Phase 2

**Use V1 daily.** Not homework — it is what makes Phase 3 real. Every request
is a labelled data point for the Auto router and every star rating is the
quality signal it will be tuned against. A router built from real usage beats
one built from invented thresholds.
