"""Controlled Auto evaluation — does Auto choose a cheaper path without
costing quality?

This answers a question unit tests cannot: **would a real user accept what Auto
picked?** It runs the same question set through forced quick, forced council,
forced deep and Auto, then compares route, cost, latency, disagreement,
evidence overrides and degradation side by side.

Two design points worth knowing before reading the numbers:

1. Every row is written with data_class="eval". A deliberate benchmark sweep is
   NOT organic usage and must never train historical routing (contract §20).
2. The set deliberately includes adversarial factual-disagreement cases, since
   3B escalation only fires on an observed factual dispute and the ordinary
   golden set rarely produces one. Without these the escalation path would go
   untested and the eval would quietly report "no escalations" as if that were
   a finding about Auto rather than about the questions.

No target percentage is assumed. Measure first.

Usage:
    python evals/auto_eval.py                       # all modes
    python evals/auto_eval.py --mode auto --mode council
    python evals/auto_eval.py --only fresh,transform
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from council.db.models import Base  # noqa: E402
from council.db.session import get_engine, init_engine  # noqa: E402
from council.engine.factory import build_engine  # noqa: E402

# Categories chosen to exercise every rung of the ladder, not to flatter Auto.
QUESTIONS = [
    # --- transformations: Auto should choose quick -----------------------
    ("transform", "Shorten this to one sentence: 'Cloud engineer with seven "
                  "years of experience across Azure and AWS infrastructure, "
                  "automation and production operations.'"),
    ("transform", "Rewrite my current summary in plainer language: 'Leveraged "
                  "cutting-edge Kubernetes to drive operational excellence.'"),
    # --- creative: Auto should choose quick ------------------------------
    ("creative", "Write me a two-line joke about YAML indentation."),
    # --- fresh external facts: Auto should choose deep -------------------
    ("fresh", "What is the latest stable Kubernetes minor version?"),
    ("fresh", "Is Terraform 1.5 still supported by HashiCorp?"),
    # --- reasoning: Auto should choose council and NOT escalate ----------
    ("reasoning", "Should I use count or for_each for several similar Azure "
                  "subnets in Terraform? Explain the trade-off."),
    ("reasoning", "When is a sidecar container the wrong pattern in Kubernetes?"),
    # --- technical DevOps/SRE --------------------------------------------
    ("devops", "An AKS deployment is stuck in ImagePullBackOff. Walk through "
               "how you would diagnose it."),
    ("devops", "How would you safely reconcile Terraform drift in a production "
               "Azure subscription?"),
    # --- code / debugging -------------------------------------------------
    ("code", "```python\nfor i in range(10):\n    if i = 5:\n        print(i)\n"
             "```\nWhy does this fail and what is the fix?"),
    # --- adversarial factual disagreement: should exercise 3B escalation --
    ("adversarial", "What is the default port for PostgreSQL, and what is the "
                    "default port for Microsoft SQL Server? Give exact numbers."),
    ("adversarial", "How many bytes are in a IPv6 address, and what is the "
                    "maximum size of an S3 object? Exact figures only."),
    ("adversarial", "What exact year was Kubernetes first released publicly, "
                    "and what year did Terraform 1.0 ship?"),
    # --- ambiguous --------------------------------------------------------
    ("ambiguous", "Make my infrastructure better."),
]

MODES = ("quick", "council", "deep", "auto")


async def run_one(engine, mode: str, question: str) -> dict:
    routing = await engine.plan(question, mode)
    try:
        result = await engine.run(question, mode, routing=routing)
    except Exception as e:  # a refusal or provider failure is a datapoint
        return {"mode": mode, "error": f"{type(e).__name__}: {e}"}

    steps = result["steps"]
    check = next((s for s in steps if s["stage"] == "combined_check"), None)
    escalation = next((s for s in steps if s["stage"] == "routing_escalation"), None)
    routing_step = next((s for s in steps if s["stage"] == "routing"), None)

    return {
        "mode": mode,
        "effective_mode": result["mode"],
        "routed_by": (routing_step or {}).get("output", {}).get("deciding_rung"),
        "routing_reason": (routing_step or {}).get("output", {}).get("reason"),
        "escalated": bool(escalation and escalation["output"].get("result") == "escalated"),
        "escalation_refusal": (
            (escalation or {}).get("output", {}).get("refusal_reason")
        ),
        "agreement": (check or {}).get("output", {}).get("agreement"),
        "disagreement_type": (check or {}).get("output", {}).get("disagreement_type"),
        "evidence_used": result["evidence_used"],
        "evidence_override": result["evidence_override"],
        "degraded": result["degraded"],
        "model_calls": result["totals"]["model_calls"],
        "cost_usd": result["totals"]["cost_usd"],
        "latency_ms": result["totals"]["latency_ms"],
        "id": result["id"],
        "answer": result["final_answer"],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", action="append", choices=MODES,
                        help="repeatable; defaults to all four")
    parser.add_argument("--only", help="comma-separated categories")
    args = parser.parse_args()
    modes = args.mode or list(MODES)
    categories = set(args.only.split(",")) if args.only else None

    questions = [(c, q) for c, q in QUESTIONS if not categories or c in categories]

    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Benchmark rows, never organic history.
    engine = build_engine(data_class="eval")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "evals" / "results" / f"auto-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for category, question in questions:
        print(f"\n=== [{category}] {question[:70]}...")
        for mode in modes:
            row = await run_one(engine, mode, question)
            row.update(category=category, question=question)
            rows.append(row)
            if "error" in row:
                print(f"  {mode:<8} ERROR {row['error'][:60]}")
            else:
                extra = ""
                if mode == "auto":
                    extra = f" -> {row['effective_mode']}"
                    if row["escalated"]:
                        extra += " (escalated)"
                    elif row["escalation_refusal"]:
                        extra += f" ({row['escalation_refusal']})"
                print(
                    f"  {mode:<8}{extra:<28} {row['model_calls']} calls  "
                    f"${row['cost_usd']:.4f}  {row['latency_ms']}ms"
                    f"{'  DEGRADED' if row['degraded'] else ''}"
                )

    (out_dir / "rows.json").write_text(json.dumps(rows, indent=2, default=str))
    print(summarise(rows, modes))
    (out_dir / "summary.txt").write_text(summarise(rows, modes))
    print(f"\nWritten to {out_dir}")
    print(
        "\nNow rate the answers. The question this eval exists to answer is not "
        "'did Auto pass?' but 'did Auto pick a cheaper path WITHOUT costing "
        "quality?' — which needs your judgement on the answers in rows.json, "
        "not just these numbers."
    )


def summarise(rows: list[dict], modes: list[str]) -> str:
    lines = ["", "=" * 64, "SUMMARY", "=" * 64]
    for mode in modes:
        got = [r for r in rows if r["mode"] == mode and "error" not in r]
        if not got:
            lines.append(f"{mode:<8} no successful runs")
            continue
        cost = sum(r["cost_usd"] for r in got)
        calls = sum(r["model_calls"] for r in got)
        latency = sum(r["latency_ms"] for r in got) / len(got)
        degraded = sum(1 for r in got if r["degraded"])
        overrides = sum(1 for r in got if r["evidence_override"])
        lines.append(
            f"{mode:<8} n={len(got):<3} ${cost:.4f} total  {calls} calls  "
            f"{latency:.0f}ms avg  {degraded} degraded  {overrides} evidence overrides"
        )

    auto = [r for r in rows if r["mode"] == "auto" and "error" not in r]
    if auto:
        lines += ["", "Auto routing:"]
        for mode in ("quick", "council", "deep"):
            chosen = [r for r in auto if r["effective_mode"] == mode]
            if chosen:
                cats = sorted({r["category"] for r in chosen})
                lines.append(f"  -> {mode:<8} {len(chosen):>2}  ({', '.join(cats)})")
        escalated = [r for r in auto if r["escalated"]]
        refused = [r for r in auto if r["escalation_refusal"]]
        lines.append(f"  escalated: {len(escalated)}   escalation refused: {len(refused)}")

        council = {r["question"]: r for r in rows if r["mode"] == "council"}
        delta = sum(
            council[r["question"]]["cost_usd"] - r["cost_usd"]
            for r in auto
            if r["question"] in council and "error" not in council[r["question"]]
        )
        lines.append(
            f"  cost vs always-council: ${delta:+.4f} "
            f"({'saved' if delta > 0 else 'spent'} over {len(auto)} questions)"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    asyncio.run(main())
