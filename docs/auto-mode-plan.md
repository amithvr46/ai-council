# Phase 3 — Auto Mode: implementation plan

**Status: PROPOSED — NOT APPROVED. No code written.**

Recorded for review per the human authorisation boundary (principles §15).

---

## 1. What Auto is, and what it is not

Auto is **not** a model that guesses quick/council/deep. Buying a model call to
decide how much model to buy is the wrong shape.

Auto is a **decision ladder**: an ordered sequence of rungs, each cheaper than
the next, where the expensive rungs only run when the cheap ones cannot decide.
In v1 the ladder never reaches a model call at all — it costs **zero calls and
zero dollars**.

```
request
  │
  ├─ Rung 0  explicit user mode?        → obey. Auto never overrides a choice.
  ├─ Rung 1  outcome resolution         → deterministic from request shape
  │            workflow outcome  ────────→ its own bounded workflow. Done.
  │            question_answer  ─────────→ continue down the ladder
  ├─ Rung 2  hard constraints           → affordability, provider availability
  ├─ Rung 3  deterministic features     → signals computed from the text
  ├─ Rung 4  historical prior           → measured outcomes for this class
  └─ decision: quick | council | deep
                │
                └─ during execution:
                   Rung 5  bounded escalation  council → deep, at most once,
                           on an OBSERVED factual disagreement
```

### The design principle underneath it

**De-escalation is impossible; escalation is cheap.** Once two candidate calls
are spent they cannot be un-spent, so a static route that guesses "expensive"
wastes money with no recovery. But a route that starts cheap can still be
rescued — *after* the models have actually disagreed.

So: **start at the cheapest defensible mode, and escalate on observation rather
than prediction.**

One consequence has to be stated plainly. Quick mode runs a single model, so it
produces **no disagreement signal** — there is nothing to compare, and therefore
no escalation path out of it. Routing to quick is a real commitment that gives
up the safety net. Quick is therefore chosen only on strong evidence (a measured
near-total agreement rate for that class, or an obviously low-stakes
transformation), never as a default.

Deep, symmetrically, is rarely chosen *statically* — its value is evidence, and
you usually only learn evidence is needed once a factual dispute appears. The
one exception is below.

---

## 2. Routing inputs, rung by rung

### Rung 0 — explicit override (free)

If the user names a mode, that mode runs. Auto is opt-in and never
second-guesses an explicit instruction.

### Rung 1 — outcome resolution (free)

`outcome_kind` decides which bounded workflow runs. Today this is
deterministic from the request shape: `generate-resume` is unambiguously
`resume_tailor`, `/ask` is `question_answer`. Workflow outcomes route to their
own pipeline and skip mode selection entirely — a resume run has no
quick/council/deep axis.

No model call. Natural-language intent capture (one message containing career
sources, a JD, durable career facts and request-only preferences) is designed in
`auto-mode-requirement.md` and is a **later** sub-phase, not v1.

### Rung 2 — hard constraints (free)

- **Affordability** — `spend.estimate_cost` / `check_affordable` already exist.
  A mode that cannot complete inside the remaining budget is removed from the
  candidate set rather than attempted and abandoned.
- **Provider availability** — if a provider is degraded, council and deep lose
  their second opinion. Prefer quick over a council that is council in name
  only, and record that this is why.

### Rung 3 — deterministic text features (free)

Computed with regex and counting. **These are seeds, not permanent rules** —
they are the starting hypothesis to be measured and revised by Rung 4, and the
plan treats them as such.

| Signal | Route toward | Why |
|---|---|---|
| Recency markers — "latest", "current", "as of", "who is now", a live version number | **deep** | The one defensible *static* deep trigger: both models can confidently agree on stale training data. This is exactly the R4 case, and agreement is worthless against it. |
| Checkable-fact shape — named entity + who/when/how many/price/version | council | Worth a cross-check |
| Code fence or stack trace | council | Code answers benefit measurably from a second opinion |
| Low-stakes transformation — "rewrite this", "reformat", "shorten" | quick | Little to disagree about |
| Opinion or creative | quick | No factual boundary to verify |

### Rung 4 — historical prior (free; SQL over rows we already have)

Grouped by `(outcome_kind, feature_bucket)`, computed from past runs:

- **Agreement rate** — how often the combined check returned `agree` /
  `disagreement_type: none`. High agreement means council spent money to
  confirm what one model already said, and quick suffices for that class.
  *This is the measurement the roadmap wanted and could not previously make.*
- **Evidence override rate** — how often `evidence_override` fired. High means
  deep genuinely pays for this class.
- **Mean `user_rating` by mode** — did the expensive mode actually produce a
  better answer, or just a more expensive one?
- **Mean cost and latency by mode.**

**Discipline:** the prior is used only at `n >= MIN_SAMPLES` (proposed: 8) for
that group. Below that, fall through to Rung 3. This mirrors `spend.py`'s
existing `MIN_SAMPLES_FOR_HISTORY = 3`, set higher here because a bad route
costs answer quality, not just estimate accuracy.

### Rung 5 — bounded escalation (during execution)

**Trigger:** council's combined check returns `disagreement_type` of `factual`
or `both`, and there are checkable claims evidence could settle.

This is the highest-value part of Auto, because it is not a prediction. Static
routing must guess before any answer exists; escalation acts on the models
having *actually* disagreed. Money goes exactly where uncertainty is real.

**Bounds — all hard, all tested:**

- At most **one** escalation per request. Never a loop.
- Escalation re-runs the affordability check. If deep is unaffordable, the
  request stays in council and records `escalation_refused_budget`. The answer
  still ships, honestly labelled.
