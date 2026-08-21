# Positive-Confirmation Patch — Review Packet

**Commit `e37ad01`** — "Only an explicit first-person assertion may establish
career experience". Parent `7b6aff7`. Narrow follow-up to the approved
hardening pass, per GPT's final scope.

Note on hashes: your rebase this morning onto `422f376` replayed the earlier
commits, so `49af7e6` is now **`44146cd`** and `e7aff0e` is now **`7b6aff7`**.
Content is byte-identical (`git diff 49af7e6 44146cd` is empty apart from your
two design docs arriving underneath). The commit GPT approved is intact.

---

## 1. Root cause

`_UNCERTAIN` and `_THIRD_PARTY` existed and worked, but were applied in the
**wrong function**.

Both were checked in `claimed_terms()`, which governs one thing only: whether a
statement may SUPERSEDE an existing denial. They were not checked in
`_classify_clause()`, which governs whether a sentence becomes career prose at
all.

The consequence is the whole defect. Blocking supersession protects a
technology the user has already explicitly denied. It does nothing for a
technology they have never mentioned — which is almost every technology, almost
all of the time. So:

```
"I think I might have used GCP once."   -> career statement
                                        -> stored as a user_statement career source
                                        -> scanned for technology names
                                        -> GCP CONFIRMED PROFESSIONAL EXPERIENCE
```

No denial anywhere in the picture. Same for `"My team used GCP"`.

This is the same defect shape as the original negation bug and the fabrication
bug before it: **a sentence that merely CONTAINS a technology name is read as
evidence the user has used it.** Third time, third entry point.

## 2. Exact behaviour changed

One condition, moved up one level:

```python
def _classify_clause(clause: str) -> str:
    if _FRAMING.search(clause) or _UNCERTAIN.search(clause) or _THIRD_PARTY.search(clause):
        return "preference"
    if classify_negation(clause) is not None:
        return "denial"
    if _FIRST_PERSON_CLAIM.search(clause) and not _REQUEST_VERB.search(clause):
        return "career"
    return "preference"
```

`_FRAMING` was already there. `_UNCERTAIN` and `_THIRD_PARTY` are the addition.

| sentence | before | after |
|---|---|---|
| "I think I might have used GCP once." | career statement → confirms GCP | preference |
| "My team used GCP." | career statement → confirms GCP | preference |
| "The client used GCP." | career statement → confirms GCP | preference |
| "Make my resume sound like I used GCP." | preference | preference *(unchanged)* |
| "The JD wants GCP." | preference | preference *(unchanged)* |
| "I used GCP professionally at my last company." | career statement | career statement *(unchanged)* |

Nothing is discarded — each reclassified sentence still reaches the model as a
preference, so a formatting request the user made is still honoured.

**Second change, same patch.** Scale and duration words (`briefly`, `a bit`,
`a little`, `once or twice`) were removed from the uncertainty pattern.
"I used Ansible briefly" is a certain assertion of limited experience, not
uncertainty about whether it happened. Leaving them in would have made this
patch erase real experience — the exact failure the denial boundary exists to
prevent, arriving through the front door. Epistemic markers only now
(`i think`, `might have`, `possibly`, `can't remember`, `vaguely`, …).

**Deliberately kept:** `claimed_terms()` retains its now-redundant guard. It is
the one function that can undo a hard truth boundary and should not depend on
an upstream filter staying correct forever. Documented as such in the code.

## 3. Files changed

```
src/council/documents/instructions.py    the moved condition + narrowed _UNCERTAIN
tests/test_positive_confirmation.py      NEW — 110 tests
tests/test_negation_hardening.py         one test reversed by design and renamed
```

Three files. No migration, no schema change, no new abstraction, no new parser
layer, no model call.

## 4. Tests added

**110 new**, in `tests/test_positive_confirmation.py`:

| group | count | what |
|---|---|---|
| must-not-establish, parser | 30 | 10 uncertain, 8 third-party, 6 request/preference, 6 framing — asserting `career_statements`, `career_text()` and `claimed_terms()` are all empty |
| nothing silently dropped | 30 | the same 30, asserting each still arrives as a preference |
| explicit first-person | 7 | the product rule still works |
| limited experience | 4 | "briefly", "a bit", "on one project" still establish experience |
| third-party as subject vs mention | 2 | "at my last company" is still the user's own claim |
| mixed sentence | 1 | "My team used GCP but I ran the Terraform pipelines myself" |
| persistence / end-to-end | 34 | the same 30 through instruction → stored source → confirmed set, plus positive control, denial-boundary regression, document-path scope check |
| recorded gap | 1 | see §8 |
| preferences still delivered | 1 | reclassified sentences reach the model |

