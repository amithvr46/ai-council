# AI Council — Negation / Denial Correctness Fix

**Review packet for GPT.** Prepared by Claude. Commit `3ce07fe` on `main`, parent `96dd02e`.

You are reviewing ONE isolated correctness fix, authorised by Amith on 2026-08-20.
He explicitly did NOT authorise the full gap-confirmation UI, adjacency inference,
Phase 3C, unrelated resume changes or new providers. If you find scope creep in
what follows, say so — that is a finding.

Tests are the final authority (dev-process rule). Baseline before this change was
427 green; it is now 515 green, 88 of them new.

---

## 1. The defect

Live behaviour before this commit, reproduced against the real code:

```
>>> from council.documents.instructions import parse
>>> parse("I have never used Harness or Jenkins").career_statements
['I have never used Harness or Jenkins']

>>> assemble_confirmed(profile, [{'authority':'user_statement',
...     'title':'Stated by you','text':'I have never used Harness or Jenkins'}])
>>> confirmed.is_confirmed('harness'), confirmed.is_confirmed('jenkins')
(True, True)
```

The causal chain:

1. `_FIRST_PERSON_CLAIM` in `documents/instructions.py` matched the sentence — it
   IS a first-person sentence containing the experience verb "used". Nothing in
   the parser looked for negation.
2. The API (`/artifacts/resume`) called `_store_user_statement(instruction.career_text())`,
   persisting the denial as a `user_statement` career source.
3. `assemble_confirmed()` scans every career source for technology names from the
   vocabulary and adds each one it finds.
4. Harness and Jenkins became CONFIRMED PROFESSIONAL EXPERIENCE, eligible for a
   submitted resume.

The general shape: **a denial stored as positive prose confirms exactly the
technologies it denies.**

## 2. The invariant now enforced

```
positive source mention + explicit user denial
  -> denial wins for career confirmation
  -> the conflict remains visible / auditable
  -> the technology cannot silently become professional experience
```

Stated by Amith as a permanent truth-boundary rule:
**EXPLICIT USER CONTRADICTION IS A HARD BOUNDARY.**

## 3. Decisions, and who made them

