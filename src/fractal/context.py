"""Bounded, auditable context retrieval with explicit instruction authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

from fractal.storage import value_sha256

_CONTEXT_URI_SCHEMES = frozenset({"workplace", "system", "local"})
_CONTEXT_URI_PATTERN = re.compile(
    r"^(?:workplace|system|local)://(?:[A-Za-z0-9._~!$&'()*+,;=:@%/-]+)?$"
)
_CONTEXT_ROOT_NAMES = frozenset(_CONTEXT_URI_SCHEMES)
_CONTEXT_ROOT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


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


@dataclass(frozen=True, slots=True)
class ContextSourceResolver:
    """Resolve logical context locators against one explicit local root set.

    The map is deliberately supplied by the caller at runtime.  A context
    catalogue may therefore travel with a Workplace without carrying any
    machine-specific path.  ``workplace://``, ``system://`` and ``local://``
    are the only durable locator schemes; the corresponding roots are local
    resolution state and are never written back to the catalogue.  ``local``
    may additionally address a named local root, for example
    ``local://guides/reference.txt`` with an explicit ``guides`` mapping.
    """

    roots: Mapping[str, Path]

    def __post_init__(self) -> None:
        normalised = _normalise_resolution_roots(self.roots)
        object.__setattr__(self, "roots", normalised)

    @classmethod
    def from_roots(
        cls,
        roots: Mapping[str, str | Path] | None = None,
        *,
        workplace_root: str | Path | None = None,
        system_root: str | Path | None = None,
        local_root: str | Path | None = None,
    ) -> ContextSourceResolver:
        return cls(
            _combine_resolution_roots(
                roots,
                workplace_root=workplace_root,
                system_root=system_root,
                local_root=local_root,
            )
        )

    def resolve(self, locator: str | Path) -> Path:
        """Resolve one logical URI and reject absent roots or root escapes."""

        return _resolve_context_locator(locator, self.roots)

    resolve_source = resolve
    resolve_locator = resolve


def _root_value(value: Any) -> Path:
    """Extract and normalise a local root without resolving symlinks away."""

    if isinstance(value, (str, Path)):
        root = Path(value).expanduser().absolute()
    elif hasattr(value, "root"):
        root = Path(value.root).expanduser().absolute()
    else:
        raise TypeError("Context resolution roots must be paths or Workplace-like objects")
    if root.is_symlink():
        raise ValueError(f"Context resolution root cannot be a symlink: {root}")
    if not root.exists():
        raise FileNotFoundError(f"Context resolution root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Context resolution root must be a directory: {root}")
    return root


def _normalise_resolution_roots(roots: Mapping[str, Any]) -> dict[str, Path]:
    if not isinstance(roots, Mapping):
        raise TypeError("Context resolution roots must be a mapping")
    normalised: dict[str, Path] = {}
    for key, value in roots.items():
        name = str(key).strip().lower()
        if name.endswith("://"):
            name = name[:-3]
        elif name.endswith("_root"):
            name = name[:-5]
        if not _CONTEXT_ROOT_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Unsupported context resolution root: {key}")
        if name in normalised:
            raise ValueError(f"Duplicate context resolution root: {name}")
        normalised[name] = _root_value(value)
    return normalised


def _combine_resolution_roots(
    roots: Mapping[str, Any] | None = None,
    *,
    workplace_root: str | Path | None = None,
    system_root: str | Path | None = None,
    local_root: str | Path | None = None,
) -> dict[str, Any]:
    combined: dict[str, Any] = dict(roots or {})
    explicit = {
        "workplace": workplace_root,
        "system": system_root,
        "local": local_root,
    }
    for name, value in explicit.items():
        if value is not None:
            combined[name] = value
    return combined


def _uri_parts(locator: str) -> tuple[str, str]:
    if not isinstance(locator, str) or not _CONTEXT_URI_PATTERN.fullmatch(locator):
        raise ValueError(f"Invalid context source locator: {locator}")
    parsed = urlsplit(locator)
    scheme = parsed.scheme.lower()
    if scheme not in _CONTEXT_URI_SCHEMES or parsed.query or parsed.fragment:
        raise ValueError(f"Invalid context source locator: {locator}")
    # ``urlsplit`` treats the text after ``//`` as netloc.  It is still part
    # of the logical path for these URI schemes (``workplace://projects``).
    relative = "/".join(
        part for part in (parsed.netloc, parsed.path.lstrip("/")) if part
    )
    relative = unquote(relative)
    if "\\" in relative or "\x00" in relative:
        raise ValueError(f"Invalid context source locator: {locator}")
    relative_path = PurePosixPath(relative or ".")
    if ".." in relative_path.parts:
        raise ValueError("Context source locator cannot contain parent traversal")
    return scheme, relative


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError("Context source path escapes its configured root") from error
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Context source path cannot traverse a symlink: {current}")


def _resolve_context_locator(locator: str | Path, roots: Mapping[str, Path]) -> Path:
    if not isinstance(locator, str):
        locator = str(locator)
    scheme, relative = _uri_parts(locator)
    root_name = scheme
    selected_relative = relative
    if scheme == "local":
        parts = PurePosixPath(relative).parts if relative else ()
        named = parts[0] if parts else None
        if named in roots and named != "local":
            root_name = named
            selected_relative = "/".join(parts[1:])
        elif named == "local" and len(parts) > 1 and "local" in roots:
            # ``local://local/external`` explicitly targets the generic local
            # root.  Keep ``local://foo`` backwards compatible as a child
            # named ``foo`` below that root.
            root_name = "local"
            selected_relative = "/".join(parts[1:])
    if root_name not in roots:
        if scheme == "local" and root_name != "local":
            raise ValueError(f"No local resolution root configured for {root_name}")
        raise ValueError(f"No local resolution root configured for {scheme}://")
    root = roots[root_name]
    # ``_root_value`` is repeated here so a mutable mapping cannot quietly
    # replace a validated root with a missing or symlinked path later.
    root = _root_value(root)
    candidate = root / selected_relative if selected_relative else root
    _reject_symlink_components(root, candidate)
    real_root = root.resolve(strict=False)
    real_candidate = candidate.resolve(strict=False)
    try:
        real_candidate.relative_to(real_root)
    except ValueError as error:
        raise ValueError("Context source path escapes its configured root") from error
    return candidate


def resolve_context_source(
    locator: str | Path,
    roots: Mapping[str, str | Path] | None = None,
    *,
    resolution_map: Mapping[str, str | Path] | None = None,
    resolution_roots: Mapping[str, str | Path] | None = None,
    root_set: Mapping[str, str | Path] | None = None,
    context_roots: Mapping[str, str | Path] | None = None,
    workplace_root: str | Path | None = None,
    system_root: str | Path | None = None,
    local_root: str | Path | None = None,
) -> Path:
    """Resolve one logical source URI using explicit runtime roots.

    ``resolution_map`` and ``roots`` are accepted as equivalent spellings so
    callers can use either the conceptual map or the root-set terminology.
    An absent root is an error; no host-specific fallback is inferred.
    """

    merged: dict[str, Any] = dict(roots or {})
    if root_set is not None:
        merged.update(root_set)
    if resolution_roots is not None:
        merged.update(resolution_roots)
    if resolution_map is not None:
        merged.update(resolution_map)
    if context_roots is not None:
        merged.update(context_roots)
    resolver = ContextSourceResolver.from_roots(
        merged,
        workplace_root=workplace_root,
        system_root=system_root,
        local_root=local_root,
    )
    return resolver.resolve(locator)


# Public aliases keep the API descriptive for callers that call the value a
# locator rather than a source.
resolve_source_locator = resolve_context_source
resolve_context_locator = resolve_context_source
resolve_context_location = resolve_context_source
resolve_locator = resolve_context_source
ContextResolver = ContextSourceResolver
ContextRootSet = ContextSourceResolver


def load_context_catalogue(path: Path) -> dict[str, Any]:
    """Load and validate a canonical Context Catalogue."""
    catalogue_path = Path(path)
    value = json.loads(catalogue_path.read_text(encoding="utf-8"))
    schema_path = Path(__file__).parent / "schemas" / "context-catalogue.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    root_ids = [source["root_id"] for source in value["sources"]]
    if len(root_ids) != len(set(root_ids)):
        raise ValueError("Context Catalogue root ids must be unique")
    # Legacy catalogues remain readable.  Mark the in-memory value so callers
    # can distinguish a compatibility read from a canonical logical source
    # record; the marker contains no machine-specific path and is never copied
    # into the migrated source entries.
    legacy_roots = [
        source["root_id"]
        for source in value["sources"]
        if "path" in source and "locator" not in source
    ]
    if legacy_roots:
        migration = value.setdefault("migration", {})
        if not isinstance(migration, dict):
            raise ValueError("Context Catalogue migration metadata must be an object")
        migration.setdefault(
            "source_record_type", str(value.get("record_type", "context-catalogue"))
        )
        migration.setdefault("source_record_version", int(value.get("record_version", 1)))
        migration.setdefault(
            "source_sha256", hashlib.sha256(catalogue_path.read_bytes()).hexdigest()
        )
        migration.setdefault("legacy_path_sources", legacy_roots)
        migration.setdefault("destination", "context/sources.json")
        migration.setdefault("read_compatibility", True)
    return value


def rebuild_context_index(
    catalogue_path: Path,
    database_path: Path,
    *,
    maximum_file_bytes: int = 2_000_000,
    roots: Mapping[str, str | Path] | None = None,
    context_roots: Mapping[str, str | Path] | None = None,
    resolution_map: Mapping[str, str | Path] | None = None,
    resolution_roots: Mapping[str, str | Path] | None = None,
    root_set: Mapping[str, str | Path] | None = None,
    workplace_root: str | Path | None = None,
    system_root: str | Path | None = None,
    local_root: str | Path | None = None,
) -> dict[str, Any]:
    """Rebuild the disposable FTS5 index from canonical catalogue sources."""
    catalogue = load_context_catalogue(catalogue_path)
    resolver = ContextSourceResolver.from_roots(
        _combine_resolution_roots(
            roots,
            **{
                "workplace_root": workplace_root,
                "system_root": system_root,
                "local_root": local_root,
            },
        )
        | dict(context_roots or {})
        | dict(root_set or {})
        | dict(resolution_roots or {})
        | dict(resolution_map or {}),
    )
    # Resolve every canonical locator before replacing the disposable index.
    # A missing explicit mapping or source root must not destroy a previously
    # usable index as a side effect of reporting the configuration error.
    resolved_sources: list[tuple[dict[str, Any], Path]] = []
    for source in catalogue["sources"]:
        if "locator" in source:
            root = resolver.resolve(source["locator"])
        else:
            # Compatibility-only path support.  New canonical output and
            # migrated catalogues never emit this field.
            root = _legacy_source_path(Path(catalogue_path), source["path"])
        if not root.exists():
            raise FileNotFoundError(root)
        resolved_sources.append((source, root))
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
        for source, root in resolved_sources:
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
                if "locator" in source:
                    base_scheme, base_relative = _uri_parts(source["locator"])
                    locator_path = "/".join(
                        part for part in (base_relative.rstrip("/"), relative.as_posix()) if part
                    )
                    locator = (
                        f"{base_scheme}://{locator_path}"
                        if locator_path
                        else f"{base_scheme}://"
                    )
                else:
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


def _legacy_source_path(catalogue_path: Path, path: str | Path) -> Path:
    """Read a legacy path relative to its catalogue, without canonicalising it."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = catalogue_path.parent / candidate
    return candidate.absolute()


