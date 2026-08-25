"""Canonical, portable Workplace state.

The Workplace is the durable user/instance boundary around Fractal.  It owns
Project records, profile and authority locations, memory, decisions, System
Review history, component state, and references to System Version records.  A
Workplace record deliberately contains only neutral identity and logical
locations; it never embeds a user's home directory, personal profile, Project
contents, credentials, or System Version values.

The public API is intentionally small and path-oriented:

``resolve_workplace_root``
    Resolve an explicit root, ``FRACTAL_WORKPLACE``, or the default
    ``~/Fractal Workplace`` in that order.
``create_workplace`` / ``load_workplace`` / ``ensure_workplace``
    Bootstrap, read, or obtain a canonical Workplace.  Legacy
    ``workspace.json`` records are never migrated implicitly; use the explicit
    migration operation when that transition is intended.
``Workplace.resolve`` / ``Workplace.location``
    Resolve a ``workplace://`` URI or a named logical location without
    allowing traversal outside the root.
``Workplace.active_projects`` / ``completed_projects``
    Return derived views from canonical Project ``record.status`` values.

The only file created by a fresh bootstrap is ``workplace.json``.  Project,
System Version, and ephemeral runtime directories are created lazily by their
own operations.  Runtime state is local and rebuildable and is therefore
never represented as committed canonical Workplace content.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

from fractal.models import ProjectRecord
from fractal.validation import validate_project_record

WORKPLACE_FILENAME = "workplace.json"
LEGACY_WORKPLACE_FILENAME = "workspace.json"
WORKPLACE_URI_SCHEME = "workplace"
WORKPLACE_RECORD_TYPE = "fractal-workplace"
WORKPLACE_RECORD_VERSION = 1
DEFAULT_WORKPLACE_DIRNAME = "Fractal Workplace"
RUNTIME_DIRNAME = ".runtime"

type ProjectLike = ProjectRecord | Mapping[str, Any]


class WorkplaceError(RuntimeError):
    """Base error for canonical Workplace operations."""


class WorkplaceNotFoundError(WorkplaceError):
    """Raised when a canonical Workplace is not present."""


class WorkplaceValidationError(WorkplaceError):
    """Raised when Workplace or version state is not canonical."""


class WorkplaceMigrationError(WorkplaceError):
    """Raised when a legacy Workplace cannot be migrated safely."""


class WorkplaceVersionStateError(WorkplaceValidationError):
    """Raised for an impossible active/unresolved-candidate combination."""


class WorkplaceAlreadyExistsError(WorkplaceError):
    """Raised when a create operation would replace invalid canonical state."""


_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?"
)
_PROJECT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_URI_PATTERN = re.compile(r"workplace://(?:[A-Za-z0-9._~/-]+)?\Z")
_VERSION_STATUSES = {
    "active",
    "candidate",
    "rejected",
    "previously-active",
    "resolved",
}
_ACTIVE_PROJECT_STATUSES = {
    "planning",
    "in_progress",
    "awaiting_completion",
    "blocked",
}


def _uri(path: str) -> str:
    return f"{WORKPLACE_URI_SCHEME}://{path}" if path else f"{WORKPLACE_URI_SCHEME}://"


# These are logical references, not filesystem paths.  Keeping the names and
# URIs in one table makes the root record portable across machines.
LOGICAL_LOCATIONS: dict[str, str] = {
    "record": _uri(WORKPLACE_FILENAME),
    "projects": _uri("projects"),
    "project_records": _uri("projects"),
    "active_projects": _uri("views/projects/active"),
    "completed_projects": _uri("views/projects/completed"),
    "profile": _uri("profile"),
    "authority": _uri("authority"),
    "memory": _uri("memory"),
    "decisions": _uri("decisions"),
    "system": _uri("system"),
    "system_versions": _uri("system/versions"),
    "system_reviews": _uri("system/reviews"),
    "components": _uri("system/components"),
    "runtime": _uri(RUNTIME_DIRNAME),
}

_SYSTEM_REFERENCES: dict[str, str] = {
    "version_records": LOGICAL_LOCATIONS["system_versions"],
    "active_pointer": _uri("system/active-version.json"),
    "candidate_pointer": _uri("system/candidate-version.json"),
    "decisions": _uri("system/decisions"),
    "reviews": LOGICAL_LOCATIONS["system_reviews"],
    "components": LOGICAL_LOCATIONS["components"],
}


def _schema_path() -> Path:
    return Path(__file__).parent / "schemas" / "workplace.schema.json"


def _validate_schema(value: dict[str, Any]) -> None:
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)


def _version_id(version: str) -> str:
    if not isinstance(version, str) or _VERSION_PATTERN.fullmatch(version) is None:
        raise WorkplaceValidationError(f"Invalid System Version: {version}")
    return version


def _project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or _PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise ValueError(f"Invalid Project id: {project_id}")
    return project_id


def _root_path(root: str | os.PathLike[str] | Path | None) -> Path:
    """Resolve a root without creating it or consulting unrelated state."""
    if root is None:
        return resolve_workplace_root()
    return Path(root).expanduser().absolute()


def resolve_workplace_root(
    root: str | os.PathLike[str] | Path | None = None,
) -> Path:
    """Resolve ``root`` > ``FRACTAL_WORKPLACE`` > ``~/Fractal Workplace``.

    Resolution is deterministic and does not create directories.  Explicit
    arguments are useful for tests and portability; callers should pass them
    whenever operating on a non-default Workplace.
    """

    if root is not None:
        return Path(root).expanduser().absolute()
    configured = os.environ.get("FRACTAL_WORKPLACE")
    if configured:
        return Path(configured).expanduser().absolute()
    return (Path.home() / DEFAULT_WORKPLACE_DIRNAME).absolute()


def workplace_path(root: str | os.PathLike[str] | Path | None = None) -> Path:
    """Return the canonical ``workplace.json`` path for a resolved root."""

    return resolve_workplace_root(root) / WORKPLACE_FILENAME


def legacy_workplace_path(root: str | os.PathLike[str] | Path | None = None) -> Path:
    """Return the legacy ``workspace.json`` path for a resolved root."""

    return resolve_workplace_root(root) / LEGACY_WORKPLACE_FILENAME


def canonical_workplace_exists(
    root: str | os.PathLike[str] | Path | None = None,
) -> bool:
    """Return whether a canonical ``workplace.json`` exists."""

    return workplace_path(root).is_file()


def workplace_exists(
    root: str | os.PathLike[str] | Path | None = None,
    *,
    include_legacy: bool = True,
) -> bool:
    """Return whether canonical or (optionally) legacy Workplace state exists."""

    path = resolve_workplace_root(root)
    canonical = (path / WORKPLACE_FILENAME).is_file()
    legacy = include_legacy and (path / LEGACY_WORKPLACE_FILENAME).is_file()
    return canonical or legacy


def _neutral_record(*, migration: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "record_type": WORKPLACE_RECORD_TYPE,
        "record_version": WORKPLACE_RECORD_VERSION,
        "workplace_id": "neutral",
        "identity": {"kind": "neutral"},
        "locations": copy.deepcopy(LOGICAL_LOCATIONS),
        "system": copy.deepcopy(_SYSTEM_REFERENCES),
        "runtime": {
            "storage_class": "local-ephemeral",
            "committed": False,
            "rebuildable": True,
        },
    }
    if migration is not None:
        value["migration"] = copy.deepcopy(migration)
    validate_workplace(value)
    return value


def validate_workplace(record: Mapping[str, Any]) -> None:
    """Validate one canonical Workplace record against its packaged schema."""

    if not isinstance(record, dict):
        raise TypeError("Workplace record must be a dictionary")
    _validate_schema(record)
    # These references are deliberately stable.  A caller must not quietly
    # turn the root record into a second version registry or an absolute path
    # manifest.
    if record["locations"] != LOGICAL_LOCATIONS:
        raise WorkplaceValidationError("Workplace locations are not canonical")
    if record["system"] != _SYSTEM_REFERENCES:
        raise WorkplaceValidationError("Workplace System references are not canonical")


def _validate_version_record(record: Mapping[str, Any]) -> None:
    if not isinstance(record, dict):
        raise WorkplaceValidationError("System Version record must be a dictionary")
    required = {"record_type", "record_version", "version", "status"}
    if set(record) - required - {"source_sha256", "source", "legacy"}:
        raise WorkplaceValidationError("Unknown System Version record field")
    if not required.issubset(record):
        raise WorkplaceValidationError("Incomplete System Version record")
    if record["record_type"] != "system-version" or record["record_version"] != 1:
        raise WorkplaceValidationError("Unsupported System Version record")
    _version_id(record["version"])
    if record["status"] not in _VERSION_STATUSES:
        raise WorkplaceValidationError(f"Invalid System Version state: {record['status']}")
    if "source_sha256" in record and (
        not isinstance(record["source_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", record["source_sha256"]) is None
    ):
        raise WorkplaceValidationError("Invalid System Version source digest")
    if "legacy" in record and not isinstance(record["legacy"], Mapping):
        raise WorkplaceValidationError("Invalid System Version legacy provenance")


def _pointer_record(kind: Literal["active", "candidate"], version_uri: str) -> dict[str, Any]:
    if not _URI_PATTERN.fullmatch(version_uri):
        raise WorkplaceValidationError(f"Invalid System Version record URI: {version_uri}")
    return {
        "record_type": "system-version-pointer",
        "record_version": 1,
        "pointer_kind": kind,
        "record_uri": version_uri,
    }


def _validate_pointer(record: Mapping[str, Any], expected_kind: str) -> None:
    if not isinstance(record, dict):
        raise WorkplaceValidationError("System Version pointer must be a dictionary")
    if set(record) != {"record_type", "record_version", "pointer_kind", "record_uri"}:
        raise WorkplaceValidationError("Invalid System Version pointer fields")
    if record["record_type"] != "system-version-pointer" or record["record_version"] != 1:
        raise WorkplaceValidationError("Unsupported System Version pointer")
    if record["pointer_kind"] != expected_kind:
        raise WorkplaceValidationError("System Version pointer kind mismatch")
    if not isinstance(record["record_uri"], str) or not _URI_PATTERN.fullmatch(
        record["record_uri"]
    ):
        raise WorkplaceValidationError("Invalid System Version pointer URI")
    if not record["record_uri"].startswith(LOGICAL_LOCATIONS["system_versions"] + "/"):
        raise WorkplaceValidationError("System Version pointer must target canonical records")


def _atomic_write(path: Path, content: bytes) -> None:
    """Write bytes with fsync + replace, leaving no partial target file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(Path(path), text.encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkplaceValidationError(f"Cannot read canonical JSON: {path}") from error
    if not isinstance(value, dict):
        raise WorkplaceValidationError(f"Canonical JSON must be an object: {path}")
    return value


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_paths(originals: Mapping[Path, bytes | None]) -> None:
    for path, content in originals.items():
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, content)
        except OSError:
            # The source legacy file is intentionally retained even if a best
            # effort rollback cannot restore a pre-existing target.
            continue