| Decision | Made by | Rule |
|---|---|---|
| Denial-then-later-positive | **Amith** (asked, chose from 3 options) | Later explicit user statement SUPERSEDES the denial. Never silent: row kept with both statements + both timestamps. |
| Denial vs positive in the SAME request | Claude | Denial wins. Supersession is for a later statement; a self-contradicting request reads negative. |
| Document re-ingestion vs denial | Claude (from the review's test list) | A document can NEVER supersede a denial, however many times re-ingested. Only the user can. |
| Three denial kinds, not one flag | Claude | `never_used` / `not_professional` / `studied_only`. All block confirmation; kept distinct so the audit trail carries the user's actual words and a later Familiarity section can tell them apart. |
| Denial term extraction scope | Claude | Technologies only, NOT domains. "No automation experience" is a broader claim than a deterministic matcher should act on. |
| Longest-match-wins on term extraction | Claude (found by a test) | See §6.3 — this one is important. |
| Persistence shape | Claude | New `career_denials` table, per-term, migration 0010. Prose could not work: the document scanner cannot distinguish a denial's text from a career source's text. |

## 4. Change map — 15 files, +1573 / -48

```
src/council/documents/instructions.py              | 251 ++++++-   negation parsing
src/council/documents/profile.py                   | 131 +++-     denied set + central enforcement
src/council/documents/store.py                     | 157 +++++    persistence, supersession, single assembly path
src/council/documents/workflow.py                  |  91 ++-      thread denials through; prompt line
src/council/documents/conflicts.py                 |  35 +        experience_denied conflicts
src/council/documents/__init__.py                  |  14 +        exports
src/council/db/models.py                           |  30 +        CareerDenialRow
src/council/db/migrations/versions/0010_...py      |  62 ++       new table
src/council/db/status.py                           |   1 +        revision evidence
src/council/api/main.py                            |  58 +-       record/supersede; report in trace
src/council/cli.py                                 |  55 +-       `council denials`; single assembly path
tests/test_negation.py                             | 720 +++++    NEW — 88 tests
tests/test_db_status.py                            |   2 +-       (existing) new table in fixture
tests/test_migrations.py                           |   7 +        (existing) assert new table
tests/test_resume_api.py                           |   7 +-       (existing) as_dict() gained `denials`
```

**No resume-contract or resume-workflow test changed behaviour.** The three
existing tests touched are mechanical fixture updates, shown in full in §7.

## 5. The three enforcement points

### 5.1 Parser — negation classified BEFORE the positive-claim test

That ordering IS the fix. Any order that tests for a positive claim first
misclassifies every denial, because a denial is a grammatically perfect
first-person experience sentence.

### 5.2 Structural removal from the confirmed set

`_apply_denials()` REMOVES the term from `ConfirmedExperience.terms` rather than
only making `is_confirmed()` return False.

Rationale: `terms` is read directly by
- `_career_context()` — the prompt's "complete truth set"
- `assemble_confirmed()` — the document-scanning vocabulary
- `scan_jd_technologies()` — the JD gap scanner

A boundary living only inside `is_confirmed()` would be bypassed by all three.
`sources` is deliberately left INTACT so the contradiction can be reported.

### 5.3 One async assembly path

`store.confirmed_experience()` loads profile + documents + denials together.
`api/main.py` and `cli.py` both go through it and no longer call
`assemble_confirmed()` themselves.

**Known residual risk for you to judge:** `assemble_confirmed(profile, documents)`
is still callable without denials (existing signature, `denials` is an optional
third argument). A NEW caller could bypass the boundary by calling it directly
instead of `confirmed_experience()`. Alternatives considered and rejected:
making `denials` required (breaks every existing test and the pure-function
property), or making the workflow load persistence itself (couples the workflow
to the DB). Is the current tradeoff right?

---

## 6. The code

### 6.1 `src/council/documents/instructions.py` (full file after the change)

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

_NOT_PROFESSIONAL_PATTERNS = (
    # "no professional GCP experience", "no production experience with GCP"
    r"\bno\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial|paid|work|"
    r"on[- ]the[- ]job|enterprise)\s+(?:\w+[- ]){0,3}?experience\b",
    r"\bno\s+(?:\w+[- ]){0,3}?experience\s+(?:\w+[- ]){0,3}?"
    r"(?:professionally|in production|at work|commercially)\b",
    # "never used it professionally", "have not used X in production"
    r"\b(?:never|not|n't)\b[^.]{0,60}?\b(?:professionally|in production|"
    r"at work|commercially|on the job)\b",
    r"\bnot\s+(?:\w+[- ]){0,3}?(?:professional|production|commercial)\s+experience\b",
)

_NEVER_USED_PATTERNS = (
    # "never used", "never worked with", "never touched"
    r"\bnever\s+(?:really\s+|actually\s+|ever\s+)?"
    r"(?:used|use|worked with|work with|touched|ran|run|built|deployed|"
    r"administered|managed|configured|supported|maintained|operated|"
    r"had|have had|been)\b",
    # "have not used", "haven't worked with", "hasn't touched"
    r"\b(?:have|has|had)\s+(?:not|never)\s+(?:\w+\s+){0,2}?"
    r"(?:used|use|worked|work|touched|ran|run|built|deployed|administered|"
    r"managed|configured|supported|maintained|operated)\b",
    r"\b(?:haven't|hasn't|hadn't|didn't|don't|doesn't)\s+(?:\w+\s+){0,2}?"
    r"(?:used|use|worked|work|touched|ran|run|built|deployed|administered|"
    r"managed|configured|supported|maintained|operated)\b",
    # "no experience with", "no hands-on experience in", "zero exposure to"
    r"\bno\s+(?:hands[- ]on\s+|direct\s+|real\s+|prior\s+)?experience\b",
    r"\b(?:zero|nil)\s+(?:hands[- ]on\s+)?(?:experience|exposure)\b",
    r"\bno\s+(?:exposure|background)\s+(?:to|in|with)\b",
    r"\b(?:don't|do not|doesn't|does not)\s+have\s+(?:any\s+)?"
    r"(?:\w+[- ]){0,3}?experience\b",
    r"\bnot\s+(?:something|a technology)\s+i(?:'ve)?\s+(?:have\s+)?used\b",
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
    """
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
        """Technologies named in POSITIVE career statements.

        Used to supersede an earlier denial: the user is the primary source on
        their own career and is allowed to correct themselves. A term denied in
        this same request is excluded by the caller — an explicit denial is a
        hard boundary within the request that states it.
        """
        found: list[str] = []
        for sentence in self.career_statements:
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


def parse(instruction: str | None) -> Instruction:
    """Split an instruction into positive career facts, denials and preferences.

    Negation is checked BEFORE the positive-claim test, and that order is the
    whole fix. "I have never used Harness" matches the first-person experience
    pattern perfectly well — it is a first-person sentence containing "used" —
    so any ordering that tests for a positive claim first classifies a denial
    as a career statement, stores it as positive prose and confirms Harness.

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
        kind = classify_negation(sentence)
        if kind is not None:
            denials.append(
                Denial(kind=kind, terms=technology_terms(sentence), statement=sentence)
            )
        elif _FIRST_PERSON_CLAIM.search(sentence) and not _REQUEST_VERB.search(sentence):
            career.append(sentence)
        else:
            preferences.append(sentence)
    return Instruction(instruction.strip(), career, preferences, denials)
```

### 6.2 Diff for every other source file

```diff
===== src/council/documents/profile.py =====
@@ -200,18 +200,69 @@
 
 
 @dataclass
+class Denied:
+    """One technology the user has explicitly said they have not used.
+
+    A plain value object with no behaviour: it is carried unchanged from the
+    instruction parser to the audit trail, so the answer to "why is this not
+    confirmed?" is always the user's own words rather than an inference.
+    """
+
+    term: str
+    kind: str  # never_used | not_professional | studied_only
+    statement: str = ""
+
+    def as_dict(self) -> dict:
+        return {"term": self.term, "kind": self.kind, "statement": self.statement}
+
+
+@dataclass
 class ConfirmedExperience:
-    """The assembled confirmed set, plus where each term came from."""
+    """The assembled confirmed set, plus where each term came from.
+
+    THE DENIAL BOUNDARY LIVES HERE, and it is enforced structurally rather
+    than by asking callers to check a second thing:
+
+        a denied term is REMOVED from `terms`.
+
+    That matters because `terms` is read directly in several places — the
+    prompt's truth set, the document-scanning vocabulary, the JD scanner — and
+    a boundary implemented only inside `is_confirmed()` would be bypassed by
+    every one of them. Making the denied term absent from the set means there
+    is no read path that can see it as experience. `denied` keeps the record
+    for audit; `sources` keeps whatever positively claimed it, so the
+    contradiction stays visible instead of being erased.
+    """
 
     terms: set[str] = field(default_factory=set)
     sources: dict[str, list[str]] = field(default_factory=dict)
+    denied: dict[str, Denied] = field(default_factory=dict)
 
     def is_confirmed(self, term: str) -> bool:
-        return normalise(term) in self.terms
+        key = normalise(term)
+        if key in self.denied:
+            return False  # redundant by construction, and deliberately kept
+        return key in self.terms
 
     def unconfirmed(self, terms: list[str]) -> list[str]:
         return [t for t in terms if not self.is_confirmed(t)]
 
+    def denial_kind(self, term: str) -> str | None:
+        entry = self.denied.get(normalise(term))
+        return entry.kind if entry else None
+
+    def is_denied(self, term: str) -> bool:
+        return normalise(term) in self.denied
+
+    def contradicted(self) -> list[str]:
+        """Denied terms that a positive career source had also established.
+
+        These are the real conflicts: a document says the technology is there,
+        the user says it is not. The denial wins for confirmation, but the
+        disagreement is reported rather than quietly dropped.
+        """
+        return sorted(t for t in self.denied if self.sources.get(t))
+
 
 def mentions(text: str, term: str) -> bool:
     """Whole-term match, tolerant of punctuation but not of substrings —
@@ -225,15 +276,22 @@
 def assemble_confirmed(
     profile: CareerProfile,
     documents: list[dict] | None = None,
+    denials: list | None = None,
 ) -> ConfirmedExperience:
-    """Union of everything any career source establishes.
+    """Union of everything any career source establishes, minus what the user denies.
 
     documents: [{"authority": ..., "title": ..., "text": ...}]
+    denials:   [Denied(...)] — explicit negative statements by the user
 
     Every career authority contributes positively. A tailored resume adds
     what it mentions and NEVER removes what it omits — the rule that lets a
     technology confirmed in the profile survive its absence from last week's
     resume.
+
+    A DENIAL is the one and only thing that subtracts, and it subtracts only
+    because it comes from the user rather than from a document. The two rules
+    are not in tension: "omission is not negative evidence" is about what a
+    document's SILENCE means, and a denial is not silence.
     """
     confirmed = ConfirmedExperience()
 
@@ -278,6 +336,38 @@
             if _mentions(text, term):
                 add(term, label)
 
+    return _apply_denials(confirmed, denials)
+
+
+def _apply_denials(
+    confirmed: ConfirmedExperience, denials: list | None
+) -> ConfirmedExperience:
+    """The single chokepoint where an explicit user denial takes effect.
+
+    Every path that produces a ConfirmedExperience ends here, so there is one
+    place to read, one place to test and no way for a downstream consumer to
+    obtain a ConfirmedExperience whose denials were never applied.
+
+    Two things happen, and the second is as important as the first:
+
+      1. the term is removed from `terms`, so no reader can see it as
+         experience — not `is_confirmed`, not the prompt truth set, not the
+         JD scanner
+      2. `sources` is left INTACT. If a career document had established the
+         term, that record survives so the contradiction can be reported
+         through the conflict mechanism. Deleting it would make the denial
+         look uncontested when it is not.
+    """
+    for denied in denials or []:
+        key = normalise(getattr(denied, "term", "") or "")
+        if not key:
+            continue
+        confirmed.terms.discard(key)
+        confirmed.denied[key] = Denied(
+            term=key,
+            kind=getattr(denied, "kind", "never_used"),
+            statement=getattr(denied, "statement", ""),
+        )
     return confirmed
 
 
@@ -343,6 +433,41 @@
     return sorted(supported), sorted(unsupported)
 
 
+def denial_vocabulary() -> list[str]:
+    """Technology names a denial may name, longest first.
+
+    Technologies only. The user's own stack (DEFAULT_TECHNOLOGIES) plus the
+    names the JD scanner recognises but the career does not claim
+    (FOREIGN_TECHNOLOGIES) — denying GCP has to work even though GCP is not in
+    the career, since that is the common case. Aliases are included so
+    "I never used Azure Kubernetes Service" denies the same thing as
+    "I never used AKS".
+
+    Domain phrases are deliberately excluded: "no automation experience" is a
+    far broader claim than a deterministic matcher should act on, and reading
+    it narrowly would be worse than not reading it at all.
+    """
+    technologies = {normalise(t) for t in DEFAULT_TECHNOLOGIES}
+    technologies |= {t.lower() for t in FOREIGN_TECHNOLOGIES}
+    spellings = set(technologies)
+    spellings |= {a for a, canonical in ALIASES.items() if canonical in technologies}
+    spellings |= set(_JD_ALIASES)
+    return sorted(spellings, key=len, reverse=True)
+
+
+def normalise_denial_term(term: str) -> str:
+    """Canonical name for a denied technology.
+
+    Runs both alias maps, because a denial can name a technology from either
+    vocabulary and the two maps canonicalise different halves of it —
+    "azure kubernetes service" through ALIASES, "google cloud" through the
+    JD aliases. Without both, "I never used Google Cloud" and "I never used
+    GCP" would deny two different things.
+    """
+    key = normalise(term)
+    return _JD_ALIASES.get(key, key)
+
+
 def detect_role_family(jd_text: str) -> tuple[str, list[str]]:
     """Identify the target role family from the JD and return its emphasis.
 
===== src/council/documents/conflicts.py =====
@@ -59,6 +59,14 @@
 
 CONFLICT_KINDS = ("role_dates", "education", "certification")
 
+# A denied technology that a career document also asserts. Kept out of
+# CONFLICT_KINDS because it is not extracted from document text by
+# `find_conflicts` — it comes from the assembled ConfirmedExperience — but it
+# is persisted and surfaced through exactly the same mechanism, which is the
+# point: the user should not have to learn a second place to look for
+# "my sources disagree about my career".
+CONFLICT_EXPERIENCE_DENIED = "experience_denied"
+
 
 def _norm_month_year(value: str) -> str:
     value = re.sub(r"\s+", " ", value.strip().lower().replace(".", ""))
@@ -146,6 +154,33 @@
     return conflicts
 
 
+def denial_conflicts(confirmed) -> list[Conflict]:
+    """Where a career source claims a technology the user says they never used.
+
+    This is NOT symmetrical with the date conflicts above. There, two sources
+    of equal standing disagree and the fact is withheld from both. Here the
+    outcome is already decided — the user outranks a document about their own
+    career, so the term stays unconfirmed — and the conflict exists to make
+    that visible and auditable rather than to ask a question.
+
+    Recording it matters because the alternative is silent disagreement: a
+    master resume that lists Harness, a user who says they never used it, and
+    nothing anywhere pointing out that one of the two needs correcting.
+    """
+    conflicts: list[Conflict] = []
+    for term in confirmed.contradicted():
+        denied = confirmed.denied[term]
+        values = [
+            {"source": source, "value": "used"}
+            for source in sorted(confirmed.sources.get(term, []))
+        ]
+        values.append({"source": "user_statement:denied by you", "value": denied.kind})
+        conflicts.append(
+            Conflict(kind=CONFLICT_EXPERIENCE_DENIED, subject=term, values=values)
+        )
+    return conflicts
+
+
 def disputed_subjects(conflicts: list[Conflict]) -> set[str]:
     """Facts generation must not assert. Withholding is the safe direction:
     a resume missing one date is recoverable, a resume with the wrong date
===== src/council/documents/store.py =====
@@ -7,6 +7,7 @@
 """
 
 import hashlib
+from datetime import UTC, datetime
 
 from sqlalchemy import select
 
@@ -17,6 +18,8 @@
     DEFAULT_DOMAINS,
     DEFAULT_TECHNOLOGIES,
     CareerProfile,
+    Denied,
+    normalise,
 )
 
 
@@ -110,6 +113,160 @@
         return [{"authority": r.authority, "title": r.title, "text": r.text} for r in rows]
 
 
+# --------------------------------------------------- denials (the boundary)
+
+
+async def load_denials() -> list[Denied]:
+    """Every technology the user has explicitly denied, still in force.
+
+    Superseded rows are excluded here rather than by callers: a caller that
+    forgets is a caller that resurrects a denial the user already corrected.
+    """
+    from council.db.models import CareerDenialRow
+
+    async with session_scope() as s:
+        rows = (
+            await s.execute(
+                select(CareerDenialRow).where(CareerDenialRow.active.is_(True))
+            )
+        ).scalars().all()
+        return [
+            Denied(term=r.term, kind=r.kind, statement=r.statement)
+            for r in sorted(rows, key=lambda r: r.term)
+        ]
+
+
+async def record_denials(denials) -> list[str]:
+    """Persist explicit denials. Returns the terms recorded.
+
+    Re-stating a denial refreshes it rather than duplicating it, and
+    re-stating one that was previously superseded puts it back in force — the
+    user changing their mind twice is still the user.
+    """
+    from council.db.models import CareerDenialRow
+
+    recorded: list[str] = []
+    async with session_scope() as s:
+        for denied in denials:
+            term = normalise(getattr(denied, "term", "") or "")
+            if not term:
+                continue
+            row = await s.get(CareerDenialRow, term)
+            if row is None:
+                row = CareerDenialRow(term=term)
+                s.add(row)
+            row.kind = getattr(denied, "kind", "never_used")
+            row.statement = getattr(denied, "statement", "") or ""
+            row.updated_at = datetime.now(UTC)
+            row.active = True
+            row.superseded_at = None
+            row.superseded_by = ""
+            recorded.append(term)
+    return sorted(set(recorded))
+
+
+async def supersede_denials(terms: list[str], statement: str) -> list[str]:
+    """A later positive user statement overrides an earlier denial.
+
+    The user is the primary source on their own career, and "I have never used
+    Harness" in March does not bind them in September once they have used it.
+    So a positive statement wins over an older denial — but never silently.
+    The row survives with `active=False`, the superseding statement and the
+    time it arrived, so the reversal can still be explained months later.
+
+    Only an explicit statement BY THE USER can do this. A document that
+    mentions the technology cannot, however many times it is re-ingested,
+    because a document mentioning Harness is not the user saying they used it.
+    """
+    from council.db.models import CareerDenialRow
+
+    reversed_terms: list[str] = []
+    async with session_scope() as s:
+        for raw in terms:
+            term = normalise(raw)
+            row = await s.get(CareerDenialRow, term)
+            if row is None or not row.active:
+                continue
+            row.active = False
+            row.superseded_at = datetime.now(UTC)
+            row.superseded_by = statement
+            row.updated_at = datetime.now(UTC)
+            reversed_terms.append(term)
+    return sorted(set(reversed_terms))
+
+
+async def list_denials(active_only: bool = False) -> list[dict]:
+    """The denial ledger, for the audit trail and the CLI."""
+    from council.db.models import CareerDenialRow
+
+    async with session_scope() as s:
+        query = select(CareerDenialRow)
+        if active_only:
+            query = query.where(CareerDenialRow.active.is_(True))
+        rows = (await s.execute(query)).scalars().all()
+        return [
+            {
+                "term": r.term,
+                "kind": r.kind,
+                "statement": r.statement,
+                "active": r.active,
+                "created_at": r.created_at.isoformat() if r.created_at else None,
+                "superseded_at": (
+                    r.superseded_at.isoformat() if r.superseded_at else None
+                ),
+                "superseded_by": r.superseded_by,
+            }
+            for r in sorted(rows, key=lambda r: r.term)
+        ]
+
+
+async def apply_instruction_facts(instruction) -> dict:
+    """Persist the durable career facts one instruction carries, in the right order.
+
+    The ORDER is the whole point, so it lives in one function rather than in
+    each caller:
+
+      1. denials are recorded FIRST, so a request that both denies and claims
+         the same technology resolves to denied. An explicit contradiction
+         inside a single request is a hard boundary, not a race.
+      2. positive claims then supersede any OLDER denial they contradict —
+         excluding anything denied in this same request, per rule 1.
+      3. only positive prose is stored as a career source, by the caller.
+
+    Returns what changed, so the caller can put it in the trace.
+    """
+    denied_now = instruction.denied_terms()
+    denials = [
+        Denied(term=term, kind=kind, statement=instruction.denial_text())
+        for term, kind in denied_now.items()
+    ]
+    recorded = await record_denials(denials) if denials else []
+
+    claimed = [t for t in instruction.claimed_terms() if t not in denied_now]
+    superseded = (
+        await supersede_denials(claimed, instruction.career_text()) if claimed else []
+    )
+    return {
+        "denied": recorded,
+        "superseded": superseded,
+        "denials": [d.as_dict() for d in instruction.denials],
+    }
+
+
+async def confirmed_experience():
+    """The one async path to an assembled ConfirmedExperience.
+
+    Profile, documents and denials are loaded together and handed to
+    `assemble_confirmed` in a single place. Every caller that needs to know
+    what the user has done goes through here, which is what stops a new caller
+    quietly assembling confirmed experience with the denial boundary missing.
+    """
+    from council.documents.profile import assemble_confirmed
+
+    profile = await load_profile()
+    return assemble_confirmed(profile, await career_documents(), await load_denials())
+
+
 # ------------------------------------------------------- 2C persistence
 
 
===== src/council/documents/workflow.py =====
@@ -29,7 +29,13 @@
 
 from council.documents import style
 from council.documents.claims import ClaimClass, classify
-from council.documents.conflicts import Conflict, disputed_subjects, find_conflicts
+from council.documents.conflicts import (
+    CONFLICT_EXPERIENCE_DENIED,
+    Conflict,
+    denial_conflicts,
+    disputed_subjects,
+    find_conflicts,
+)
 from council.documents.discovery import (
     DISCOVERY_INSTRUCTION,
     DiscoveryCache,
@@ -42,6 +48,7 @@
 from council.documents.profile import (
     CareerProfile,
     ConfirmedExperience,
+    Denied,
     assemble_confirmed,
     detect_role_family,
     mentions,
@@ -143,6 +150,28 @@
         }
 
 
+def _merge_denials(stored: list | None, instruction: Instruction) -> list[Denied]:
+    """Durable denials plus the ones this request states, this request winning.
+
+    A denial has to bite on the run that states it. "Tailor this for the Azure
+    DevOps role — I have never used Harness" must not produce a Harness bullet
+    and then start behaving next time; that is a defect the user experiences
+    once and stops trusting the system over.
+
+    A positive claim in this request does NOT remove a stored denial here.
+    Superseding is a durable change to the record and belongs with the code
+    that persists it (`store.apply_instruction_facts`), which runs before this
+    and hands back an already-updated `stored` list. Doing it in two places
+    would be two chances to disagree.
+    """
+    merged: dict[str, Denied] = {
+        d.term: d for d in (stored or []) if getattr(d, "term", "")
+    }
+    for term, kind in instruction.denied_terms().items():
+        merged[term] = Denied(term=term, kind=kind, statement=instruction.denial_text())
+    return sorted(merged.values(), key=lambda d: d.term)
+
+
 def _career_context(
     profile: CareerProfile,
     confirmed: ConfirmedExperience,
@@ -169,9 +198,25 @@
         lines.append(f"established achievements: {'; '.join(profile.achievements)}")
     if profile.notes:
         lines.append(f"notes: {profile.notes}")
-    if conflicts:
+    if confirmed.denied:
+        # Denied technologies are already absent from the truth set above, so
+        # this line is not what enforces the boundary — the assembled set is.
+        # It is here because a model that simply never sees "Harness" may still
+        # reach for it off the JD, whereas a model told the user has explicitly
+        # denied it will not. Naming it is cheaper than hoping.
+        denied = "; ".join(
+            f"{d.term} ({d.kind.replace('_', ' ')})"
+            for d in sorted(confirmed.denied.values(), key=lambda d: d.term)
+        )
+        lines.append(
+            "EXPLICITLY DENIED BY THE USER — these are NOT experience and must "
+            "never appear as skills, bullets or summary claims, no matter how "
+            f"strongly the job description asks for them: {denied}"
+        )
+    material = [c for c in conflicts if c.kind != CONFLICT_EXPERIENCE_DENIED]
+    if material:
         disputed = "; ".join(
-            f"{c.subject} ({' vs '.join(c.distinct_values)})" for c in conflicts
+            f"{c.subject} ({' vs '.join(c.distinct_values)})" for c in material
         )
         lines.append(
             "DISPUTED — career sources disagree on these. Do NOT state them. "
@@ -280,12 +325,17 @@
         *,
         cache: DiscoveryCache | None = None,
         trace: WorkflowTrace | None = None,
+        denials: list | None = None,
     ) -> tuple[JDAnalysis, ConfirmedExperience]:
         """Mechanical, except for one conditional cheap call (A2)."""
         trace = trace or WorkflowTrace()
-        confirmed = assemble_confirmed(profile, documents)
+        confirmed = assemble_confirmed(profile, documents, denials)
         family, emphasis = detect_role_family(jd_text)
-        conflicts = find_conflicts(documents)
+        # A denial that contradicts a career source is a real disagreement and
+        # goes through the same conflict channel as a disputed date. Unlike a
+        # date, its outcome is already decided — the user outranks a document
+        # about their own career — so it is recorded to be seen, not resolved.
+        conflicts = find_conflicts(documents) + denial_conflicts(confirmed)
 
         async def ask_model(candidates: list[str]) -> dict:
             # Discovery is an ENHANCEMENT: it widens gap reporting to terms the
@@ -317,6 +367,7 @@
             gaps=discovery.gaps,
             escalated=discovery.escalated,
             conflicts=len(conflicts),
+            denied=sorted(confirmed.denied),
         )
         return JDAnalysis(family, emphasis, discovery, conflicts), confirmed
 
@@ -524,11 +575,23 @@
         *,
         cache: DiscoveryCache | None = None,
         instruction: str | Instruction | None = None,
+        denials: list | None = None,
     ) -> GeneratedResume:
-        """`instruction` is one natural-language line from the user. Its career
-        statements were already turned into a user_statement career source by
-        the caller; only the request-only preferences reach the writing stages,
-        which is what stops "keep it to 2 pages" becoming a career fact."""
+        """`instruction` is one natural-language line from the user. Its positive
+        career statements were already turned into a user_statement career source
+        by the caller; only the request-only preferences reach the writing stages,
+        which is what stops "keep it to 2 pages" becoming a career fact.
+
+        `denials` is the durable set of technologies the user has explicitly
+        said they have not used. It is passed in rather than read here so the
+        workflow stays free of persistence, but a caller that omits it gets a
+        run without the boundary — which is why the API and CLI both go through
+        `store.confirmed_experience()`/`store.load_denials()` and never
+        assemble the set themselves.
+
+        Denials stated in THIS request are folded in below, so a denial takes
+        effect on the very run that states it rather than only the next one.
+        """
         trace = WorkflowTrace()
         self._sources_blob = _sources_blob(documents)
         self._instruction = (
@@ -538,8 +601,16 @@
         if self._instruction.preferences:
             trace.record("user_preferences", count=len(self._instruction.preferences))
 
+        effective_denials = _merge_denials(denials, self._instruction)
+        if effective_denials:
+            trace.record(
+                "denials_applied",
+                terms=sorted({d.term for d in effective_denials}),
+            )
+
         analysis, confirmed = await self.analyse(
-            jd_text, profile, documents, cache=cache, trace=trace
+            jd_text, profile, documents, cache=cache, trace=trace,
+            denials=effective_denials,
         )
         plan = await self.select(jd_text, analysis, profile, confirmed, trace)
         draft = await self.draft(jd_text, analysis, plan, profile, confirmed, trace)
===== src/council/documents/__init__.py =====
@@ -1,24 +1,38 @@
-from council.documents.claims import ClaimClass, ClaimFinding, classify
-from council.documents.extract import ExtractionError, extract
-from council.documents.profile import (
-    CareerProfile,
-    ConfirmedExperience,
-    assemble_confirmed,
-    detect_role_family,
-)
-from council.documents.style import blocking_violations, check, prompt_guidance
-
-__all__ = [
-    "CareerProfile",
-    "ClaimClass",
-    "ClaimFinding",
-    "ConfirmedExperience",
-    "ExtractionError",
-    "assemble_confirmed",
-    "blocking_violations",
-    "check",
-    "classify",
-    "detect_role_family",
-    "extract",
-    "prompt_guidance",
-]
+from council.documents.claims import ClaimClass, ClaimFinding, classify
+from council.documents.extract import ExtractionError, extract
+from council.documents.instructions import (
+    NEVER_USED,
+    NOT_PROFESSIONAL,
+    STUDIED_ONLY,
+    Denial,
+    Instruction,
+)
+from council.documents.profile import (
+    CareerProfile,
+    ConfirmedExperience,
+    Denied,
+    assemble_confirmed,
+    detect_role_family,
+)
+from council.documents.style import blocking_violations, check, prompt_guidance
+
+__all__ = [
+    "NEVER_USED",
+    "NOT_PROFESSIONAL",
+    "STUDIED_ONLY",
+    "CareerProfile",
+    "ClaimClass",
+    "ClaimFinding",
+    "ConfirmedExperience",
+    "Denial",
+    "Denied",
+    "ExtractionError",
+    "Instruction",
+    "assemble_confirmed",
+    "blocking_violations",
+    "check",
+    "classify",
+    "detect_role_family",
+    "extract",
+    "prompt_guidance",
+]
===== src/council/db/models.py =====
@@ -243,6 +243,36 @@
     resolved: Mapped[bool] = mapped_column(Boolean, default=False)
 
 
+class CareerDenialRow(Base):
+    """A technology the user has explicitly said they have not used.
+
+    A durable NEGATIVE career fact, stored per-term rather than as prose. Prose
+    was the defect: a denial kept as text is indistinguishable from a positive
+    career source, and every technology name it contains gets confirmed by the
+    document scanner. Structure is what makes the boundary enforceable.
+
+    Rows are never deleted. A later positive statement from the user supersedes
+    the denial — they are the primary source on their own career and are
+    allowed to correct themselves, or to have learned the technology since —
+    but the superseded row stays with both statements and both timestamps, so
+    the reversal is auditable rather than silent.
+    """
+
+    __tablename__ = "career_denials"
+
+    term: Mapped[str] = mapped_column(String(120), primary_key=True)  # normalised
+    kind: Mapped[str] = mapped_column(String(32), default="never_used")
+    # never_used | not_professional | studied_only
+    statement: Mapped[str] = mapped_column(Text, default="")  # the user's own words
+    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
+    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
+    active: Mapped[bool] = mapped_column(Boolean, default=True)
+    superseded_at: Mapped[datetime | None] = mapped_column(
+        DateTime(timezone=True), nullable=True
+    )
+    superseded_by: Mapped[str] = mapped_column(Text, default="")  # the positive claim
+
+
 class ArtifactRow(Base):
     """A generated document and the trace behind it.
 
===== src/council/db/status.py =====
@@ -29,6 +29,7 @@
     ("0007", ["technology_cache", "source_conflicts", "artifacts"], []),
     ("0008", [], ["outcome_kind"]),
     ("0009", [], ["data_class"]),
+    ("0010", ["career_denials"], []),
 ]
 
 HEAD = REVISION_EVIDENCE[-1][0]
===== src/council/api/main.py =====
@@ -16,7 +16,6 @@
 from council.documents.profile import (
     AUTHORITY_JD,
     CAREER_AUTHORITIES,
-    assemble_confirmed,
     detect_role_family,
     scan_jd_technologies,
 )
@@ -559,13 +558,23 @@
 @app.get("/career-profile")
 async def get_career_profile():
     """The profile plus the assembled confirmed experience, showing which
-    source established each term. Additive only — no source subtracts."""
+    source established each term.
+
+    Documents are additive only — no document subtracts. The one thing that
+    does subtract is the user explicitly saying they have not used something,
+    which is reported separately rather than folded in silently: seeing why a
+    technology is missing matters as much as seeing that it is.
+    """
+    from council.documents.store import confirmed_experience, list_denials
+
     profile = await load_profile()
-    confirmed = assemble_confirmed(profile, await career_documents())
+    confirmed = await confirmed_experience()
     return {
         "profile": profile.as_dict(),
         "confirmed": sorted(confirmed.terms),
         "sources": {k: v for k, v in sorted(confirmed.sources.items())},
+        "denied": {t: d.as_dict() for t, d in sorted(confirmed.denied.items())},
+        "denial_ledger": await list_denials(),
     }
 
 
@@ -584,9 +593,10 @@
     jd_text = body.get("text", "")
     if not jd_text.strip():
         raise HTTPException(status_code=422, detail="jd text required")
+    from council.documents.store import confirmed_experience
+
     family, emphasis = detect_role_family(jd_text)
-    profile = await load_profile()
-    confirmed = assemble_confirmed(profile, await career_documents())
+    confirmed = await confirmed_experience()
     tech_supported, tech_unsupported = scan_jd_technologies(jd_text, confirmed)
     return {
         "role_family": family,
@@ -596,6 +606,9 @@
         # The honest answer to "what does this role want that I can't claim?"
         "technologies_supported": tech_supported,
         "technologies_unsupported": tech_unsupported,
+        # A gap the user has already ruled out. Reporting it as a plain gap
+        # would invite the same question again on the next Azure DevOps JD.
+        "technologies_denied": [t for t in tech_unsupported if confirmed.is_denied(t)],
     }
 
 
@@ -623,6 +636,8 @@
     from council.documents.instructions import parse as parse_instruction
     from council.documents.render import render_docx
     from council.documents.store import (
+        apply_instruction_facts,
+        load_denials,
         load_discovery_cache,
         save_artifact,
         save_conflicts,
@@ -641,20 +656,30 @@
     if not jd_text:
         raise HTTPException(status_code=422, detail="a job description is required")
 
-    # One line of natural language carries two different things. The durable
-    # career facts become a real career source with user_statement provenance;
-    # the request-only preferences never touch the profile.
+    # One line of natural language carries three different things. POSITIVE
+    # career facts become a real career source with user_statement provenance.
+    # NEGATIVE ones become durable per-term denials and are NEVER stored as
+    # career prose — storing them there is precisely what turned "I have never
+    # used Harness" into confirmed Harness experience. Request-only preferences
+    # never touch the profile at all.
     instruction = parse_instruction(body.instruction)
+    facts = await apply_instruction_facts(instruction)
     if instruction.has_career_statements:
         await _store_user_statement(instruction.career_text())
 
     profile = await load_profile()
     documents = await career_documents()
+    denials = await load_denials()
     cache = await load_discovery_cache()
     workflow = build_resume_workflow()
     try:
         result = await workflow.run(
-            jd_text, profile, documents, cache=cache, instruction=instruction
+            jd_text,
+            profile,
+            documents,
+            cache=cache,
+            instruction=instruction,
+            denials=denials,
         )
     except GenerationFailed as e:
         raise HTTPException(status_code=502, detail=str(e)) from None
@@ -680,6 +705,11 @@
             "review": result.review.model_dump() if result.review else None,
             "findings": result.findings,
             "instruction": instruction.as_dict(),
+            # What this request changed about the durable career record. A
+            # denial that took effect, or a denial the user reversed, is a
+            # bigger deal than any wording choice in the document and belongs
+            # in the trace where it can be found later.
+            "career_facts": facts,
             **result.trace.as_dict(),
         },
         cost_usd=result.trace.cost_usd,
@@ -701,14 +731,22 @@
         "model_calls": result.trace.model_calls,
         "download_url": f"/artifacts/{artifact_id}/download",
         "instruction": instruction.as_dict(),
+        "career_facts": facts,
     }
 
 
 async def _store_user_statement(text: str) -> None:
-    """Persist an explicit career statement as its own career source.
+    """Persist an explicit POSITIVE career statement as its own career source.
 
     Kept distinct from document-derived evidence so the sources map can still
     answer "who established this?" honestly.
+
+    POSITIVE is not a caveat, it is the precondition. Whatever reaches this
+    function is scanned for technology names by `assemble_confirmed`, and every
+    name found becomes confirmed professional experience. A negative sentence
+    arriving here would confirm exactly the technologies it denies, which is
+    the defect this whole path was reworked to prevent — so denials are split
+    out by the parser and can never be part of `career_text()`.
     """
     from council.documents.extract import Extracted
     from council.documents.profile import AUTHORITY_USER_STATEMENT
===== src/council/cli.py =====
@@ -10,14 +10,16 @@
 from council.documents.profile import (
     AUTHORITY_JD,
     CAREER_AUTHORITIES,
-    assemble_confirmed,
     detect_role_family,
     scan_jd_technologies,
 )
 from council.documents.render import render_docx
 from council.documents.store import (
     career_documents,
+    confirmed_experience,
     list_conflicts,
+    list_denials,
+    load_denials,
     load_discovery_cache,
     load_profile,
     resolve_conflict,
@@ -164,11 +166,18 @@
 
     async def _run():
         await _ensure_schema()
-        p = await load_profile()
-        return p, assemble_confirmed(p, await career_documents())
+        return await load_profile(), await confirmed_experience()
 
     p, confirmed = asyncio.run(_run())
     typer.echo(f"{len(confirmed.terms)} confirmed terms")
+    if confirmed.denied:
+        typer.echo(
+            "explicitly NOT experience (your own words): "
+            + ", ".join(
+                f"{d.term} ({d.kind.replace('_', ' ')})"
+                for d in sorted(confirmed.denied.values(), key=lambda d: d.term)
+            )
+        )
     if p.employers:
         typer.echo(f"employers: {', '.join(p.employers)}")
     if p.roles:
@@ -223,8 +232,7 @@
         except ExtractionError as e:
             typer.echo(f"cannot read {file.name}: {e}", err=True)
             raise typer.Exit(1) from None
-        p = await load_profile()
-        return extracted.text, assemble_confirmed(p, await career_documents())
+        return extracted.text, await confirmed_experience()
 
     text, confirmed = asyncio.run(_run())
     family, emphasis = detect_role_family(text)
@@ -236,6 +244,11 @@
     typer.echo(f"unsupported emphasis: {', '.join(unsupported) or '(none)'}")
     typer.echo(f"JD technologies you have:    {', '.join(tech_supported) or '(none)'}")
     typer.echo(f"JD technologies you do NOT:  {', '.join(tech_unsupported) or '(none)'}")
+    denied_here = [t for t in tech_unsupported if confirmed.is_denied(t)]
+    if denied_here:
+        typer.echo(
+            f"  of those, already ruled out by you: {', '.join(denied_here)}"
+        )
     if unsupported or tech_unsupported:
         typer.echo(
             "\nUnsupported means no career source establishes it. The resume will "
@@ -273,7 +286,9 @@
         documents = await career_documents()
         cache = await load_discovery_cache()
         workflow = build_resume_workflow()
-        result = await workflow.run(jd.text, p, documents, cache=cache)
+        result = await workflow.run(
+            jd.text, p, documents, cache=cache, denials=await load_denials()
+        )
         await save_discovery_cache(cache)
         await save_conflicts(result.analysis.conflicts)
         path = render_docx(result.draft, out, name=name, contact=contact)
@@ -347,6 +362,34 @@
         typer.echo(f"    resolve with: council resolve-conflict {row['id']} \"<value>\"")
 
 
+@app.command()
+def denials():
+    """Technologies you have explicitly said you have not used.
+
+    A denial outranks any document that mentions the technology, so nothing
+    listed here can appear on a generated resume. Superseded rows are shown
+    too: a reversal is a change to your career record and should be as
+    inspectable as the denial it replaced.
+    """
+
+    async def _run():
+        await _ensure_schema()
+        return await list_denials()
+
+    rows = asyncio.run(_run())
+    if not rows:
+        typer.echo("no denials recorded")
+        return
+    for row in rows:
+        state = "active" if row["active"] else "superseded"
+        typer.echo(f"[{row['kind']}] {row['term']}  ({state})")
+        if row["statement"]:
+            typer.echo(f"    you said: {row['statement']}")
+        if not row["active"]:
+            typer.echo(f"    later claimed: {row['superseded_by']}")
+            typer.echo(f"    reversed at: {row['superseded_at']}")
+
+
 @app.command("resolve-conflict")
 def resolve_conflict_cmd(conflict_id: str, value: str):
     """Settle a disputed fact so documents can state it again."""
```

### 6.3 The over-denial trap (found by a test, worth your attention)

First implementation of `technology_terms()` scanned the vocabulary and collected
every whole-word match. A test caught this:

```
>>> technology_terms("I never used Azure Kubernetes Service")
['aks', 'azure', 'kubernetes']     # WRONG
```

"Azure Kubernetes Service" contains "Azure" and "Kubernetes" as whole words, so
one narrow denial erased two of Amith's strongest technologies.

Fixed by longest-match-wins with the matched span blanked out, so a shorter name
nested inside a longer one cannot also match:

```
>>> technology_terms("I never used Azure Kubernetes Service")
['aks']
>>> technology_terms("I never used Azure Kubernetes Service or Jenkins")
['aks', 'jenkins']
```

Note the deliberate asymmetry: **positive assembly still matches all three**,
because "Azure Kubernetes Service" genuinely evidences Azure and Kubernetes. Only
denial is longest-match — the user denied one specific thing.

The reasoning recorded in the code: over-denial is worse than the bug being
fixed. The original defect ADDS experience the user does not have; over-denial
DELETES experience they do.

---

## 7. Existing tests changed (all three, in full)

**`tests/test_db_status.py`** — one line, the new table in the fixture:

```diff
 ALL_TABLES = [
     "requests", "steps", "conversations", "evidence_items", "claim_assessments",
     "budget_settings", "documents", "career_profile", "technology_cache",
-    "source_conflicts", "artifacts",
+    "source_conflicts", "artifacts", "career_denials",
 ]
```

**`tests/test_migrations.py`** — added assertion, nothing removed:

```diff
+    # 0010: durable negative career facts. Without this table a denial only
+    # lives for one request, and "I have never used Harness" has to be repeated
+    # on every resume or the technology comes back.
+    assert "career_denials" in tables
+    assert {"term", "kind", "statement", "active", "superseded_by"} <= columns(
+        "career_denials"
+    )
     # 0009: routing statistics must never mix data populations.
```

**`tests/test_resume_api.py::test_an_empty_instruction_is_harmless`** — `as_dict()`
gained a third key:

```diff
 def test_an_empty_instruction_is_harmless():
-    assert parse(None).as_dict() == {"career_statements": [], "preferences": []}
+    assert parse(None).as_dict() == {
+        "career_statements": [],
+        "preferences": [],
+        "denials": [],
+    }
     assert parse("   ").preferences == []
+    assert parse("   ").denials == []
```

---

## 8. New tests — `tests/test_negation.py` (88)

Full file:

```python
"""Explicit user contradiction is a hard boundary.

The defect these tests exist to prevent, in full:

    instruction: "I have never used Harness or Jenkins"
    -> the instruction parser matched the first-person experience pattern
       (it IS a first-person sentence containing "used")
    -> the sentence was stored as a user_statement career source
    -> assemble_confirmed scanned that source for technology names
    -> Harness and Jenkins became CONFIRMED PROFESSIONAL EXPERIENCE
    -> both were eligible for a submitted resume

A denial reaching positive career prose confirms exactly the technologies it
denies. That is the failure mode, and it is why several tests below assert on
the shape of the stored data rather than only on `is_confirmed()`: a fix that
returns the right answer from one accessor while leaving the denied term in
`confirmed.terms` would leave every direct reader of that set still wrong.

The invariant:

    positive source mention + explicit user denial
      -> denial wins for career confirmation
      -> the conflict stays visible and auditable
      -> the technology cannot silently become professional experience
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from council.api.main import app
from council.db.models import CareerDenialRow, DocumentRow
from council.db.session import session_scope
from council.documents.conflicts import CONFLICT_EXPERIENCE_DENIED, denial_conflicts
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
    AUTHORITY_USER_STATEMENT,
    CareerProfile,
    Denied,
    assemble_confirmed,
    scan_jd_technologies,
)
from council.documents.store import (
    apply_instruction_facts,
    confirmed_experience,
    list_denials,
    load_denials,
    record_denials,
    store_document,
)
from council.documents.workflow import _career_context, _merge_denials
from tests.test_resume_api import (
    GCP_JD,
    _client,
    _upload_master,
)


def _profile(**kwargs) -> CareerProfile:
    """A profile with nothing seeded, so a confirmed term can only have come
    from the document or denial under test."""
    kwargs.setdefault("technologies", [])
    kwargs.setdefault("domains", [])
    return CareerProfile(**kwargs)


# --------------------------------------------------------------------------
# 1. The parser must not read a denial as a career claim
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "I have never used Harness",
        "I never worked with Jenkins",
        "I have not used Harness",
        "I haven't worked with Jenkins",
        "I have no professional GCP experience",
        "I only studied GCP",
        "I have no experience with Datadog",
        "I don't have any Kafka experience",
        "I have never touched OpenShift",
        "I have zero experience with Puppet",
        "I've never really used Chef",
        "I have no hands-on experience with Spinnaker",
        "I have no exposure to Anthos",
        "I only ever studied Terraform Enterprise",
        "I only used Rancher in a home lab",
        "My Istio experience is personal projects only",
        "I have never used Harness professionally",
    ],
)
def test_a_denial_is_never_a_positive_career_statement(sentence):
    """The single most important assertion in the file.

    `career_statements` is what becomes a user_statement career source, and a
    career source is scanned for technology names. Anything that lands here is
    a technology the system will treat as professional experience.
    """
    parsed = parse(sentence)
    assert parsed.career_statements == []
    assert parsed.denials, f"not detected as a denial: {sentence!r}"
    assert parsed.career_text() == ""


@pytest.mark.parametrize(
    ("sentence", "kind"),
    [
        ("I have never used Harness", NEVER_USED),
        ("I never worked with Jenkins", NEVER_USED),
        ("I haven't worked with Jenkins", NEVER_USED),
        ("I have no experience with Datadog", NEVER_USED),
        ("I have no professional GCP experience", NOT_PROFESSIONAL),
        ("I have no production experience with Kafka", NOT_PROFESSIONAL),
        ("I have never used Harness professionally", NOT_PROFESSIONAL),
        ("I only studied GCP", STUDIED_ONLY),
        ("I only used Rancher in a home lab", STUDIED_ONLY),
        ("My Istio experience is personal projects only", STUDIED_ONLY),
    ],
)
def test_the_kind_of_denial_is_recorded(sentence, kind):
    """All three block confirmation; they are not the same statement.

    "I only studied GCP" and "I have never used GCP" are both true reasons GCP
    is not professional experience, and flattening them would throw away the
    user's actual words for no benefit.
    """
    assert classify_negation(sentence) == kind
    assert parse(sentence).denials[0].kind == kind


def test_a_positive_claim_still_parses_as_a_career_statement():
    """The fix must not swing the other way. Negation handling that swallows
    real claims would quietly erase experience the user does have."""
    parsed = parse("I also have professional Harness experience.")
    assert parsed.career_statements == ["I also have professional Harness experience."]
    assert parsed.denials == []


def test_preferences_are_still_preferences():
    parsed = parse("Emphasise AKS and keep it to 2 pages.")
    assert parsed.career_statements == []
    assert parsed.denials == []
    assert parsed.preferences == ["Emphasise AKS and keep it to 2 pages."]


def test_one_instruction_can_carry_all_three():
    parsed = parse(
        "I also have professional Harness experience. I have never used Jenkins. "
        "Emphasise AKS and keep it to 2 pages."
    )
    assert parsed.career_statements == ["I also have professional Harness experience."]
    assert [d.kind for d in parsed.denials] == [NEVER_USED]
    assert parsed.denials[0].terms == ["jenkins"]
    assert parsed.preferences == ["Emphasise AKS and keep it to 2 pages."]
    # And the positive prose that gets persisted contains no denial text.
    assert "never" not in parsed.career_text().lower()


def test_a_denial_naming_several_technologies_covers_all_of_them():
    parsed = parse("I have never used Harness or Jenkins")
    assert parsed.denied_terms() == {"harness": NEVER_USED, "jenkins": NEVER_USED}


def test_denial_terms_are_canonicalised():
    """Aliases from both vocabularies, or "never used AKS" and "never used
    Azure Kubernetes Service" would deny two different things."""
    assert technology_terms("I never used Azure Kubernetes Service") == ["aks"]
    assert technology_terms("I have no experience with Google Cloud Platform") == ["gcp"]
    assert technology_terms("I have never used k8s") == ["kubernetes"]


def test_a_longer_technology_name_does_not_deny_the_names_inside_it():
    """The over-denial trap.

    "Azure Kubernetes Service" contains "Azure" and "Kubernetes" as whole
    words. Denying AKS must not erase two of the strongest things in the
    career — that failure is worse than the one being fixed, because it
    deletes real experience instead of adding false experience.
    """
    assert technology_terms("I have never used Azure Kubernetes Service") == ["aks"]
    assert technology_terms("I have no experience with Google Kubernetes Engine") == [
        "gke"
    ]
    assert technology_terms("I have never used Azure Key Vault") == ["key vault"]


def test_two_separately_named_technologies_are_both_denied():
    """Consuming the matched span must not swallow a genuinely separate name."""
    assert technology_terms("I have never used Harness or Jenkins") == [
        "harness",
        "jenkins",
    ]
    assert technology_terms("I never used Azure Kubernetes Service or Jenkins") == [
        "aks",
        "jenkins",
    ]


def test_a_denial_of_an_unknown_technology_is_still_a_denial():
    """It blocks nothing specific — the name is not in the vocabulary — but it
    must still be kept out of positive career prose, which is the half that
    causes false confirmation."""
    parsed = parse("I have never used Wibbletron")
    assert parsed.career_statements == []
    assert parsed.denials[0].terms == []


def test_an_empty_instruction_produces_no_denials():
    assert parse(None).denials == []
    assert parse("   ").denials == []


# --------------------------------------------------------------------------
# 2. The named cases from the review
# --------------------------------------------------------------------------


def _confirmed_after(statement: str, document_text: str = "") -> object:
    """Assemble confirmed experience the way the live path does: the denial is
    extracted from the instruction, the positive prose (if any) is stored as a
    career source."""
    parsed = parse(statement)
    documents = []
    if parsed.career_text():
        documents.append(
            {
                "authority": AUTHORITY_USER_STATEMENT,
                "title": "Stated by you",
                "text": parsed.career_text(),
            }
        )
    if document_text:
        documents.append(
            {
                "authority": AUTHORITY_MASTER_RESUME,
                "title": "Master resume",
                "text": document_text,
            }
        )
    denials = [
        Denied(term=t, kind=k, statement=statement)
        for t, k in parsed.denied_terms().items()
    ]
    return assemble_confirmed(_profile(), documents, denials)


def test_never_used_harness_does_not_confirm_harness():
    confirmed = _confirmed_after("I have never used Harness")
    assert confirmed.is_confirmed("harness") is False
    assert "harness" not in confirmed.terms


def test_never_worked_with_jenkins_does_not_confirm_jenkins():
    confirmed = _confirmed_after("I never worked with Jenkins")
    assert confirmed.is_confirmed("jenkins") is False
    assert "jenkins" not in confirmed.terms


def test_no_professional_gcp_experience_is_not_professional_experience():
    confirmed = _confirmed_after("I have no professional GCP experience")
    assert confirmed.is_confirmed("gcp") is False
    assert confirmed.denial_kind("gcp") == NOT_PROFESSIONAL


def test_only_studied_gcp_is_not_professional_experience():
    confirmed = _confirmed_after("I only studied GCP")
    assert confirmed.is_confirmed("gcp") is False
    assert confirmed.denial_kind("gcp") == STUDIED_ONLY


def test_the_original_defect_sentence():
    """Verbatim from the review. Both technologies, one sentence."""
    confirmed = _confirmed_after("I have never used Harness or Jenkins")
    assert confirmed.is_confirmed("harness") is False
    assert confirmed.is_confirmed("jenkins") is False
    assert {"harness", "jenkins"}.isdisjoint(confirmed.terms)


def test_a_document_mention_plus_a_denial_means_the_denial_wins():
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Azure DevOps pipelines with Harness and Terraform.",
    )
    assert confirmed.is_confirmed("harness") is False
    # Terraform came from the same document and is untouched — a denial is
    # surgical, not a reason to distrust the whole source.
    assert confirmed.is_confirmed("terraform") is True


def test_the_document_that_claimed_it_is_still_recorded():
    """`sources` must survive the denial. Erasing it would make the conflict
    invisible and the denial look uncontested when it is not."""
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Azure DevOps pipelines with Harness.",
    )
    assert confirmed.sources["harness"] == ["master_resume:Master resume"]
    assert confirmed.contradicted() == ["harness"]


def test_the_contradiction_is_recorded_as_a_source_conflict():
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Azure DevOps pipelines with Harness.",
    )
    conflicts = denial_conflicts(confirmed)
    assert [c.kind for c in conflicts] == [CONFLICT_EXPERIENCE_DENIED]
    assert conflicts[0].subject == "harness"
    sources = {v["source"] for v in conflicts[0].values}
    assert "master_resume:Master resume" in sources
    assert any("denied by you" in s for s in sources)


