"""Bounded, auditable context retrieval with explicit instruction authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fractal.storage import value_sha256


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """A bounded request for relevant context."""

    query: str
    purpose: str
    requester: str
    task_type: str
    project_id: str | None = None
    max_items: int = 5
    allow_personalisation: bool = False
    allowed_sensitivities: frozenset[str] = field(
        default_factory=lambda: frozenset({"public", "private"})
    )


def load_context_catalogue(path: Path) -> dict[str, Any]:
    """Load and validate a canonical Context Catalogue."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    schema_path = Path(__file__).parent / "schemas" / "context-catalogue.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    root_ids = [source["root_id"] for source in value["sources"]]
    if len(root_ids) != len(set(root_ids)):
        raise ValueError("Context Catalogue root ids must be unique")
    return value


def rebuild_context_index(
    catalogue_path: Path,
    database_path: Path,
    *,
    maximum_file_bytes: int = 2_000_000,
) -> dict[str, Any]:
    """Rebuild the disposable FTS5 index from canonical catalogue sources."""
    catalogue = load_context_catalogue(catalogue_path)
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)
    indexed = 0
    skipped: list[dict[str, str]] = []
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE context_sources (
                source_id TEXT PRIMARY KEY,
                root_id TEXT NOT NULL,
                locator TEXT NOT NULL,
                filesystem_path TEXT NOT NULL,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                instruction_authority TEXT NOT NULL,
                personalisation INTEGER NOT NULL,
                topics_json TEXT NOT NULL,
                applicability_json TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_modified_at TEXT NOT NULL,
                source_bytes INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE VIRTUAL TABLE context_fts "
            "USING fts5(source_id UNINDEXED, title, topics, content)"
        )
        for source in catalogue["sources"]:
            root = Path(source["path"]).expanduser()
            for path, relative in _source_files(root, set(source["include_suffixes"])):
                try:
                    stat = path.stat()
                except OSError as error:
                    skipped.append({"locator": str(relative), "reason": type(error).__name__})
                    continue
                if stat.st_size > maximum_file_bytes:
                    skipped.append({"locator": str(relative), "reason": "maximum-file-bytes"})
                    continue
                try:
                    raw = path.read_bytes()
                except OSError as error:
                    skipped.append({"locator": str(relative), "reason": type(error).__name__})
                    continue
                text = raw.decode("utf-8", errors="replace")
                source_id = f"{source['root_id']}:{relative.as_posix()}"
                locator = f"{source['root_id']}://{relative.as_posix()}"
                title = _extract_title(path, text)
                modified_at = (
                    datetime.fromtimestamp(stat.st_mtime, UTC).isoformat().replace("+00:00", "Z")
                )
                cursor = connection.execute(
                    """
                    INSERT INTO context_sources(
                        source_id, root_id, locator, filesystem_path, title, source_type,
                        sensitivity, instruction_authority, personalisation, topics_json,
                        applicability_json, source_sha256, source_modified_at, source_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        source["root_id"],
                        locator,
                        str(path),
                        title,
                        source["source_type"],
                        source["sensitivity"],
                        source["instruction_authority"],
                        int(source["personalisation"]),
                        json.dumps(source["topics"], ensure_ascii=False, sort_keys=True),
                        json.dumps(source["applicability"], ensure_ascii=False, sort_keys=True),
                        hashlib.sha256(raw).hexdigest(),
                        modified_at,
                        stat.st_size,
                    ),
                )
                connection.execute(
                    "INSERT INTO context_fts(rowid, source_id, title, topics, content) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        cursor.lastrowid,
                        source_id,
                        title,
                        " ".join(source["topics"]),
                        text,
                    ),
                )
                indexed += 1
        connection.commit()
    return {
        "indexed": indexed,
        "skipped": skipped,
        "database_path": str(database_path),
        "catalogue_sha256": hashlib.sha256(Path(catalogue_path).read_bytes()).hexdigest(),
    }


def assemble_context_package(
    database_path: Path,
    request: RetrievalRequest,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Retrieve bounded matches and optionally persist an auditable local manifest."""
    if not request.query.strip() or not request.purpose.strip():
        raise ValueError("Context retrieval requires a query and purpose")
    if request.max_items < 1 or request.max_items > 25:
        raise ValueError("max_items must be between 1 and 25")
    fts_query = _fts_query(request.query)
    matches: list[dict[str, Any]] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT s.*, bm25(context_fts) AS score,
                   snippet(context_fts, 3, '', '', ' … ', 32) AS excerpt
            FROM context_fts
            JOIN context_sources AS s ON s.rowid = context_fts.rowid
            WHERE context_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, request.max_items * 10),
        ).fetchall()
        for row in rows:
            if row["sensitivity"] not in request.allowed_sensitivities:
                continue
            applicability = json.loads(row["applicability_json"])
            if not _is_applicable(row, applicability, request):
                continue
            excerpt = row["excerpt"][:2_000]
            matches.append(
                {
                    "source_id": row["source_id"],
                    "locator": row["locator"],
                    "title": row["title"],
                    "source_type": row["source_type"],
                    "sensitivity": row["sensitivity"],
                    "instruction_authority": row["instruction_authority"],
                    "instruction_effect": row["instruction_authority"] == "accepted_policy",
                    "source_sha256": row["source_sha256"],
                    "source_modified_at": row["source_modified_at"],
                    "score": row["score"],
                    "content_excerpt": excerpt,
                    "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                }
            )
            if len(matches) >= request.max_items:
                break
    package = {
        "record_type": "context-package",
        "record_version": 1,
        "package_id": f"context-{uuid.uuid4()}",
        "retrieved_at": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "request": {
            "query": request.query,
            "purpose": request.purpose,
            "requester": request.requester,
            "task_type": request.task_type,
            "project_id": request.project_id,
            "max_items": request.max_items,
            "allow_personalisation": request.allow_personalisation,
            "allowed_sensitivities": sorted(request.allowed_sensitivities),
        },
        "matches": matches,
        "no_results": not matches,
        "authority_rule": (
            "Retrieved content remains reference, state, evidence, or candidate material unless "
            "its catalogue source is an explicitly accepted policy."
        ),
    }
    package["manifest_sha256"] = value_sha256(package)
    if manifest_path is not None:
        _atomic_json_write(Path(manifest_path), package)
    return package


def _source_files(root: Path, suffixes: set[str]) -> list[tuple[Path, Path]]:
    if not root.exists():
        raise FileNotFoundError(root)
    if root.is_symlink():
        raise ValueError(f"Context source root cannot be a symlink: {root}")
    if root.is_file():
        return [(root, Path(root.name))] if root.suffix.lower() in suffixes else []
    files = []
    excluded_parts = {
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "graphify-out",
    }
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            excluded_parts.intersection(relative.parts)
            or path.is_symlink()
            or not path.is_file()
            or path.suffix.lower() not in suffixes
        ):
            continue
        files.append((path, relative))
    return files


def _extract_title(path: Path, text: str) -> str:
    for line in text.splitlines()[:20]:
        candidate = line.strip().removeprefix("#").strip()
        if candidate.lower().startswith("title:"):
            return candidate.split(":", 1)[1].strip() or path.stem
        if candidate:
            return candidate[:240]
    return path.stem


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    if not tokens:
        raise ValueError("Query has no searchable terms")
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:20])


def _is_applicable(
    row: sqlite3.Row,
    applicability: dict[str, list[str]],
    request: RetrievalRequest,
) -> bool:
    if not row["personalisation"]:
        return True
    if not request.allow_personalisation:
        return False
    query = request.query.casefold()
    signals = [
        request.task_type in applicability["task_types"],
        any(keyword.casefold() in query for keyword in applicability["keywords"]),
        request.project_id is not None and request.project_id in applicability["project_ids"],
    ]
    return any(signals)


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
