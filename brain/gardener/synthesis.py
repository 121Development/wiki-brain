"""Gardener phase C: weekly synthesis on the premium model.

Different job from the curator: instead of many small structural ops, one
whole-vault read producing a single dated page — what changed, what's
drifting, contradictions, expiring intelligence, and what deserves attention
next. This is the only pass that justifies premium-model cost, so it runs on
gardener.synthesis_model (curation runs on the cheap tier).

Guardrails: writes exactly one new page (syntheses/weekly-YYYY-WW.md),
never edits existing pages, logs, and git-commits like every gardener run.
"""
from __future__ import annotations

from datetime import datetime, timezone

import frontmatter as fm

from brain.config import Config
from brain.core.vault import Vault
from brain.lint.rules import LintReport

SYSTEM_PROMPT = """You are the weekly reviewer of a personal + business \
knowledge wiki. You receive the recent activity log, the lint report, and \
page summaries. Write a concise weekly synthesis in markdown covering: \
1) what changed and why it matters, 2) emerging themes or connections across \
pages, 3) contradictions or drift you noticed, 4) expiring or stale \
intelligence to re-verify, 5) 3-5 concrete suggestions for what to add or \
investigate next. Be specific, reference pages with [[wikilinks]], and be \
brief — under 600 words. Output only the markdown body, no frontmatter."""


def _tail(path, lines: int = 60) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8").splitlines()[-lines:])


def _build_prompt(vault: Vault, report: LintReport) -> str:
    pages = [p for p in vault.wiki_pages() if p.path.stem not in ("index", "log")]
    summaries = [
        f"- [[{p.path.stem}]] (type={p.type}, tags={p.tags}, "
        f"updated={p.metadata.get('timestamp','?')}): "
        f"{p.metadata.get('summary') or p.metadata.get('description','')}"
        for p in pages
    ]
    return "\n".join([
        "RECENT LOG (last 60 lines of wiki/log.md):",
        _tail(vault.config.wiki_dir / "log.md"),
        "\nLINT REPORT:",
        report.to_text(),
        f"\nALL PAGES ({len(pages)}):",
        *summaries,
        "\nWrite this week's synthesis.",
    ])


def run_synthesis(vault: Vault, report: LintReport, config: Config) -> str | None:
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    stem = f"weekly-{year}-{week:02d}"
    path = vault.config.wiki_dir / "syntheses" / f"{stem}.md"
    if path.exists():
        return None  # already synthesized this week

    import anthropic  # optional dep: pip install 'brain-tools[gardener]'

    from brain.core.playbooks import load_system_prompt

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.gardener.synthesis_model,
        max_tokens=2000,
        system=load_system_prompt(config, "synthesize", SYSTEM_PROMPT),
        messages=[{"role": "user", "content": _build_prompt(vault, report)}],
    )
    body = "".join(b.text for b in resp.content if b.type == "text").strip()

    if config.gardener.dry_run:
        print(f"[dry-run] would write syntheses/{stem}.md:\n{body}")
        return None

    meta = {
        "type": "synthesis",
        "title": f"Weekly Synthesis {year}-W{week:02d}",
        "description": f"Automated weekly review for ISO week {year}-W{week:02d}",
        "tags": ["personal", "business"],
        "timestamp": now.isoformat(),
        "created_by": "gardener",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dumps(fm.Post(body, **meta)) + "\n", encoding="utf-8")
    vault.append_log(f"{now.date().isoformat()} [gardener/synthesis] wrote syntheses/{stem}.md")
    if config.gardener.git_commit:
        from brain.gardener.curator import _git_commit
        _git_commit(vault, f"gardener: weekly synthesis {year}-W{week:02d}")
    return str(path.relative_to(vault.config.wiki_dir))
