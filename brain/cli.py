"""brain CLI: ingest | lint | search | status | integrate | garden | serve"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from brain.config import load_config
from brain.core.vault import Vault


def _vault() -> Vault:
    return Vault(load_config())


@click.group()
def cli():
    """Personal brain tools: ingest, lint, search, garden."""


# ---------------------------------------------------------------- ingest
@cli.command()
@click.argument("module", type=click.Choice(["manual", "sessions", "hermes", "pai"]))
@click.argument("targets", nargs=-1)
def ingest(module: str, targets: tuple[str, ...]):
    """Stage raw content from a source module (phase 1: acquisition only)."""
    from brain.ingest.manifest import Manifest

    config = load_config()
    manifest = Manifest(config)
    if module == "manual":
        if not targets:
            raise click.UsageError("manual ingest needs at least one file/dir/URL")
        from brain.ingest.manual import ManualModule
        mod = ManualModule(config, list(targets))
    elif module == "sessions":
        if not targets:
            raise click.UsageError("sessions ingest needs at least one transcript/note path")
        from brain.ingest.sessions import SessionsModule
        mod = SessionsModule(config, list(targets))
    elif module == "hermes":
        from brain.ingest.hermes import HermesModule
        mod = HermesModule(config)
    else:
        from brain.ingest.pai import PAIModule
        mod = PAIModule(config)
    staged, skipped = mod.stage(manifest)
    click.echo(f"{module}: staged {staged}, skipped {skipped} (unchanged)")
    if staged:
        click.echo("Run an agent integration pass or `brain status` to see the queue.")


@cli.command()
def status():
    """Show unintegrated raw files awaiting wiki integration."""
    from brain.ingest.manifest import Manifest

    config = load_config()
    pending = Manifest(config).unintegrated()
    if not pending:
        click.echo("All staged raw files are integrated.")
        return
    click.echo(f"{len(pending)} raw file(s) awaiting integration:")
    for e in pending:
        click.echo(f"  [{e['origin']}] {e['path']}")


@cli.command()
@click.argument("key")
def integrate(key: str):
    """Mark a manifest entry integrated (key = origin:origin_id).

    Called by the integrating agent after it has updated the wiki.
    """
    from brain.ingest.manifest import Manifest

    m = Manifest(load_config())
    if m.mark_integrated(key):
        m.save()
        click.echo(f"marked integrated: {key}")
    else:
        click.echo(f"unknown key: {key}", err=True)
        sys.exit(1)


@cli.command("integrate-run")
@click.option("--apply", "apply_", is_flag=True, help="Override dry_run for this run")
def integrate_run(apply_: bool):
    """Run the internal integration agent over the unintegrated queue.

    Fully self-contained phase 2: reads staged raw items, calls the LLM for
    an integration plan, applies validated page writes, marks integrated.
    """
    from brain.core.lock import LockedError, run_lock
    from brain.integrate.agent import run_integration

    config = load_config()
    if apply_:
        config.integrator.dry_run = False
    try:
        with run_lock(config, "integrate"):
            results = run_integration(Vault(config), config)
    except LockedError as e:
        raise click.ClickException(str(e))
    if not results:
        click.echo("queue empty")
        return
    for r in results:
        click.echo(r)


@cli.command("apply-plan")
@click.option("--kind", type=click.Choice(["integrate", "curate"]), required=True)
@click.option("--key", default="", help="Manifest key origin:origin_id (integrate only)")
@click.option("--file", "file_", type=click.Path(exists=True), default=None,
              help="Plan JSON file (default: read stdin)")
def apply_plan(kind: str, key: str, file_: str | None):
    """Apply an externally produced JSON plan through the brain's validators.

    This is how pointed agents (via playbooks) write to the wiki: they gather
    context with brain tools, produce a plan, and submit it here — the exact
    same validated code path the internal agent uses (path guards, schema
    checks, caps, provenance, manifest, log, git commit).
    """
    import json as _json

    from brain.core.lock import LockedError, run_lock

    config = load_config()
    vault = Vault(config)
    raw = Path(file_).read_text() if file_ else sys.stdin.read()
    try:
        plan = _json.loads(raw)
    except _json.JSONDecodeError as e:
        raise click.ClickException(f"invalid plan JSON: {e}")

    try:
        with run_lock(config, kind):
            if kind == "integrate":
                if not key or ":" not in key:
                    raise click.ClickException(
                        "--key origin:origin_id required for integrate plans"
                    )
                from brain.ingest.manifest import Manifest
                from brain.integrate.agent import (
                    apply_integration_plan,
                    post_run_housekeeping,
                )
                m = Manifest(config)
                entry = m.entries.get(key)
                if entry is None:
                    raise click.ClickException(f"unknown manifest key: {key}")
                raw_path = Path(entry["path"])
                try:
                    raw_rel = str(raw_path.resolve().relative_to(config.vault_path.resolve()))
                except ValueError:
                    raw_rel = str(raw_path)
                ops, applied = apply_integration_plan(vault, config, plan, key, raw_rel)
                if applied:
                    post_run_housekeeping(vault, config, f"apply-plan: {key}")
            else:
                from brain.gardener.curator import apply_curation_plan
                ops = apply_curation_plan(vault, plan, config)
                if ops:
                    from brain.gardener.mechanical import rebuild_index
                    from brain.search import keyword
                    rebuild_index(vault)
                    keyword.index(vault)
                    from datetime import datetime, timezone
                    today = datetime.now(timezone.utc).date().isoformat()
                    for line in ops:
                        vault.append_log(f"{today} [gardener/curator-external] {line}")
    except LockedError as e:
        raise click.ClickException(str(e))
    for o in ops:
        click.echo(o)
    if not ops:
        click.echo("no valid operations in plan")


# ------------------------------------------------------------------ lint
@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def lint(as_json: bool):
    """Run mechanical lint checks over the wiki."""
    from brain.lint.rules import run_lint

    report = run_lint(_vault())
    click.echo(report.to_json() if as_json else report.to_text())
    sys.exit(1 if report.total_issues else 0)


# ---------------------------------------------------------------- search
@cli.command()
@click.argument("query")
@click.option("-n", "--limit", default=10)
@click.option("--tag", default=None, help="Filter by domain tag (keyword backend)")
@click.option("--semantic", is_flag=True, help="Use qmd vector search")
def search(query: str, limit: int, tag: str | None, semantic: bool):
    """Search the wiki (keyword FTS5 by default, qmd if configured)."""
    vault = _vault()
    backend = vault.config.search_backend
    if semantic or backend == "qmd":
        from brain.search import qmd
        hits = qmd.search(vault, query, limit, mode="vsearch" if semantic else "query")
    else:
        from brain.search import keyword
        hits = keyword.search(vault, query, limit, tag)
    if not hits:
        click.echo("no results")
        return
    for h in hits:
        click.echo(f"{h.path}  —  {h.title}")
        if h.snippet:
            click.echo(f"    {h.snippet}")


@cli.command()
def reindex():
    """Rebuild the keyword search index."""
    from brain.search import keyword

    n = keyword.index(_vault())
    click.echo(f"indexed {n} pages")


# ---------------------------------------------------------------- review
@cli.command()
@click.option("--clear", "clear_path", default=None,
              help="Clear the review flag on a page (wiki-relative path or stem)")
def review(clear_path: str | None):
    """List pages flagged for human review (agents set `review` frontmatter
    on uncertain judgment calls instead of stalling the batch)."""
    vault = _vault()
    if clear_path:
        page = vault.get_page(clear_path)
        if page is None:
            raise click.ClickException(f"page not found: {clear_path}")
        if "review" not in page.metadata:
            click.echo("no review flag on that page")
            return
        del page.metadata["review"]
        page.save()
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        rel = page.path.relative_to(vault.config.wiki_dir)
        vault.append_log(f"{today} [human] review cleared: {rel}")
        click.echo(f"cleared: {rel}")
        return
    flagged = [
        p for p in vault.wiki_pages()
        if p.metadata.get("review") and p.path.stem not in ("index", "log")
    ]
    if not flagged:
        click.echo("review queue empty")
        return
    click.echo(f"{len(flagged)} page(s) awaiting review:")
    for p in flagged:
        rel = p.path.relative_to(vault.config.wiki_dir)
        click.echo(f"  {rel}: {p.metadata['review']}")


# ---------------------------------------------------------------- garden
@cli.command()
@click.option("--no-llm", is_flag=True, help="Mechanical pass only")
@click.option("--apply", "apply_", is_flag=True, help="Override dry_run for this run")
@click.option("--synthesis", "with_synthesis", is_flag=True,
              help="Also run the weekly synthesis pass (premium model)")
def garden(no_llm: bool, apply_: bool, with_synthesis: bool):
    """Curate the wiki: mechanical fixes + LLM curation + optional synthesis."""
    from brain.gardener.mechanical import run_mechanical

    config = load_config()
    vault = Vault(config)
    report = run_mechanical(vault)
    click.echo(report.to_text())

    if no_llm or not config.gardener.llm_enabled:
        return
    if apply_:
        config.gardener.dry_run = False
    from brain.core.lock import LockedError, run_lock
    from brain.gardener.curator import run_curator

    try:
        with run_lock(config, "curate"):
            applied = run_curator(vault, report, config)
    except LockedError as e:
        raise click.ClickException(str(e))
    for line in applied:
        click.echo(f"applied: {line}")
    if applied:
        click.echo(f"gardener applied {len(applied)} operation(s); see wiki/log.md")

    if with_synthesis or config.gardener.synthesis_enabled:
        from brain.gardener.synthesis import run_synthesis
        written = run_synthesis(vault, report, config)
        click.echo(f"synthesis: {written}" if written
                   else "synthesis: skipped (already exists this week, or dry-run)")


@cli.command()
@click.option("--apply", "apply_", is_flag=True, help="Override dry_run for this run")
def synthesize(apply_: bool):
    """Run only the weekly synthesis pass (premium model, one page per week)."""
    from brain.gardener.synthesis import run_synthesis
    from brain.lint.rules import run_lint

    config = load_config()
    if apply_:
        config.gardener.dry_run = False
    vault = Vault(config)
    written = run_synthesis(vault, run_lint(vault), config)
    click.echo(f"wrote {written}" if written
               else "skipped (already exists this week, or dry-run)")


# ------------------------------------------------------------------ mcp
@cli.command()
def serve():
    """Run the MCP server (stdio) exposing brain tools to agents."""
    from brain.mcp_server import main

    main()


if __name__ == "__main__":
    cli()
