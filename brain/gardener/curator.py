"""Gardener phase B: LLM curation. Compounding wiki improvement on a schedule.

What it does per run (bounded by config caps):
1. Consumes the mechanical lint report + a sample of recent/least-linked pages.
2. Asks the model for a curation plan: entity/concept extraction, link
   injections, broken-link resolutions, and (capped) new stub pages.
3. Applies the plan as strict, validated operations — never freeform writes.

Guardrails:
- dry_run default: prints the plan, touches nothing.
- max_pages_edited_per_run / max_new_pages_per_run hard caps.
- Only wiki/ is writable; raw/ and log/index structure are protected.
- Every applied run is logged to wiki/log.md and (optionally) git-committed
  in the vault, so any run can be reverted with `git revert`.
- Operations are validated (target exists, type is known) before applying.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone

from brain.config import Config
from brain.core.frontmatter import VALID_TYPES, load_page
from brain.core.graph import build_graph
from brain.core.vault import Vault
from brain.ingest.base import slugify
from brain.lint.rules import LintReport

SYSTEM_PROMPT = """You are the curator of a personal knowledge wiki (Open Knowledge \
Format: markdown pages with YAML frontmatter, [[wikilinks]] between pages).
Your job is compounding improvement: densify links, extract entities (people, \
organizations, projects, tools) and concepts that recur across pages, resolve \
broken links, and propose a small number of new stub pages that would make the \
wiki more navigable. Be conservative: only propose changes you are confident \
improve the wiki. Respond ONLY with JSON matching the requested schema."""

PLAN_SCHEMA_HINT = """{
  "add_links": [{"page": "<stem>", "find": "<exact text in body>", "replace": "<same text with [[wikilink]] added>", "reason": ""}],
  "new_pages": [{"title": "", "type": "entity|concept", "description": "", "tags": ["personal|work|research"], "body": "<short stub, may contain [[wikilinks]]>", "reason": ""}],
  "fix_broken_links": [{"page": "<stem>", "broken_target": "", "action": "retarget|remove", "new_target": "<existing stem or empty>", "reason": ""}]
}"""


def _model_call(config: Config, prompt: str) -> dict:
    import anthropic  # optional dep: pip install 'brain-tools[gardener]'

    from brain.core.playbooks import load_system_prompt

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.gardener.curator_model,
        max_tokens=4000,
        system=load_system_prompt(config, "curate", SYSTEM_PROMPT),
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


def _build_prompt(vault: Vault, report: LintReport) -> str:
    graph = build_graph(vault)
    # Prioritize least-connected pages: they benefit most from curation.
    pages = [p for p in vault.wiki_pages() if p.path.stem not in ("index", "log")]
    pages.sort(key=lambda p: len(graph.backlinks.get(p.path.stem.lower(), set())))
    sample = pages[:15]

    parts = [
        "LINT REPORT (mechanical issues found):",
        report.to_text(),
        "\nEXISTING PAGE STEMS (only these are valid wikilink targets):",
        ", ".join(sorted(graph.known)),
        "\nPAGE SAMPLE (least-linked first):",
    ]
    for p in sample:
        body = p.content[:1200]
        parts.append(f"\n--- {p.path.stem} (type={p.type}, tags={p.tags}) ---\n{body}")
    parts.append(
        "\nProduce a curation plan as JSON with this schema:\n" + PLAN_SCHEMA_HINT
    )
    return "\n".join(parts)


def apply_curation_plan(vault: Vault, plan: dict, config: Config) -> list[str]:
    applied: list[str] = []
    edits = 0
    g = build_graph(vault)

    for op in plan.get("fix_broken_links", []) + plan.get("add_links", []):
        if edits >= config.gardener.max_pages_edited_per_run:
            break
        stem = op.get("page", "")
        path = vault.resolve_wikilink(stem)
        if path is None:
            continue
        page = load_page(path)
        if "find" in op:  # add_links op
            find, replace = op.get("find", ""), op.get("replace", "")
            if find and find in page.content and "[[" in replace:
                page.content = page.content.replace(find, replace, 1)
                page.save(); edits += 1
                applied.append(f"link+ {stem}: {op.get('reason','')}")
        else:  # fix_broken_links op
            broken = op.get("broken_target", "")
            if op.get("action") == "retarget" and op.get("new_target", "").lower() in g.known:
                page.content = page.content.replace(f"[[{broken}]]", f"[[{op['new_target']}]]")
            elif op.get("action") == "remove":
                page.content = page.content.replace(f"[[{broken}]]", broken)
            else:
                continue
            page.save(); edits += 1
            applied.append(f"linkfix {stem}: {broken} -> {op.get('new_target') or 'unlinked'}")

    created = 0
    for op in plan.get("new_pages", []):
        if created >= config.gardener.max_new_pages_per_run:
            break
        ptype = op.get("type", "concept")
        if ptype not in VALID_TYPES or ptype in ("index", "log"):
            continue
        title = op.get("title", "").strip()
        if not title:
            continue
        stem = slugify(title)
        subdir = {"entity": "entities", "concept": "concepts", "client": "clients"}.get(ptype, "concepts")
        path = vault.config.wiki_dir / subdir / f"{stem}.md"
        if path.exists():
            continue
        import frontmatter as fm
        meta = {
            "type": ptype,
            "title": title,
            "description": op.get("description", ""),
            "tags": op.get("tags") or ["personal"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "created_by": "gardener",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fm.dumps(fm.Post(op.get("body", ""), **meta)) + "\n", encoding="utf-8")
        created += 1
        applied.append(f"page+ {subdir}/{stem}: {op.get('reason','')}")
    return applied


def _git_commit(vault: Vault, message: str) -> None:
    cwd = vault.config.vault_path
    subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=cwd, capture_output=True)


def run_curator(vault: Vault, report: LintReport, config: Config) -> list[str]:
    prompt = _build_prompt(vault, report)
    plan = _model_call(config, prompt)

    if config.gardener.dry_run:
        print("[dry-run] curation plan:")
        print(json.dumps(plan, indent=2))
        return []

    applied = apply_curation_plan(vault, plan, config)
    today = datetime.now(timezone.utc).date().isoformat()
    for line in applied:
        vault.append_log(f"{today} [gardener/curator] {line}")
    if applied:
        from brain.gardener.mechanical import rebuild_index
        from brain.search import keyword
        rebuild_index(vault)
        keyword.index(vault)
        if config.gardener.git_commit:
            _git_commit(vault, f"gardener: {len(applied)} curation op(s) on {today}")
    return applied
