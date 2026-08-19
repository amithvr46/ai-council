# AI Council

A personal multi-model AI system. GPT and Claude answer every question independently, then the system compares, critiques, verifies and judges to produce **one stronger final answer** — instead of two answers you have to compare yourself.

Core principle: **evidence outranks model opinion, and no uncontrolled loops.**

## Pipeline (V1)

```
question
   ↓
router (quick | council | deep)
   ↓
GPT + Claude answer in parallel, independently
   ↓
one combined check: agreement level, disagreement type, checkable claims
   ↓
agree/partial ──→ synthesis ──→ final answer
disagree ──────→ disagreement report        (judge lands in milestone 2)
```

Every model call is persisted to Postgres (`requests` + `steps`) with prompt version, tokens, cost and latency. Per-mode call budgets are enforced in code — the system cannot loop.

## Status

**Milestone 1 (complete):** provider layer (`generate()` only), versioned prompts, combined check, synthesis, graceful degradation, budgets, full execution traces, FastAPI + CLI, golden benchmark set.

**Milestone 2 (complete):** blinded judge with dimension-level verdicts (never forced to pick a winner), one cross-critique round for reasoning disagreements in deep mode, verifier on the opposite provider auditing final-answer claims as SUPPORTED / INFERRED / UNSUPPORTED / CONTRADICTED, at most one revision. Live-verified: the verifier caught a deliberately planted fake claim and demanded revision.

**Milestone 3 (complete):** Next.js web UI — ask page with live stage streaming (SSE), expandable trace (candidates, agreement check, judge dimension verdicts, verifier claim audit), 1–5 rating, history browser and live cost stats. New API: `POST /ask/async`, `GET /requests/{id}/stream` (SSE), `GET /requests` (paginated), `GET /stats`.

**Milestone 4 (complete) — the evidence layer. V1 is done.** Deep mode gathers evidence before any answer exists: an evidence planner turns checkable claims into web searches and runnable code, the tools run under hard caps, and an assessor judges each claim strictly against what came back (SUPPORTED / CONTRADICTED / INSUFFICIENT_BY_EVIDENCE). Evidence supremacy is enforced in engine code, not just prompts:

- **R1** — a factual dispute the evidence could not settle cannot be resolved by plausibility; a judge that picks a winner anyway is recorded as a constraint violation and forced into revision.
- **R2** — a verifier `pass` cannot stand over a claim the evidence contradicted; the verdict is overridden to `revise` in code.
- **R3** — the evidence bundle and its binding verdicts precede source material for the judge, synthesis, verifier and revision.
- **R4** — when evidence contradicts a claim *both* candidates asserted, the agreement shortcut is disabled and the request escalates to the judge, which can reject both.

Every source, executed snippet, claim and verdict is persisted (`evidence_items`, `claim_assessments`) so any decision can be audited afterwards.

```
council on disagreement:  candidates -> check -> blinded judge -> final
deep with evidence:       candidates -> check -> evidence plan -> tools ->
                          evidence assessment -> (critique if reasoning dispute) ->
                          judge/synthesis -> verifier -> (one revision) -> final
```

Evidence is deep mode only in V1. Web retrieval needs a Tavily or Brave key
(`EVIDENCE_SEARCH_PROVIDER` in `.env`); with no key it reports itself
unavailable and claims become INSUFFICIENT — a gap in evidence never becomes
confidence. Code execution runs model-written Python in an isolated process
with a temp cwd, scrubbed env, timeout and POSIX rlimits; it is a
blast-radius limiter, not a security boundary, and can be disabled with
`EVIDENCE_CODE_EXECUTION=false`.

**Phase 2A (complete) — spend control.** Cost is estimated *before* a run
starts, from your own history (p75 over 30 days, falling back to per-mode
defaults until there are 3 samples). A run that cannot finish inside the
remaining budget is refused up front rather than stopping half-way: the API
returns 402 with the estimate and what's left. Limits are runtime-editable via
`GET/PUT /budget`.

**Phase 2B (complete) — career source ingestion.** Real career documents go in;
confirmed experience comes out. Every career source contributes **positively**
and none ever subtracts, so a tailored resume that omits Harness is a selective
view, not evidence that Harness was never used. A job description is the
*target*, never career evidence — ingesting a GCP-heavy JD does not make the
system able to claim GCP.

```bash
.venv/bin/council ingest ~/resume.docx --authority master_resume
.venv/bin/council profile --sources        # confirmed terms + what established each
.venv/bin/council profile-set employers "Acme" "Globex"
.venv/bin/council analyze-jd ~/jd.txt      # role family + what the JD wants that you can't claim
```

