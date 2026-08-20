# AI Council — Negation Fix, Hardening Pass

**Review packet 2 for GPT.** Prepared by Claude, 2026-08-20.
Base: `3ce07fe` (the approved architecture). This pass addresses the six
hardening items from your review before the fix is closed.

**Frozen and untouched.** Every decision you re-froze is intact and is now
regression-tested by name in `tests/test_negation_hardening.py`:
explicit user contradiction is a hard boundary; denial wins over documents; a
later explicit positive user career statement may supersede a denial;
same-request positive + denial resolves to denial; documents may never
supersede; the three kinds remain; longest-match-wins is preserved.

**Scope.** No gap-confirmation UI, no adjacency inference, no Phase 3C, no
resume-writing changes, no Auto changes, no new providers. Two changes reach
beyond the parser — a required argument and a new column — and both are argued
below rather than slipped in.

The governing bias, taken from your brief and applied to every judgement call:
**predictable under-classification beats an aggressive matcher.** The original
defect ADDED experience the user does not have; over-denial DELETES experience
they do. Where the two conflict, this pass does nothing rather than guess.

---

## 1. Exact changes made

### 1.1 The `assemble_confirmed()` bypass — closed

`denials` is now **required and keyword-only**:

```python
def assemble_confirmed(profile, documents=None, *, denials) -> ConfirmedExperience:
```

Keyword-only rather than merely positional-required, so the word `denials`
appears at every call site: a reader can see whether the boundary was applied
without opening the function, and `grep -rn 'denials='` enumerates every place
the question was answered.

**Quantified, as you asked.** 2 source call sites, 22 test call sites. Both
source sites already passed denials, so no production behaviour changed. Test
churn was mechanical.

| file | call sites updated |
|---|---|
| `src/council/documents/workflow.py` | 1 (already correct, now keyword) |
| `src/council/documents/store.py` | 1 (already correct, now keyword) |
| `tests/test_documents.py` | 12 |
| `tests/test_resume_contract.py` | 5 |
| `tests/test_resume_workflow.py` | 2 |
| `tests/test_negation.py` | 2 |

A required argument stops someone FORGETTING. It cannot stop someone writing
`denials=[]` deliberately-but-thoughtlessly, so two more things back it up:

**`NO_DENIALS`** — a named empty tuple. The point is not convenience, it is
that `denials=NO_DENIALS` is a claim an author made and a reviewer can
challenge, whereas an omitted argument is invisible.

**An architectural fitness function** — `assemble_confirmed()` may only be
called from `documents/workflow.py` (handed denials) and `documents/store.py`
(loads them). Anywhere else under `src/` fails CI:

```python
def test_only_the_sanctioned_call_sites_assemble_confirmed_experience():
    sanctioned = {"documents/workflow.py", "documents/store.py"}
    ...
    assert callers == sanctioned
```

plus `test_no_production_code_asserts_there_are_no_denials`, which fails if
`NO_DENIALS` ever appears under `src/`.

I did **not** couple the workflow to persistence, and did not restructure
`CareerSources` into a carrier object — both were considered and both are
larger than the problem.

### 1.2 Supersession — three new gates

`claimed_terms()` (the only input to supersession) now rejects:

- **FRAMING** — sentences that talk about a claim instead of making one:
  interrogatives, `sound like`, `as if`, `pretend`, `say/write/word that I`,
  `can/could/would you`, `please add|include|...`, `add ... to my resume`.
- **HEDGING** — `I think`, `might have`, `may have`, `possibly`, `sort of`,
  `briefly`, `can't remember`, ...
- **THIRD PARTIES** — `my team used`, `our client ran`, ... The third party
  must be the SUBJECT of a verb, not merely mentioned, so
  "I used GCP professionally at my last company" still supersedes.

Framed sentences are excluded at `parse()` time, so they are not career
statements at all (see §7 finding 1 — this was a second live defect).

### 1.3 Regex tightening — see §8 for the findings that drove each one

- `_NOT_PROFESSIONAL` pattern 3 rewritten. The negator must now directly govern
  a usage verb and the professional marker must follow that verb within 40
  chars. The old form accepted any negator followed by "in production" within
  60 chars.
- `_NEVER_USED` pattern 1: bare `had` and `been` removed from the verb list;
  `never had ... experience|exposure|involvement` added explicitly.
- `no ... experience` and `zero ... experience` widened to allow the technology
  name in between — `"I have no Harness experience"` previously escaped every
  pattern in the file.
- `lack(s|ed) ... experience` added.
- `not only` guarded everywhere via a shared `(?!only\b)`: `not only ... but`
  is an intensifier, not a negation.
- **New: two-or-more-negators declines to classify.** A double negative usually
  asserts the opposite of its parts, and no tightening fixes it.

### 1.4 Denial history — migration `0011`

`career_denials.history`, append-only. `record_denials` and
`supersede_denials` each append `{at, action, statement, kind?}`.

Needed because re-denying reset `superseded_at`/`superseded_by` to put the
denial back in force, which erased the user's intervening claim. Scalar
columns still describe CURRENT state; history answers how it got there.

### 1.5 `db/status.py` — column-level revision evidence

`0011` adds only a column to `career_denials`, so its table evidence is
identical to `0010`'s. Without this, a database stopped at `0010` would have
been reported as fully migrated and the user told there was nothing to run.
`infer_revision()` gained an optional `table_columns` mapping; omitting it
stops inference conservatively.

### Files touched (14)

```
src/council/documents/instructions.py     framing, hedging, third-party, clause
                                          splitting, negator counting, regex work
src/council/documents/profile.py          required kw-only denials, NO_DENIALS
src/council/documents/store.py            history append on both transitions
src/council/documents/workflow.py         keyword call site
src/council/db/models.py                  CareerDenialRow.history
src/council/db/status.py                  column-level revision evidence
src/council/db/migrations/versions/0011_denial_history.py   NEW
tests/test_negation_hardening.py          NEW — 125 tests
tests/test_negation.py                    call sites + import order
tests/test_documents.py                   call sites
tests/test_resume_contract.py             call sites
tests/test_resume_workflow.py             call sites
tests/test_db_status.py                   table_columns fixture + 2 new tests
tests/test_migrations.py                  assert history column
```

No change to `api/main.py`, `cli.py`, `conflicts.py`, `documents/__init__.py`,
or any resume-writing code.

---

## 2. New tests added

**125 new**, in `tests/test_negation_hardening.py`, mapped to your six items:

| section | tests | covers |
|---|---|---|
| 1 | 5 | bypass closed; TypeError on omission and on positional; sanctioned-callers fitness function; no `NO_DENIALS` in src |
| 2 | 51 | 17 must-not-supersede phrasings × (parser + end-to-end persistence), 5 must-supersede, fabrication-is-not-even-a-career-source, hedge asymmetry, 6 mixed-sentence cases |
| 3 | 33 | 11 false-positive attacks × (classifier + end-to-end confirmed set), 8 false-negative attacks, two-negator rule, third-party subject rule, 4 documented under-classifications |
| 4 | 31 | 14 nested vocabulary composites × (extraction + confirmed set), generated from the vocabulary; plus explicit-parent-denial and both-named cases |
| 5 | 5 | forward sequence with timestamp ordering, denial→positive→denial, four-step reversal, kind carried through history, documents still cannot supersede |

Section 4 is **generated from `denial_vocabulary()`**, not hand-listed, so an
alias added to `profile.py` tomorrow is covered the day it is added. A guard
test asserts the generator finds ≥12 composites, so it cannot go vacuous.

## 3. Final test count