def test_no_conflict_when_nothing_claimed_it():
    """A denial of something no source ever asserted is not a disagreement. It
    would be noise in the conflicts list and would train the user to ignore it."""
    confirmed = _confirmed_after("I have never used Harness")
    assert denial_conflicts(confirmed) == []


# --------------------------------------------------------------------------
# 3. Central enforcement — no reader can bypass the boundary
# --------------------------------------------------------------------------


def test_the_denied_term_is_absent_from_the_raw_term_set():
    """The structural half of the fix.

    `terms` is read directly by the prompt's truth set, the document-scanning
    vocabulary and the JD scanner. A boundary living only inside
    `is_confirmed()` would be bypassed by every one of them.
    """
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Harness pipelines.",
    )
    assert "harness" not in confirmed.terms
    assert confirmed.unconfirmed(["harness"]) == ["harness"]


def test_the_jd_scanner_reports_a_denied_technology_as_unsupported():
    confirmed = _confirmed_after(
        "I have never used Jenkins",
        document_text="Ran Jenkins jobs nightly.",
    )
    supported, unsupported = scan_jd_technologies(
        "You will maintain Jenkins pipelines.", confirmed
    )
    assert "jenkins" in unsupported
    assert "jenkins" not in supported


def test_the_prompt_truth_set_excludes_denied_technologies():
    """What the model is allowed to treat as true must not contain it, and the
    model is additionally told outright — a model that simply never sees
    Harness may still reach for it off the JD."""
    confirmed = _confirmed_after(
        "I have never used Harness",
        document_text="Ran Harness pipelines and Terraform.",
    )
    context = _career_context(_profile(), confirmed, [])
    truth_line = next(
        line for line in context.splitlines() if line.startswith("technologies")
    )
    assert "harness" not in truth_line
    assert "terraform" in truth_line
    assert "EXPLICITLY DENIED BY THE USER" in context
    assert "harness" in context