def _remove_empty_directories(directories: set[Path]) -> None:
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue


def _safe_join(root: Path, relative: str | Path) -> Path:
    root = root.absolute()
    relative_path = PurePosixPath(str(relative))
    if ".." in relative_path.parts:
        raise ValueError("Workplace location cannot contain parent traversal")
    candidate = root / relative
    real_root = root.resolve(strict=False)
    real_candidate = candidate.resolve(strict=False)
    try:
        real_candidate.relative_to(real_root)
    except ValueError as error:
        raise ValueError("Workplace location escapes its root") from error
    return candidate


def resolve_logical_location(
    root: str | os.PathLike[str] | Path,
    location: str | os.PathLike[str] | Path,
) -> Path:
    """Resolve a logical Workplace URI or relative path inside ``root``."""

    root_path = _root_path(root)
    real_root = root_path.resolve(strict=False)
    raw = str(location) if isinstance(location, Path) else os.fspath(location)
    if raw.startswith(f"{WORKPLACE_URI_SCHEME}:"):
        parsed = urlsplit(raw)
        if parsed.scheme != WORKPLACE_URI_SCHEME or parsed.query or parsed.fragment:
            raise ValueError(f"Invalid Workplace URI: {raw}")
        relative = "/".join(part for part in (parsed.netloc, parsed.path.lstrip("/")) if part)
        relative = unquote(relative)
        if relative in {"", "."}:
            return root_path
    elif raw.startswith("/"):
        candidate = Path(raw).expanduser()
        try:
            candidate.resolve(strict=False).relative_to(real_root)
        except ValueError as error:
            raise ValueError("Absolute Workplace path escapes its root") from error
        return candidate
    else:
        relative = raw
    return _safe_join(root_path, relative)


