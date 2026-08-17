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

**Milestone 1 (complete):** provider layer (`generate()` only), versioned prompts, combined check, synthesis, disagreement report, graceful degradation, budgets, full execution traces, FastAPI + CLI, golden benchmark set, 21 passing tests.

**Milestone 2 (next):** blinded judge with dimension-level verdicts, cross-critique round for reasoning disagreements, verifier auditing claims as SUPPORTED / INFERRED / UNSUPPORTED / CONTRADICTED, one bounded revision.

**Milestone 3:** Next.js UI with SSE stage streaming. **Milestone 4:** evidence tools (web retrieval + sandboxed code execution).

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
