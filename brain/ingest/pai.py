"""PAI (Personal AI Infrastructure) memory adapter (pull-based).

PAI accumulates knowledge as markdown and JSONL under its install root. This
module PULLS selected trees on the brain's schedule — PAI never pushes.
Manifest content-hash dedup gives living documents natural update semantics:
when a file changes, it restages as an update for the integrator to fold in.

Markdown is staged VERBATIM (the original file text — frontmatter is parsed
only to read `phase`/`title`, never re-serialized, so hashes stay stable
across library versions). A malformed file is a stderr warning + skip, never
a run-killer: one bad file must not block acquisition of the rest.

What gets staged (defaults, all configurable via `sources` globs):
  MEMORY/WORK/*/ISA.md                 — task articulations; ONLY completed
                                         ones (in-progress work is noise;
                                         finished work carries the decisions)
  MEMORY/LEARNING/REFLECTIONS/*.jsonl  — one item per record, keyed by
                                         content hash (stable under edits)
  MEMORY/KNOWLEDGE/**/*.md             — typed knowledge notes
  USER/TELOS/*.md                      — mission/goals/beliefs (living docs)
  USER/PROJECTS/PROJECTS.md            — project state snapshots

Config ([ingest.pai] in brain.toml):
  enabled  = true|false
  pai_root = "~/.claude/PAI"
  sources  = [globs relative to pai_root]
  exclude  = ["**/README.md", "README.md"]   # scaffolding, not knowledge
"""
from __future__ import annotations

import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Iterator

import frontmatter

from brain.ingest.base import RawItem, SourceModule
from brain.ingest.manifest import sha256

DEFAULT_SOURCES = [
    "MEMORY/WORK/*/ISA.md",
    "MEMORY/LEARNING/REFLECTIONS/*.jsonl",
    "MEMORY/KNOWLEDGE/**/*.md",
    "USER/TELOS/*.md",
    "USER/PROJECTS/PROJECTS.md",
]
DEFAULT_EXCLUDE = ["**/README.md", "README.md"]


def _jsonl_to_markdown(obj: dict) -> str:
    """Render one JSONL record as readable markdown, not a raw JSON dump."""
    lines = []
    for key, value in obj.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


class PAIModule(SourceModule):
    name = "pai"

    def fetch(self) -> Iterator[RawItem]:
        if not self.settings.get("enabled"):
            return
        root = Path(self.settings.get("pai_root", "~/.claude/PAI")).expanduser()
        if not root.is_dir():
            print(f"pai: pai_root not found ({root}) — skipping.", file=sys.stderr)
            return
        sources = self.settings.get("sources", DEFAULT_SOURCES)
        if isinstance(sources, str):
            sources = [sources]
        exclude = self.settings.get("exclude", DEFAULT_EXCLUDE)
        if isinstance(exclude, str):
            exclude = [exclude]
        for pattern in sources:
            for f in sorted(root.glob(pattern)):
                if not f.is_file():
                    continue
                try:
                    rel = str(f.relative_to(root))
                    text = f.read_text(encoding="utf-8")
                except (ValueError, UnicodeDecodeError) as e:
                    print(f"pai: skipping {f} ({e})", file=sys.stderr)
                    continue
                if any(fnmatchcase(rel, pat) for pat in exclude):
                    continue
                if f.suffix == ".jsonl":
                    yield from self._from_jsonl(text, rel, f.stem)
                elif f.suffix == ".md":
                    item = self._from_markdown(text, rel, f.stem)
                    if item:
                        yield item

    def _from_markdown(self, text: str, rel: str, stem: str) -> RawItem | None:
        phase = title = None
        try:
            post = frontmatter.loads(text)
            phase = post.get("phase")
            title = post.get("title") or post.get("task")
        except Exception as e:
            print(f"pai: {rel}: frontmatter parse error ({e})", file=sys.stderr)
            if rel.startswith("MEMORY/WORK/"):
                # Can't verify phase: complete — skip rather than leak in-flight work.
                return None
        # Work ISAs: only completed articulations are knowledge; skip in-flight.
        if rel.startswith("MEMORY/WORK/") and phase != "complete":
            return None
        section = "/".join(Path(rel).parts[:2])
        return RawItem(
            origin_id=rel,
            title=str(title or stem),
            content=text,
            extra_meta={"pai_section": section, "pai_path": rel},
        )

    def _from_jsonl(self, text: str, rel: str, stem: str) -> Iterator[RawItem]:
        section = "/".join(Path(rel).parts[:2])
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Content-hash key: stable when other lines are edited, moved,
            # or compacted; identical records intentionally collapse to one.
            key = sha256(line.encode("utf-8"))[:12]
            title = str(obj.get("task_description") or obj.get("title") or f"{stem}-{key}")
            yield RawItem(
                origin_id=f"{rel}#{key}",
                title=title,
                content=_jsonl_to_markdown(obj),
                extra_meta={"pai_section": section, "pai_path": rel},
            )