def logical_uri(
    root: str | os.PathLike[str] | Path,
    path: str | os.PathLike[str] | Path,
) -> str:
    """Return a portable ``workplace://`` URI for a path under ``root``."""

    root_path = _root_path(root)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    real_candidate = candidate.resolve(strict=False)
    real_root = root_path.resolve(strict=False)
    try:
        real_candidate.relative_to(real_root)
        relative = candidate.relative_to(root_path)
    except ValueError as error:
        raise ValueError("Path is outside the Workplace root") from error
    return _uri(relative.as_posix()) if relative.parts else _uri("")


def _legacy_version_values(legacy: Mapping[str, Any]) -> tuple[str | None, str | None]:
    system = legacy.get("system", {})
    if not isinstance(system, Mapping):
        raise WorkplaceMigrationError("Legacy Workspace system section is not an object")
    active = system.get("active_version")
    candidate = system.get("candidate_version")
    if active is not None:
        active = _version_id(active)
    if candidate is not None:
        candidate = _version_id(candidate)
    return active, candidate


def _legacy_documents(
    root: Path,
    legacy: Mapping[str, Any],
    source_sha256: str,
) -> dict[Path, dict[str, Any]]:
    active, candidate = _legacy_version_values(legacy)
    documents: dict[Path, dict[str, Any]] = {}
    versions: dict[str, str] = {}
    if active is not None:
        versions[active] = "active"
    if candidate is not None and candidate != active:
        versions[candidate] = "candidate"
    for version, status in versions.items():
        version_record = {
            "record_type": "system-version",
            "record_version": 1,
            "version": version,
            "status": status,
            "source_sha256": source_sha256,
            "source": "legacy-workspace.json",
        }
        legacy_system = legacy.get("system")
        if isinstance(legacy_system, Mapping):
            preserved = {
                key: legacy_system[key]
                for key in ("repository", "live_adapter_projection")
                if key in legacy_system
            }
            if preserved:
                version_record["legacy"] = preserved
        _validate_version_record(version_record)
        documents[_safe_join(root, f"system/versions/{version}.json")] = version_record
    if active is not None:
        uri = logical_uri(root, _safe_join(root, f"system/versions/{active}.json"))
        documents[_safe_join(root, "system/active-version.json")] = _pointer_record(
            "active", uri
        )
    # A legacy record occasionally mirrored an active version as its
    # candidate.  Treat the duplicate as resolved history; an unresolved
    # candidate is represented only by a distinct candidate pointer now.
    if candidate is not None and candidate != active:
        uri = logical_uri(root, _safe_join(root, f"system/versions/{candidate}.json"))
        documents[_safe_join(root, "system/candidate-version.json")] = _pointer_record(
            "candidate", uri
        )
    return documents


