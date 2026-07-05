"""Manual module: stage local files/dirs or a URL into raw/manual/.

Usage: brain ingest manual <path-or-url> [...]
"""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Iterator

from brain.ingest.base import RawItem, SourceModule

TEXT_EXTS = {".md", ".txt", ".markdown", ".org", ".rst", ".csv", ".json", ".yaml", ".yml"}


class ManualModule(SourceModule):
    name = "manual"

    def __init__(self, config, targets: list[str] | None = None):
        super().__init__(config)
        self.targets = targets or []

    def fetch(self) -> Iterator[RawItem]:
        for target in self.targets:
            if target.startswith(("http://", "https://")):
                yield self._fetch_url(target)
            else:
                yield from self._fetch_path(Path(target).expanduser())

    def _fetch_url(self, url: str) -> RawItem:
        req = urllib.request.Request(url, headers={"User-Agent": "brain-tools/0.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return RawItem(
            origin_id=url,
            title=url.rstrip("/").rsplit("/", 1)[-1] or url,
            content=body,
            extra_meta={"source_url": url},
        )

    def _fetch_path(self, path: Path) -> Iterator[RawItem]:
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in TEXT_EXTS)
        elif path.is_file():
            files = [path]
        else:
            raise FileNotFoundError(path)
        for f in files:
            yield RawItem(
                origin_id=str(f.resolve()),
                title=f.stem,
                content=f.read_text(encoding="utf-8", errors="replace"),
                extra_meta={"source_path": str(f.resolve())},
            )
