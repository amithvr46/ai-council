import asyncio
import json
from pathlib import Path

import typer

from council.db.models import Base
from council.db.session import get_engine, init_engine
from council.documents.extract import ExtractionError, extract
from council.documents.profile import (
    AUTHORITY_JD,
    CAREER_AUTHORITIES,
    assemble_confirmed,
    detect_role_family,
    scan_jd_technologies,
)
from council.documents.render import render_docx
from council.documents.store import (
    career_documents,
    list_conflicts,
    load_discovery_cache,
    load_profile,
    resolve_conflict,
    save_artifact,
    save_conflicts,
    save_discovery_cache,
    save_profile,
    store_document,
)
from council.engine.factory import build_engine, build_resume_workflow

app = typer.Typer(help="AI Council — ask once, get one verified answer.")


async def _ensure_schema():
    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.command()
def ask(
    question: str,
    mode: str = typer.Option("auto", help="auto | quick | council | deep"),
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


@app.command("routing-report")
def routing_report(
    data_class: str = typer.Option(
        "real", help="real | eval | synthetic — populations are never mixed"
    ),
):
    """How Auto's routing is actually performing, per outcome and mode.

    Only one population at a time, deliberately: synthetic rows must never
    reach routing statistics, and a benchmark sweep is not evidence of real
    usage.
    """
    from council.engine.routing_report import collect, render

    async def _run():
        await _ensure_schema()
        return await collect(data_class)

    typer.echo(render(asyncio.run(_run()), data_class))


@app.command()
def ingest(
    path: str,
    authority: str = typer.Option(
        "supporting",
        help="profile | user_statement | master_resume | supporting | tailored_resume | jd",
    ),
    title: str = typer.Option("", help="Display name; defaults to the filename"),
):
    """Ingest a career source or a job description.

    Career sources add to confirmed experience and never subtract. A JD is the
    target, not evidence — ingesting one never makes the system claim what it
    asks for.
    """
    if authority not in (*CAREER_AUTHORITIES, AUTHORITY_JD):
        typer.echo(f"unknown authority '{authority}'", err=True)
        raise typer.Exit(2)

    file = Path(path).expanduser()
    if not file.is_file():
        typer.echo(f"no such file: {file}", err=True)
        raise typer.Exit(2)

    async def _run():
        await _ensure_schema()
        try:
            extracted = extract(file.name, file.read_bytes())
        except ExtractionError as e:
            typer.echo(f"cannot read {file.name}: {e}", err=True)
            raise typer.Exit(1) from None
        return await store_document(
            filename=file.name, title=title, authority=authority, extracted=extracted
        )

    row, duplicate = asyncio.run(_run())
    state = "already ingested" if duplicate else "ingested"
    typer.echo(
        f"{state}: {row.title} [{row.authority}] "
        f"{row.detected_kind}, {row.char_count} chars, id={row.id}"
    )
    if row.truncated:
        typer.echo("  note: text was truncated at the size limit")


@app.command()
def profile(
    show_sources: bool = typer.Option(False, "--sources", help="Show what established each term"),
):
    """Show confirmed experience assembled from every career source."""

    async def _run():
        await _ensure_schema()
        p = await load_profile()
        return p, assemble_confirmed(p, await career_documents())

    p, confirmed = asyncio.run(_run())
    typer.echo(f"{len(confirmed.terms)} confirmed terms")
    if p.employers:
        typer.echo(f"employers: {', '.join(p.employers)}")
    if p.roles:
        typer.echo(f"roles: {', '.join(p.roles)}")
    if show_sources:
        for term in sorted(confirmed.terms):
            typer.echo(f"  {term}  <-  {', '.join(confirmed.sources[term])}")
    else:
        typer.echo(", ".join(sorted(confirmed.terms)))


# Module-level singleton: typer needs it as a default, ruff's B008 objects to
# the inline call.
_PROFILE_VALUES = typer.Argument(..., help="Replacement values for that field")


@app.command("profile-set")
def profile_set(
    field: str = typer.Argument(
        ..., help="technologies | domains | roles | employers | certifications | achievements"
    ),
    values: list[str] = _PROFILE_VALUES,
):
    """Set a profile field. The profile is extensible by design — adding real
    experience later is a command, not a redesign."""
    allowed = {"technologies", "domains", "roles", "employers", "certifications", "achievements"}
    if field not in allowed:
        typer.echo(f"unknown field '{field}'; expected one of {sorted(allowed)}", err=True)
        raise typer.Exit(2)

    async def _run():
        await _ensure_schema()
        await save_profile(**{field: list(values)})

    asyncio.run(_run())
    typer.echo(f"{field}: {', '.join(values)}")


@app.command("analyze-jd")
def analyze_jd(path: str):
    """Report which role family a JD targets and how the confirmed career maps
    onto it, without treating the JD as evidence."""
    file = Path(path).expanduser()
    if not file.is_file():
        typer.echo(f"no such file: {file}", err=True)
        raise typer.Exit(2)

    async def _run():
        await _ensure_schema()
        try:
            extracted = extract(file.name, file.read_bytes())
        except ExtractionError as e:
            typer.echo(f"cannot read {file.name}: {e}", err=True)
            raise typer.Exit(1) from None
        p = await load_profile()
        return extracted.text, assemble_confirmed(p, await career_documents())

    text, confirmed = asyncio.run(_run())
    family, emphasis = detect_role_family(text)
    supported = [e for e in emphasis if confirmed.is_confirmed(e)]
    unsupported = [e for e in emphasis if not confirmed.is_confirmed(e)]
    tech_supported, tech_unsupported = scan_jd_technologies(text, confirmed)
    typer.echo(f"role family: {family}")
    typer.echo(f"supported emphasis:   {', '.join(supported) or '(none)'}")
    typer.echo(f"unsupported emphasis: {', '.join(unsupported) or '(none)'}")
    typer.echo(f"JD technologies you have:    {', '.join(tech_supported) or '(none)'}")
    typer.echo(f"JD technologies you do NOT:  {', '.join(tech_unsupported) or '(none)'}")
    if unsupported or tech_unsupported:
        typer.echo(
            "\nUnsupported means no career source establishes it. The resume will "
            "not claim these. Add real experience with `council profile-set` if any "
            "of them belong."
        )


@app.command("generate-resume")
def generate_resume(
    jd_path: str,
    out: str = typer.Option("resume.docx", help="Output DOCX path"),
    name: str = typer.Option("", help="Your name for the header"),
    contact: str = typer.Option("", help="Contact line for the header"),
    trace: bool = typer.Option(False, help="Print the full internal trace"),
):
    """Career sources + JD -> submission-ready DOCX.

    You do not pick models, modes, keywords or review stages (contract A12).
    """
    file = Path(jd_path).expanduser()
    if not file.is_file():
        typer.echo(f"no such file: {file}", err=True)
        raise typer.Exit(2)

    async def _run():
        await _ensure_schema()
        try:
            jd = extract(file.name, file.read_bytes())
        except ExtractionError as e:
            typer.echo(f"cannot read {file.name}: {e}", err=True)
            raise typer.Exit(1) from None

        p = await load_profile()
        documents = await career_documents()
        cache = await load_discovery_cache()
        workflow = build_resume_workflow()
        result = await workflow.run(jd.text, p, documents, cache=cache)
        await save_discovery_cache(cache)
        await save_conflicts(result.analysis.conflicts)
        path = render_docx(result.draft, out, name=name, contact=contact)
        await save_artifact(
            kind="resume_tailor",
            jd_document_id=None,
            role_family=result.analysis.role_family,
            title=file.stem,
            content=result.draft.model_dump(),
            trace={
                "analysis": result.analysis.as_dict(),
                "review": result.review.model_dump() if result.review else None,
                "findings": result.findings,
                **result.trace.as_dict(),
            },
            cost_usd=result.trace.cost_usd,
            file_path=str(path),
        )
        return result, path

    result, path = asyncio.run(_run())
    typer.echo(f"wrote {path}")
    typer.echo(
        f"role family: {result.analysis.role_family} | "
        f"{result.trace.model_calls} model calls | ${result.trace.cost_usd:.4f}"
    )
    typer.echo(f"match quality: {result.analysis.match_quality} (advisory)")
    if result.analysis.gaps:
        typer.echo(f"gaps not claimed: {', '.join(result.analysis.gaps)}")
        typer.echo(
            "  The resume was still written — these are reported, never "
            "manufactured. The application decision stays yours."
        )
    if result.analysis.conflicts:
        typer.echo(
            f"{len(result.analysis.conflicts)} unresolved source conflict(s) — "
            "those facts were withheld. Run `council conflicts` to see them."
        )
    if result.findings:
        typer.echo(f"\n{len(result.findings)} finding(s) survived correction:")
        for f in result.findings:
            typer.echo(f"  [{f['class']}] {f['location']}: {'; '.join(f['reasons'])}")
    if result.review is not None:
        typer.echo(f"\nwould submit: {result.review.would_submit}")
    if trace:
        typer.echo(json.dumps(result.as_dict(), indent=2, default=str))


@app.command()
def conflicts():
    """Material factual disagreements between your career sources.

    These are not wording differences. Until one is resolved the disputed fact
    is withheld from generated documents rather than guessed at.
    """

    async def _run():
        await _ensure_schema()
        return await list_conflicts()

    rows = asyncio.run(_run())
    if not rows:
        typer.echo("no unresolved conflicts")
        return
    for row in rows:
        values = " vs ".join(sorted({v["value"] for v in row["values"] or []}))
        typer.echo(f"[{row['kind']}] {row['subject']}")
        typer.echo(f"    {values}")
        for v in row["values"] or []:
            typer.echo(f"      {v['value']}  <-  {v['source']}")
        typer.echo(f"    resolve with: council resolve-conflict {row['id']} \"<value>\"")


@app.command("resolve-conflict")
def resolve_conflict_cmd(conflict_id: str, value: str):
    """Settle a disputed fact so documents can state it again."""

    async def _run():
        await _ensure_schema()
        return await resolve_conflict(conflict_id, value)

    if not asyncio.run(_run()):
        typer.echo(f"no conflict with id {conflict_id}", err=True)
        raise typer.Exit(2)
    typer.echo(f"resolved: {value}")


if __name__ == "__main__":
    app()
