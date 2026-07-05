"""Thin wrapper around the qmd CLI (BM25 + vector + reranking).

qmd ships its own CLI and MCP server; this wrapper exists so `brain search`
has a single entrypoint regardless of backend, and so the backend can be
swapped later for a custom embedding pipeline without touching callers.
"""
from __future__ import annotations

import shutil
import subprocess

from brain.core.vault import Vault
from brain.search.keyword import Hit


def available(command: str = "qmd") -> bool:
    return shutil.which(command) is not None


def ensure_collection(vault: Vault) -> None:
    """Register the wiki dir as a qmd collection named 'brain' (idempotent)."""
    cmd = vault.config.qmd_command
    subprocess.run(
        [cmd, "collection", "add", str(vault.config.wiki_dir), "--name", "brain"],
        capture_output=True, text=True, check=False,
    )


def search(vault: Vault, query: str, limit: int = 10, mode: str = "search") -> list[Hit]:
    """mode: 'search' (BM25), 'vsearch' (vector), 'query' (hybrid+rerank)."""
    cmd = vault.config.qmd_command
    if not available(cmd):
        raise RuntimeError(
            f"'{cmd}' not found on PATH. Install qmd (bun install -g "
            f"https://github.com/tobi/qmd) or set search.backend='keyword'."
        )
    ensure_collection(vault)
    proc = subprocess.run(
        [cmd, mode, query, "-c", "brain", "-n", str(limit)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"qmd failed: {proc.stderr.strip()}")
    hits = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            hits.append(Hit(path=line, title=line, snippet="", score=0.0))
    return hits