def _migrated_record(legacy: Mapping[str, Any], source_sha256: str) -> dict[str, Any]:
    active, candidate = _legacy_version_values(legacy)
    migration = {
        "source_record_type": str(legacy.get("record_type", "legacy-workspace")),
        "source_record_version": int(legacy.get("record_version", 1)),
        "source_sha256": source_sha256,
        "candidate_duplicate_resolved": candidate is not None and candidate == active,
    }
    return _neutral_record(migration=migration)


def _load_canonical(root: Path, *, validate_versions: bool = True) -> Workplace:
    path = root / WORKPLACE_FILENAME
    if not path.is_file():
        raise WorkplaceNotFoundError(f"Canonical Workplace not found: {path}")
    if path.is_symlink():
        raise WorkplaceValidationError("Canonical Workplace record cannot be a symlink")
    value = _read_json(path)
    try:
        validate_workplace(value)
    except Exception as error:
        if isinstance(error, WorkplaceError):
            raise
        raise WorkplaceValidationError(f"Invalid Workplace record: {path}") from error
    workplace = Workplace(root=root, record=value)
    if validate_versions:
        workplace.validate_version_state()
    return workplace


def create_workplace(
    root: str | os.PathLike[str] | Path | None = None,
) -> Workplace:
    """Create a neutral fresh Workplace and verify its read-back.

    Existing valid canonical state is returned unchanged.  A legacy-only root
    is rejected rather than migrated or overwritten.  No child directory is
    created during fresh bootstrap.
    """

    root_path = _root_path(root)
    canonical = root_path / WORKPLACE_FILENAME
    legacy = root_path / LEGACY_WORKPLACE_FILENAME
    if canonical.exists():
        return _load_canonical(root_path)
    if legacy.exists():
        raise WorkplaceMigrationError(
            f"Legacy Workplace exists at {legacy}; run explicit "
            f"`fractal workplace migrate --root {root_path}`"
        )
    if root_path.exists() and not root_path.is_dir():
        raise WorkplaceError(f"Workplace root is not a directory: {root_path}")
    root_path.mkdir(parents=True, exist_ok=True)
    record = _neutral_record()
    try:
        _atomic_json_write(canonical, record)
        loaded = _load_canonical(root_path)
    except Exception:
        # No speculative canonical tree should survive a failed first write.
        canonical.unlink(missing_ok=True)
        raise
    return loaded