def test_denial_conflicts_do_not_pollute_the_disputed_line():
    """The DISPUTED line means "sources disagree, omit the fact". A denial is
    already decided, so mixing the two would misdescribe both."""
    confirmed = _confirmed_after(
        "I have never used Harness", document_text="Ran Harness pipelines."
    )
    context = _career_context(_profile(), confirmed, denial_conflicts(confirmed))
    assert "DISPUTED" not in context
    assert "EXPLICITLY DENIED BY THE USER" in context


def test_assemble_confirmed_without_denials_is_unchanged():
    """Existing callers keep their behaviour; the parameter is additive."""
    documents = [
        {
            "authority": AUTHORITY_MASTER_RESUME,
            "title": "m",
            "text": "Terraform and Harness.",
        }
    ]
    confirmed = assemble_confirmed(_profile(), documents)
    assert confirmed.is_confirmed("harness") is True
    assert confirmed.denied == {}


# --------------------------------------------------------------------------
# 4. Adversarial phrasing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "I have never used Harness",
        "I have not used Harness",
        "I haven't worked with Harness",
        "I have no professional experience with Harness",
        "I only studied Harness",
        "I only have personal experience with Harness",
        "My Harness experience is lab only",
        "I have never worked with Harness",
        "I hadn't used Harness at any point",
        "I do not have any Harness experience",
        "I have no hands-on experience with Harness",
        "I have no prior experience with Harness",
        "I never really used Harness",
        "I have zero exposure to Harness",
        "I have no exposure to Harness",
    ],
)
def test_adversarial_negation_phrasing_all_block_confirmation(sentence):
    confirmed = _confirmed_after(
        sentence, document_text="Ran Azure DevOps pipelines with Harness."
    )
    assert confirmed.is_confirmed("harness") is False, sentence
    assert confirmed.denial_kind("harness") is not None, sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "I have used Harness in production",
        "I have professional Harness experience",
        "I ran Harness pipelines for two years",
        "I built our Harness deployment templates",
    ],
)
def test_positive_phrasing_is_not_mistaken_for_a_denial(sentence):
    """The inverse guard. Over-eager negation would erase real experience,
    which is the one failure worse than the one being fixed."""
    assert classify_negation(sentence) is None
    assert parse(sentence).career_statements == [sentence]