- `disagreement_type: reasoning` does **not** escalate — the existing critique
  round handles reasoning disputes, and evidence cannot settle them.
- On escalation the budget tracker switches to deep's ceiling with calls
  already spent counted against it, so the total stays within
  `MODE_BUDGETS["deep"]`. **Open design item:** confirm `BudgetTracker`
  supports a mid-flight ceiling change, or add it explicitly.

---

## 3. Fallback behaviour

Ordered, entirely deterministic:

1. No usable history → Rung 3 features decide.
2. Features inconclusive → **council**. The middle option, and the only one
   that preserves the escalation path.
3. Council unaffordable → quick, recorded as budget-constrained.
4. Quick unaffordable → the existing 402 refusal. Unchanged.
5. **The router itself raises** → council, log the error, complete the request.

Point 5 is a requirement, not a nicety. Auto is an optimisation, and an
optimisation that can break the product is a bad trade. **Routing must never
become a new failure mode.**

---

## 4. Budget behaviour

- Forward affordability runs against the *chosen* mode before work begins —
  existing mechanism, unchanged.
- Escalation re-checks affordability at the escalation point.
- No path exceeds `MODE_BUDGETS` for the mode it lands in.
- Escalation capped at one per request.
- Every routing and escalation decision is persisted, so spend attributable to
  Auto's choices can be audited after the fact rather than inferred.

Principles §9 says budget discipline matters *more* as autonomy increases.
Auto is the first component that spends money on its own judgement, so its
decisions are auditable by construction.

---

## 5. Instrumentation

**No migration required.** `Step.provider`, `Step.model` and
`Step.prompt_version` are already nullable, so Auto records a zero-cost step:

```
stage         = "routing"
provider      = None,  model = None
cost_usd      = 0,     api_attempts = 0
output        = { chosen, candidates_considered, deciding_rung,
                  features, prior, reason }
```

Escalation records a second step (`stage="routing_escalation"`) carrying the
trigger and the budget decision. Both emit on the SSE stream, so the UI can
show *why* a request was routed as it was.

**`council routing-report`** — a CLI report over those rows: per group, sample
count, agreement rate, evidence-override rate, rating by mode, cost by mode,
and what Auto would choose today. This is the "measure" step principles §7
requires, and it is what turns the Rung 3 heuristics from guesses into
maintained values.

---

## 6. Acceptance tests

Deterministic, against fakes, no API keys:

| # | Test |
|---|---|
| 1 | An explicit mode is always obeyed — Auto never overrides a user's choice |
| 2 | A recency marker routes to deep even when history says "agree" |
| 3 | An unknown class with no history routes to council, not deep |
| 4 | A router exception falls back to council and the request still completes |
| 5 | Budget that cannot afford council → quick, recorded as budget-constrained |
| 6 | Budget that cannot afford quick → 402, unchanged |
| 7 | Factual disagreement escalates council → deep exactly once |
| 8 | Reasoning-only disagreement does **not** escalate |
| 9 | Unaffordable escalation is refused; the answer still returns, labelled |
| 10 | Escalation cannot fire twice |
| 11 | A prior with `n < MIN_SAMPLES` is ignored |
| 12 | A prior with near-total agreement routes to quick |
| 13 | Every routing decision writes a routing step carrying a reason |
| 14 | **Routing adds zero model calls** — call count identical to the static mode |
| 15 | A `resume_tailor` outcome bypasses mode routing entirely |

**Real-outcome test (principles §7).** Run the golden set through Auto and
compare total cost and mean rating against forced-council. Auto succeeds only
if it **cuts cost without cutting quality**. A test count alone does not settle
this.

---

## 7. Expected model-call and cost impact

**Routing itself: 0 calls, $0.** No classifier is bought in v1.

Illustrative arithmetic using current per-mode estimates (quick $0.02,
council $0.08, deep $0.25):

| Effect | Per request | Applies to |
|---|---|---|
| Class routed to quick instead of council | **−$0.06** | Classes with measured near-total agreement |
| Escalation council → deep | **+$0.17** | Only on observed factual disagreement |

If roughly half of `question_answer` runs currently end in agreement and ~10%
show a factual dispute, the net is a saving. **But that mix is unmeasured
today, so this is arithmetic, not a forecast.** The honest claim is that the
plan is instrumented to find out rather than to assert — which is why the
`routing-report` is part of v1 rather than an afterthought.

---

## 8. Sub-phasing

Deliberately ordered so the data-dependent part comes last, because the data
does not exist yet:

- **3A** — router skeleton, Rungs 0–3, instrumentation, `routing-report`.
  Delivers value immediately and starts collecting routing rows.
- **3B** — Rung 5 escalation. The highest-value behaviour; independent of
  history.
- **3C** — Rung 4 historical prior, enabled once real usage has accumulated
  and the report shows the heuristics need correcting.

---

## 9. Explicitly out of scope for Phase 3

Phase 3 is routing and orchestration intelligence. It is not an agent.

- No Code Workspace, no Build Mode, no general autonomy.
- No additional providers.
- No model-based intent classification in v1. The hook is documented; it is
  built only if measurement proves the deterministic features insufficient.
- No natural-language career-fact capture yet — designed in
  `auto-mode-requirement.md`, sequenced after the routing core.

**On the authorisation boundary (§15):** Auto chooses *how thoroughly to
execute a request the user already made*. Escalating council → deep is more
careful execution of the same question — it is not new scope, not a new
subsystem and not a product change. Auto has no authority to do anything the
user did not ask for, and nothing in this plan gives it any.
