"""Keyword search over the wiki using SQLite FTS5. Zero external deps."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from brain.core.vault import Vault


@dataclass
class Hit:
    path: str
    title: str
    snippet: str
    score: float


def _connect(db_path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
            path UNINDEXED, title, tags, body, tokenize='porter unicode61')"""
    )
    return con


def index(vault: Vault) -> int:
    """Rebuild the FTS index from the wiki. Returns page count."""
    con = _connect(vault.config.search_db_path)
    with con:
        con.execute("DELETE FROM pages")
        n = 0
        for page in vault.wiki_pages():
            con.execute(
                "INSERT INTO pages (path, title, tags, body) VALUES (?,?,?,?)",
                (
                    str(page.path.relative_to(vault.config.wiki_dir)),
                    page.title,
                    " ".join(page.tags),
                    page.content,
                ),
            )
            n += 1
    con.close()
    return n


def search(vault: Vault, query: str, limit: int = 10, tag: str | None = None) -> list[Hit]:
    db = vault.config.search_db_path
    if not db.exists():
        index(vault)
    con = _connect(db)
    sql = """SELECT path, title,
                    snippet(pages, 3, '>>', '<<', ' … ', 20) AS snip,
                    bm25(pages) AS score
             FROM pages WHERE pages MATCH ?"""
    params: list = [query]
    if tag:
        sql += " AND tags LIKE ?"
        params.append(f"%{tag}%")
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Fallback for queries with FTS-special characters
        rows = con.execute(
            sql, [f'"{query}"'] + params[1:]
        ).fetchall()
    con.close()
    return [Hit(path=r[0], title=r[1], snippet=r[2], score=r[3]) for r in rows]
