"""Link graph over wiki pages: outlinks, backlinks, orphans, broken links."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from brain.core.vault import Vault


@dataclass
class LinkGraph:
    # page stem (lowercase) -> set of target stems it links to
    outlinks: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    backlinks: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    known: set[str] = field(default_factory=set)

    def broken_links(self) -> dict[str, set[str]]:
        """page -> targets that don't resolve to any wiki page."""
        out = {}
        for page, targets in self.outlinks.items():
            missing = {t for t in targets if t not in self.known}
            if missing:
                out[page] = missing
        return out

    def orphans(self) -> set[str]:
        """Pages with no backlinks (excluding index/log)."""
        skip = {"index", "log"}
        return {
            p for p in self.known
            if p not in skip and not self.backlinks.get(p)
        }

    def neighbors(self, stem: str) -> dict[str, list[str]]:
        s = stem.lower()
        return {
            "outlinks": sorted(self.outlinks.get(s, set())),
            "backlinks": sorted(self.backlinks.get(s, set())),
        }


def build_graph(vault: Vault) -> LinkGraph:
    g = LinkGraph()
    pages = vault.wiki_pages()
    g.known = {p.path.stem.lower() for p in pages}
    for page in pages:
        src = page.path.stem.lower()
        for target in page.wikilinks():
            tgt = target.lower()
            g.outlinks[src].add(tgt)
            g.backlinks[tgt].add(src)
    return g