The end-to-end group is the one that matters — `claimed_terms()` being empty is
only half a guarantee; the other half is that nothing in the write path
confirms the technology anyway.

**One existing test reversed**, deliberately:
`test_a_hedged_claim_may_be_prose_but_may_not_reverse_a_boundary` asserted that
hedged sentences stayed in career prose. That was the behaviour I flagged as
wrong in the hardening review, and this patch is GPT's instruction to fix it.
Renamed to `test_an_uncertain_claim_is_not_career_prose_at_all`, with the
reversal and its reason in the docstring.

## 5. Full suite

**752 passed** (was 642). No failures, no skips, no xfails.

## 6. Ruff

`ruff check src tests` — All checks passed.

## 7. Migrations

**None required.** This patch is pure classification logic. Schema is unchanged
at `0011`.

## 8. Genuine correctness defects discovered while testing

**One, and it is worth your attention — a pre-existing gap, not caused by this
patch.**

My first positive-control test failed, which is how it surfaced:

```
"I used GCP professionally at my last company."
  -> parsed correctly as an explicit first-person career statement
  -> claimed_terms() == ["gcp"]          (the term IS recognised)
  -> stored as a user_statement career source
  -> is_confirmed("gcp") == False        (never becomes confirmed experience)
```

Confirmation scans career sources against `DEFAULT_TECHNOLOGIES`, which does
not contain `gcp`. The denial vocabulary does. So the user can DENY a
technology outside the baseline list, but cannot ESTABLISH one by stating it
plainly — the two vocabularies disagree.

This works directly against the product goal: provide your career information
and Council understands it. It is category (A) by your own test — a user can
state true experience and have it silently dropped — but it lives in the
confirmation-vocabulary / technology-discovery path, not in instruction
classification, so I did not fix it inside a patch you scoped to the latter.

Recorded as `test_a_stated_technology_outside_the_baseline_vocabulary_is_not_confirmed`,
which asserts current behaviour so the gap is visible in CI and a future fix
has something to flip. **Recommend this as the next correctness item**, ahead
of resuming the Auto review, because it is squarely on the Career Experience
Profile path you want to get back to.

## 9. Deliberately NOT implemented

1. **Carrier type / truth abstraction / another parser layer / LLM negation
   classifier.** Explicitly out per your instruction, and none is needed.
2. **The vocabulary gap in §8.** Flagged, tested, not fixed.
3. **`studied_only` living in `career_denials`.** Left alone as directed.
   Recorded here as design debt: "studied only" is not semantically equivalent
   to "never used", and it currently shares a table whose name asserts that it
   is. Deferred modeling question, no behavioural impact today. This packet is
   committed to the repo, so the record persists — move it to a debt list if
   you keep one.
4. **Hedged/third-party statements as WEAK evidence.** There is a design in
   which "I think I used GCP" becomes a low-confidence fact rather than
   nothing. That is a new concept in the truth model and you have not asked for
   one. Conservative drop, as instructed.
5. **Widening `_THIRD_PARTY` beyond subject position.** "GCP was used on my
   team" is passive and not caught. Under-classification: it fails towards
   treating the sentence as a preference, which is safe. Not chased.

## 10. Final commit hash

**`e37ad01`**

All three changed files verified byte-identical to the tested content
(md5, CR-normalised).

## 11. Working tree

Clean. `git status --short` reports only `_to_delete/`, which holds stale git
lock files — the mount refuses `unlink`, so git cannot remove its own
`.git/*.lock` files and each operation leaves one that blocks the next. Delete
that folder from Windows when convenient.

`main` is **ahead of `origin/main` by 1** (this commit). Not pushed.

---

## Stopping here

No further hardening pass. Awaiting GPT review.

After approval the queue is: the §8 vocabulary gap if you agree it is next,
then the Auto evaluation post-review that has been waiting since this started —
steps B, C and D of your original sequencing.