# --------------------------------------------------------------------------
# 5. Durability — a denial survives re-ingestion and outlives the request
# --------------------------------------------------------------------------


async def test_a_denial_persists_and_reloads(db):
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="never")])
    assert [d.term for d in await load_denials()] == ["harness"]


async def test_restating_a_denial_does_not_duplicate_it(db):
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="a")])
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="b")])
    denials = await load_denials()
    assert len(denials) == 1
    assert denials[0].statement == "b"


async def test_reingesting_a_document_leaves_the_denial_authoritative(db):
    """The re-ingestion case from the review.

    A document is not the user asserting anything. However many career sources
    mention Harness, and however many times they are re-uploaded, only the user
    can reverse their own denial.
    """
    await record_denials([Denied(term="harness", kind=NEVER_USED, statement="never")])

    from council.documents.extract import Extracted

    for i in range(3):
        await store_document(
            filename=f"resume-{i}.txt",
            title=f"Master resume v{i}",
            authority=AUTHORITY_MASTER_RESUME,
            # Distinct text each time so content-hash dedup does not hide the
            # effect being tested.
            extracted=Extracted(
                text=f"Ran Harness pipelines and Terraform in year {2020 + i}.",
                char_count=60,
                truncated=False,
                detected_kind="text",
            ),
        )
        confirmed = await confirmed_experience()
        assert confirmed.is_confirmed("harness") is False
        assert confirmed.is_confirmed("terraform") is True

    # Still one denial, still active, never weakened by the repetition.
    ledger = await list_denials()
    assert len(ledger) == 1
    assert ledger[0]["active"] is True