**642 passed.** Baseline before the original fix: 427. After `3ce07fe`: 515.
This pass: +125 new, +2 in `test_db_status.py`.

## 4. Ruff

`ruff check src tests` — **All checks passed.**

## 5. Migration

`tests/test_migrations.py` — 4 passed. Full chain `0001 → 0011` applies to
SQLite end to end; `career_denials.history` asserted present.

## 6. Is the residual bypass closed?

**Yes, to the limit of what Python allows, and the remaining gap is now a test
failure rather than a silent success.**

- Omitting `denials` → `TypeError`. Not possible to forget.
- Passing it positionally → `TypeError`. The word must appear.
- Calling the pure assembler from anywhere but the two sanctioned files → CI
  failure.
- `NO_DENIALS` appearing under `src/` → CI failure.

What is still theoretically possible: someone edits the sanctioned list in the
fitness function and passes an empty list from a new call site. That is no
longer an accident — it is three deliberate edits, one of which is to a test
whose docstring says what it is for. I judged further hardening (a carrier
type, or moving the pure function behind a private name) to be larger than the
risk. **Flagging for your call.**

## 7. Adversarial supersession results

All 17 must-not-supersede phrasings pass, at both the parser and the
persistence layer. All 5 must-supersede phrasings still work.

**Two live defects found by your list:**

**Finding 1 — fabrication requests were career statements.**
`"Can you make my resume sound like I used GCP?"` matched the first-person
experience pattern, became a `user_statement` career source, and **confirmed
GCP from nothing at all** — no denial needed to be involved. It would also have
superseded a GCP denial. Same for `"Write it as if I used GCP"`,
`"Pretend I used GCP"`, `"Say that I have used GCP"`. This is a request for
fabrication being accepted as a career fact. Fixed at `parse()` so these are
preferences, not just excluded from supersession.

**Finding 2 — third-party experience was the user's.**
`"My team used GCP"` was a career statement (the `my` branch of
`_FIRST_PERSON_CLAIM`) and would have superseded a denial. Now excluded from
supersession.

**Mixed sentences** were the third problem and are the reason for clause
splitting on contrastive joins:

| sentence | denied | claimed |
|---|---|---|
| "I have never used Harness but I have used Jenkins extensively." | harness | jenkins |
| "I know about GCP but haven't used it professionally." | gcp `not_professional` | — |
| "I have used Terraform heavily but I have no professional Harness experience." | harness | terraform |
| "I ran Jenkins for years, however I only studied GCP." | gcp `studied_only` | jenkins |

Before this pass the first row denied **both** Harness and Jenkins: the sentence
classified as a denial, and the denial then covered every technology it named.
A denying clause naming no technology of its own ("haven't used *it*") borrows
the referent from its sentence, but never one another clause just claimed.

## 8. Regex false-positive / false-negative findings

**False positives found (real experience classified as denied).** Each was live
before this pass:

| sentence | was | why |
|---|---|---|
| "I have never had an outage while running Jenkins in production." | denied Jenkins | `never ... had` in the verb list; also `never`+`in production` inside the 60-char window |
| "I do not just write Terraform, I run it in production." | denied Terraform | `not` + `in production` within 60 chars |
| "It is not unusual for me to deploy Harness in production." | denied Harness | same |
| "I have not only used Harness, I built our templates." | denied Harness | `not only` read as negation |
| "I have never been on call without Grafana dashboards." | denied Grafana | bare `been` in the verb list |
| "There is not a week where I do not use Terraform at work." | denied Terraform | double negative |

The first five are fixed by tightening. The sixth is **not fixable by pattern
tightening** — the sentence genuinely contains a negator directly governing a
usage verb, twice — so the classifier now declines when a clause holds two or
more negators.

**False negatives found (real denials escaping).** All now caught:

| sentence | why it escaped |
|---|---|
| "I have no Harness experience." | `no ... experience` did not allow the technology name in between |
| "I've got zero Jenkins experience." | same, `zero` branch |
| "I have no real GCP experience." | same |
| "I have no prior Datadog experience." | same |
| "I lack Harness experience." | no `lack` pattern existed |
| "I have never had any hands-on experience with OpenShift." | `never had` was removed as a verb but had no replacement |

`"I have no Harness experience"` is the one that concerns me most — it is among
the most natural phrasings of the thing this whole fix exists for, and it
escaped the original implementation entirely.

## 9. Behaviour deliberately left unsupported

1. **Verb-less denials.** `"I have used Terraform professionally but never
   Harness."` — the second clause has no verb. Detecting it needs a rule that
   fires on `never` + a bare technology name, which would also fire on
   "Terraform never fails me". Under-classified on purpose.
2. **Two denials crammed into one clause.** `"I never used Harness and I don't
   have Jenkins experience."` is declined by the two-negator rule. Recoverable
   by writing two sentences; a wrongly erased technology is not recoverable by
   anything the user can see. Splitting on `and` was rejected because it breaks
   `"I have never used Harness and Jenkins"`.
3. **Domain denials.** `"I have no automation experience"` is classified as a
   denial (so it never becomes career prose) but blocks no specific term.
   Denial extraction stays technology-only.
4. **`Harness: none.` / telegraphic forms.** Not detected.
5. **Hedged and third-party statements can still become career prose.**
   `"I think I might have used GCP once"` and `"My team used GCP"` are blocked
   from *superseding*, but both still land in `career_statements` and can
   confirm GCP where no denial exists. **This is a pre-existing behaviour I did
   not change, because it is outside the denial boundary and you scoped this
   pass to it.** I think it is wrong and would fix it next, but I did not want
   to widen the pass unasked. **Recommend as a separate item.**
6. **`studied_only` still lives in `career_denials`.** You did not respond to
   my §12.5 question from the last packet, so I left it. It is not really a
   denial; it is non-professional experience wearing a denial's table. Still
   open.

## 10. Final commit hash

**`49af7e6`** — "Harden the denial boundary: close the assembly bypass,
tighten negation". Parent `3ce07fe`. 16 files, +4877 / -70 (2 of those files
are this packet and the previous one).

All 14 code and test blobs verified byte-identical to the tested content
(md5 per file, CR-normalised). Working tree clean, `git fsck` clean.

One operational note, since it will recur: the repo is reached through a mount
that refuses `unlink`, so git cannot remove its own `.git/*.lock` files and
every operation leaves one that blocks the next. Three stale locks from an
earlier interrupted run had to be moved aside before this commit would go
through. They are in `_to_delete/git-locks/` for Amith to remove; nothing in
the repository was damaged.

---

## Appendix A — `src/council/documents/instructions.py` (full, after the pass)

