"""Source module contract.

A module is dumb and safe: fetch() yields normalized items; the runner writes
them to raw/<module.name>/ with provenance frontmatter, updates the manifest,
and never touches wiki/. Wiki integration is the agent's job (phase 2).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

import frontmatter

from brain.config import Config
from brain.ingest.manifest import Manifest, sha256


@dataclass
class RawItem:
    origin_id: str          # stable ID within the source (path, message id, url…)
    title: str
    content: str            # normalized markdown body
    extra_meta: dict | None = None


def slugify(text: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "untitled"


class SourceModule(ABC):
    name: str = "base"

    def __init__(self, config: Config):
        self.config = config
        self.settings = config.ingest.get(self.name, {})

    @abstractmethod
    def fetch(self) -> Iterator[RawItem]:
        """Yield items from the source. Must be side-effect free."""

    def stage(self, manifest: Manifest) -> tuple[int, int]:
        """Write new/changed items to raw/<name>/. Returns (staged, skipped)."""
        target = self.config.raw_dir / self.name
        target.mkdir(parents=True, exist_ok=True)
        staged = skipped = 0
        for item in self.fetch():
            content_hash = sha256(item.content.encode("utf-8"))
            if manifest.seen(self.name, item.origin_id, content_hash):
                skipped += 1
                continue
            meta = {
                "title": item.title,
                "origin": self.name,
                "origin_id": item.origin_id,
                "content_hash": content_hash,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                **(item.extra_meta or {}),
            }
            path = target / f"{slugify(item.title)}-{content_hash[:8]}.md"
            path.write_text(
                frontmatter.dumps(frontmatter.Post(item.content, **meta)) + "\n",
                encoding="utf-8",
            )
            manifest.record(self.name, item.origin_id, path, content_hash)
            staged += 1
        manifest.save()
        return staged, skipped
