"""Internal integration agent: phase 2 without an external agent.

`brain integrate-run` pulls unintegrated raw items from the manifest and,
per item: searches the wiki for related pages, asks the model for a strict
JSON integration plan (create/update pages), validates and applies it, logs,
marks the item integrated, and git-commits. This makes the brain fully
self-contained — acquire → integrate → curate → synthesize all run from cron.

External agents over MCP remain supported for interactive integration; both
paths converge on the same manifest flag, so they never double-process.

Guardrails (same philosophy as the gardener):
- dry_run default: plans printed, nothing written, nothing marked integrated.
- Caps: max_items_per_run, max_pages_per_item.
- Writes only under wiki/; index.md and log.md untouchable; frontmatter
  schema-validated before writing; unknown types rejected.
- The model may return skip=true for low-value items (e.g. empty sessions):
  they're marked integrated with a log line, keeping the queue clean.
- Every applied plan → log.md lines + one git commit per run for rollback.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import frontmatter as fm

from brain.config import Config
from brain.core.frontmatter import VALID_TYPES, Page, validate_page
from brain.core.vault import Vault
from brain.ingest.manifest import Manifest
from brain.search import keyword

SYSTEM_PROMPT = """You are the librarian of a personal + business knowledge \
wiki (markdown pages, YAML frontmatter, [[wikilinks]]). You receive one staged \
raw item plus related existing pages. Integrate the item: update existing \
pages where possible (preferred), create new pages only when clearly needed. \
Small densely-linked pages beat long ones. Every claim you add must carry a \
receipt: the raw path in the page's `sources` frontmatter, and for external \
claims a source + date in the body. Set `expires` (YYYY-MM-DD) on \
time-sensitive intelligence. For session digests, extract only decisions, \
caught mistakes, preferences, and recurring patterns worth keeping a month \
from now; if nothing qualifies, skip. Include a one-line `summary` in each \
page's frontmatter. Proceed confidently on clear items; when genuinely \
uncertain about a judgment call, still integrate but set `review_reason` on \
that page so a human can check it later — flag the uncertain page, never \
stall the batch. Respond ONLY with JSON matching the schema — no prose, no \
markdown fences."""

PLAN_SCHEMA = """{
  "skip": false,
  "skip_reason": "",
  "pages": [
    {
      "path": "entities/jane-doe.md",
      "action": "create" | "update",
      "frontmatter": {"type": "entity|concept|source|synthesis|client",
                      "title": "", "description": "",
                      "summary": "one-line distillation",
                      "tags": ["personal|business|research"],
                      "status": "active|dormant|archived (optional)",
                      "kind": "area|project (optional, business pages)",
                      "expires": "YYYY-MM-DD (optional)",
                      "sources": ["raw/..."]},
      "review_reason": "set ONLY if uncertain — flags page for human review",
      "body": "full page body markdown, may contain [[wikilinks]]"
    }
  ],
  "log": "one line describing the integration"
}"""


def _model_call(config: Config, prompt: str) -> dict:
    import anthropic  # optional dep: pip install 'brain-tools[gardener]'

    from brain.core.playbooks import load_system_prompt

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.integrator.model,
        max_tokens=8000,
        system=load_system_prompt(config, "integrate", SYSTEM_PROMPT),
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def _related_pages(vault: Vault, raw_text: str, limit: int = 5) -> list[Page]:
    """Find wiki pages related to the raw item via keyword search on its terms."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", raw_text)
    seen: list[str] = []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.append(lw)
        if len(seen) >= 12:
            break
    if not seen:
        return []
    try:
        hits = keyword.search(vault, " OR ".join(seen), limit=limit)
    except Exception:  # noqa: BLE001 - related pages are best-effort
        return []
    pages = []
    for h in hits:
        p = vault.get_page(h.path)
        if p and p.path.stem not in ("index", "log"):
            pages.append(p)
    return pages


def _build_prompt(vault: Vault, entry: dict, raw_text: str) -> str:
    related = _related_pages(vault, raw_text)
    parts = [
        f"RAW ITEM (origin={entry['origin']}, path={entry['path']}):",
        raw_text[:20_000],
        "\nEXISTING PAGE STEMS (valid wikilink targets):",
        ", ".join(sorted(vault.page_name_index().keys())) or "(wiki is empty)",
        "\nRELATED PAGES (update these where possible):",
    ]
    if related:
        for p in related:
            rel = p.path.relative_to(vault.config.wiki_dir)
            parts.append(f"\n--- {rel} ---\n{p.dump()[:4000]}")
    else:
        parts.append("(none found)")
    parts.append("\nProduce an integration plan as JSON with this schema:\n" + PLAN_SCHEMA)
    return "\n".join(parts)