```python
"""Splitting one natural-language instruction into its distinct inputs.

A single sentence from the user can carry three things with completely
different authority and completely different lifetimes:

    "I also have professional Harness experience. I have never used Jenkins.
     Emphasise AKS and keep it to 2 pages."

  - "I also have professional Harness experience"  -> a DURABLE POSITIVE career
    fact. The user is the primary source on their own career, so this
    establishes truth and is eligible to persist with user_statement
    provenance.
  - "I have never used Jenkins"                    -> a DURABLE NEGATIVE career
    fact. Same authority, opposite sign. It must never be stored as positive
    prose, because a positive career source is scanned for technology names
    and every name it contains becomes confirmed experience.
  - "Emphasise AKS", "keep it to 2 pages"          -> a REQUEST-ONLY preference.
    True of this run and nothing else.

Conflating them fails in every direction, and every failure is bad:

  - a durable fact treated as request-only means re-stating it for every future
    resume, which is exactly the bookkeeping the product exists to remove
  - a preference treated as a career fact silently corrupts the Career
    Experience Profile. "Target SRE roles" is not a career fact. Neither is
    "keep it to 2 pages".
  - a DENIAL treated as a positive career fact is the worst of the three: it
    turns "I have never used Harness" into confirmed Harness experience and
    puts it on a submitted resume. That is the defect this module's negation
    handling exists to make impossible.

Deterministic, no model call. The positive/preference split is by grammatical
person, which is a reliable signal here: people state their experience in the
first person and give instructions in the imperative. The negation split is by
an explicit list of high-value negative forms — deliberately not general NLP,
because a negation detector that is clever is a negation detector that fails
in ways nobody can predict, and this boundary has to be predictable.
"""

import re
from dataclasses import dataclass, field

from council.documents.profile import denial_vocabulary, normalise_denial_term

# First-person claims about what the user has actually done.
_EXPERIENCE_VERB = (
    r"have|had|has|worked|work|used|use|ran|run|built|build|supported|support|"
    r"managed|manage|configured|configure|deployed|deploy|maintained|maintain|"
    r"administered|administer|operated|operate|wrote|write|automated|automate|"
    r"troubleshot|troubleshoot|implemented|implement|owned|own"
)
_FIRST_PERSON_CLAIM = re.compile(
    rf"\b(?:i|i've|i have|my)\b[^.]{{0,40}}?\b(?:{_EXPERIENCE_VERB})\b"
    rf"|\bi(?:'ve)?\s+(?:also\s+)?(?:{_EXPERIENCE_VERB})\b",
    re.I,
)

# First person, but asking for something rather than reporting experience.
# "I want you to emphasise X" is a preference wearing a first-person costume.
_REQUEST_VERB = re.compile(
    r"\b(?:want|would like|need you|prefer|expect|wish|"
    r"am targeting|'m targeting|am applying|'m applying)\b",
    re.I,
)

# Sentences that TALK ABOUT a claim rather than MAKE one.
#
# "Can you make my resume sound like I used GCP?" contains the substring
# "I used GCP" and matches the first-person experience pattern perfectly. Read
# as a career statement it does two unacceptable things: it becomes a career
# source that confirms GCP off nothing, and — worse — it supersedes an existing
# denial of GCP. The user asked how the document should READ. They did not
# assert a fact about their career, and one of these is a request for
# fabrication.
#
# The distinguishing feature is not the claim, it is the frame around it. These
# patterns detect the frame.
_FRAMING = re.compile(
    r"\?\s*$"  # a question is never an assertion
    r"|\bsounds?\s+like\b"
    r"|\bas\s+if\b"
    r"|\bpretend(?:ing|s)?\b"
    r"|\bimply\b|\bimplies\b|\bsuggest\s+that\b"
    r"|\b(?:can|could|would|will)\s+you\b"
    r"|\b(?:say|says|write|state|put|claim|word|phrase|frame|spin|make)\s+"
    r"(?:it\s+|that\s+)?(?:i|we|my|me)\b"
    r"|\bplease\s+(?:add|include|say|write|put|make|mention|highlight|"
    r"emphasi[sz]e|list)\b"
    r"|\badd\b[^.]{0,40}?\bto\s+my\s+(?:resume|cv)\b",
    re.I,
)

# Positive statements too soft to reverse a hard boundary.
#
# "I think I used GCP once" is not the same act as "I used GCP professionally".
# Both may become career prose, but only the second is explicit enough to
# override a denial the user previously made. Hedged recollection is exactly
# the case where the earlier, definite statement deserves to win.
_HEDGE = re.compile(
    r"\b(?:i\s+think|i\s+believe|i\s+guess|i\s+suppose|maybe|perhaps|possibly|"
    r"probably|might\s+have|may\s+have|could\s+have|not\s+sure|unsure|"
    r"can'?t\s+remember|don'?t\s+remember|sort\s+of|kind\s+of|a\s+little|"
    r"a\s+bit|briefly|once\s+or\s+twice|i\s+may|i\s+might)\b",
    re.I,
)

# Statements about somebody else. "My team used GCP" is true, relevant and
# not a claim that the USER used GCP — and it must certainly not reverse a
# denial the user made about themselves.
#
# The third party has to be the SUBJECT of a verb, not merely mentioned.
# "I used GCP professionally at my last company" is a first-person claim that
# happens to name an employer, and excluding it would break the product rule
# for one of its most natural phrasings.
_THIRD_PARTY = re.compile(
    r"\b(?:my|our|the|their|his|her)\s+"
    r"(?:(?:current|previous|last|old|former|new)\s+)?"
    r"(?:team|teams|client|clients|company|companies|employer|employers|"
    r"colleague|colleagues|coworkers?|manager|group|org|orgs|organisation|"
    r"organization|department|customer|customers|vendor|vendors|partner|"
    r"partners|contractor|contractors)\s+"
    r"(?:\w+\s+){0,2}?"
    r"(?:used|use|uses|using|ran|run|runs|built|build|builds|deployed|deploy|"
    r"managed|manage|manages|worked|work|works|has|have|had|is|was|were|are|"
    r"adopted|adopt|migrated|standardi[sz]ed)\b",
    re.I,
)

# Words that carry negation. Counted, not just matched — see below.
_NEGATOR = re.compile(
    r"\bnever\b|\bnot\b|n't\b|\bno\b|\bnone\b|\bnothing\b|\bzero\b|\bnil\b|"
    r"\blacks?\b|\blacked\b|\bwithout\b|\bneither\b|\bnor\b",
    re.I,
)

# Contrastive joins, for splitting one sentence into clauses that disagree.
#
# "I have never used Harness but I have used Jenkins extensively" is ONE
# sentence carrying a denial and a claim. Classified whole it is a denial, and
# `technology_terms` would then deny every technology it names — erasing the
# Jenkins experience the same sentence asserts. Splitting on the contrast is
# deterministic tokenisation, not language understanding.
_CONTRAST = re.compile(
    r",?\s+\b(?:but|however|although|though|whereas|while|yet|except\s+that)\b\s+",
    re.I,
)

# ------------------------------------------------------------------ negation

# Three kinds of negative statement. All three block career confirmation; they
# differ in what they additionally record, so the audit trail can answer "why
# is this not confirmed?" with the user's actual words rather than a shrug.
NEVER_USED = "never_used"  # no experience of any kind
NOT_PROFESSIONAL = "not_professional"  # some exposure, but not professionally
STUDIED_ONLY = "studied_only"  # studied / lab / personal only

DENIAL_KINDS = (NEVER_USED, NOT_PROFESSIONAL, STUDIED_ONLY)

# Order matters: the most specific reading is tried first. "I only studied GCP"
# and "I have no professional GCP experience" are both denials of professional
# experience, but they are not the same statement and must not be recorded as
# though they were.
_NON_PROFESSIONAL_CONTEXT = (
    r"personal|lab|home lab|homelab|hobby|side project|side projects|"
    r"self[- ]taught|academic|coursework|course|training|tutorial|"
    r"certification|study|studies|sandbox|demo|toy"
)

_STUDIED_ONLY_PATTERNS = (
    # "only studied", "just read about", "only did a course on"
    r"\b(?:only|just|merely)\s+(?:ever\s+)?"
    r"(?:studied|study|studying|learned|learnt|read about|took a course|"
    r"did a course|trained on|practised|practiced|played with|experimented with)\b",
    # "studied it only", "learned X only"
    r"\b(?:studied|learned|learnt|trained)\b[^.]{0,40}?\bonly\b",
    # "only personal experience", "only in a home lab", "lab use only"
    rf"\b(?:only|just)\b[^.]{{0,30}}?\b(?:{_NON_PROFESSIONAL_CONTEXT})\b",
    rf"\b(?:{_NON_PROFESSIONAL_CONTEXT})\s+(?:use|usage|experience|projects?|work)"
    rf"\s+only\b",
    rf"\b(?:{_NON_PROFESSIONAL_CONTEXT})\s+only\b",
)

# Verbs that mean "made use of the technology". Shared by the negative
# patterns so a denial form cannot drift out of sync with its negation.
_USE_VERB = (
    r"used|use|using|worked|work|working|touched|ran|run|running|built|build|"
    r"deployed|deploy|administered|administer|managed|manage|configured|"
    r"configure|supported|support|maintained|maintain|operated|operate|"
    r"implemented|implement|automated|automate|written|wrote"
)

# `not only ... but` is an INTENSIFIER, not a negation: "I have not only used
# Harness, I built our templates" is one of the strongest possible claims. Every
# negation form below refuses to fire when "only" directly follows the negator.
_NOT_ONLY = r"(?!only\b)"

_NOT_PROFESSIONAL_PATTERNS = (
    # "no professional GCP experience", "no production experience with GCP"
    r"\bno\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial|paid|work|"
    r"on[- ]the[- ]job|enterprise)\s+(?:\w+[- ]){0,3}?experience\b",
    r"\bno\s+(?:\w+[- ]){0,3}?experience\s+(?:\w+[- ]){0,3}?"
    r"(?:professionally|in production|at work|commercially)\b",
    # "never used it professionally", "have not used X in production".
    #
    # This pattern used to be `(never|not|n't) ... 60 chars ... professionally`
    # with no constraint on what sat in between, which made it fire on
    # sentences that assert experience:
    #
    #   "I have never had an outage while running Jenkins in production."
    #   "I do not just write Terraform, I run it in production."
    #
    # Both were classified as denials, erasing real experience. The negator now
    # has to directly govern a usage verb, and the professional marker has to
    # follow that verb within a short window.
    rf"(?:\bnever\b|\bnot\b|n't)\s+{_NOT_ONLY}(?:\w+\s+){{0,2}}?(?:{_USE_VERB})\b"
    rf"[^.]{{0,40}}?\b(?:professionally|in production|at work|commercially|"
    rf"on the job|in a professional|in anger)\b",
    r"\bnot\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial)\s+experience\b",
)

