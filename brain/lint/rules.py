"""Mechanical lint rules. Deterministic, free, cron-safe.

Semantic checks (contradictions, coverage gaps) are NOT here — they belong to
the gardener's LLM pass, which consumes this report as input.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from brain.core.frontmatter import validate_page
from brain.core.graph import build_graph
from brain.core.vault import Vault

STALE_DAYS = 180


@dataclass
class LintReport:
    schema_errors: dict[str, list[str]] = field(default_factory=dict)
    broken_links: dict[str, list[str]] = field(default_factory=dict)
    orphans: list[str] = field(default_factory=list)
    duplicate_titles: dict[str, list[str]] = field(default_factory=dict)
    stale_pages: list[str] = field(default_factory=list)
    expired_pages: list[str] = field(default_factory=list)
    index_missing: list[str] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return (
            sum(len(v) for v in self.schema_errors.values())
            + sum(len(v) for v in self.broken_links.values())
            + len(self.orphans)
            + len(self.duplicate_titles)
            + len(self.stale_pages)
            + len(self.expired_pages)
            + len(self.index_missing)
        )

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, default=str)

    def to_text(self) -> str:
        lines = [f"lint: {self.total_issues} issue(s)"]
        for name, mapping in [
            ("schema", self.schema_errors),
            ("broken links", self.broken_links),
            ("duplicate titles", self.duplicate_titles),
        ]:
            for k, v in mapping.items():
                for item in v:
                    lines.append(f"  [{name}] {k}: {item}")
        for o in self.orphans:
            lines.append(f"  [orphan] {o} has no backlinks")
        for s in self.stale_pages:
            lines.append(f"  [stale] {s} not updated in {STALE_DAYS}+ days")
        for e in self.expired_pages:
            lines.append(f"  [expired] {e} passed its 'expires' date — verify or supersede")
        for m in self.index_missing:
            lines.append(f"  [index] {m} not referenced in index.md")
        return "\n".join(lines)


def run_lint(vault: Vault) -> LintReport:
    report = LintReport()
    pages = vault.wiki_pages()
    rel = lambda p: str(p.path.relative_to(vault.config.wiki_dir))  # noqa: E731

    # 1. Frontmatter schema
    for page in pages:
        if page.path.stem in ("index", "log"):
            continue
        problems = validate_page(page, require_domain_tag=True)
        if problems:
            report.schema_errors[rel(page)] = problems

    # 2. Broken wikilinks + orphans
    graph = build_graph(vault)
    report.broken_links = {k: sorted(v) for k, v in graph.broken_links().items()}
    report.orphans = sorted(graph.orphans())

    # 3. Duplicate titles
    seen: dict[str, list[str]] = {}
    for page in pages:
        title = page.title.strip().lower()
        seen.setdefault(title, []).append(rel(page))
    report.duplicate_titles = {t: ps for t, ps in seen.items() if len(ps) > 1}

    # 4. Staleness (frontmatter timestamp)
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    for page in pages:
        if page.metadata.get("status") == "archived":
            continue  # archived pages are expected to be old
        ts = page.metadata.get("timestamp")
        if ts is None:
            continue
        try:
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < cutoff:
                report.stale_pages.append(rel(page))
        except ValueError:
            pass

    # 5. Expiry: pages whose 'expires' frontmatter date has passed.
    #    Use for time-limited intelligence (pricing, competitor info, tactics).
    now = datetime.now(timezone.utc)
    for page in pages:
        exp = page.metadata.get("expires")
        if exp is None:
            continue
        try:
            dt = datetime.fromisoformat(str(exp))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < now:
                report.expired_pages.append(rel(page))
        except ValueError:
            report.schema_errors.setdefault(rel(page), []).append(
                f"unparseable 'expires' value: {exp!r}"
            )

    # 6. Index drift: pages not mentioned in index.md
    index_path = vault.config.wiki_dir / "index.md"
    if index_path.is_file():
        index_text = index_path.read_text(encoding="utf-8").lower()
        for page in pages:
            stem = page.path.stem.lower()
            if stem in ("index", "log"):
                continue
            if stem not in index_text:
                report.index_missing.append(rel(page))

    return report
