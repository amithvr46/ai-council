# Intelligent resume expansion — gap analysis

**Status: ANALYSIS ONLY. Nothing implemented. Awaiting decision.**

Requested before amending the frozen Phase 2 contract to permit limited,
role-plausible expansion into technologies absent from career evidence.

---

## 0. The distinction the amendment turns on

The amendment argues from "absence from a two-page resume is not a negative
career fact." That premise is correct, and it is already the system's rule. But
it supports two different conclusions, and only one of them is currently
implemented:

| | Rule | Status |
|---|---|---|
| **A** | Omission never SUBTRACTS. A resume that doesn't mention Harness doesn't un-confirm Harness. | Implemented, tested, correct |
| **B** | Adjacency may ADD. Azure + Kubernetes + Terraform confirmed ⇒ GCP may be asserted. | Not implemented. This is the amendment. |

They feel like the same rule — both are shaped like "absence is not denial" —
but they run in opposite directions. A protects true things from being lost. B
creates claims no source establishes.

Their failure modes are not symmetric:

- **Rule A failing** produces a weaker resume than the career deserves.
- **Rule B failing** produces a resume asserting professional GCP experience to
  an employer when the only basis was that GCP is adjacent to Azure.

The second is not a style defect. It is a claim the user has to defend in an
interview, and the system would have made it without ever asking.

**The underlying problem in the amendment is real and worth solving**: the
Career Profile is incomplete, and nobody should maintain a database of every
technology touched across seven years. The question is whether the fix is to
*infer* the missing experience or to *make confirming it nearly free*.

---

## 1. Frozen rules this conflicts with

| Location | Rule |
|---|---|
| `workflow.check()` L428 | Any gap term in a bullet → `GAP_TECHNOLOGY` finding |
| `workflow.check()` L473 | Any gap term in the skills list → `GAP_TECHNOLOGY` |
| `claims.classify()` | Unconfirmed candidate term → `UNSUPPORTED_EXPANSION` |
| `discovery.discover()` | "Discovery names it; career evidence decides it" — the model may DISCOVER a technology, never CONFIRM it |
| `profile.assemble_confirmed()` | Only career authorities contribute; a JD contributes nothing |
| `prompts/resume_generate.v1.md` §4 | "Anything using a GAP technology" is forbidden |
| `prompts/resume_select.v1.md` §31 | "Never plan around a GAP technology" |
| `prompts/resume_review.v1.md` §36/§47 | A gap technology appearing "anywhere, including implied" is a **blocking** finding |
| Contract §4 (frozen) | "NOT PERMITTED WITHOUT CAREER EVIDENCE: new technologies, new cloud providers" — names this case explicitly |
| Contract §6 | "NEVER solve an unsupported requirement by copying the JD term into my resume" |

The last two are the sharpest: the frozen contract lists "new cloud providers"
as a named prohibition, and the amendment's central example is a new cloud
provider.

## 2. Tests that would change

Six, across three files:

- `test_resume_contract.py::test_F_no_gap_technology_survives_into_the_artifact`
  — asserts findings are exactly `{GAP_TECHNOLOGY}` for a planted GCP skill and
  GKE bullet
- `test_resume_contract.py::test_A_jd_technology_never_becomes_career_experience`
- `test_resume_contract.py::test_A_even_a_model_calling_it_a_technology_cannot_confirm_it`
- `test_resume_api.py::test_the_whole_one_step_path` — asserts GCP/GKE/Cloud
  Run/Cloud SQL appear nowhere in the produced DOCX
- `test_resume_workflow.py::test_check_flags_a_gap_technology_in_a_bullet`
- `test_resume_workflow.py::test_check_flags_a_gap_technology_hiding_in_the_skills_list`

None of these should be deleted. Under any option below they become
*conditional*: the prohibition still holds for unexpanded technologies, and the
tests gain an explicit expansion path rather than losing the assertion.

## 3. Distinguishing the five categories

| Category | Signal | Mechanism |
|---|---|---|
| Explicit user experience | "I also have professional GCP experience" | Deterministic — `instructions.parse()` already extracts this and files it as `user_statement` |
| Explicit contradiction | "I have never worked on GCP" / "only studied it" | Deterministic — needs a negation branch in the same parser. **Hard boundary, overrides everything.** |
| JD keyword copying | Bullet reproduces JD phrasing | Deterministic — `mirroring.py` already detects it |
| Unrelated fabrication | JD wants Salesforce/COBOL/SAP, career is cloud infra | Requires a domain judgement |
| Reasonable adjacent expansion | JD wants GCP, career is Azure/AWS/K8s/Terraform | Requires a domain judgement |

Three of five are already deterministic. The last two are the same judgement
call viewed from opposite ends, and that judgement is the entire cost of this
amendment.

## 4. Deterministic, or bounded model judgement?

**Adjacency cannot be done deterministically.** It would need a maintained
adjacency graph over every technology pair — which is the
`FOREIGN_TECHNOLOGIES` maintenance problem again, except combinatorial and with
a much worse failure mode: a missing edge under-claims (harmless), a wrong edge
puts a false claim on a resume.

So Rule B requires **one bounded model call**: given the confirmed career set,
the role family and the gap technology, is this an adjacent capability a
practitioner at this level would plausibly have used? Cacheable per
(career-fingerprint, technology), so repeat JDs cost nothing.

**The confirmation alternative requires no judgement at all** — it asks.

## 5. Preventing 20 JD technologies becoming 20 invented skills

Under any expansion design, four mechanical limits:

1. **Cap the count.** At most **2** expanded technologies per resume, chosen by
   JD prominence. Not a tunable — a hard constant.