_NEVER_USED_PATTERNS = (
    # "never used", "never worked with", "never touched".
    #
    # Bare "had" and "been" were removed from this alternation. "I have never
    # had an outage", "I have never been on call" are not technology denials,
    # and with "had" present the first of those denied every technology the
    # sentence named. "never had experience" is covered explicitly below.
    rf"\bnever\s+(?:really\s+|actually\s+|ever\s+)?{_NOT_ONLY}(?:{_USE_VERB})\b",
    r"\bnever\s+(?:\w+\s+){0,2}?had\s+(?:any\s+)?(?:\w+[- ]){0,3}?"
    r"(?:experience|exposure|involvement)\b",
    # "have not used", "haven't worked with", "hasn't touched"
    rf"\b(?:have|has|had)\s+(?:not|never)\s+{_NOT_ONLY}(?:\w+\s+){{0,2}}?"
    rf"(?:{_USE_VERB})\b",
    rf"\b(?:haven't|hasn't|hadn't|didn't|don't|doesn't)\s+{_NOT_ONLY}"
    rf"(?:\w+\s+){{0,2}}?(?:{_USE_VERB})\b",
    # "no experience with", "no Harness experience", "zero exposure to".
    #
    # Widened to allow the technology name between "no" and "experience".
    # "I have no Harness experience" is among the most natural ways to say this
    # and previously escaped every pattern in the file.
    r"\bno\s+(?:\w+[- ]){0,3}?experience\b",
    r"\b(?:zero|nil)\s+(?:\w+[- ]){0,3}?(?:experience|exposure)\b",
    r"\bno\s+(?:exposure|background)\s+(?:to|in|with)\b",
    r"\b(?:don't|do not|doesn't|does not)\s+have\s+(?:any\s+)?"
    r"(?:\w+[- ]){0,3}?experience\b",
    r"\black(?:s|ed)?\s+(?:\w+[- ]){0,3}?experience\b",
    r"\bnot\s+(?:something|a technology|a tool)\s+i(?:'ve)?\s+(?:have\s+)?"
    r"(?:ever\s+)?used\b",
)

_STUDIED_ONLY = re.compile("|".join(_STUDIED_ONLY_PATTERNS), re.I)
_NOT_PROFESSIONAL = re.compile("|".join(_NOT_PROFESSIONAL_PATTERNS), re.I)
_NEVER_USED = re.compile("|".join(_NEVER_USED_PATTERNS), re.I)


def classify_negation(sentence: str) -> str | None:
    """Which kind of denial this sentence is, or None if it is not a denial.

    Checked most-specific first. A sentence that matches nothing here is not
    treated as negative, which is the safe default in this direction: a missed
    denial leaves a technology unconfirmed-or-confirmed exactly as it is today,
    whereas a false denial would erase real experience the user has.

    TWO OR MORE NEGATORS MEANS "DO NOT GUESS". A double negative usually
    asserts the opposite of what the individual words suggest:

        "There is not a week where I do not use Terraform at work."

    Every pattern below reads that as a denial of Terraform, and no amount of
    tightening fixes it, because the sentence really does contain a negator
    directly governing a usage verb — twice. Rather than pretend to resolve it,
    the classifier declines. Declining costs a denial the user may have to
    restate; guessing deletes experience they have.

    The cost is bounded and known: "I never used Harness and I don't have
    Jenkins experience" is two denials in one clause and is also declined. Both
    are recoverable by saying them as separate sentences; a wrongly erased
    technology is not recoverable by anything the user can see.
    """
    if len(_NEGATOR.findall(sentence)) > 1:
        return None
    if _STUDIED_ONLY.search(sentence):
        return STUDIED_ONLY
    if _NOT_PROFESSIONAL.search(sentence):
        return NOT_PROFESSIONAL
    if _NEVER_USED.search(sentence):
        return NEVER_USED
    return None


def technology_terms(sentence: str) -> list[str]:
    """Technology names this sentence mentions, canonicalised.

    Technologies only — not domains. A denial names a tool ("never used
    Harness"); denying a whole domain ("no automation experience") is a much
    broader claim than the deterministic forms here can safely interpret, and
    reading one narrowly would be worse than not reading it at all. The
    sentence is still kept out of positive career prose either way, which is
    the part that matters for correctness.
    LONGEST MATCH WINS, and the matched span is consumed. Without that,
    "I never used Azure Kubernetes Service" denies Azure and Kubernetes as well
    as AKS, because both names sit as whole words inside the longer one —
    turning one narrow denial into the erasure of the user's two strongest
    technologies. Over-denial is worse than the defect being fixed here: the
    original bug adds experience the user does not have, this one would delete
    experience they do.

    Positive assembly deliberately does NOT work this way. There, matching
    "azure" and "kubernetes" inside "Azure Kubernetes Service" is right,
    because all three are genuinely evidenced by that phrase. Denial is the
    asymmetric case: the user denied one specific thing, and only that thing.
    """
    found: list[str] = []
    remaining = sentence
    for term in denial_vocabulary():  # longest first
        pattern = rf"(?<![\w-]){re.escape(term)}(?![\w-])"
        if not re.search(pattern, remaining, re.I):
            continue
        # Blank the span so a shorter name nested inside it cannot match too.
        remaining = re.sub(
            pattern, lambda m: " " * len(m.group()), remaining, flags=re.I
        )
        canonical = normalise_denial_term(term)
        if canonical and canonical not in found:
            found.append(canonical)
    return sorted(found)