def _path_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _logical_locator_for_path(path: Path, roots: Mapping[str, Path]) -> str:
    """Convert one legacy local path into a logical URI, or fail safely."""

    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    # Prefer the narrowest matching root.  This avoids turning a path inside
    # ``system`` into a broad ``local`` locator when callers provide both.
    matches: list[tuple[int, str, Path]] = []
    for scheme, root in roots.items():
        root = _root_value(root)
        if not _path_under_root(candidate, root):
            continue
        _reject_symlink_components(root, candidate)
        matches.append((len(root.parts), scheme, root))
    if not matches:
        raise ValueError(
            f"Legacy context source path cannot be mapped to a logical root: {candidate}"
        )
    _, scheme, root = max(matches, key=lambda item: item[0])
    relative = candidate.relative_to(root)
    if scheme in _CONTEXT_URI_SCHEMES:
        return f"{scheme}://{relative.as_posix()}" if relative.parts else f"{scheme}://"
    # Named external roots live inside the logical local namespace; the name
    # remains explicit so a later rebuild can select the same mapped root.
    return (
        f"local://{scheme}/{relative.as_posix()}"
        if relative.parts
        else f"local://{scheme}"
    )


def _legacy_migration_roots(
    destination_path: Path,
    roots: Mapping[str, Any] | None,
    *,
    workplace_root: str | Path | None,
    system_root: str | Path | None,
    local_root: str | Path | None,
) -> dict[str, Path]:
    combined = _combine_resolution_roots(
        roots,
        workplace_root=workplace_root,
        system_root=system_root,
        local_root=local_root,
    )
    # A destination beneath a canonical Workplace is a safe convenience for
    # migration callers.  It never guesses a system or local root.
    if "workplace" not in combined:
        destination_root = destination_path.parent
        if destination_root.name == "context":
            combined["workplace"] = destination_root.parent
    if not combined:
        # Keep the error explicit instead of embedding the old absolute path
        # in a durable output file.
        raise ValueError(
            "Legacy context migration requires an explicit workplace, system, or local root"
        )
    return _normalise_resolution_roots(combined)


