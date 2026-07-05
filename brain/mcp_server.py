"""MCP server (stdio) — same functions as the CLI, exposed as agent tools.

Install: pip install 'brain-tools[mcp]'
Claude Desktop / Claude Code config:
  { "mcpServers": { "brain": { "command": "brain", "args": ["serve"] } } }
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from brain.config import load_config
from brain.core.graph import build_graph
from brain.core.vault import Vault

mcp = FastMCP("brain")


def _vault() -> Vault:
    return Vault(load_config())


@mcp.tool()
def brain_search(query: str, limit: int = 10, semantic: bool = False) -> str:
    """Search the personal wiki. Returns matching page paths with snippets.
    Set semantic=true for vector search (requires qmd backend)."""
    vault = _vault()
    if semantic or vault.config.search_backend == "qmd":
        from brain.search import qmd
        hits = qmd.search(vault, query, limit, mode="vsearch" if semantic else "query")
    else:
        from brain.search import keyword
        hits = keyword.search(vault, query, limit)
    return json.dumps([h.__dict__ for h in hits], indent=2)


@mcp.tool()
def brain_get_page(name: str) -> str:
    """Read a wiki page by stem (e.g. 'anthropic') or relative path
    (e.g. 'entities/anthropic.md'). Returns frontmatter + body."""
    page = _vault().get_page(name)
    if page is None:
        return f"page not found: {name}"
    return page.dump()


@mcp.tool()
def brain_get_neighbors(name: str) -> str:
    """Get outlinks and backlinks for a page — navigate the wiki map cheaply
    before reading full page bodies."""
    graph = build_graph(_vault())
    return json.dumps(graph.neighbors(name), indent=2)


@mcp.tool()
def brain_lint() -> str:
    """Run mechanical lint checks (schema, broken links, orphans, staleness).
    Use the report to decide which pages need semantic curation."""
    from brain.lint.rules import run_lint
    return run_lint(_vault()).to_json()


@mcp.tool()
def brain_status() -> str:
    """List staged raw files that have NOT yet been integrated into the wiki.
    These are the integration queue for the agent."""
    from brain.ingest.manifest import Manifest
    pending = Manifest(load_config()).unintegrated()
    return json.dumps(pending, indent=2)


@mcp.tool()
def brain_read_raw(path: str) -> str:
    """Read a staged raw file by absolute or vault-relative path."""
    from pathlib import Path
    config = load_config()
    p = Path(path)
    if not p.is_absolute():
        p = config.vault_path / path
    if not p.is_file() or config.raw_dir not in p.resolve().parents:
        return f"not a raw file: {path}"
    return p.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def brain_write_page(relative_path: str, content: str) -> str:
    """Create or overwrite a wiki page. relative_path is under wiki/
    (e.g. 'entities/jane-doe.md'). Content must include YAML frontmatter
    with at least: type, title, description, tags, timestamp."""
    config = load_config()
    target = (config.wiki_dir / relative_path).resolve()
    if config.wiki_dir.resolve() not in target.parents:
        return "refused: path escapes wiki/"
    if target.name in ("log.md",):
        return "refused: log.md is append-only, use brain_log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {relative_path}"


@mcp.tool()
def brain_log(message: str) -> str:
    """Append one line to wiki/log.md. Prefix format is added automatically:
    'YYYY-MM-DD [agent] <message>'."""
    from datetime import datetime, timezone
    vault = _vault()
    today = datetime.now(timezone.utc).date().isoformat()
    vault.append_log(f"{today} [agent] {message}")
    return "logged"


@mcp.tool()
def brain_mark_integrated(key: str) -> str:
    """Mark a raw manifest entry as integrated after updating the wiki.
    key format: 'origin:origin_id' as shown by brain_status."""
    from brain.ingest.manifest import Manifest
    m = Manifest(load_config())
    ok = m.mark_integrated(key)
    if ok:
        m.save()
    return "marked integrated" if ok else f"unknown key: {key}"


@mcp.tool()
def brain_review_queue() -> str:
    """List wiki pages flagged with `review` frontmatter — uncertain judgment
    calls made by autonomous passes that a human should verify."""
    vault = _vault()
    flagged = [
        {"path": str(p.path.relative_to(vault.config.wiki_dir)),
         "reason": p.metadata["review"]}
        for p in vault.wiki_pages()
        if p.metadata.get("review") and p.path.stem not in ("index", "log")
    ]
    return json.dumps(flagged, indent=2)


@mcp.tool()
def brain_get_playbook(name: str) -> str:
    """Read a playbook (integrate | curate | synthesize): the canonical
    workflow an agent should follow to run that pass against this brain,
    including the plan JSON schema and how to submit it."""
    config = load_config()
    path = config.vault_path / "playbooks" / f"{name}.md"
    if not path.is_file():
        return f"no playbook named {name}; available: " + ", ".join(
            sorted(p.stem for p in (config.vault_path / "playbooks").glob("*.md"))
            if (config.vault_path / "playbooks").is_dir() else []
        )
    return path.read_text(encoding="utf-8")


@mcp.tool()
def brain_apply_plan(kind: str, plan_json: str, key: str = "") -> str:
    """Apply a JSON plan through the brain's validated code path (same
    guardrails as the internal agent: wiki-only paths, schema checks, caps,
    provenance, manifest, log, git commit).
    kind: 'integrate' (requires key='origin:origin_id' from brain_status)
    or 'curate'. plan_json: the plan per the playbook's schema."""
    import json as _json

    from brain.core.lock import LockedError, run_lock

    config = load_config()
    vault = _vault()
    try:
        plan = _json.loads(plan_json)
    except _json.JSONDecodeError as e:
        return f"invalid plan JSON: {e}"
    try:
        with run_lock(config, kind):
            if kind == "integrate":
                if not key:
                    return "key required for integrate plans (see brain_status)"
                from pathlib import Path

                from brain.ingest.manifest import Manifest
                from brain.integrate.agent import (
                    apply_integration_plan,
                    post_run_housekeeping,
                )
                entry = Manifest(config).entries.get(key)
                if entry is None:
                    return f"unknown manifest key: {key}"
                raw_path = Path(entry["path"])
                try:
                    raw_rel = str(raw_path.resolve().relative_to(config.vault_path.resolve()))
                except ValueError:
                    raw_rel = str(raw_path)
                ops, applied = apply_integration_plan(vault, config, plan, key, raw_rel)
                if applied:
                    post_run_housekeeping(vault, config, f"apply-plan: {key}")
            elif kind == "curate":
                from datetime import datetime, timezone

                from brain.gardener.curator import apply_curation_plan
                from brain.gardener.mechanical import rebuild_index
                from brain.search import keyword
                ops = apply_curation_plan(vault, plan, config)
                if ops:
                    rebuild_index(vault)
                    keyword.index(vault)
                    today = datetime.now(timezone.utc).date().isoformat()
                    for line in ops:
                        vault.append_log(f"{today} [gardener/curator-external] {line}")
            else:
                return "kind must be 'integrate' or 'curate'"
    except LockedError as e:
        return str(e)
    return "\n".join(ops) if ops else "no valid operations in plan"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