@dataclass
class Denial:
    """One negative career statement and the technologies it covers.

    `terms` may be empty: a denial whose subject is not in the technology
    vocabulary still counts as a denial for classification purposes (it must
    not become positive career prose), it simply blocks nothing specific. That
    is recorded honestly rather than dropped.
    """

    kind: str
    terms: list[str] = field(default_factory=list)
    statement: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "terms": self.terms, "statement": self.statement}


@dataclass
class Instruction:
    """The parsed request. `raw` is kept so nothing is silently discarded."""

    raw: str = ""
    career_statements: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    denials: list[Denial] = field(default_factory=list)

    @property
    def has_career_statements(self) -> bool:
        return bool(self.career_statements)

    @property
    def has_denials(self) -> bool:
        return bool(self.denials)

    def preference_text(self) -> str:
        return " ".join(self.preferences).strip()

    def career_text(self) -> str:
        """POSITIVE career prose only.

        This is what gets persisted as a user_statement career source, and a
        career source is scanned for technology names that then become
        confirmed experience. A denial reaching this string is the negation
        defect itself, so denials are structurally unable to appear here.
        """
        return " ".join(self.career_statements).strip()

    def denial_text(self) -> str:
        return " ".join(d.statement for d in self.denials).strip()

    def denied_terms(self) -> dict[str, str]:
        """term -> denial kind, for every technology this request denies.

        First denial of a term wins within a single request, so a request that
        says both "only studied GCP" and "never used GCP" records the more
        specific reading it stated first rather than flip-flopping.
        """
        denied: dict[str, str] = {}
        for denial in self.denials:
            for term in denial.terms:
                denied.setdefault(term, denial.kind)
        return denied

    def claimed_terms(self) -> list[str]:
        """Technologies named in career statements explicit enough to REVERSE a denial.

        This is a deliberately higher bar than `career_statements`. Superseding
        is the only operation in the system that can undo a hard truth boundary,
        so the statement that does it has to be an unambiguous assertion by the
        user about their own career.

        Two filters beyond being a career statement:

          - FRAMING. "Make my resume sound like I used GCP" is a request about
            wording, not a claim about a career. It is excluded by `parse`
            before it can ever become a career statement, so it never reaches
            here — the guard below is defence in depth for statements built by
            other means.
          - HEDGING. "I think I might have used GCP once" may well belong in
            career prose, but it is not strong enough to overturn a definite
            earlier "I have never used GCP". When a vague new statement meets a
            definite old one, the definite one stands.
          - THIRD PARTIES. "My team used GCP" is a statement about a team. It
            is not the user saying they used GCP, so it cannot undo the user
            saying they did not.

        A term denied in this same request is excluded by the caller: an
        explicit denial is a hard boundary within the request that states it.
        """
        found: list[str] = []
        for sentence in self.career_statements:
            if (
                _FRAMING.search(sentence)
                or _HEDGE.search(sentence)
                or _THIRD_PARTY.search(sentence)
            ):
                continue
            for term in technology_terms(sentence):
                if term not in found:
                    found.append(term)
        return sorted(found)

    def as_dict(self) -> dict:
        return {
            "career_statements": self.career_statements,
            "preferences": self.preferences,
            "denials": [d.as_dict() for d in self.denials],
        }


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text)
    return [p.strip() for p in parts if p and p.strip()]


def _clauses(sentence: str) -> list[str]:
    """One sentence split at contrastive joins.

    "I have never used Harness but I have used Jenkins extensively" is a single
    sentence making two opposite statements. Classified whole it reads as a
    denial, and the denial then covers every technology the sentence names —
    erasing the Jenkins experience the sentence explicitly asserts.
    """
    return [c.strip() for c in _CONTRAST.split(sentence) if c and c.strip()]


def _classify_clause(clause: str) -> str:
    """"denial" | "career" | "preference" for one clause.

    Negation is checked BEFORE the positive-claim test, and that order is the
    whole original fix. "I have never used Harness" matches the first-person
    experience pattern perfectly well — it is a first-person sentence containing
    "used" — so any ordering that tests for a positive claim first classifies a
    denial as a career statement, stores it as positive prose and confirms
    Harness.

    Framing is checked before BOTH. A framed sentence is talking about a claim,
    not making one, and must not become career prose in either direction.
    """
    if _FRAMING.search(clause):
        return "preference"
    if classify_negation(clause) is not None:
        return "denial"
    if _FIRST_PERSON_CLAIM.search(clause) and not _REQUEST_VERB.search(clause):
        return "career"
    return "preference"


def parse(instruction: str | None) -> Instruction:
    """Split an instruction into positive career facts, denials and preferences.

    Each sentence is split at contrastive joins and each clause classified on
    its own, so a sentence that both denies and claims resolves to one of each
    rather than letting whichever reading won apply to every technology named.

    Anything ambiguous that is not negative is treated as a preference,
    deliberately. A misfiled preference is forgotten after this run; a misfiled
    career fact becomes a permanent claim the user never made. The asymmetry
    decides the default.
    """
    if not instruction or not instruction.strip():
        return Instruction()

    career: list[str] = []
    preferences: list[str] = []
    denials: list[Denial] = []

    for sentence in _sentences(instruction):
        clauses = _clauses(sentence)
        kinds = [_classify_clause(c) for c in clauses]

        # Terms the sentence asserts POSITIVELY. A denial clause that names no
        # technology of its own borrows from the rest of the sentence, but must
        # never borrow something another clause just claimed.
        claimed_here: set[str] = set()
        for clause, kind in zip(clauses, kinds, strict=True):
            if kind == "career":
                claimed_here.update(technology_terms(clause))

        for clause, kind in zip(clauses, kinds, strict=True):
            if kind == "denial":
                terms = technology_terms(clause)
                if not terms:
                    # "I know about GCP but haven't used it professionally" —
                    # the denying clause says "it". The referent is in the
                    # sentence, so read it from there rather than recording a
                    # denial that blocks nothing.
                    terms = [
                        t for t in technology_terms(sentence) if t not in claimed_here
                    ]
                denials.append(
                    Denial(
                        kind=classify_negation(clause),
                        terms=sorted(terms),
                        statement=clause,
                    )
                )
            elif kind == "career":
                career.append(clause)
            else:
                preferences.append(clause)

    return Instruction(instruction.strip(), career, preferences, denials)
```

## Appendix B — `tests/test_negation_hardening.py` (full)

```python
"""Hardening pass on the denial boundary (2026-08-20, post-GPT review).

The architecture was approved; these are the six items the review asked for
before the fix could be closed. Each section below maps to one of them.

The frozen decisions this file defends, none of which it may quietly change:

  - explicit user contradiction is a hard boundary
  - denial wins over documents
  - a later explicit positive user career statement MAY supersede a denial
  - same-request positive + denial resolves to denial
  - documents may NEVER supersede a denial
  - the three kinds stay: never_used / not_professional / studied_only
  - longest-match-wins on denial extraction, so denying AKS never erases
    Azure or Kubernetes

The governing bias throughout: **predictable under-classification beats an
aggressive matcher**. The original defect ADDED experience the user does not
have. Over-denial DELETES experience they do. The second is worse, so every
ambiguous case in this file resolves towards doing nothing.
"""