# --------------------------------------------------------------------------
# 6. A positive statement after an earlier denial
#
# Decided rule: the later explicit user statement SUPERSEDES the denial. The
# user is the primary source on their own career and may have learned the
# technology since. It is never silent — the row survives with both statements
# and both timestamps.
# --------------------------------------------------------------------------


async def test_a_later_positive_statement_supersedes_an_earlier_denial(db):
    await apply_instruction_facts(parse("I have never used Harness"))
    assert (await confirmed_experience()).is_confirmed("harness") is False

    await apply_instruction_facts(parse("I have professional Harness experience now"))
    assert await load_denials() == []


async def test_the_reversal_is_recorded_not_erased(db):
    """"Do not silently resolve the contradiction." The row stays, carrying
    the denial, the statement that overrode it and when that happened."""
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have used Harness in production since then"))

    ledger = await list_denials()
    assert len(ledger) == 1
    row = ledger[0]
    assert row["term"] == "harness"
    assert row["active"] is False
    assert row["statement"] == "I have never used Harness"
    assert "used Harness in production" in row["superseded_by"]
    assert row["superseded_at"] is not None


async def test_a_denial_and_a_claim_in_the_same_request_resolve_to_denied(db):
    """Within one request an explicit denial is a hard boundary, not a race.

    Supersession is for a LATER statement — the user changing their mind over
    time. A single instruction that says both things is contradicting itself,
    and the safe reading of a self-contradiction is the negative one.
    """
    await apply_instruction_facts(
        parse("I have professional Harness experience. I have never used Harness.")
    )
    denials = await load_denials()
    assert [d.term for d in denials] == ["harness"]
    assert (await confirmed_experience()).is_confirmed("harness") is False


