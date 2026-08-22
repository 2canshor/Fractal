"""Rebuildable SQLite FTS5 index for canonical Project records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def rebuild_project_index(project_root: Path, database_path: Path) -> int:
    """Replace the derived Project index from canonical record files."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)
    records = []
    for path in sorted(Path(project_root).glob("*/record.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        body = json.dumps(value, ensure_ascii=False, sort_keys=True)
        records.append((value["project_id"], value["title"], value["status"], body))
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE VIRTUAL TABLE projects USING fts5(project_id UNINDEXED, title, status, body)"
        )
        connection.executemany(
            "INSERT INTO projects(project_id, title, status, body) VALUES (?, ?, ?, ?)",
            records,
        )
        connection.commit()
    return len(records)


def search_project_index(database_path: Path, query: str) -> list[dict[str, Any]]:
    """Search a derived Project index and return ranked identifiers."""
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT project_id, title, status, rank "
            "FROM projects WHERE projects MATCH ? ORDER BY rank",
            (query,),
        ).fetchall()
    return [
        {"project_id": row[0], "title": row[1], "status": row[2], "rank": row[3]}
        for row in rows
    ]
