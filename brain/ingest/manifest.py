"""Manifest of staged raw files.

Each entry: {origin, origin_id, path, content_hash, retrieved_at, integrated}
The `integrated` flag is the handoff point between deterministic acquisition
(phase 1, this code) and LLM integration (phase 2, agent-driven).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from brain.config import Config


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Manifest:
    def __init__(self, config: Config):
        self.path = config.manifest_path
        self.entries: dict[str, dict] = {}
        if self.path.exists():
            self.entries = json.loads(self.path.read_text())

    def save(self) -> None:
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True))

    def key(self, origin: str, origin_id: str) -> str:
        return f"{origin}:{origin_id}"

    def seen(self, origin: str, origin_id: str, content_hash: str) -> bool:
        """True if this exact content was already staged (skip re-ingest)."""
        entry = self.entries.get(self.key(origin, origin_id))
        return bool(entry and entry["content_hash"] == content_hash)

    def record(self, origin: str, origin_id: str, path: Path, content_hash: str) -> None:
        self.entries[self.key(origin, origin_id)] = {
            "origin": origin,
            "origin_id": origin_id,
            "path": str(path),
            "content_hash": content_hash,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "integrated": False,
        }

    def mark_integrated(self, key: str) -> bool:
        if key in self.entries:
            self.entries[key]["integrated"] = True
            return True
        return False

    def unintegrated(self) -> list[dict]:
        return [e for e in self.entries.values() if not e.get("integrated")]
