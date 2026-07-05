"""Sessions module: stage coding/agent session transcripts into raw/sessions/.

Designed for the Claude Code SessionEnd hook (see hooks/session_end.sh), but
works with any JSONL transcript or plain markdown session note.

Usage:
  brain ingest sessions <transcript.jsonl | note.md> [...]

Phase 1 only: this stages a readable digest of the session (user prompts and
assistant text, truncated). Mining it for decisions/mistakes/patterns is the
integration agent's job (phase 2), per AGENTS.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from brain.ingest.base import RawItem, SourceModule

MAX_TURN_CHARS = 2000
MAX_DIGEST_CHARS = 60_000


class SessionsModule(SourceModule):
    name = "sessions"

    def __init__(self, config, targets: list[str] | None = None):
        super().__init__(config)
        self.targets = targets or []

    def fetch(self) -> Iterator[RawItem]:
        for target in self.targets:
            path = Path(target).expanduser()
            if not path.is_file():
                raise FileNotFoundError(path)
            if path.suffix == ".jsonl":
                yield self._from_jsonl(path)
            else:
                yield RawItem(
                    origin_id=str(path.resolve()),
                    title=f"session-{path.stem}",
                    content=path.read_text(encoding="utf-8", errors="replace"),
                    extra_meta={"session_source": str(path.resolve())},
                )

    def _from_jsonl(self, path: Path) -> RawItem:
        """Digest a Claude Code transcript: user prompts + assistant text only."""
        turns: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            role, text = self._extract_turn(obj)
            if text:
                text = text[:MAX_TURN_CHARS]
                turns.append(f"**{role}:** {text}")
        date = datetime.now(timezone.utc).date().isoformat()
        digest = "\n\n".join(turns)[:MAX_DIGEST_CHARS] or "(empty transcript)"
        return RawItem(
            origin_id=str(path.resolve()),
            title=f"session-{date}-{path.stem[:12]}",
            content=digest,
            extra_meta={"session_source": str(path.resolve()), "turns": len(turns)},
        )

    @staticmethod
    def _extract_turn(obj: dict) -> tuple[str, str]:
        msg = obj.get("message", obj)
        role = msg.get("role") or obj.get("type") or "unknown"
        content = msg.get("content")
        if isinstance(content, str):
            return role, content.strip()
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            return role, "\n".join(p for p in parts if p).strip()
        return role, ""
