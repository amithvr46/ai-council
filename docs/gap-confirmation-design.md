# One-time gap confirmation — design

**Status: DESIGN ONLY. Nothing implemented. Awaiting approval.**

> USER CONFIRMS WHAT IS TRUE. COUNCIL DECIDES HOW TO WRITE IT.

Ask once about a material gap, remember the answer forever, then write freely.
No adjacency expansion, no opt-in suggestion feature.

---

## 0. A live defect this design depends on

Verified against the current code before designing anything:

```
"I have never used Harness or Jenkins."
  -> instructions.parse()   files it as a CAREER STATEMENT
  -> assemble_confirmed()   confirms {'harness', 'jenkins'}
```

Telling the system you have **never** used something currently **confirms** it.
Two independent causes:

1. `_FIRST_PERSON_CLAIM` matches "I ... used" and ignores the negation.
2. `assemble_confirmed()` scans stored text for vocabulary terms, so the word
   "Harness" confirms Harness regardless of the sentence around it.

The frozen rule *"explicit contradiction is a hard boundary"* is not merely
missing — it is currently inverted. GCP happens to escape this only because it
is not in `DEFAULT_TECHNOLOGIES`; any known technology is affected.

This is a correctness bug independent of the confirmation flow, and the flow
cannot be built on top of it. **It should be fixed first, on its own.**

---

## 1. Detecting a material gap

Deterministic, no model call. Scored per gap term already produced by
`DiscoveryResult.gaps`:

| Signal | Weight |
|---|---|
| Appears in the JD's title or first ~200 characters | +3 |
| Appears on a line under a *required / must have* heading | +2 |
| Each mention after the first | +1 each |
| It is the JD's dominant cloud platform (out-mentions every confirmed platform) | +2 |

Ask only when **score ≥ 3**, and never about more than **2 terms per run**,
most prominent first. A term mentioned once in a "nice to have" list scores 1
and is never raised.

Suppressed entirely when any of these hold:

- already answered before, in either direction
- already confirmed by any career source
- named in this request's instruction (the user already told us)
- the JD is not the dominant input (no JD, no questions)

Net effect on your real JD: **GCP and GKE would be asked once**; Cloud Run and
Cloud SQL score 1 each and stay silent.

## 2. UI — post-hoc, never a form, never blocking

The critical property: **generation never waits for an answer.** The resume is
produced first with the gap correctly excluded, exactly as today. The question
appears *beside the finished result*:

```
  Your resume is ready                              [ Download DOCX ]

  This role leans on GCP. Nothing in your sources mentions it.
  [ Used professionally ]  [ Only studied ]  [ Never used ]
```

Answering "Used professionally" reveals one button — **Regenerate including
GCP** — and nothing else changes. So:

- One-Step stays one step. A user who ignores the question still has their
  resume.
- The question is answered once in a lifetime, not once per application.
- No modal, no wizard, no required field.

At most two chips ever appear. After both are answered the row is gone
permanently.

## 3–5. Persistence, reusing `AUTHORITY_USER_STATEMENT`, and negatives

Three answers, three destinations:

| Answer | Stored as | Effect |
|---|---|---|
| Used professionally | a `user_statement` document — the existing mechanism, unchanged | confirmed permanently, full writing freedom |
| Only studied | `career_profile.studied_only` | may appear in a clearly non-professional skills line; never in a bullet |
| Never used | `career_profile.never_used` | permanently excluded; never asked again |

**Negatives must not be stored as documents.** A document reading "I have never
used GCP" would be scanned for vocabulary and confirm GCP — cause (2) of the
defect above. They belong in structured fields, not prose.

Smallest schema change: two JSON columns on the existing `career_profile`
singleton (migration 0010). No new table, no new store module.

```python
never_used:    list[str]
studied_only:  list[str]
```

**The enforcement point matters more than the storage.** `ConfirmedExperience`
gains a `denied` set, and `is_confirmed()` returns False for any denied term —
so every existing consumer (`check()`, `classify()`, `scan_jd_technologies()`,
the prompt context) inherits the hard boundary with no change of its own. One
place to enforce, one place to test, impossible to bypass by adding a caller.

A denial also **outranks a positive statement**, including a career document:
if a source mentions GCP and the user has said "never used", denial wins and
the conflict surfaces through the existing `source_conflicts` machinery rather
than being silently resolved.

## 6. Interaction with the One-Step request

Additive only. `POST /artifacts/resume` gains one response field:

```json
"pending_confirmations": [
  { "term": "gcp", "prominence": 8,
    "question": "This role leans on GCP. Nothing in your sources mentions it." }
]
```

One new endpoint:

```
POST /career-profile/confirmations   { "term": "gcp", "answer": "professional" }
```

Regeneration is just the existing `POST /artifacts/resume` called again. No new
workflow, no change to the resume engine, no change to routing or
`outcome_kind`.

Instruction handling gains a **negation branch** so "I have never worked on
GCP" is parsed as a denial rather than a claim — the parser half of the defect
in §0.

## 7. Tests

**The defect (independent of everything else):**

1. "I have never used Harness" does not confirm Harness — parser
2. "I have never used Harness" does not confirm Harness — assembly
3. "I only studied GCP" is not professional experience
4. A denial outranks a positive mention in a career document
5. Denial survives re-ingestion of a document mentioning the term

**Prominence:**

6. A dominant platform in the JD title scores as material
7. A single "nice to have" mention never triggers a question
8. At most two questions per run
9. Nothing is asked when the instruction already answers it
10. Nothing is asked about an already-confirmed technology
11. Nothing is asked twice, in either direction

**Persistence and effect:**

12. "Used professionally" → confirmed, and a bullet using it passes `check()`
13. "Never used" → still a gap, still excluded from the DOCX
14. "Only studied" → allowed in a labelled skills line, refused in a bullet
15. Answers survive across runs — the question does not return

**One-Step integrity:**

16. Generation never blocks on an unanswered confirmation
17. A resume is produced with the gap excluded even when questions are pending
18. Regeneration after "used professionally" includes it, written by Council
19. No expansion path exists — an unanswered gap is still never claimed

## 8. Sequencing

Three separable pieces, and I would not ship them together:

**First, alone: the negation defect (§0).** It is a live correctness bug, it
inverts a frozen rule, and it does not depend on any of this. Small: a parser
branch, the `denied` set, and the migration. Worth its own commit and review.

**Second: the Auto evaluation.** Already prepared and waiting on your machine.
It is cheap, and its findings could change routing. Confirmation touches
neither Auto nor routing, so nothing is gained by delaying the eval behind it —
and if the eval surfaces a routing problem, I would rather fix that with no
half-built feature in the tree.

**Third: the confirmation flow itself** (prominence, the two chips, the
endpoint, regeneration).

Rationale for that order: the defect is urgent and independent; the eval is the
last thing standing between us and Phase 3 Core complete; the confirmation flow
is a genuine feature and deserves to land on a clean tree with the eval's
findings already known.

---

## Preserved throughout

- JD is relevance and emphasis, never truth by itself
- absence is not denial
- explicit contradiction is a hard boundary — **fixed, since it currently is not**
- user statements are authoritative
- no silent experience inference
- no inference laundering into the Career Profile

**Nothing implemented. Awaiting approval.**
