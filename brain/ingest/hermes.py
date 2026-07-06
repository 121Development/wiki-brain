"""Hermes agent memory adapter (pull-based).

Hermes keeps its memory as a handful of living markdown files (identity/soul,
agent notes, user profile). This module PULLS them on the brain's schedule —
Hermes never pushes. Each `##` section becomes one raw item with a stable
origin_id, so when a single section changes only that section restages
(manifest content-hash dedup handles the rest). Content is staged VERBATIM —
never paraphrase source memory; distilling is the integrator's job (phase 2).

Config ([ingest.hermes] in brain.toml):
  enabled     = true|false
  memory_path = "~/.hermes"                    # root of the Hermes memory tree
  sources     = ["SOUL.md", "memories/*.md"]   # globs relative to memory_path

If Hermes lives on another machine, sync the tree here first (e.g. rsync in
the same cron that runs `brain ingest hermes`). A missing memory_path is a
warning + zero items, not an error, so cron chains don't break while the
sync isn't set up yet.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterator

from brain.ingest.base import RawItem, SourceModule, slugify

DEFAULT_SOURCES = ["SOUL.md", "memories/*.md"]

_HEADING = re.compile(r"^##(?!#)\s+(.*)$")


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, verbatim-body) per `##` section.

    Content before the first `##` is returned under the heading "intro".
    A file with no `##` headings returns a single ("", whole-text) entry.
    """
    lines = text.splitlines()
    if not any(_HEADING.match(line) for line in lines):
        return [("", text)]
    sections: list[tuple[str, list[str]]] = [("intro", [])]
    for line in lines:
        m = _HEADING.match(line)
        if m:
            sections.append((m.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    return [
        (heading, "\n".join(body).strip())
        for heading, body in sections
        if "\n".join(body).strip()
    ]


def _dedupe_slugs(headings: list[str]) -> list[str]:
    """Slugify headings; disambiguate duplicates with a counter suffix.

    Heading-keyed (not positional) so inserting or reordering sections never
    shifts the ids of unrelated sections; only true duplicates get suffixes.
    """
    counts: dict[str, int] = {}
    out = []
    for h in headings:
        slug = slugify(h) if h else "full"
        counts[slug] = counts.get(slug, 0) + 1
        out.append(slug if counts[slug] == 1 else f"{slug}-{counts[slug]}")
    return out


class HermesModule(SourceModule):
    name = "hermes"

    def fetch(self) -> Iterator[RawItem]:
        if not self.settings.get("enabled"):
            return
        root = Path(self.settings.get("memory_path", "~/.hermes")).expanduser()
        if not root.is_dir():
            print(
                f"hermes: memory_path not found ({root}) — skipping. "
                "Sync the Hermes memory tree to this machine (e.g. rsync) "
                "or set [ingest.hermes].memory_path.",
                file=sys.stderr,
            )
            return
        sources = self.settings.get("sources", DEFAULT_SOURCES)
        if isinstance(sources, str):
            sources = [sources]
        for pattern in sources:
            for f in sorted(root.glob(pattern)):
                if not f.is_file() or f.suffix != ".md":
                    continue
                try:
                    rel = str(f.relative_to(root))
                    text = f.read_text(encoding="utf-8")
                except (ValueError, UnicodeDecodeError) as e:
                    print(f"hermes: skipping {f} ({e})", file=sys.stderr)
                    continue
                parts = split_sections(text)
                slugs = _dedupe_slugs([h for h, _ in parts])
                for (heading, body), slug in zip(parts, slugs):
                    yield RawItem(
                        origin_id=f"{rel}#{slug}",
                        title=f"{f.stem}: {heading}" if heading else f.stem,
                        content=body,
                        extra_meta={"hermes_file": rel, "hermes_section": heading},
                    )