def _validate_and_write(vault: Vault, op: dict, raw_rel_path: str) -> str | None:
    """Validate one page op and write it. Returns applied-description or None."""
    rel = op.get("path", "")
    target = (vault.config.wiki_dir / rel).resolve()
    if vault.config.wiki_dir.resolve() not in target.parents:
        return None
    if target.name in ("index.md", "log.md"):
        return None
    meta = dict(op.get("frontmatter") or {})
    if meta.get("type") not in VALID_TYPES or meta.get("type") in ("index", "log"):
        return None
    if op.get("review_reason"):
        meta["review"] = str(op["review_reason"])[:200]
    meta.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    sources = meta.get("sources") or []
    if raw_rel_path not in sources:
        sources.append(raw_rel_path)
    meta["sources"] = sources
    page = Page(path=target, metadata=meta, content=op.get("body", ""))
    if validate_page(page, require_domain_tag=True):
        return None  # schema problems -> reject the op
    action = op.get("action", "create")
    if action == "create" and target.exists():
        action = "update"  # be tolerant; overwrite-as-update
    target.parent.mkdir(parents=True, exist_ok=True)
    page.save()
    return f"{action} {rel}"


def _git_commit(vault: Vault, message: str) -> None:
    cwd = vault.config.vault_path
    subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=cwd, capture_output=True)


def apply_integration_plan(
    vault: Vault, config: Config, plan: dict, key: str, raw_rel: str,
    manifest: Manifest | None = None,
) -> tuple[list[str], bool]:
    """Validate and apply one integration plan. Shared by the internal agent
    and `brain apply-plan` (external agents). Returns (descriptions, applied).
    Does NOT reindex/commit — callers do post-run housekeeping.

    If `manifest` is provided, marks are made on it WITHOUT saving (the
    caller owns persistence — prevents two instances clobbering each other's
    marks). If None, a fresh manifest is loaded, marked, and saved here."""
    own_manifest = manifest is None
    if own_manifest:
        manifest = Manifest(config)
    today = datetime.now(timezone.utc).date().isoformat()

    if plan.get("skip"):
        manifest.mark_integrated(key)
        if own_manifest:
            manifest.save()
        vault.append_log(
            f"{today} [integrator] skip {raw_rel}: {plan.get('skip_reason', 'low value')}"
        )
        return [f"skip {raw_rel}"], True

    applied_ops = []
    for op in (plan.get("pages") or [])[: config.integrator.max_pages_per_item]:
        desc = _validate_and_write(vault, op, raw_rel)
        if desc:
            applied_ops.append(desc)
    if applied_ops:
        manifest.mark_integrated(key)
        if own_manifest:
            manifest.save()
        vault.append_log(
            f"{today} [integrator] {plan.get('log', raw_rel)} "
            f"({'; '.join(applied_ops)})"
        )
        return applied_ops, True
    return [f"no valid ops for {raw_rel} (left in queue)"], False


def post_run_housekeeping(vault: Vault, config: Config, commit_msg: str) -> None:
    """Rebuild index, reindex search, git-commit. Shared by both executors."""
    from brain.gardener.mechanical import rebuild_index

    rebuild_index(vault)
    keyword.index(vault)
    if config.integrator.git_commit:
        _git_commit(vault, commit_msg)


def run_integration(vault: Vault, config: Config) -> list[str]:
    manifest = Manifest(config)
    queue = manifest.unintegrated()[: config.integrator.max_items_per_run]
    if not queue:
        return []

    today = datetime.now(timezone.utc).date().isoformat()
    results: list[str] = []
    any_applied = False

    for entry in queue:
        raw_path = Path(entry["path"])
        if not raw_path.is_file():
            manifest.mark_integrated(manifest.key(entry["origin"], entry["origin_id"]))
            results.append(f"gone: {entry['path']} (marked integrated)")
            continue
        raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
        try:
            raw_rel = str(raw_path.resolve().relative_to(config.vault_path.resolve()))
        except ValueError:
            raw_rel = str(raw_path)

        plan = _model_call(config, _build_prompt(vault, entry, raw_text))

        if config.integrator.dry_run:
            print(f"[dry-run] plan for {raw_rel}:")
            print(json.dumps(plan, indent=2))
            continue

        key = manifest.key(entry["origin"], entry["origin_id"])
        ops, applied = apply_integration_plan(
            vault, config, plan, key, raw_rel, manifest=manifest
        )
        results.extend(ops)
        if applied and config.integrator.git_commit:
            # Per-item commits: every autonomous integration is an
            # individually reviewable/revertible diff.
            _git_commit(vault, f"integrate: {raw_rel}")
        any_applied = any_applied or applied

    manifest.save()
    if any_applied:
        post_run_housekeeping(
            vault, config, f"integrator: index refresh after {len(results)} op(s) on {today}"
        )
    return results
