"""PAI (Personal AI Infrastructure, Daniel Miessler) memory adapter (skeleton).

PAI keeps memory as a hierarchy of markdown files (telos, projects, learnings,
context). Point [ingest.pai].pai_path at the memory root. Each markdown file
becomes one raw item; directory structure is preserved in origin_id and tagged
in extra_meta so the integrating agent can map PAI sections to wiki domains.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from brain.ingest.base import RawItem, SourceModule


class PAIModule(SourceModule):
    name = "pai"

    def fetch(self) -> Iterator[RawItem]:
        if not self.settings.get("enabled"):
            return
        root = Path(self.settings.get("pai_path", "~/pai/memory")).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"pai_path not found: {root}")
        for f in sorted(root.rglob("*.md")):
            rel = f.relative_to(root)
            section = rel.parts[0] if len(rel.parts) > 1 else "root"
            yield RawItem(
                origin_id=str(rel),
                title=f.stem,
                content=f.read_text(encoding="utf-8", errors="replace"),
                extra_meta={"pai_section": section},
            )
