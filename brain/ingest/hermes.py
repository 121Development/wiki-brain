"""Hermes agent memory adapter (skeleton).

Assumes Hermes memory is a directory of markdown/JSON files; point
[ingest.hermes].memory_path at it in brain.toml. Adjust _parse() to match
your actual Hermes memory schema when you wire it up.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from brain.ingest.base import RawItem, SourceModule


class HermesModule(SourceModule):
    name = "hermes"

    def fetch(self) -> Iterator[RawItem]:
        if not self.settings.get("enabled"):
            return
        root = Path(self.settings.get("memory_path", "~/.hermes/memory")).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"hermes memory_path not found: {root}")
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix == ".md":
                yield RawItem(
                    origin_id=str(f.relative_to(root)),
                    title=f.stem,
                    content=f.read_text(encoding="utf-8", errors="replace"),
                )
            elif f.suffix in (".json", ".jsonl"):
                yield from self._parse_json(f, root)

    def _parse_json(self, f: Path, root: Path) -> Iterator[RawItem]:
        lines = f.read_text(encoding="utf-8").splitlines() if f.suffix == ".jsonl" \
            else [f.read_text(encoding="utf-8")]
        for i, line in enumerate(l for l in lines if l.strip()):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            body = obj.get("content") or obj.get("memory") or json.dumps(obj, indent=2)
            yield RawItem(
                origin_id=f"{f.relative_to(root)}#{i}",
                title=obj.get("title") or f"{f.stem}-{i}",
                content=str(body),
                extra_meta={"hermes_keys": sorted(obj.keys())},
            )
