# Product requirement: Auto mode (post-M3)

**Status:** recorded, not scheduled. Do not implement with guessed rules.
**Recorded:** 2026-08-18, from Amith.

## The requirement

For normal daily use the user should not have to decide between Quick,
Council and Deep, nor think about which model handles a question. The
default UI becomes:

```
Auto | Quick | Council | Deep        (Auto selected by default)
```

In Auto mode, AI Council determines the processing level itself:
simple/low-risk requests run Quick; important questions escalate to
Council; complex, high-risk, high-uncertainty or verification-heavy
requests escalate to Deep. Manual mode selection remains as an override.

Normal usage: **ask → send → the system decides the path → one final
answer.** The whole point of AI Council is that the user stops making
model-level decisions ("GPT or Claude?", "is this important enough for
Deep?"). The system's job is to make those decisions; manual controls
exist only for deliberate override.

## Design constraints (agreed)

- The Auto router must be designed from data, not arbitrary rules:
  eval results, cost, latency, disagreement rates and task categories.
- The existing per-request records (mode, cost, latency, degraded,
  user_rating, steps) are the raw material. Two inputs the router will
  need that were NOT originally captured per request:
  - task category — **now captured** as `requests.outcome_kind`
    (migration 0008, commit 4d61496). Vocabulary in `council/outcomes.py`;
    `artifacts.kind` uses the same vocabulary, so requests and artifacts
    read as one intent stream.
  - predicted vs actual disagreement (did Council mode end up agreeing?)
    — still not captured.
- Auto adds a decision step; it must stay cheap (small model or
  heuristic-from-data) and must itself be recorded in the trace like
  every other stage.

## Natural-language intent understanding (Auto scope)

The One-Step resume experience is the concrete case that defines this:

> "Update this resume for the attached Azure DevOps JD. I also have
> professional Harness and Argo CD experience. Emphasise AKS and production
> troubleshooting and keep it to 2 pages."

Auto must resolve that single message into four distinct things, because they
have different authority and different lifetimes:

| Input | Meaning | Lifetime |
|---|---|---|
| Career sources | Existing career evidence | Durable |
| JD | Relevance and emphasis target — **never** evidence | This request |
| "I have professional Argo CD experience" | Authoritative career fact | **Durable, eligible to persist** |
| "emphasise AKS", "keep it to 2 pages" | Request-specific preference | This request only |

The third and fourth are the hard part, and conflating them is the failure
mode to design against in both directions:

- Treating a durable career fact as request-only means the user re-states it
  for every future resume — the bookkeeping the product exists to remove.
- Treating a request-only instruction as a durable career fact silently
  corrupts the Career Experience Profile. "Target SRE roles" is not a career
  fact. Neither is "keep it to 2 pages".

So classification is a decision with real consequences, not a formatting
convenience, and **no statement persists blindly**. A durable career fact is
*eligible* to persist; the persistence rules still apply.

**Provenance is already in place.** `AUTHORITY_USER_STATEMENT` exists in
`documents/profile.py` and is a full career authority: it confirms exactly like
a document, contributes positively, never subtracts, and stays distinguishable
in the sources map so "you told us" is never reported as "your resume says so".
Regression tests cover confirmation, additivity and the fact that adding this
authority did not weaken the rule keeping the JD out.

Nothing user-facing writes it yet, deliberately. No `profile-add` command, no
command-heavy UX for what should be one sentence in a request. The capture path
is natural language, and natural language is Auto's job.

The rule that survives all of it:

> Explicit user career statements establish **truth**.
> Council decides **how to write it**.

## Sequencing

M4 (evidence tools) remains next. Auto mode is the milestone after —
by then there will be real usage data to design the router properly.