def load_workplace(
    root: str | os.PathLike[str] | Path | None = None,
    *,
    migrate_legacy: bool = False,
) -> Workplace:
    """Load and validate canonical state without changing a legacy root.

    ``migrate_legacy`` is retained as a compatibility keyword for callers that
    explicitly selected the old API behaviour.  New callers should use
    :func:`migrate_legacy_workspace` (or ``fractal workplace migrate``) so the
    write and deletion boundary is unmistakable.
    """

    root_path = _root_path(root)
    if (root_path / WORKPLACE_FILENAME).is_file():
        return _load_canonical(root_path)
    if migrate_legacy and (root_path / LEGACY_WORKPLACE_FILENAME).is_file():
        return migrate_legacy_workspace(root_path)
    if (root_path / LEGACY_WORKPLACE_FILENAME).is_file():
        raise WorkplaceMigrationError(
            f"Legacy Workplace exists at {root_path / LEGACY_WORKPLACE_FILENAME}; "
            "run explicit `fractal workplace migrate`"
        )
    raise WorkplaceNotFoundError(f"No Workplace record at {root_path}")


def ensure_workplace(
    root: str | os.PathLike[str] | Path | None = None,
) -> Workplace:
    """Load or create one canonical Workplace without implicit migration.

    Status and other read-oriented callers use this helper.  Seeing a legacy
    ``workspace.json`` is therefore an explicit issue, never permission to
    rewrite or remove the source record.
    """

    root_path = _root_path(root)
    if (root_path / WORKPLACE_FILENAME).is_file():
        return _load_canonical(root_path)
    if (root_path / LEGACY_WORKPLACE_FILENAME).is_file():
        raise WorkplaceMigrationError(
            f"Legacy Workplace exists at {root_path / LEGACY_WORKPLACE_FILENAME}; "
            "run explicit `fractal workplace migrate`"
        )
    return create_workplace(root_path)


def validate_version_state(
    workplace: Workplace | str | os.PathLike[str] | Path,
) -> None:
    """Validate the active/candidate System Version pointers for a Workplace."""

    if isinstance(workplace, Workplace):
        workplace.validate_version_state()
    else:
        load_workplace(workplace).validate_version_state()


def migrate_legacy_workspace(
    root: str | os.PathLike[str] | Path,
) -> Workplace:
    """Migrate ``workspace.json`` to ``workplace.json`` on exactly ``root``.

    All replacement records are written and read back before the legacy source
    is removed.  A duplicate active/candidate version is collapsed to one
    resolved active pointer; a distinct candidate remains a candidate.  The
    source is retained whenever any write, validation, or read-back fails.
    """

    root_path = _root_path(root)
    if root_path.is_symlink():
        raise WorkplaceMigrationError("Migration root cannot be a symlink")
    legacy_path = root_path / LEGACY_WORKPLACE_FILENAME
    canonical_path = root_path / WORKPLACE_FILENAME
    if canonical_path.is_file():
        return _load_canonical(root_path)
    if not legacy_path.is_file():
        raise WorkplaceNotFoundError(f"Legacy Workplace not found: {legacy_path}")
    if legacy_path.is_symlink():
        raise WorkplaceMigrationError("Legacy Workplace source cannot be a symlink")
    try:
        source_bytes = legacy_path.read_bytes()
        legacy = json.loads(source_bytes.decode("utf-8"))
        if not isinstance(legacy, dict):
            raise ValueError("Legacy Workspace record must be an object")
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        record = _migrated_record(legacy, source_sha256)
        documents = _legacy_documents(root_path, legacy, source_sha256)
        validate_workplace(record)
    except Exception as error:
        if isinstance(error, WorkplaceError):
            raise
        raise WorkplaceMigrationError("Legacy Workplace is not migratable") from error

    target_paths = [*documents, canonical_path]
    targets = {path: _read_bytes(path) for path in target_paths}
    created_directories: set[Path] = set()
    for path in target_paths:
        directory = path.parent
        while directory != root_path:
            if not directory.exists():
                created_directories.add(directory)
            directory = directory.parent
    root_path.mkdir(parents=True, exist_ok=True)
    try:
        for path, value in documents.items():
            _atomic_json_write(path, value)
        _atomic_json_write(canonical_path, record)
        loaded = _load_canonical(root_path)
        # Explicitly read every replacement, so a mocked or short read cannot
        # accidentally permit removal of the only legacy source.
        for path, expected in documents.items():
            actual = _read_json(path)
            if actual != expected:
                raise WorkplaceMigrationError(f"Migration read-back mismatch: {path}")
        if loaded.record != record:
            raise WorkplaceMigrationError("Migration root read-back mismatch")
        legacy_path.unlink()
        return loaded
    except Exception as error:
        _restore_paths(targets)
        _remove_empty_directories(created_directories)
        if isinstance(error, WorkplaceError):
            raise
        raise WorkplaceMigrationError("Legacy Workplace migration failed") from error


