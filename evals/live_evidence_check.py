"""Live web-evidence verification for the final V1 review.

Runs the five deep-mode scenarios the reviewer asked for against the REAL
APIs (both model providers + the configured web search provider) and prints
a full per-scenario report: candidate positions, evidence retrieved,
assessment verdicts, judge decision, verifier result, final outcome, model
calls, physical API attempts, cost and latency.

These are LIVE tests. They cost real money (roughly $0.30-0.60 for the full
run) and their outcomes depend on what the web returns today — unlike the
deterministic suite in tests/, which uses fakes and must pass identically
every time.

Usage:
    .venv\\Scripts\\python evals\\live_evidence_check.py            (all)
    .venv\\Scripts\\python evals\\live_evidence_check.py --only 3   (one)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from council.config import get_settings  # noqa: E402
from council.db.models import Base  # noqa: E402
from council.db.session import get_engine, init_engine  # noqa: E402
from council.engine.factory import build_engine  # noqa: E402

SCENARIOS = [
    {
        "id": 1,
        "name": "Models correct + web evidence agrees",
        "question": (
            "In Kubernetes, what happens when a readiness probe fails but the liveness "
            "probe still passes? Be precise about restarts versus Service endpoints."
        ),
        "expect": (
            "Both models should be right (no restart; pod removed from Service endpoints) "
            "and evidence should SUPPORT them. Expect no override, verifier pass."
        ),
    },
    {
        "id": 2,
        "name": "Models disagree factually + web evidence resolves it",
        "question": (
            "Is the Kubernetes KMS v1 encryption provider still supported and enabled by "
            "default, or has it been deprecated and disabled? Give the exact versions."
        ),
        "expect": (
            "Version specifics invite disagreement. Evidence should SUPPORT the correct "
            "side (KMS v1 deprecated since 1.28, disabled by default since 1.29)."
        ),
    },
    {
        "id": 3,
        "name": "Both models agree on something wrong + evidence corrects them",
        "question": (
            "Kubernetes encrypts Secrets at rest in etcd by default, so an external secret "
            "manager isn't needed for encryption — right?"
        ),
        "expect": (
            "Models often accept the false premise. Evidence must CONTRADICT it "
            "(base64 encoding, not encryption; EncryptionConfiguration required) and, if "
            "both candidates asserted it, R4 should escalate to the judge."
        ),
    },
    {
        "id": 4,
        "name": "Conflicting / insufficient web evidence -> uncertainty",
        "question": (
            "What is the exact current market share of Kubernetes versus HashiCorp Nomad "
            "in production container orchestration this quarter?"
        ),
        "expect": (
            "No authoritative current figure exists; sources conflict and are dated. "
            "Expect INSUFFICIENT_EVIDENCE verdicts and preserved uncertainty — not a "
            "confident number."
        ),
    },
    {
        "id": 5,
        "name": "Web unavailable -> INSUFFICIENT, not silent success",
        "question": (
            "What is the default value of the `terminationGracePeriodSeconds` field for a "
            "Kubernetes Pod, and did it change in any recent release?"
        ),
        "expect": (
            "Run with web search forcibly disabled. Evidence must report UNAVAILABLE and "
            "claims must be INSUFFICIENT_EVIDENCE — never silently confident."
        ),
        "disable_web": True,
    },
]


def _fmt(result: dict, scenario: dict) -> str:
    steps = result["steps"]

    def stage(name):
        return next((s for s in steps if s["stage"] == name), None)

    lines = [
        "=" * 78,
        f"SCENARIO {scenario['id']}: {scenario['name']}",
        "=" * 78,
        f"QUESTION: {scenario['question']}",
        f"EXPECTATION: {scenario['expect']}",
        "",
        "--- CANDIDATE POSITIONS ---",
    ]
    for label in ("candidate_a", "candidate_b"):
        st = stage(label)
        if st is None:
            lines.append(f"{label}: (missing)")
            continue
        text = (st.get("output") or {}).get("text", "")
        lines.append(f"{label} [{st['provider']}]: {text[:400].strip()}...")

    check = stage("combined_check")
    if check:
        out = check["output"] or {}
        lines += [
            "",
            "--- AGREEMENT CHECK ---",
            f"agreement={out.get('agreement')} type={out.get('disagreement_type')}",
            f"summary: {out.get('summary', '')[:300]}",
            f"checkable claims: {len(out.get('checkable_claims', []))}",
        ]

    plan = stage("evidence_plan")
    if plan:
        lines += ["", "--- EVIDENCE PLAN ---"]
        for q in (plan["output"] or {}).get("queries", []):
            lines.append(f"  [{q['tool']}] {q['query'][:120]}")

    lines += ["", f"--- EVIDENCE RETRIEVED ({len(result.get('evidence', []))} items) ---"]
    for e in result.get("evidence", []):
        if e["status"] == "ok":
            src = e["source_url"] or "(executed code)"
            lines.append(f"  [E{e['ordinal']}] {e['kind']} {e['latency_ms']}ms {src}")
            lines.append(f"        {e['snippet'][:160].strip()}")
        else:
            lines.append(f"  [E{e['ordinal']}] {e['kind']} {e['status'].upper()}: {e['error']}")

    lines += ["", "--- EVIDENCE ASSESSMENT VERDICTS ---"]
    for c in result.get("claim_assessments", []):
        lines.append(f"  [{c['verdict']}] ({c['made_by']}) {c['claim'][:110]}")
        lines.append(f"        cites={c['citations']} — {c['rationale'][:160]}")

    guard = stage("assessor_guard_corrections")
    if guard:
        lines += ["", "--- ASSESSOR GUARD CORRECTIONS ---", json.dumps(guard["output"], indent=2)]

    for name, label in [
        ("evidence_override", "R4 EVIDENCE OVERRIDE (agreement escalated to judge)"),
        ("evidence_constraint_violation", "R1 CONSTRAINT VIOLATION (forced uncertainty)"),
        ("evidence_supremacy_override", "R2 VERIFIER OVERRIDE (pass -> revise)"),
    ]:
        st = stage(name)
        if st:
            lines += ["", f"--- {label} ---", json.dumps(st["output"], indent=2)[:600]]

    judge = stage("judge")
    if judge:
        out = judge["output"] or {}
        lines += [
            "",
            "--- JUDGE ---",
            f"decision={out.get('decision')} confidence={out.get('confidence')}",
            f"rationale: {out.get('rationale', '')[:300]}",
        ]
    elif stage("synthesis"):
        lines += ["", "--- SYNTHESIS (candidates agreed; no judge) ---"]

    ver = stage("verifier")
    if ver:
        out = ver["output"] or {}
        lines += ["", f"--- VERIFIER: {str(out.get('verdict', '')).upper()} ---"]
        for c in out.get("claims", [])[:8]:
            lines.append(f"  [{c['classification']}] {c['claim'][:110]}")
        for r in out.get("reasons", []):
            lines.append(f"  reason: {r[:200]}")
    if stage("revision"):
        lines += ["", "--- REVISION APPLIED (one pass) ---"]

    t = result["totals"]
    lines += [
        "",
        "--- FINAL ANSWER ---",
        (result["final_answer"] or "(none)")[:900],
        "",
        "--- METRICS ---",
        f"status={result['status']} degraded={result['degraded']} "
        f"evidence_used={result.get('evidence_used')} "
        f"evidence_override={result.get('evidence_override')}",
        f"model_calls={t['model_calls']}  physical_api_attempts={t['api_attempts']}  "
        f"cost=${t['cost_usd']:.4f}  latency={t['latency_ms']}ms",
        f"stages: {[s['stage'] for s in steps]}",
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, default=None, help="run one scenario by id")
    args = parser.parse_args()

    settings = get_settings()
    print(f"web search provider: {settings.evidence_search_provider}")
    print(f"code execution: {settings.evidence_code_execution}")
    print(f"models: {settings.openai_model_flagship} / {settings.anthropic_model_flagship}\n")

    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    scenarios = [s for s in SCENARIOS if args.only is None or s["id"] == args.only]
    total_cost = 0.0
    for scenario in scenarios:
        engine = build_engine()
        if scenario.get("disable_web"):
            # Force the unavailable path without touching the user's .env.
            engine.evidence_tools["web"].available = False
        question = scenario["question"]
        if isinstance(question, tuple):  # guard against a stray trailing comma
            question = question[0]
        print(f"running scenario {scenario['id']} ...", flush=True)
        try:
            result = await engine.run(question, "deep")
        except Exception as e:  # a failure is itself a reportable result
            print(f"SCENARIO {scenario['id']} FAILED: {type(e).__name__}: {e}\n")
            continue
        total_cost += result["totals"]["cost_usd"]
        print(_fmt(result, {**scenario, "question": question}))

    print("=" * 78)
    print(f"TOTAL COST FOR THIS RUN: ${total_cost:.4f}")
    print("These are LIVE results (real APIs, real web). The deterministic suite")
    print("in tests/ uses fakes and is the reproducible contract.")


if __name__ == "__main__":
    asyncio.run(main())