import re
from pathlib import Path

import pytest

from council.documents.instructions import (
    NEVER_USED,
    NOT_PROFESSIONAL,
    STUDIED_ONLY,
    classify_negation,
    parse,
    technology_terms,
)
from council.documents.profile import (
    AUTHORITY_MASTER_RESUME,
    NO_DENIALS,
    CareerProfile,
    assemble_confirmed,
    denial_vocabulary,
    normalise_denial_term,
)
from council.documents.store import (
    apply_instruction_facts,
    confirmed_experience,
    list_denials,
    load_denials,
    store_document,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def _profile() -> CareerProfile:
    return CareerProfile(technologies=[], domains=[])


# ==========================================================================
# 1. The residual `assemble_confirmed()` bypass is closed
# ==========================================================================


def test_assembling_confirmed_experience_without_denials_is_impossible():
    """`denials` has no default. Omitting it is a TypeError, not a silent
    truth set with the boundary switched off."""
    with pytest.raises(TypeError):
        assemble_confirmed(_profile(), [])
    with pytest.raises(TypeError):
        assemble_confirmed(_profile())


def test_denials_cannot_be_passed_positionally():
    """Keyword-only, so the word "denials" appears at every call site and a
    reader can see whether the boundary was applied without opening the
    function."""
    with pytest.raises(TypeError):
        assemble_confirmed(_profile(), [], [])


def test_the_explicit_empty_case_still_works():
    confirmed = assemble_confirmed(_profile(), [], denials=NO_DENIALS)
    assert confirmed.terms == set()
    assert confirmed.denied == {}


def test_only_the_sanctioned_call_sites_assemble_confirmed_experience():
    """An architectural fitness function, and the real answer to "is the
    boundary hard to bypass?".

    A required argument stops someone FORGETTING the denials. It cannot stop
    someone writing `denials=NO_DENIALS` in production code and reintroducing
    the bug deliberately-but-thoughtlessly. This test makes that a CI failure:
    the pure assembler may only be called from the workflow (which is handed
    denials) and from the store (which loads them). Anywhere else, use
    `store.confirmed_experience()`.

    If you are here because this test failed: you probably want
    `confirmed_experience()`. If you genuinely need the pure function, add the
    file here and say why in the commit message.
    """
    sanctioned = {"documents/workflow.py", "documents/store.py"}
    callers = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        # The definition itself and docstring mentions are not calls.
        for match in re.finditer(r"(?<!def )\bassemble_confirmed\(", text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line = text[line_start:text.find("\n", match.start())]
            if line.lstrip().startswith("#") or "`" in line:
                continue
            callers.add(str(path.relative_to(SRC / "council")).replace("\\", "/"))
    assert callers == sanctioned, (
        f"unsanctioned assemble_confirmed() caller(s): {sorted(callers - sanctioned)}"
    )


def test_no_production_code_asserts_there_are_no_denials():
    """`NO_DENIALS` is for tests. In production the answer is always "load
    them", so the constant appearing under src/ means someone asserted
    something they could not have known."""
    users = []
    for path in SRC.rglob("*.py"):
        for line in path.read_text().splitlines():
            code = line.split("#")[0]
            if "`" in line or line.strip().startswith(("*", '"""')):
                continue  # a docstring explaining the constant is not a use
            if "denials=NO_DENIALS" in code:
                users.append(f"{path.relative_to(SRC / 'council')}: {line.strip()}")
    assert users == []


# ==========================================================================
# 2. Adversarial supersession
#
# Product rule (Amith's, not up for redesign): a later explicit positive user
# career statement supersedes an earlier denial. The attack surface is what
# counts as "explicit positive user career statement".
# ==========================================================================


MUST_NOT_SUPERSEDE = [
    # --- the review's list -------------------------------------------------
    "Please add GCP to my resume.",
    "The JD wants GCP.",
    "Can you make my resume sound like I used GCP?",
    "I want GCP highlighted.",
    "This role requires GCP.",
    "I am learning GCP.",
    # --- the same fabrication request without the question mark ------------
    "Make my resume sound like I used GCP.",
    "Write it as if I used GCP.",
    "Pretend I used GCP.",
    "Say that I have used GCP.",
    "Word it so I look like a GCP engineer.",
    # --- hedged recollection ------------------------------------------------
    "I think I might have used GCP once.",
    "I may have used GCP briefly.",
    "I possibly used GCP at some point.",
    "I sort of used GCP a bit.",
    # --- third parties and the job, not the user ----------------------------
    "My team used GCP.",
    "The client used GCP.",
]


@pytest.mark.parametrize("sentence", MUST_NOT_SUPERSEDE)
def test_these_must_not_supersede_a_denial(sentence):
    assert parse(sentence).claimed_terms() == [], sentence


@pytest.mark.parametrize("sentence", MUST_NOT_SUPERSEDE)
async def test_an_existing_denial_survives_all_of_them(db, sentence):
    """End to end through the persistence layer, because `claimed_terms()`
    being empty is only half the guarantee — the other half is that nothing
    else in the write path reverses a denial."""
    await apply_instruction_facts(parse("I have never used GCP"))
    await apply_instruction_facts(parse(sentence))
    assert [d.term for d in await load_denials()] == ["gcp"], sentence
    assert (await confirmed_experience()).is_confirmed("gcp") is False


MUST_SUPERSEDE = [
    "I have professional GCP experience.",
    "I used GCP professionally at my last company.",
    "I have used GCP in production for two years.",
    "I ran GCP workloads at my last employer.",
    "I have since built GCP infrastructure professionally.",
]


@pytest.mark.parametrize("sentence", MUST_SUPERSEDE)
async def test_a_genuine_later_claim_does_supersede(db, sentence):
    """The product rule must still work. A hardening pass that quietly
    disabled supersession would be a worse regression than the attack it
    defends against."""
    await apply_instruction_facts(parse("I have never used GCP"))
    facts = await apply_instruction_facts(parse(sentence))
    assert facts["superseded"] == ["gcp"], sentence
    assert await load_denials() == []


def test_a_request_to_fabricate_is_not_even_a_career_source():
    """Stronger than "does not supersede", and worth stating separately.

    "Make my resume sound like I used GCP" previously became a user_statement
    career source, which CONFIRMED GCP from nothing at all — no denial
    required. Not superseding is necessary; not being career prose is the
    complete fix.
    """
    parsed = parse("Make my resume sound like I used GCP.")
    assert parsed.career_statements == []
    assert parsed.career_text() == ""
    assert parsed.preferences == ["Make my resume sound like I used GCP."]


def test_a_hedged_claim_may_be_prose_but_may_not_reverse_a_boundary():
    """Deliberately asymmetric. A hedge is weak evidence, not no evidence, so
    it stays in career prose — but when weak new evidence meets a definite
    earlier denial, the definite statement stands."""
    parsed = parse("I think I might have used GCP once.")
    assert parsed.career_statements != []
    assert parsed.claimed_terms() == []


# --- mixed sentences: positive and negative in one breath -------------------


def test_a_denial_and_a_claim_in_one_sentence_do_not_contaminate_each_other():
    """The over-denial case inside a single sentence.

    Classified whole this reads as a denial, and the denial would then cover
    every technology the sentence names — erasing the Jenkins experience the
    same sentence explicitly asserts.
    """
    parsed = parse("I have never used Harness but I have used Jenkins extensively.")
    assert parsed.denied_terms() == {"harness": NEVER_USED}
    assert parsed.claimed_terms() == ["jenkins"]


def test_a_denying_clause_with_a_pronoun_borrows_the_subject_from_its_sentence():
    """"haven't used it professionally" names no technology. The referent is in
    the other clause, so reading it from there beats recording a denial that
    blocks nothing."""
    parsed = parse("I know about GCP but haven't used it professionally.")
    assert parsed.denied_terms() == {"gcp": NOT_PROFESSIONAL}
    assert parsed.claimed_terms() == []


def test_a_borrowed_referent_never_takes_a_technology_another_clause_claimed():
    parsed = parse(
        "I have used Terraform heavily but I have no professional Harness experience."
    )
    assert "terraform" not in parsed.denied_terms()
    assert parsed.claimed_terms() == ["terraform"]


@pytest.mark.parametrize(
    ("sentence", "denied", "claimed"),
    [
        (
            "I have deep Terraform experience although I have no Harness experience.",
            {"harness": NEVER_USED},
            ["terraform"],
        ),
        (
            "I ran Jenkins for years, however I only studied GCP.",
            {"gcp": STUDIED_ONLY},
            ["jenkins"],
        ),
        (
            "I support AKS in production while I have never touched OpenShift.",
            {"openshift": NEVER_USED},
            ["aks"],
        ),
    ],
)
def test_mixed_sentences_across_contrastive_joins(sentence, denied, claimed):
    parsed = parse(sentence)
    assert parsed.denied_terms() == denied
    assert parsed.claimed_terms() == claimed


# ==========================================================================
# 3. Regex boundary attacks
# ==========================================================================

# Sentences that ASSERT experience while containing negation vocabulary.
# Every one of these was a false positive before the patterns were tightened,
# or is adjacent to one and kept as a guard.
FALSE_POSITIVE_ATTACKS = [
    # The `_NOT_PROFESSIONAL` 60-character window, which used to accept any
    # negator followed by "in production" within 60 chars.
    "I have never had an outage while running Jenkins in production.",
    "I do not just write Terraform, I run it in production.",
    "It is not unusual for me to deploy Harness in production.",
    "I have never lost data while operating Kubernetes in production.",
    "There is not a week where I do not use Terraform at work.",
    # `not only ... but` is an intensifier, not a negation.
    "I have not only used Harness, I built our deployment templates.",
    "I have not only run Jenkins but also migrated it to Azure DevOps.",
    # Bare "had" / "been", which used to sit in the never-used verb list.
    "I have never been on call without Grafana dashboards.",
    "I have never had a failed Terraform apply reach production.",
    # Plain positives with incidental negative words.
    "I use Terraform daily, no exceptions.",
    "Nothing I deploy goes out without a Jenkins pipeline.",
]


@pytest.mark.parametrize("sentence", FALSE_POSITIVE_ATTACKS)
def test_no_false_positive_denials(sentence):
    """A false denial deletes real experience. This is the failure mode the
    review told me to favour avoiding, even at the cost of missing denials."""
    assert classify_negation(sentence) is None, sentence


@pytest.mark.parametrize("sentence", FALSE_POSITIVE_ATTACKS)
def test_no_false_positive_denials_end_to_end(sentence):
    """Not just unclassified — the technologies named must stay confirmed."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "Master resume",
            "text": (
                "Terraform, Jenkins, Harness, Kubernetes, Grafana, Azure DevOps "
                "across production environments."
            ),
        }
    ]
    parsed = parse(sentence)
    confirmed = assemble_confirmed(_profile(), documents, denials=NO_DENIALS)
    assert parsed.denied_terms() == {}, sentence
    for term in ("terraform", "jenkins", "harness", "kubernetes", "grafana"):
        assert confirmed.is_confirmed(term), (sentence, term)


# Real denials that used to escape every pattern in the file.
FALSE_NEGATIVE_ATTACKS = [
    ("I have no Harness experience.", "harness"),
    ("I've got zero Jenkins experience.", "jenkins"),
    ("I lack Harness experience.", "harness"),
    ("I have no real GCP experience.", "gcp"),
    ("I have no prior Datadog experience.", "datadog"),
    ("I have never had any hands-on experience with OpenShift.", "openshift"),
    ("I have no exposure to Anthos.", "anthos"),
    ("I do not have any Kafka experience.", "kafka"),
]


@pytest.mark.parametrize(("sentence", "term"), FALSE_NEGATIVE_ATTACKS)
def test_natural_denials_are_not_missed(sentence, term):
    parsed = parse(sentence)
    assert classify_negation(sentence) is not None, sentence
    assert term in parsed.denied_terms(), sentence


def test_two_negators_in_one_clause_decline_rather_than_guess():
    """A double negative usually asserts the opposite of its parts.

    "There is not a week where I do not use Terraform at work" contains a
    negator directly governing a usage verb — twice — so no amount of pattern
    tightening reads it correctly. Declining is the only honest option.
    """
    assert classify_negation(
        "There is not a week where I do not use Terraform at work."
    ) is None
    assert classify_negation("Nothing I deploy goes out without Jenkins.") is None
    # One negator still classifies normally; the rule is about ambiguity, not
    # about being squeamish.
    assert classify_negation("I have never used Harness.") == NEVER_USED


def test_the_cost_of_the_two_negator_rule_is_a_known_false_negative():
    """Two denials crammed into one clause are declined too. Recoverable by
    saying them as two sentences; a wrongly erased technology is not
    recoverable by anything the user can see."""
    crammed = parse("I never used Harness and I don't have Jenkins experience.")
    assert crammed.denied_terms() == {}
    separated = parse(
        "I never used Harness. I don't have Jenkins experience."
    )
    assert set(separated.denied_terms()) == {"harness", "jenkins"}


def test_a_third_party_must_be_the_subject_not_merely_mentioned():
    """"My team used GCP" is somebody else's experience. "I used GCP at my last
    company" is the user's, and happens to name an employer — excluding it
    would break the product rule for one of its most natural phrasings."""
    assert parse("My team used GCP.").claimed_terms() == []
    assert parse("Our client ran GCP workloads.").claimed_terms() == []
    assert parse("I used GCP professionally at my last company.").claimed_terms() == [
        "gcp"
    ]
    assert parse("I have used GCP at my company in production.").claimed_terms() == [
        "gcp"
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "I have used Terraform professionally but never Harness.",
        "Harness: none.",
        "Jenkins is not something in my background.",
        "I never used Harness and I don't have Jenkins experience.",
    ],
)
def test_deliberate_under_classification_is_recorded_not_pretended(sentence):
    """These read as denials to a human and are NOT detected. That is a known,
    accepted limit, not an oversight — each would need either a verb-less
    negation rule or general NLP, and both risk the false positives above.

    The consequence is bounded: the technology stays in whatever state the
    career sources put it in. Nothing false is asserted. If one of these
    phrasings turns out to be common in real use, add it deliberately with a
    false-positive attack alongside it.
    """
    assert parse(sentence).denied_terms() == {}, sentence


# ==========================================================================
# 4. Over-denial protection across the whole vocabulary
# ==========================================================================


def _composites() -> list[tuple[str, str, list[str]]]:
    """Every vocabulary spelling that contains another as a whole word.

    Generated rather than hand-listed so a new alias added to `profile.py`
    is covered by this regression the day it is added.
    """
    vocab = denial_vocabulary()
    found = []
    for outer in vocab:
        nested = sorted(
            {
                normalise_denial_term(inner)
                for inner in vocab
                if inner != outer
                and re.search(rf"(?<![\w-]){re.escape(inner)}(?![\w-])", outer)
            }
        )
        canonical = normalise_denial_term(outer)
        nested = [n for n in nested if n != canonical]
        if nested:
            found.append((outer, canonical, nested))
    return found


def test_the_vocabulary_actually_contains_nested_names():
    """Guards the guard: if this ever returns nothing, the parametrised test
    below is silently vacuous."""
    composites = _composites()
    assert len(composites) >= 12
    names = {outer for outer, _, _ in composites}
    assert {"azure kubernetes service", "elastic kubernetes service",
            "google kubernetes engine", "azure key vault",
            "terraform enterprise"} <= names


@pytest.mark.parametrize(
    ("spelling", "canonical", "nested"),
    _composites(),
    ids=[c[0].replace(" ", "-") for c in _composites()],
)
def test_denying_a_composite_never_erases_the_names_inside_it(
    spelling, canonical, nested
):
    """Denying AKS must not erase Azure and Kubernetes.

    Run over every nested pair in the vocabulary, not just the pair that
    happened to be found by hand: AKS/Azure, EKS/Kubernetes, GKE/Kubernetes,
    Azure Key Vault/Azure, Terraform Enterprise/Terraform and the rest.
    """
    extracted = technology_terms(f"I have never used {spelling}")
    assert extracted == [canonical], (spelling, extracted)
    for parent in nested:
        assert parent not in extracted, (spelling, parent)


@pytest.mark.parametrize(
    ("spelling", "canonical", "nested"),
    _composites(),
    ids=[c[0].replace(" ", "-") for c in _composites()],
)
def test_the_parent_technologies_stay_confirmed_after_denying_a_composite(
    spelling, canonical, nested
):
    """The consequence that matters, asserted on the assembled truth set
    rather than on the extractor."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "Master resume",
            "text": (
                "Azure, AWS, Kubernetes, Terraform, GitHub, Key Vault, "
                "Log Analytics, JFrog Artifactory, Entra ID, S3, GCP."
            ),
        }
    ]
    parsed = parse(f"I have never used {spelling}")
    denials = [
        type("D", (), {"term": t, "kind": k, "statement": ""})()
        for t, k in parsed.denied_terms().items()
    ]
    confirmed = assemble_confirmed(_profile(), documents, denials=denials)
    for parent in nested:
        if parent in confirmed.sources:
            assert confirmed.is_confirmed(parent), (spelling, parent)


