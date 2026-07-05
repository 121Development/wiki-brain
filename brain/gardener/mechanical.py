"""Gardener phase A: deterministic, free fixes applied on every run.

Safe auto-fixes only:
- rebuild the auto-generated section of index.md (fixes index drift)
- rebuild the search index
Everything requiring judgment (fixing broken links, creating pages, injecting
connections) is queued for the curator (phase B).
"""
from __future__ import annotations

from datetime import datetime, timezone

from brain.core.vault import Vault
from brain.lint.rules import LintReport, run_lint
from brain.search import keyword

AUTO_BEGIN = "<!-- BEGIN AUTO-INDEX -->"
AUTO_END = "<!-- END AUTO-INDEX -->"


def rebuild_index(vault: Vault) -> int:
    """Regenerate the auto section of index.md: one line per page, grouped by type."""
    pages = [p for p in vault.wiki_pages() if p.path.stem not in ("index", "log")]
    by_type: dict[str, list] = {}
    for p in pages:
        by_type.setdefault(p.type or "untyped", []).append(p)

    lines = [AUTO_BEGIN]
    for ptype in sorted(by_type):
        lines.append(f"\n## {ptype}\n")
        for p in sorted(by_type[ptype], key=lambda x: x.title.lower()):
            rel = p.path.relative_to(vault.config.wiki_dir)
            desc = p.metadata.get("description", "")
            lines.append(f"- [[{p.path.stem}]] — {desc}  `({rel})`")
    lines.append(f"\n{AUTO_END}")
    block = "\n".join(lines)

    index_path = vault.config.wiki_dir / "index.md"
    text = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    if AUTO_BEGIN in text and AUTO_END in text:
        pre = text.split(AUTO_BEGIN)[0]
        post = text.split(AUTO_END)[1]
        index_path.write_text(pre + block + post, encoding="utf-8")
    else:
        index_path.write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    return len(pages)


def run_mechanical(vault: Vault) -> LintReport:
    """Run auto-fixes, reindex search, return fresh lint report for the curator."""
    n = rebuild_index(vault)
    keyword.index(vault)
    report = run_lint(vault)
    today = datetime.now(timezone.utc).date().isoformat()
    vault.append_log(
        f"{today} [gardener/mechanical] reindexed {n} pages; "
        f"{report.total_issues} issue(s) remain for curation"
    )
    return report