async def test_a_positive_claim_about_something_else_reverses_nothing(db):
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have professional Jenkins experience"))
    assert [d.term for d in await load_denials()] == ["harness"]


async def test_a_denial_can_be_restated_after_being_superseded(db):
    """The user changing their mind twice is still the user."""
    await apply_instruction_facts(parse("I have never used Harness"))
    await apply_instruction_facts(parse("I have used Harness professionally"))
    await apply_instruction_facts(parse("Actually I have never used Harness"))
    denials = await load_denials()
    assert [d.term for d in denials] == ["harness"]
    assert (await confirmed_experience()).is_confirmed("harness") is False


async def test_superseding_reports_what_changed(db):
    await apply_instruction_facts(parse("I have never used Harness"))
    facts = await apply_instruction_facts(parse("I have used Harness professionally"))
    assert facts["superseded"] == ["harness"]
    assert facts["denied"] == []


# --------------------------------------------------------------------------
# 7. "Only studied" is stored, but never quietly promoted
# --------------------------------------------------------------------------


async def test_studied_only_is_stored_as_structured_career_information(db):
    await apply_instruction_facts(parse("I only studied GCP"))
    ledger = await list_denials()
    assert ledger[0]["term"] == "gcp"
    assert ledger[0]["kind"] == STUDIED_ONLY
    assert ledger[0]["statement"] == "I only studied GCP"