`analyze-jd` answers the question that actually matters before applying:

```
role family: infrastructure
JD technologies you have:    ci/cd, docker, helm, kubernetes, linux, python, terraform, ...
JD technologies you do NOT:  cloud run, cloud sql, gcp, gke
```

Nothing on the "do NOT" line will be written into a resume. Your documents stay
in your local database and never enter this repository.

Same operations over HTTP: `POST /documents` (multipart), `GET/PATCH/DELETE
/documents`, `GET/PUT /career-profile`, `POST /career-profile/analyze-jd`.

**Phase 2C (complete) — the resume workflow.** Career sources + JD in, a
submission-ready DOCX out. You do not choose models, modes, keywords, bullets
or review stages.

```bash
council generate-resume ~/jd.txt --out tailored.docx --name "Your Name" --contact "City | linkedin"
council conflicts                      # material factual disagreements between your sources
council resolve-conflict <id> "value"
```

    JD analysis -> experience selection -> tailored draft -> multi-lens review
      -> claim + style checks -> bounded correction -> DOCX

The governing rule, from the frozen contract:

> AI Council has freedom to formulate realistic professional experience. It
> does not have freedom to fabricate career facts.

So it writes natural engineer-voiced bullets from confirmed technologies,
domains and role context without needing the exact wording to exist in an old
resume — while four things are enforced in code rather than asked of a prompt:

- **Gap technologies never appear.** Not in a bullet, not in the skills list.
  A GCP-primary JD produces a resume with no GCP in it.
- **Confirmed nouns do not license an invented relationship.** Confirmed AWS,
  Terraform, Kubernetes and Python do not establish "built Kubernetes clusters
  on AWS using Terraform and automated the entire platform through Python" —
  every noun true, the history manufactured. Tier 2B catches it.
- **Unless a career source actually says it.** A real project the master resume
  establishes is not flagged as invented; support requires one source sentence
  to assert substantially the same thing, not words scattered across a file.
- **Never a comma immediately before "and".** Deterministic rule, applied
  deterministically after correction rather than trusted to a model.

The draft and the review sit on opposite providers — a model reviewing its own
writing rates it well. Mechanical checks run after the model review and are not
advisory.

Technology discovery is conditional (contract A2): known terms are classified
locally and a cheap model call fires only when unrecognised technical terms
remain, cached so a repeat JD costs nothing. The model may **discover** a
technology from a JD; it can never **confirm** you have used it — that comes
only from career sources.

A typical run is 4 model calls at roughly $0.11.

## Setup

```bash
uv venv .venv && uv pip install -p .venv/bin/python -e ".[dev]"
cp .env.example .env        # add your OPENAI_API_KEY and ANTHROPIC_API_KEY
docker compose up -d        # Postgres
.venv/bin/alembic upgrade head
```

## Use

```bash
# CLI
.venv/bin/council ask "Should I use count or for_each here?" --mode council
.venv/bin/council ask "..." --mode quick        # single model, alternates GPT/Claude
.venv/bin/council show <request-id>             # full stored trace

# API
.venv/bin/uvicorn council.api.main:app --reload
# POST /ask {"question": "...", "mode": "council"}
# GET  /requests/{id}          — full execution trace
# POST /requests/{id}/rating   — 1-5, feeds the model-performance learning layer

# Web UI (needs the API running; Node 18+)
cd frontend && npm install && npm run dev
# open http://localhost:3000
```

## Evals

```bash
.venv/bin/python evals/run_evals.py --mode quick --mode council
```

Runs the golden set (`goldens/questions.yaml` — 18 fixed questions including false-premise traps, uncertainty-is-correct cases and coding bugs), writes blinded answer files for human rating plus a key to unblind afterward.

## Tests

```bash
.venv/bin/pytest -q        # runs against fakes + SQLite; no API keys needed
.venv/bin/ruff check src tests evals
```

## Design rules (frozen for V1)

Providers expose `generate()` and nothing else — all orchestration lives in the engine. One combined structured-output call does agreement + disagreement-type + claim extraction. Factual disagreements go to evidence, reasoning disagreements get at most one critique round. The judge is blinded and never forced to pick a winner. Max one revision. Budgets: quick 1, council 5, deep 9 model calls. Provider failures degrade gracefully and are recorded, never hidden. Prompt versions are stamped into every step row.
