import asyncio
import json

import typer

from council.db.models import Base
from council.db.session import get_engine, init_engine
from council.engine.factory import build_engine

app = typer.Typer(help="AI Council — ask once, get one verified answer.")


async def _ensure_schema():
    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.command()
def ask(
    question: str,
    mode: str = typer.Option("council", help="quick | council | deep"),
    trace: bool = typer.Option(False, help="Print the full execution trace as JSON"),
):
    """Ask the council a question."""

    async def _run():
        await _ensure_schema()
        engine = build_engine()
        return await engine.run(question, mode)

    result = asyncio.run(_run())
    if trace:
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    typer.echo(result["final_answer"] or f"(no answer — status={result['status']})")
    t = result["totals"]
    flags = " DEGRADED" if result["degraded"] else ""
    typer.echo(
        f"\n--- {result['mode']} | {t['model_calls']} calls | "
        f"{t['input_tokens']}+{t['output_tokens']} tok | "
        f"${t['cost_usd']:.4f} | {t['latency_ms']}ms{flags} | id={result['id']}"
    )


@app.command()
def show(request_id: str):
    """Show the stored trace for a past request."""

    async def _run():
        await _ensure_schema()
        engine = build_engine()
        return await engine.get_request(request_id)

    typer.echo(json.dumps(asyncio.run(_run()), indent=2, default=str))


if __name__ == "__main__":
    app()
