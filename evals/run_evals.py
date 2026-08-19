"""Run the golden set through the pipeline and store results for blind rating.

Usage:
    python evals/run_evals.py --mode quick --mode council
    python evals/run_evals.py --only g03,g07 --mode council

Results land in evals/results/<timestamp>/ as one markdown file per run plus
a results.json with ids, costs and latencies. Answers are written WITHOUT
revealing which mode produced them where possible, so you can blind-rate.
Requires live API keys in .env.
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from council.db.models import Base  # noqa: E402
from council.db.session import get_engine, init_engine  # noqa: E402
from council.engine.factory import build_engine  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", action="append", default=None, help="quick|council|deep")
    parser.add_argument("--only", default=None, help="comma-separated golden ids")
    args = parser.parse_args()
    modes = args.mode or ["quick", "council"]

    goldens = yaml.safe_load((ROOT / "goldens" / "questions.yaml").read_text())
    if args.only:
        wanted = set(args.only.split(","))
        goldens = [g for g in goldens if g["id"] in wanted]

    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    engine = build_engine(data_class="eval")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "evals" / "results" / stamp
    outdir.mkdir(parents=True)

    records = []
    for g in goldens:
        for mode in modes:
            print(f"[{g['id']}] {mode} ...", flush=True)
            try:
                result = await engine.run(g["question"], mode)
            except Exception as e:  # keep going; a failed run is itself data
                records.append({"golden": g["id"], "mode": mode, "error": str(e)})
                continue
            records.append(
                {
                    "golden": g["id"],
                    "category": g["category"],
                    "mode": mode,
                    "request_id": result["id"],
                    "status": result["status"],
                    "degraded": result["degraded"],
                    "totals": result["totals"],
                }
            )
            # Blind file: the answer file does not reveal which mode produced it.
            token = f"{g['id']}-{random.randint(1000, 9999)}"
            (outdir / f"answer-{token}.md").write_text(
                f"# {g['id']} ({g['category']})\n\n"
                f"**Question:** {g['question']}\n\n---\n\n{result['final_answer']}\n"
            )
            key_path = outdir / "key.json"
            key = json.loads(key_path.read_text()) if key_path.exists() else {}
            key[token] = mode
            key_path.write_text(json.dumps(key, indent=2))

    (outdir / "results.json").write_text(json.dumps(records, indent=2))
    total_cost = sum(r["totals"]["cost_usd"] for r in records if "totals" in r)
    print(f"\nDone. {len(records)} runs, total ${total_cost:.4f}. Results in {outdir}")
    print("Blind-rate the answer-*.md files, then check key.json to see which mode was which.")


if __name__ == "__main__":
    asyncio.run(main())