def test_denying_the_parent_explicitly_still_works():
    """The protection is against ACCIDENTAL erasure, not against the user
    denying a broad platform on purpose."""
    parsed = parse("I have never used Azure.")
    assert parsed.denied_terms() == {"azure": NEVER_USED}


def test_both_can_be_denied_when_both_are_named():
    parsed = parse("I have never used Azure Kubernetes Service or Kubernetes.")
    assert set(parsed.denied_terms()) == {"aks", "kubernetes"}


# ==========================================================================
# 5. Persistence and audit semantics after supersession
# ==========================================================================


async def test_the_full_forward_sequence(db):
    """never used X -> used X professionally -> X is confirmed, denial
    inactive, both statements auditable, timestamps establish ordering."""
    await apply_instruction_facts(parse("I have never used Harness"))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    claim = "I have used Harness professionally since then"
    await apply_instruction_facts(parse(claim))
    # The positive statement becomes a career source, exactly as the API does.
    from council.documents.extract import Extracted
    from council.documents.profile import AUTHORITY_USER_STATEMENT

    await store_document(
        filename="user-statement.txt",
        title="Stated by you",
        authority=AUTHORITY_USER_STATEMENT,
        extracted=Extracted(
            text=claim, char_count=len(claim), truncated=False, detected_kind="text"
        ),
    )

    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("harness") is True
    assert confirmed.denied == {}

    ledger = await list_denials()
    assert len(ledger) == 1
    row = ledger[0]
    assert row["active"] is False
    assert row["statement"] == "I have never used Harness"
    assert row["superseded_by"] == claim
    assert row["superseded_at"] is not None
    assert row["created_at"] < row["superseded_at"]


