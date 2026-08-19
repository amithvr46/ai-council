# AI Council — Permanent Product and Engineering Principles

Status: **permanent**. These outrank any individual phase document. A roadmap
item that conflicts with a principle here is wrong, not the principle.

This file describes direction and constraints. It does not authorise
implementation of anything described in it. See §15.

---

## 1. Core product philosophy

> **Outcome orchestration rather than model orchestration.**

AI Council is not "an application that asks two models and compares their
answers." That is an implementation detail of one moment in time.

- **Model orchestration** asks: *which model should answer this?*
- **Outcome orchestration** asks: *what is the user trying to accomplish, and
  what combination of models, tools, evidence, context, workflow, execution
  and verification is required to deliver the finished result?*

Models are infrastructure. The provider set will change — additional vendors,
local and open-weight models, specialised models, retrieval and research
systems. The user should not have to care, and the product's value must
survive the underlying models changing.

The layer AI Council owns is the outcome layer: understanding the desired
result, choosing how to reach it, doing the work, verifying it, and returning
the finished artifact.

## 2. One-Step Solutions

"One-Step Solutions" is the user-facing vision; outcome orchestration is the
philosophy underneath it.

```
user states desired outcome
  -> understand intent
  -> select workflow
  -> select model(s)
  -> retrieve relevant context
  -> use required tools
  -> perform the work
  -> check evidence
  -> test / verify
  -> correct within bounded limits
  -> finished result
```

Worked examples of the shape:

| Input | Finished result |
|---|---|
| Career sources + JD | Submission-ready truthful tailored resume |
| Notes / source material | Professional finished documentation |
| Repository + issue | Diagnosed, corrected and tested code |
| Screenshot + error | Diagnosis plus corrected configuration |
| Product requirement | Planned, implemented, tested, reviewed project |
| Question needing current facts | Evidence-grounded verified answer |

The objective is not better chat. It is *"I need this done"* answered with
bounded work that produces the thing.

## 3. Models are infrastructure, not the product

Product workflows must not couple to a specific vendor. The provider
abstraction exposing `generate()` only — with orchestration roles living in the
engine — exists for this reason and stays.

Future routing may weigh task category, model strengths, measured historical
performance, disagreement probability, evidence requirements, privacy
requirements, latency, cost, remaining budget, local versus cloud execution
and provider availability. The user should be increasingly insulated from all
of it.

## 4. Think forward, do not feature-bloat

Both collaborating models should proactively surface adjacent capabilities
rather than waiting to be asked, especially where a small decision now
prevents meaningful rework later. Every suggestion is classified:

- **BUILD NOW** — high value at current scope, or small and avoids real rework.
- **ROADMAP** — strategically valuable, not worth interrupting the milestone.
- **DEFER / REJECT** — complexity, cost, maintenance or risk exceeds
  demonstrated value.

Judged against: user value, outcome quality, reliability, security, privacy,
hallucination risk, cost, latency, extensibility, scalability, implementation
effort, maintenance burden, auditability, future rework avoided.

Technical interest is not a reason to build something.

## 5. Design for growth without building for it

The system may stay personal, become a serious developer tool or become a
public product. That is undecided, so: do not build enterprise infrastructure
prematurely, and do not close doors unnecessarily.

Credible expansion paths to preserve — **not** current scope: more providers,
more tools, more evidence sources, multimodal input, persistent memory,
project/workspace context, Code Workspace, Build Mode, local models,
specialised workflows, stronger security boundaries, multi-user architecture,
public deployment, observability, billing and usage controls, enterprise
privacy requirements.

## 6. Clean, versioned, auditable engineering

Every meaningful change stays intentional, tested, committed, traceable,
reviewable and reversible. It must remain answerable: what changed, why, in
which commit, proved by which tests, via which migration, under which prompt
version, at what runtime cost, and whether it improved actual outcomes.

Lifecycle:

```
design -> consider adjacent implications -> build -> test
  -> adversarial review -> ONE consolidated correction pass
  -> commit -> freeze -> use -> measure
  -> reopen only on evidence or a deliberate new requirement
  -> versioned improvement
```

Avoided: build, rethink, rebuild, change direction, rebuild, review forever.

**Frozen does not mean immutable.** It means stable, tested and accepted until
evidence or a deliberate requirement justifies a v2.

## 7. Tests are necessary; real outcomes are the product

A passing suite is engineering evidence, not success. Every major workflow also
answers *"would a real user actually use this result?"*

- Resume: would it actually be submitted?
- Code: does the fix run and pass tests?
- Documentation: would an engineer actually send it?
- Evidence: did evidence genuinely improve correctness?
- Auto: did it choose an appropriate quality/cost path?
- Build Mode: did it produce a usable project?

