"""Vault access: iterate wiki pages, resolve wikilink targets, read raw files."""
from __future__ import annotations

from pathlib import Path

from brain.config import Config
from brain.core.frontmatter import Page, load_page


class Vault:
    def __init__(self, config: Config):
        self.config = config
        if not config.wiki_dir.is_dir():
            raise FileNotFoundError(f"wiki dir not found: {config.wiki_dir}")

    def wiki_pages(self) -> list[Page]:
        pages = []
        for p in sorted(self.config.wiki_dir.rglob("*.md")):
            try:
                pages.append(load_page(p))
            except Exception as e:  # noqa: BLE001 - lint reports parse failures
                pages.append(Page(path=p, metadata={}, content=f"<<PARSE ERROR: {e}>>"))
        return pages

    def raw_files(self) -> list[Path]:
        if not self.config.raw_dir.is_dir():
            return []
        return sorted(
            p for p in self.config.raw_dir.rglob("*")
            if p.is_file() and p.name != ".gitkeep"
        )

    def page_name_index(self) -> dict[str, Path]:
        """Map lowercase page stem -> path, for wikilink resolution."""
        idx: dict[str, Path] = {}
        for p in self.config.wiki_dir.rglob("*.md"):
            idx[p.stem.lower()] = p
        return idx

    def resolve_wikilink(self, target: str) -> Path | None:
        return self.page_name_index().get(target.strip().lower())

    def get_page(self, name_or_path: str) -> Page | None:
        p = Path(name_or_path)
        if p.is_file():
            return load_page(p)
        candidate = self.config.wiki_dir / name_or_path
        if candidate.is_file():
            return load_page(candidate)
        resolved = self.resolve_wikilink(Path(name_or_path).stem)
        return load_page(resolved) if resolved else None

    def append_log(self, line: str) -> None:
        """Append a single line to wiki/log.md (greppable format: YYYY-MM-DD [actor] msg)."""
        log = self.config.wiki_dir / "log.md"
        with log.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