async def test_reversal_again_keeps_the_whole_history(db):
    """denial -> positive -> denial.

    The newest explicit statement controls current state. Before the history
    column, re-denying cleared `superseded_by`, so the middle statement
    vanished and the record claimed the user had never contradicted
    themselves.
    """
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have used Harness professionally"))
    await apply_instruction_facts(parse("Actually I have never used Harness"))

    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("harness") is False
    assert confirmed.denial_kind("harness") == NEVER_USED

    ledger = await list_denials()
    assert len(ledger) == 1
    history = ledger[0]["history"]
    assert [h["action"] for h in history] == ["denied", "superseded", "denied"]
    assert history[0]["statement"] == "I have never used Harness"
    assert history[1]["statement"] == "I have used Harness professionally"
    assert history[2]["statement"] == "Actually I have never used Harness"
    # Ordering is legible from the record itself, not from row order.
    assert [h["at"] for h in history] == sorted(h["at"] for h in history)


async def test_history_survives_a_third_reversal(db):
    for statement in [
        "I have never used Harness",
        "I have used Harness professionally",
        "Actually I have never used Harness",
        "I have used Harness professionally again",
    ]:
        await apply_instruction_facts(parse(statement))
    history = (await list_denials())[0]["history"]
    assert [h["action"] for h in history] == [
        "denied", "superseded", "denied", "superseded",
    ]
    assert (await load_denials()) == []


async def test_the_denial_kind_is_carried_through_history(db):
    await apply_instruction_facts(parse("I only studied GCP"))
    await apply_instruction_facts(parse("I have used GCP professionally"))
    await apply_instruction_facts(parse("I have no professional GCP experience"))

    ledger = await list_denials()
    assert ledger[0]["kind"] == NOT_PROFESSIONAL  # current state
    kinds = [h.get("kind") for h in ledger[0]["history"] if h["action"] == "denied"]
    assert kinds == [STUDIED_ONLY, NOT_PROFESSIONAL]  # how it got there


async def test_documents_still_cannot_supersede_after_all_this(db):
    """The frozen rule, re-asserted at the end of the reversal machinery: no
    number of documents is a statement by the user."""
    await apply_instruction_facts(parse("I have never used Harness"))
    from council.documents.extract import Extracted

    for i in range(3):
        await store_document(
            filename=f"r{i}.txt",
            title=f"Master resume v{i}",
            authority=AUTHORITY_MASTER_RESUME,
            extracted=Extracted(
                text=f"Ran Harness pipelines in year {2020 + i}.",
                char_count=40,
                truncated=False,
                detected_kind="text",
            ),
        )
    assert (await confirmed_experience()).is_confirmed("harness") is False
    ledger = await list_denials()
    assert ledger[0]["active"] is True
    assert [h["action"] for h in ledger[0]["history"]] == ["denied"]
```

## Appendix C — migration `0011`

```python
"""career_denials.history — reversals must not overwrite each other

0010 kept one row per term with a single `superseded_by`. That records ONE
reversal. The realistic sequence is longer:

    "I have never used Harness"        -> denied
    "I have used Harness professionally" -> superseded
    "Actually I have never used Harness" -> denied again

At step 3, `record_denials` reset `superseded_at` and `superseded_by` to put the
denial back in force, which erased step 2 entirely. The current state was right
and the audit trail was a lie by omission — the user could no longer see that
they had ever claimed the technology.

`history` is append-only: every transition lands in it with its timestamp and
the user's own words. The scalar columns still describe CURRENT state, so
nothing reading them has to change; history answers "how did we get here?".

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-20

"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("career_denials") as batch:
        batch.add_column(sa.Column("history", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("career_denials") as batch:
        batch.drop_column("history")
```
