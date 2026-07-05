"""OKF-style page model: YAML frontmatter + markdown body.

Required frontmatter key: type. Recommended: title, description, tags, timestamp.
Wiki pages additionally carry: sources (list of raw/ paths this page derives from).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MDLINK_RE = re.compile(r"\[[^\]]*\]\((?!https?://|mailto:)([^)#\s]+)")

VALID_TYPES = {"entity", "concept", "source", "synthesis", "client", "index", "log", "moc"}
# Optional lifecycle fields (PARA-derived, frontmatter not folders):
STATUS_VALUES = {"active", "dormant", "archived"}   # page lifecycle
KIND_VALUES = {"area", "project"}                    # business actionability


@dataclass
class Page:
    path: Path
    metadata: dict
    content: str

    @property
    def type(self) -> str | None:
        return self.metadata.get("type")

    @property
    def title(self) -> str:
        return self.metadata.get("title") or self.path.stem.replace("-", " ").title()

    @property
    def tags(self) -> list[str]:
        t = self.metadata.get("tags") or []
        return t if isinstance(t, list) else [t]

    def wikilinks(self) -> list[str]:
        """Targets of [[wikilinks]] in the body (page names, no extension)."""
        return [m.strip() for m in WIKILINK_RE.findall(self.content)]

    def relative_md_links(self) -> list[str]:
        """Relative markdown link targets (non-http)."""
        return MDLINK_RE.findall(self.content)

    def dump(self) -> str:
        post = frontmatter.Post(self.content, **self.metadata)
        return frontmatter.dumps(post) + "\n"

    def save(self) -> None:
        self.path.write_text(self.dump(), encoding="utf-8")


def load_page(path: Path) -> Page:
    post = frontmatter.load(path)
    return Page(path=path, metadata=dict(post.metadata), content=post.content)


def validate_page(page: Page, require_domain_tag: bool = False) -> list[str]:
    """Mechanical schema checks. Returns list of problems (empty = valid)."""
    problems = []
    if not page.metadata:
        problems.append("missing frontmatter entirely")
        return problems
    if not page.type:
        problems.append("missing required 'type' in frontmatter")
    elif page.type not in VALID_TYPES:
        problems.append(f"unknown type '{page.type}' (expected one of {sorted(VALID_TYPES)})")
    if not page.metadata.get("title"):
        problems.append("missing 'title'")
    if not page.metadata.get("description"):
        problems.append("missing 'description'")
    if not page.metadata.get("timestamp"):
        problems.append("missing 'timestamp'")
    if require_domain_tag and not page.tags:
        problems.append("missing tags (at least one domain tag required)")
    status = page.metadata.get("status")
    if status is not None and status not in STATUS_VALUES:
        problems.append(f"invalid status '{status}' (expected {sorted(STATUS_VALUES)})")
    kind = page.metadata.get("kind")
    if kind is not None and kind not in KIND_VALUES:
        problems.append(f"invalid kind '{kind}' (expected {sorted(KIND_VALUES)})")
    return problems