def _canonical_context_catalogue(
    legacy: Mapping[str, Any],
    *,
    catalogue_path: Path,
    roots: Mapping[str, Path],
    source_sha256: str,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    legacy_path_sources: list[str] = []
    for source in legacy["sources"]:
        migrated = dict(source)
        path = migrated.pop("path", None)
        migrated.pop("legacy", None)
        if path is not None:
            legacy_path_sources.append(str(source["root_id"]))
            migrated["locator"] = _logical_locator_for_path(
                _legacy_source_path(catalogue_path, path), roots
            )
        locator = migrated.get("locator")
        if not isinstance(locator, str):
            raise ValueError(f"Context source has no logical locator: {source['root_id']}")
        # Resolve each logical locator once before writing.  This catches an
        # absent root, root escape, and symlink traversal before any mutation.
        _resolve_context_locator(locator, roots)
        sources.append(migrated)
    canonical: dict[str, Any] = {
        "record_type": legacy["record_type"],
        "record_version": legacy["record_version"],
        "sources": sources,
    }
    if "$schema" in legacy:
        canonical["$schema"] = legacy["$schema"]
    canonical["migration"] = {
        "source_record_type": str(legacy.get("record_type", "context-catalogue")),
        "source_record_version": int(legacy.get("record_version", 1)),
        "source_sha256": source_sha256,
        "legacy_path_sources": legacy_path_sources,
        "destination": "context/sources.json",
    }
    schema_path = Path(__file__).parent / "schemas" / "context-catalogue.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(canonical)
    return canonical


def migrate_context_catalogue(
    catalogue_path: Path,
    destination_path: Path | None = None,
    *,
    target_path: Path | None = None,
    output_path: Path | None = None,
    roots: Mapping[str, str | Path] | None = None,
    resolution_map: Mapping[str, str | Path] | None = None,
    resolution_roots: Mapping[str, str | Path] | None = None,
    root_set: Mapping[str, str | Path] | None = None,
    workplace_root: str | Path | None = None,
    system_root: str | Path | None = None,
    local_root: str | Path | None = None,
    remove_legacy: bool = False,
) -> dict[str, Any]:
    """Migrate a legacy catalogue to ``context/sources.json`` safely.

    The old file is read and retained by default for compatibility.  If
    ``remove_legacy`` is requested, it is removed only after the canonical
    destination has been written and successfully read back.  Repeating a
    migration returns the validated destination unchanged.
    """

    source_path = Path(catalogue_path).expanduser().absolute()
    if source_path.is_dir():
        root_path = source_path
        candidates = (
            root_path / "memory" / "catalogue" / "context-catalogue.json",
            root_path / "context-catalogue.json",
        )
        source_path = next(
            (candidate for candidate in candidates if candidate.is_file()), candidates[0]
        )
        if destination_path is None and target_path is None and output_path is None:
            destination_path = root_path / "context" / "sources.json"
    if target_path is not None:
        if destination_path is not None and Path(destination_path) != Path(target_path):
            raise ValueError("Specify only one context catalogue destination")
        destination_path = target_path
    if output_path is not None:
        if destination_path is not None and Path(destination_path) != Path(output_path):
            raise ValueError("Specify only one context catalogue destination")
        destination_path = output_path
    if destination_path is None:
        if source_path.name == "context-catalogue.json" and source_path.parent.name == "catalogue":
            destination_path = source_path.parent.parent.parent / "context" / "sources.json"
        else:
            destination_path = source_path.parent / "context" / "sources.json"
    destination = Path(destination_path).expanduser().absolute()
    if source_path == destination:
        return load_context_catalogue(source_path)
    if destination.exists():
        existing = load_context_catalogue(destination)
        if any("path" in source for source in existing["sources"]):
            raise ValueError(
                f"Canonical context catalogue still contains legacy paths: {destination}"
            )
        return existing
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.is_symlink():
        raise ValueError(f"Legacy context catalogue cannot be a symlink: {source_path}")
    source_bytes = source_path.read_bytes()
    legacy = load_context_catalogue(source_path)
    resolution_roots = _legacy_migration_roots(
        destination,
        {
            **dict(roots or {}),
            **dict(root_set or {}),
            **dict(resolution_roots or {}),
            **dict(resolution_map or {}),
        },
        workplace_root=workplace_root,
        system_root=system_root,
        local_root=local_root,
    )
    canonical = _canonical_context_catalogue(
        legacy,
        catalogue_path=source_path,
        roots=resolution_roots,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
    _atomic_json_write(destination, canonical)
    read_back = load_context_catalogue(destination)
    if read_back != canonical:
        destination.unlink(missing_ok=True)
        raise ValueError("Migrated context catalogue failed read-back verification")
    if remove_legacy:
        source_path.unlink()
    return read_back


# Compatibility spellings used by callers that name the source as a legacy
# catalogue or use the destination's canonical filename in the API.
migrate_legacy_context_catalogue = migrate_context_catalogue
migrate_context_sources = migrate_context_catalogue
migrate_legacy_catalogue = migrate_context_catalogue


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
