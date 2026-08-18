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
  need that are NOT yet captured per request:
  - task category (coding / factual / architecture / writing / ...)
  - predicted vs actual disagreement (did Council mode end up agreeing?)
  Capturing these cheaply should be considered when M4 touches the
  pipeline, so router design has data to work with by the time it's built.
- Auto adds a decision step; it must stay cheap (small model or
  heuristic-from-data) and must itself be recorded in the trace like
  every other stage.

## Sequencing

M4 (evidence tools) remains next. Auto mode is the milestone after —
by then there will be real usage data to design the router properly.