async def test_studied_only_never_becomes_confirmed_experience(db):
    """Contract §4: studied-only must not turn into ATS keyword stuffing. The
    mechanism is simply that it is not in the confirmed set, so nothing that
    writes from confirmed experience can reach it."""
    await apply_instruction_facts(parse("I only studied GCP"))
    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("gcp") is False
    assert "gcp" not in confirmed.terms
    context = _career_context(_profile(), confirmed, [])
    truth_line = next(
        line for line in context.splitlines() if line.startswith("technologies")
    )
    assert "gcp" not in truth_line


def test_studied_only_is_distinguishable_from_never_used():
    """So a later Familiarity/Exposure section can show one and not the other
    without having to re-ask the user."""
    studied = _confirmed_after("I only studied GCP")
    never = _confirmed_after("I have never used GCP")
    assert studied.denial_kind("gcp") == STUDIED_ONLY
    assert never.denial_kind("gcp") == NEVER_USED


# --------------------------------------------------------------------------
# 8. This-request denials bite on this request
# --------------------------------------------------------------------------


def test_a_denial_in_this_request_applies_to_this_run():
    """A denial that only takes effect next time is a defect the user
    experiences once and stops trusting the system over."""
    merged = _merge_denials([], parse("I have never used Harness"))
    assert [d.term for d in merged] == ["harness"]


def test_stored_and_request_denials_merge_with_the_request_winning():
    stored = [Denied(term="gcp", kind=NEVER_USED, statement="old")]
    merged = _merge_denials(stored, parse("I only studied GCP"))
    assert [(d.term, d.kind) for d in merged] == [("gcp", STUDIED_ONLY)]


def test_merging_with_no_denials_anywhere_is_empty():
    assert _merge_denials(None, parse("Emphasise AKS.")) == []


# --------------------------------------------------------------------------
# 9. End to end through the API
# --------------------------------------------------------------------------


async def test_a_denial_in_the_instruction_never_becomes_a_career_source(db, monkeypatch):
    """The live defect, end to end.

    Before the fix this request stored "I have never used Harness or Jenkins"
    as a user_statement career source and confirmed both technologies off it.
    """
    client = _client(monkeypatch)
    try:
        _upload_master(client)
        r = client.post(
            "/artifacts/resume",
            json={
                "jd_text": GCP_JD,
                "instruction": "I have never used Harness or Jenkins. Keep it to 2 pages.",
                "name": "A. Candidate",
            },
        )
        assert r.status_code == 200, r.text
    finally:
        client.__exit__(None, None, None)

    async with session_scope() as s:
        statements = (
            await s.execute(
                select(DocumentRow).where(
                    DocumentRow.authority == AUTHORITY_USER_STATEMENT
                )
            )
        ).scalars().all()
    assert statements == [], "a denial was stored as a positive career source"

    async with session_scope() as s:
        denied = (await s.execute(select(CareerDenialRow))).scalars().all()
    assert {d.term for d in denied} == {"harness", "jenkins"}

    confirmed = await confirmed_experience()
    assert confirmed.is_confirmed("harness") is False
    assert confirmed.is_confirmed("jenkins") is False


async def test_the_api_reports_what_the_request_changed(db, monkeypatch):
    client = _client(monkeypatch)
    try:
        _upload_master(client)
        r = client.post(
            "/artifacts/resume",
            json={
                "jd_text": GCP_JD,
                "instruction": "I have never used Harness.",
            },
        )
        body = r.json()
    finally:
        client.__exit__(None, None, None)
    assert body["career_facts"]["denied"] == ["harness"]
    assert body["instruction"]["denials"][0]["kind"] == NEVER_USED


async def test_the_career_profile_endpoint_shows_denials(db, monkeypatch):
    await record_denials([Denied(term="gcp", kind=STUDIED_ONLY, statement="only studied")])
    client = TestClient(app)
    with client:
        body = client.get("/career-profile").json()
    assert body["denied"]["gcp"]["kind"] == STUDIED_ONLY
    assert "gcp" not in body["confirmed"]


async def test_jd_analysis_separates_denied_gaps_from_unknown_ones(db, monkeypatch):
    """A gap the user already ruled out should not be presented the same way as
    one they have never been asked about — that is what makes the later
    confirmation feature able to skip it."""
    await record_denials([Denied(term="gcp", kind=NEVER_USED, statement="never used")])
    client = TestClient(app)
    with client:
        body = client.post("/career-profile/analyze-jd", json={"text": GCP_JD}).json()
    assert "gcp" in body["technologies_unsupported"]
    assert body["technologies_denied"] == ["gcp"]
```

---

## 9. Before / after

Against a master resume containing "Ran Azure DevOps pipelines with Harness and
Jenkins, plus Terraform.":

| instruction | harness | jenkins | gcp | terraform |
|---|---|---|---|---|
| *(none)* | confirmed | confirmed | — | confirmed |
| "I have never used Harness or Jenkins" | **denied** `never_used` | **denied** `never_used` | — | confirmed |
| "I have no professional GCP experience" | confirmed | confirmed | **denied** `not_professional` | confirmed |
| "I only studied GCP" | confirmed | confirmed | **denied** `studied_only` | confirmed |
| "I also have professional Harness experience" | confirmed *(career statement, unchanged)* | confirmed | — | confirmed |

Note row 2: Terraform from the SAME document is untouched. A denial is surgical,
not a reason to distrust the whole source.

## 10. Verification evidence

- 515 tests green (`pytest -q`), baseline 427.
- `ruff check src tests` clean.
- Migration chain applies to SQLite end to end (`tests/test_migrations.py`).
- All 15 committed blobs verified byte-identical to the tested content
  (md5 per file, CR-normalised — the repo has a CRLF/LF working-tree quirk).
- `git fsck` clean.

## 11. Explicitly NOT in this commit

- the one-time gap confirmation UI
- adjacency inference of any kind
- Phase 3C
- any change to resume selection, drafting, review or correction logic
- new providers or features
- **no rerun of the Auto evaluation** — the 56 runs stand; this change is independent

## 12. Questions I would like reviewed

1. **§5.3 residual bypass.** `assemble_confirmed()` remains callable without
   denials. Acceptable, or should the boundary be harder?
2. **Supersession semantics.** Amith chose "later positive statement supersedes".
   Same-request contradiction resolves to denied, and documents can never
   supersede. Is there a case where an unintended positive phrasing silently
   reverses a denial the user meant to keep?
3. **Denial scope excludes domains.** "I have no automation experience" is
   classified as a denial (so it never becomes positive prose) but blocks no
   specific term. Under-enforcement — is that the right call, or should domains
   be denyable?
4. **Regex coverage.** 15 adversarial phrasings tested. What high-value negative
   form is missing? Conversely — is any pattern too greedy and at risk of
   swallowing a genuine positive claim? `_NOT_PROFESSIONAL` pattern 3
   (`never|not|n't ... professionally|in production|at work`) has a 60-character
   window and is the one I am least sure of.
5. **`studied_only` storage.** It lives in `career_denials` alongside the true
   denials, distinguished only by `kind`. It is not really a denial — it is
   non-professional experience. Right table, or should it be separate?
6. **Prompt-level belt-and-braces.** `_career_context()` now emits an
   "EXPLICITLY DENIED BY THE USER" line even though denied terms are already
   absent from the truth set. Useful redundancy, or does naming the technology
   in the prompt at all create a risk the model reaches for it?

## 13. Unexpected findings (not caused by this change)

1. **`test_cancel_after_completion_is_404` is flaky.** Reproduced failing ~1 in 5
   on a pristine checkout of `96dd02e`. Pre-existing; will produce noise during
   the Auto review.
2. **CRLF/LF working-tree churn.** Any non-Windows git sees ~22,000 phantom line
   changes across 127 files. `git -c core.autocrlf=true diff` is empty. Cosmetic,
   but it makes cross-platform diffing unreliable.