Deterministic correctness **and** real-world output quality. Neither alone.

## 8. Security and privacy are permanent

Not bolted on later. The system already handles personal career information
and documents, and will handle more: screenshots, source code, repositories,
project memory, conversation history, credentials, potentially third-party
data.

Standing requirements: proactively identify weaknesses rather than waiting for
an incident; local storage is not automatically secure; external API calls stay
visible and deliberate; secrets never enter commits; future memory needs
deletion and control mechanisms; future code execution needs isolation matched
to its real risk; public deployment requires a dedicated security review rather
than an assumption that personal-use controls generalise.

## 9. Budgets and bounded execution are permanent

No uncontrolled loops. No "keep trying until it works." No hidden call
explosions. No silent cost escalation.

Every autonomous workflow carries appropriate bounds: model-call limit,
API-attempt limit, retry limit, repair limit, evidence and tool limits, time
limit, per-request cost ceiling, daily and monthly limits, forward
affordability check, and a kill/cancel path.

**As autonomy increases, budget discipline becomes more important, not less.**

## 10. Hallucination and verification philosophy

Model consensus is not truth. Two models can agree and both be wrong. Evidence
outranks model confidence where evidence exists. Repetition across models does
not promote an unsupported statement to fact. Where reliable verification is
impossible, honest uncertainty beats manufactured certainty.

For career and resume work specifically:

- career evidence = **truth boundary**
- JD = **relevance and emphasis target**
- AI Council = **intelligent, experienced writer**

A JD requirement never becomes career experience.

## 11. Collaboration between models

Neither collaborating model is the coder-only, and neither is correct by virtue
of being the reviewer. Either may identify defects, propose architecture
changes, spot product opportunities, challenge assumptions, raise security or
privacy risks, suggest simplifications or propose roadmap items.

Brand identity settles nothing. Disagreement is resolved by reasoning, tests,
real-world output, measured behaviour and the owner's final product decision.

Normally one adversarial review and one consolidated correction pass is enough.
Endless debate between reviewers is a failure mode, not diligence.

## 12. Focus discipline

Long-term vision does not authorise present-tense scope. The current finish
line is tracked in `roadmap.md` and changes only by explicit decision.

## 13. Recorded future items

Revisited and prioritised after Auto Mode, not built on sight: multimodal
expansion, lightweight cross-chat memory, project/workspace continuity,
persistent preferences, Code Workspace, Build Mode, local and open-weight
models, additional providers, production-grade security and privacy, public
product architecture, customer-facing One-Step Solutions.

Cross-chat memory, when it arrives, should retrieve relevant prior context when
useful — not concatenate history into every request.

## 14. Product thesis

> **AI Council — outcome orchestration rather than model orchestration.**
> **One-Step Solutions.**

Models change. Tools change. Workflows expand. The product owns understanding
the desired outcome, choosing how to accomplish it, performing the work,
verifying it and returning the finished result.

## 15. Human authorisation and the autonomy boundary

**Permanent governance requirement.**

No individual model — current or future, local or hosted — has authority to
independently initiate implementation, modify architecture, expand scope, act
on its own recommendation, or make consequential product decisions.

Models **may** autonomously think, analyse, critique, identify risk, suggest
and propose plans. Models **may not** authorise or implement those proposals.
*"We should build X"* is a recommendation, never authorisation to build X.
Explicit approval from the owner is required before implementation.

One deliberate distinction: once an outcome and a bounded workflow are
approved, AI Council **may** autonomously perform the already-approved internal
steps needed to complete it, without asking permission per model call.

> Approved: *"tailor this resume for this JD."*
> Council may then analyse, draft, review, verify, correct within limits and
> produce the DOCX on its own.

But if, during execution, a model concludes that a new subsystem, an
architecture change, an additional provider, a material scope expansion, a
security-impacting change or a materially more expensive approach would be
better — it **stops at that boundary**, presents the proposal and waits.

```
USER              authorises outcomes and boundaries
AI COUNCIL        orchestrates approved execution
INDIVIDUAL MODELS supply intelligence, hold no independent authority
```

All approved autonomous execution remains subject to scope limits, model and
tool budgets, cost ceilings, retry and repair caps, audit logs, tests,
cancellation controls and checkpoints.

Code Workspace and Build Mode must be designed with this boundary built in from
the start, rather than having human authorisation retrofitted once autonomy
already exists.

No mechanism is implemented for this section beyond what current scope already
requires. The principle is recorded now; the enforcement design is surfaced at
the phase that needs it.
