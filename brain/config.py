"""Configuration loading. Resolution order:
1. $BRAIN_CONFIG
2. ./brain.toml
3. ~/.config/brain/brain.toml
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GardenerConfig:
    llm_enabled: bool = False
    # Model tiering: cheap tier for routine curation, premium for synthesis.
    curator_model: str = "claude-haiku-4-5-20251001"
    synthesis_model: str = "claude-sonnet-4-6"
    synthesis_enabled: bool = False
    max_pages_edited_per_run: int = 10
    max_new_pages_per_run: int = 3
    dry_run: bool = True
    git_commit: bool = True


@dataclass
class IntegratorConfig:
    model: str = "claude-sonnet-4-6"
    max_items_per_run: int = 10
    max_pages_per_item: int = 6
    dry_run: bool = True
    git_commit: bool = True


@dataclass
class Config:
    vault_path: Path
    search_backend: str = "keyword"
    qmd_command: str = "qmd"
    ingest: dict = field(default_factory=dict)
    gardener: GardenerConfig = field(default_factory=GardenerConfig)
    integrator: IntegratorConfig = field(default_factory=IntegratorConfig)

    @property
    def raw_dir(self) -> Path:
        return self.vault_path / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.vault_path / "wiki"

    @property
    def state_dir(self) -> Path:
        d = self.vault_path / ".brain"
        d.mkdir(exist_ok=True)
        return d

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"

    @property
    def search_db_path(self) -> Path:
        return self.state_dir / "search.db"


def _candidates() -> list[Path]:
    out = []
    if env := os.environ.get("BRAIN_CONFIG"):
        out.append(Path(env))
    out.append(Path.cwd() / "brain.toml")
    out.append(Path.home() / ".config" / "brain" / "brain.toml")
    return out


def load_config() -> Config:
    for path in _candidates():
        if path.is_file():
            data = tomllib.loads(path.read_text())
            vault = Path(os.path.expanduser(data["vault"]["path"])).resolve()
            search = data.get("search", {})
            g = data.get("gardener", {})
            i = data.get("integrator", {})
            return Config(
                vault_path=vault,
                search_backend=search.get("backend", "keyword"),
                qmd_command=search.get("qmd_command", "qmd"),
                ingest=data.get("ingest", {}),
                gardener=GardenerConfig(
                    llm_enabled=g.get("llm_enabled", False),
                    curator_model=g.get("curator_model", "claude-haiku-4-5-20251001"),
                    synthesis_model=g.get("synthesis_model", "claude-sonnet-4-6"),
                    synthesis_enabled=g.get("synthesis_enabled", False),
                    max_pages_edited_per_run=g.get("max_pages_edited_per_run", 10),
                    max_new_pages_per_run=g.get("max_new_pages_per_run", 3),
                    dry_run=g.get("dry_run", True),
                    git_commit=g.get("git_commit", True),
                ),
                integrator=IntegratorConfig(
                    model=i.get("model", "claude-sonnet-4-6"),
                    max_items_per_run=i.get("max_items_per_run", 10),
                    max_pages_per_item=i.get("max_pages_per_item", 6),
                    dry_run=i.get("dry_run", True),
                    git_commit=i.get("git_commit", True),
                ),
            )
    raise FileNotFoundError(
        "No brain.toml found. Copy brain.toml.example to brain.toml and set vault.path."
    )