@dataclass(frozen=True, slots=True)
class Workplace:
    """A validated canonical Workplace rooted at one portable directory."""

    root: Path
    record: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).absolute())
        validate_workplace(self.record)

    @classmethod
    def create(cls, root: str | os.PathLike[str] | Path | None = None) -> Workplace:
        return create_workplace(root)

    @classmethod
    def create_fresh(cls, root: str | os.PathLike[str] | Path | None = None) -> Workplace:
        """Explicit spelling for callers bootstrapping a new root."""

        return create_workplace(root)

    @classmethod
    def exists(
        cls,
        root: str | os.PathLike[str] | Path | None = None,
        *,
        include_legacy: bool = True,
    ) -> bool:
        return workplace_exists(root, include_legacy=include_legacy)

    @classmethod
    def load(
        cls,
        root: str | os.PathLike[str] | Path | None = None,
        *,
        migrate_legacy: bool = False,
    ) -> Workplace:
        return load_workplace(root, migrate_legacy=migrate_legacy)

    @classmethod
    def ensure(cls, root: str | os.PathLike[str] | Path | None = None) -> Workplace:
        return ensure_workplace(root)

    @classmethod
    def migrate(cls, root: str | os.PathLike[str] | Path) -> Workplace:
        return migrate_legacy_workspace(root)

    @property
    def record_path(self) -> Path:
        return self.root / WORKPLACE_FILENAME

    @property
    def workplace_path(self) -> Path:
        return self.record_path

    @property
    def project_root(self) -> Path:
        return self.root / "projects"

    @property
    def projects_root(self) -> Path:
        return self.project_root

    @property
    def runtime_root(self) -> Path:
        """Return the ignored, lazily-created local runtime directory."""

        return self.root / RUNTIME_DIRNAME

    @property
    def runtime_path(self) -> Path:
        return self.runtime_root

    @property
    def runtime_is_ephemeral(self) -> bool:
        return self.record["runtime"]["storage_class"] == "local-ephemeral"

    @property
    def runtime_ignored(self) -> bool:
        return self.runtime_is_ephemeral and not self.record["runtime"]["committed"]

    @property
    def locations(self) -> dict[str, Path]:
        """Return named logical locations mapped to paths (without creating them)."""

        return {name: self.resolve(uri) for name, uri in self.record["locations"].items()}

    @property
    def logical_locations(self) -> dict[str, str]:
        return copy.deepcopy(self.record["locations"])

    def location(self, name_or_uri: str, *parts: str) -> Path:
        """Resolve a named logical location, URI, or safe relative suffix."""

        base = self.record["locations"].get(name_or_uri, name_or_uri)
        path = self.resolve(base)
        for part in parts:
            path = _safe_join(self.root, path.relative_to(self.root) / part)
        return path

    def resolve(self, location: str | os.PathLike[str] | Path) -> Path:
        return resolve_logical_location(self.root, location)

    def resolve_location(self, location: str | os.PathLike[str] | Path) -> Path:
        """Compatibility spelling for :meth:`resolve`."""

        return self.resolve(location)

    def uri(self, path: str | os.PathLike[str] | Path) -> str:
        return logical_uri(self.root, path)

    def project_record_path(self, project_id: str) -> Path:
        return _safe_join(self.root, Path("projects") / _project_id(project_id) / "record.json")

    def project_record_uri(self, project_id: str) -> str:
        return self.uri(self.project_record_path(project_id))

    def runtime_file(self, name: str) -> Path:
        """Return a safe path below ignored runtime state.

        Asking Workplace for a runtime file is an actual runtime access, so it
        also establishes the nested ignore boundary before returning the path.
        Fresh Workplace bootstrap remains small because this method is lazy.
        """

        if not name or Path(name).name != name:
            raise ValueError("Runtime file name must be one path component")
        self.ensure_runtime()
        return _safe_join(self.root, Path(RUNTIME_DIRNAME) / name)

    def ensure_runtime(self) -> Path:
        """Create and return the ephemeral runtime directory on demand."""

        if self.runtime_root.is_symlink():
            raise WorkplaceError("Workplace runtime root cannot be a symlink")
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        gitignore = self.runtime_root / ".gitignore"
        expected = b"*\n!.gitignore\n"
        if gitignore.is_symlink():
            raise WorkplaceError("Workplace runtime .gitignore cannot be a symlink")
        if not gitignore.is_file() or gitignore.read_bytes() != expected:
            _atomic_write(gitignore, expected)
        return self.runtime_root

    def save(self) -> Workplace:
        """Atomically write this record and return its verified read-back."""

        previous = _read_bytes(self.record_path)
        try:
            _atomic_json_write(self.record_path, self.record)
            return _load_canonical(self.root)
        except Exception:
            _restore_paths({self.record_path: previous})
            raise

    def reload(self) -> Workplace:
        return _load_canonical(self.root)

    def validate_version_state(self) -> None:
        """Reject impossible active/unresolved-candidate pointer combinations."""

        active = self._read_pointer("active")
        candidate = self._read_pointer("candidate")
        if active is None or candidate is None:
            return
        active_record = self._read_version_for_pointer(active)
        candidate_record = self._read_version_for_pointer(candidate)
        active_version = active_record["version"]
        candidate_version = candidate_record["version"]
        unresolved = candidate_record["status"] in {"candidate"}
        if active_version == candidate_version and unresolved:
            raise WorkplaceVersionStateError(
                "A System Version cannot be active and an unresolved candidate"
            )

    def version_record_path(self, version: str) -> Path:
        return _safe_join(self.root, Path("system/versions") / f"{_version_id(version)}.json")

    def version_record_uri(self, version: str) -> str:
        return self.uri(self.version_record_path(version))

    def read_version_record(self, version: str) -> dict[str, Any]:
        path = self.version_record_path(version)
        if not path.is_file():
            raise WorkplaceNotFoundError(f"System Version record not found: {version}")
        record = _read_json(path)
        _validate_version_record(record)
        return record

    def write_version_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(record))
        _validate_version_record(value)
        path = self.version_record_path(value["version"])
        previous = _read_bytes(path)
        try:
            _atomic_json_write(path, value)
            read_back = self.read_version_record(value["version"])
            if read_back != value:
                raise WorkplaceValidationError("System Version record read-back mismatch")
            self.validate_version_state()
            return read_back
        except Exception:
            _restore_paths({path: previous})
            raise

    def set_version_pointer(
        self,
        kind: Literal["active", "candidate"],
        version: str,
    ) -> dict[str, Any]:
        """Point ``active`` or ``candidate`` at an existing canonical record."""

        if kind not in {"active", "candidate"}:
            raise ValueError("Version pointer kind must be active or candidate")
        record = self.read_version_record(version)
        path = self.root / "system" / (
            "active-version.json" if kind == "active" else "candidate-version.json"
        )
        pointer = _pointer_record(kind, self.version_record_uri(record["version"]))
        previous = _read_bytes(path)
        _atomic_json_write(path, pointer)
        try:
            self.validate_version_state()
        except Exception:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
            raise
        return pointer

    def _read_pointer(self, kind: Literal["active", "candidate"]) -> dict[str, Any] | None:
        path = self.root / "system" / (
            "active-version.json" if kind == "active" else "candidate-version.json"
        )
        if not path.exists():
            return None
        pointer = _read_json(path)
        _validate_pointer(pointer, kind)
        return pointer

    def _read_version_for_pointer(self, pointer: Mapping[str, Any]) -> dict[str, Any]:
        uri = pointer["record_uri"]
        path = self.resolve(uri)
        expected_root = self.resolve(LOGICAL_LOCATIONS["system_versions"])
        try:
            path.relative_to(expected_root)
        except ValueError as error:
            raise WorkplaceValidationError("Version pointer escapes canonical records") from error
        record = _read_json(path)
        _validate_version_record(record)
        expected_name = path.stem
        if record["version"] != expected_name:
            raise WorkplaceValidationError("System Version URI and record disagree")
        return record

    def write_project(self, project: ProjectLike, *, overwrite: bool = True) -> dict[str, Any]:
        """Write one canonical Project record and verify its read-back."""

        value = (
            project.to_dict()
            if isinstance(project, ProjectRecord)
            else copy.deepcopy(dict(project))
        )
        validate_project_record(value)
        path = self.project_record_path(value["project_id"])
        if path.exists() and not overwrite:
            raise WorkplaceAlreadyExistsError(value["project_id"])
        previous = _read_bytes(path)
        try:
            _atomic_json_write(path, value)
            read_back = _read_json(path)
            validate_project_record(read_back)
            if read_back != value:
                raise WorkplaceValidationError("Project record read-back mismatch")
            return read_back
        except Exception:
            _restore_paths({path: previous})
            raise

    def iter_projects(self) -> Iterator[dict[str, Any]]:
        """Yield direct canonical Project records in deterministic path order."""

        if not self.project_root.is_dir():
            return
        for project_dir in sorted(self.project_root.iterdir(), key=lambda item: item.name):
            if not project_dir.is_dir() or project_dir.is_symlink():
                continue
            record_path = project_dir / "record.json"
            if not record_path.is_file() or record_path.is_symlink():
                continue
            value = _read_json(record_path)
            validate_project_record(value)
            if value["project_id"] != project_dir.name:
                raise WorkplaceValidationError(
                    f"Project directory and record id disagree: {project_dir.name}"
                )
            yield value

    def project_view(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return a fresh derived Project view filtered by canonical status."""

        projects = list(self.iter_projects())
        if status is None:
            return projects
        return [project for project in projects if project["status"] == status]

    def active_projects(self) -> list[dict[str, Any]]:
        return [
            project
            for project in self.iter_projects()
            if project["status"] in _ACTIVE_PROJECT_STATUSES
        ]

    def completed_projects(self) -> list[dict[str, Any]]:
        return self.project_view("completed")


def active_projects(
    root: str | os.PathLike[str] | Path | None = None,
) -> list[dict[str, Any]]:
    """Return the derived active Project view for one Workplace root."""

    return ensure_workplace(root).active_projects()


def completed_projects(
    root: str | os.PathLike[str] | Path | None = None,
) -> list[dict[str, Any]]:
    """Return the derived completed Project view for one Workplace root."""

    return ensure_workplace(root).completed_projects()


# Readable aliases for callers that prefer noun-first names.
bootstrap_workplace = create_workplace
load_or_migrate_workplace = ensure_workplace
migrate_workspace = migrate_legacy_workspace
resolve_location = resolve_logical_location
resolve_root = resolve_workplace_root
resolve_workplace_location = resolve_logical_location
logical_location = logical_uri
has_workplace = workplace_exists
bootstrap = create_workplace
migrate_legacy = migrate_legacy_workspace
load_or_create = ensure_workplace


__all__ = [
    "DEFAULT_WORKPLACE_DIRNAME",
    "LEGACY_WORKPLACE_FILENAME",
    "LOGICAL_LOCATIONS",
    "RUNTIME_DIRNAME",
    "WORKPLACE_FILENAME",
    "Workplace",
    "WorkplaceAlreadyExistsError",
    "WorkplaceError",
    "WorkplaceMigrationError",
    "WorkplaceNotFoundError",
    "WorkplaceValidationError",
    "WorkplaceVersionStateError",
    "active_projects",
    "bootstrap",
    "bootstrap_workplace",
    "canonical_workplace_exists",
    "completed_projects",
    "create_workplace",
    "ensure_workplace",
    "has_workplace",
    "legacy_workplace_path",
    "logical_location",
    "load_or_create",
    "load_or_migrate_workplace",
    "load_workplace",
    "logical_uri",
    "migrate_legacy",
    "migrate_legacy_workspace",
    "migrate_workspace",
    "resolve_location",
    "resolve_logical_location",
    "resolve_root",
    "resolve_workplace_location",
    "resolve_workplace_root",
    "validate_workplace",
    "validate_version_state",
    "workplace_exists",
    "workplace_path",
]