2. **Require a confirmed anchor.** Expansion is only permitted into a
   technology whose *category* is already confirmed (cloud platform → cloud
   platform). GCP anchors on Azure/AWS. Salesforce anchors on nothing.
3. **Require career-set density.** Expansion only when the confirmed set is
   already strong in that domain (≥ N confirmed terms in the same category),
   so a thin profile cannot bootstrap breadth.
4. **Never expand a hard fact.** Expansion may add a technology; it may never
   add a date, employer, certification, metric or scale.

## 6. Bullet-level bounds

- At most **2 bullets** total mention expanded technologies.
- Expanded technologies appear in **at most one role** — the most recent
  relevant one — never spread across a career to imply a long history.
- An expanded technology may appear in the skills list **only if** it also
  appears in a bullet, so it can never become a silent keyword.
- No expanded technology in the headline or summary, which is where a claim
  reads as a defining strength.

## 7. Interaction with Tier 2B

This is the sharpest technical risk, and it needs an absolute rule.

Tier 2B exists because confirmed nouns do not authorise a specific historical
relationship between them. An *expanded* technology is weaker than a confirmed
one. So:

> **An expanded technology may never participate in a relationship claim.**

"Provisioned GKE clusters with Terraform and wired them into the existing
CI/CD pipeline" is expansion (GKE) *plus* a manufactured relationship — two
inferences stacked, presented as one history. Mechanically: any bullet
containing an expanded term is run through `find_implementation_claims()` with
the routine-work exemption **disabled**, and any hit is blocking.

## 8. Provenance

A fourth authority level, and one non-negotiable constraint:

    AUTHORITY_INFERRED_ADJACENT   # never persisted, never reusable

**Expansion must never feed back into the Career Profile.** If an inferred GCP
bullet were stored as a career source, the next run would read it as
established experience and expand again from there. That is inference
laundering, and after three or four resumes the profile would contain claims
with no human origin at all and no way to tell which.

Concretely: expansion lives only in the artifact trace for that one run. The
confirmed set is unchanged by it. Every subsequent run re-derives expansion
from evidence, or not at all.

## 9. Should the reviewer disclose it?

**Yes, and to the user, not only to the trace.** This is the part I would
refuse to build without.

The person answering interview questions about these bullets is the user. If
the system writes "provisioned GKE clusters" on the strength of an adjacency
guess, the user must know *which specific bullets* are inferred before sending
the document — not be able to find out, but be shown, on the result screen,
in plain language:

> Two bullets describe GCP work. No career source establishes GCP; they were
> written because it is adjacent to your Azure and Kubernetes experience.
> Confirm or remove them before sending.

Silent expansion is the failure mode that turns a helpful product into one that
embarrasses its user in a room it isn't in.

## 10. Recommendation

### Recommended: confirmation, not inference

Instead of guessing whether the user has used GCP, **ask once, ever.**

When a JD's gap technologies are prominent, the result surfaces one question
with three answers:

> This role leans on **GCP** and **GKE**. Nothing in your career sources
> mentions them.
> **[ Used professionally ]  [ Only studied / personal ]  [ Never used ]**

- "Used professionally" → files a `user_statement` career source. GCP is
  confirmed permanently, Council writes the bullets with full freedom, and the
  question never appears again.
- "Only studied" → allowed in a clearly-labelled skills line, never as
  professional experience.
- "Never used" → recorded as a **negative** fact, so no future run ever raises
  it and no expansion could ever add it.

Why this is better than adjacency inference:

1. **It solves the actual problem permanently.** The complaint is profile
   incompleteness. Inference works around it forever; confirmation fixes it,
   one technology at a time, and the profile genuinely converges.
2. **Zero fabrication risk.** Nothing is ever claimed that the user did not
   assert.
3. **It is not bullet micromanagement.** One question per unknown technology
   *for all time* — not per resume, not per bullet. After a few applications
   there is nothing left to ask.
4. **Your own frozen contract already permits it.** §13 names
   *"Did you actually use GCP professionally?"* as an appropriate clarification.
   This is that question, asked once and remembered.
5. **It is fully deterministic.** No adjacency graph, no model call, no
   judgement to get wrong.
6. **It captures negatives**, which inference cannot. Today "never used GCP" has
   nowhere to live.

Cost: roughly the size of the instruction parser plus one UI control.

### If you still want adjacency expansion

It is buildable under the bounds in §5–§9: max 2 technologies, one role, no
relationship claims, no hard facts, never persisted, disclosed prominently, one
cached model call. Best offered as an explicit opt-in — a "suggest adjacent
experience" toggle, off by default — so an inferred claim is always something
the user asked for rather than something that appeared.

### Risks I would not want left unstated

- **Interview exposure.** An inferred bullet is a claim the user defends in
  person. Adjacency is not experience: Azure fluency does not mean knowing what
  a GCP service account or VPC Service Control is.
- **Silent normalisation.** Once expansion is routine it stops being read; the
  disclosure becomes wallpaper, and the boundary erodes without any decision to
  erode it.
- **The precedent.** Every other outcome — code fixes, technical docs — inherits
  whatever truth boundary the resume workflow establishes. This is the first
  place we would permit output that no evidence supports.

### The honest summary

Both designs solve "I shouldn't have to maintain a technology database."
Confirmation solves it by making the database fill itself from your own
answers. Expansion solves it by guessing on your behalf and telling you
afterwards. My recommendation is confirmation, with adjacency expansion
available as an opt-in if you want it after using the confirmation flow for a
few real applications.

**Nothing implemented. Awaiting your decision.**
