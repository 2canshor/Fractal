"""Deterministic whole-tree migration for legacy Workplace records.

The small ``workplace.migrate`` helper predates the current Workplace tree.  It
is intentionally kept as a compatibility operation for a single
``workspace.json`` record.  This module owns the larger, once-per-root
conversion: it builds a complete candidate beside the supplied root, validates
the candidate, and switches the directories only after every check has passed.

There are two useful properties worth calling out:

* the source root and any supplied runtime/event roots are the only inputs the
  migration reads or writes; no home-directory or application-support path is
  inferred; and
* all old bytes that are intentionally replaced (pointers, mixed policy,
  context catalogue, method registry, and adapter routes) are retained under
  ``system/history`` in the candidate.  This makes a migration auditable while
  keeping the active Workplace surface canonical and portable.

The public functions are deliberately dependency-light so they can also be
used by a rehearsal script without importing the CLI.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from fractal.context import load_context_catalogue
from fractal.migrations import CURRENT_PROJECT_SCHEMA_VERSION, migrate_project_record
from fractal.models import utc_now
from fractal.storage import value_sha256
from fractal.validation import validate_project_record
from fractal.workplace import (
    LOGICAL_LOCATIONS,
    Workplace,
    _pointer_record,
    _validate_version_record,
    _version_id,
    validate_workplace,
)

MIGRATION_RECORD_TYPE = "workplace-tree-migration"
MIGRATION_RECORD_VERSION = 1
CONTEXT_LEGACY_RELATIVE = Path("memory/catalogue/context-catalogue.json")
CONTEXT_CANONICAL_RELATIVE = Path("context/sources.json")
AUTHORITY_RELATIVE = Path("authority/bindings.json")
POLICY_RELATIVE = Path("policies/current.json")
METHOD_STATUS_RELATIVE = Path("system/method-status.json")
ADAPTER_PREFERENCES_RELATIVE = Path("adapters/preferences.json")
RUNTIME_RELATIVE = Path(".runtime")

_VERSION_RE = re.compile(
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?\Z"
)
_PROJECT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_RESOLVED_CANDIDATE_STATUSES = frozenset(
    {"activated", "active", "historical", "resolved", "previously-active", "rejected"}
)
_UNRESOLVED_CANDIDATE_STATUSES = frozenset(
    {"candidate", "proposed", "staged", "pending", "in_progress", "in-progress", "open"}
)
_RAW_TOP_LEVEL_DIRS = frozenset(
    {
        "cache",
        "caches",
        "logs",
        "pids",
        "raw-live",
        "raw-live-discovery",
        "runtime",
        "session",
        "sessions",
        "sockets",
        "tool-dumps",
        "tool_dumps",
    }
)
_RAW_RELATIVE_PREFIXES = (
    "discovery/raw/",
    "discovery/quarantine/",
    "imports/live-discovery/",
    "imports/quarantine/",
    "imports/raw/",
    "imports/raw-live/",
    "system/components/live-discovery/",
    "system/components/quarantine/",
    "system/components/raw/",
    "system/components/raw-live/",
)
_RAW_FILENAME_PATTERNS = (
    re.compile(r"(?:^|-)live-surface\.json\Z", re.IGNORECASE),
    re.compile(r"(?:^|-)live-tools?\.json\Z", re.IGNORECASE),
    re.compile(r"(?:^|-)tool-(?:inventory|catalogue)\.json\Z", re.IGNORECASE),
    re.compile(r"(?:^|-)socket-(?:inventory|state|dump)\.json\Z", re.IGNORECASE),
    re.compile(r"(?:^|-)raw-(?:session|turn|tool|socket)\.[A-Za-z0-9]+\Z", re.IGNORECASE),
)
_RAW_RECORD_MARKERS = frozenset(
    {
        "raw-session",
        "raw-turn",
        "raw-tool",
        "raw-socket",
        "live-tool-inventory",
        "live-tool-catalogue",
        "live-surface",
        "socket-inventory",
        "socket-state",
        "session-dump",
        "tool-inventory",
        "tool-dump",
        "component-discovery-dump",
    }
)
_RAW_KEY_MARKERS = frozenset(
    {
        "cwd",
        "working_directory",
        "working_dir",
        "socket",
        "socket_path",
        "messaging_socket_path",
        "tool_inventory",
        "tool_snapshot",
        "live_tools",
        "discovered_tools",
        "raw_session",
        "raw_turn",
        "process_inventory",
    }
)
_LEGITIMATE_COMPONENT_STEMS = frozenset(
    {"registry", "selection", "status", "evidence", "user-surface", "discovery-policy"}
)
_RECEIPT_KEYS = frozenset(
    {
        "claim",
        "evidence_id",
        "event_hash",
        "observed_at",
        "receipt_id",
        "recorded_at",
        "source",
        "status",
    }
)
_SECRET_KEY_MARKERS = frozenset(
    {
        "token",
        "password",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
        "private_key",
        "authorization",
    }
)
_HOME_PATH_MARKER = "/" + "Users" + "/"
_PATH_RE = re.compile(
    r"(?:^|[\s'\"=:(\[])(?:"
    + re.escape(_HOME_PATH_MARKER)
    + r"|/home/|/private/tmp/|/tmp/|/var/folders/|/var/run/|/run/|[A-Za-z]:[\\/])"
)
_IP_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9])")
_SOCKET_RE = re.compile(
    r"(?:^|[\s'\"=:(\[])(?:unix://|/[^\s'\"]+\.sock(?:\b|$)|[^\s'\"]*socket[^\s'\"]*)",
    re.IGNORECASE,
)


class WorkplaceMigrationError(RuntimeError):
    """Base error for whole-tree migration failures."""


class MigrationPreflightError(WorkplaceMigrationError):
    """Raised when the source cannot be migrated without guessing."""


class MigrationValidationError(WorkplaceMigrationError):
    """Raised when a staged candidate fails schema or integrity checks."""


class MigrationSwitchError(WorkplaceMigrationError):
    """Raised when a verified candidate cannot be switched into place."""


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Read-only preflight output.

    Paths in ``to_dict`` are represented as ``<root>`` so the plan is safe to
    persist in a portable evidence record.  ``root`` remains available to
    callers that need to execute the plan locally.
    """

    root: Path
    operations: tuple[str, ...]
    source_inventory: dict[str, Any]
    warnings: tuple[str, ...] = ()
    already_canonical: bool = False
    source_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": MIGRATION_RECORD_TYPE,
            "record_version": MIGRATION_RECORD_VERSION,
            "operations": list(self.operations),
            "warnings": list(self.warnings),
            "already_canonical": self.already_canonical,
            "source_sha256": self.source_sha256,
            "source_inventory": self.source_inventory,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Verified result of a whole-tree migration."""

    root: Path
    changed: bool
    operations: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any]
    validation: dict[str, Any]
    rollback: dict[str, Any]
    plan: MigrationPlan
    preserved: dict[str, Any] = field(default_factory=dict)

    @property
    def idempotent(self) -> bool:
        return not self.changed

    @property
    def verified(self) -> bool:
        return bool(self.validation.get("valid"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": MIGRATION_RECORD_TYPE,
            "record_version": MIGRATION_RECORD_VERSION,
            "changed": self.changed,
            "idempotent": self.idempotent,
            "verified": self.verified,
            "operations": list(self.operations),
            "before": self.before,
            "after": self.after,
            "validation": self.validation,
            "rollback": self.rollback,
            "preserved": self.preserved,
            "plan": self.plan.to_dict(),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _path(root: str | os.PathLike[str] | Path, *, label: str = "root") -> Path:
    value = Path(root).expanduser().absolute()
    if value.is_symlink():
        raise MigrationPreflightError(f"Migration {label} cannot be a symlink: {value}")
    if value.exists() and not value.is_dir():
        raise MigrationPreflightError(f"Migration {label} must be a directory: {value}")
    return value


def _ensure_inside(root: Path, candidate: Path, *, label: str) -> Path:
    candidate = candidate.expanduser().absolute()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise MigrationPreflightError(f"{label} escapes the supplied root: {candidate}") from error
    if candidate.is_symlink():
        raise MigrationPreflightError(f"{label} cannot be a symlink: {candidate}")
    return candidate


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationPreflightError(f"{label} is not readable JSON: {path}") from error
    if not isinstance(value, dict):
        raise MigrationPreflightError(f"{label} must be a JSON object: {path}")
    return value


def _file_path(value: str | os.PathLike[str] | Path, *, label: str) -> Path:
    """Resolve an explicit file input without consulting a public default."""

    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise MigrationPreflightError(f"Migration {label} cannot be a symlink: {path}")
    if not path.is_file():
        raise MigrationPreflightError(f"Migration {label} must be a readable file: {path}")
    return path


def _context_root_path(value: str | os.PathLike[str] | Path, *, label: str) -> Path:
    """Resolve an explicitly mapped context directory or file."""

    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise MigrationPreflightError(f"Migration {label} cannot be a symlink: {path}")
    if path.exists() and not (path.is_dir() or path.is_file()):
        raise MigrationPreflightError(f"Migration {label} must be a file or directory: {path}")
    return path


def _portable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _normalise_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _manifest_for_active_pointer(pointer_path: Path, version: str) -> dict[str, Any]:
    manifest_path = pointer_path.parent / "versions" / f"{version}.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise MigrationPreflightError(
            f"Verified active System Version manifest is missing: {version}"
        )
    manifest = _read_json(manifest_path, label="Verified active System Version manifest")
    expected = manifest.get("manifest_sha256")
    unsigned = {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    if (
        not isinstance(expected, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected)
        or value_sha256(unsigned) != expected
    ):
        raise MigrationPreflightError(
            f"Verified active System Version manifest integrity failed: {version}"
        )
    return manifest


def _verify_explicit_active_state(
    *,
    active_pointer_path: str | os.PathLike[str] | Path | None,
    live_state_path: str | os.PathLike[str] | Path | None,
) -> dict[str, Any] | None:
    """Verify caller-supplied active state before staging any Workplace bytes.

    The migration deliberately has no default pointer or live-state location.
    A caller may provide either source, but when both are supplied the mutable
    read model must agree with the pointer and its immutable manifest.
    """

    if active_pointer_path is None and live_state_path is None:
        return None
    pointer_path = (
        _file_path(active_pointer_path, label="active pointer")
        if active_pointer_path
        else None
    )
    state_path = (
        _file_path(live_state_path, label="live state") if live_state_path else None
    )
    pointer: dict[str, Any] | None = None
    if pointer_path is not None:
        pointer = _read_json(pointer_path, label="Verified active System Version pointer")
    state: dict[str, Any] | None = None
    if state_path is not None:
        state = _read_json(state_path, label="Verified live runtime state")
        expected_state_digest = state.get("state_sha256")
        unsigned_state = {
            key: item for key, item in state.items() if key != "state_sha256"
        }
        if (
            not isinstance(expected_state_digest, str)
            or value_sha256(unsigned_state) != expected_state_digest
        ):
            raise MigrationPreflightError("Verified live runtime state integrity failed")
        live_version = state.get("system_version")
        if not isinstance(live_version, Mapping):
            raise MigrationPreflightError("Verified live runtime state has no System Version")
        if pointer is None:
            source_path = live_version.get("source_path")
            if not isinstance(source_path, str) or not source_path:
                raise MigrationPreflightError(
                    "Verified live runtime state does not identify its active pointer"
                )
            pointer_path = _file_path(source_path, label="live-state active pointer")
            pointer = _read_json(pointer_path, label="Verified active System Version pointer")
    if pointer is None or pointer_path is None:
        raise MigrationPreflightError("Verified active state did not provide an active pointer")
    version = pointer.get("version") or pointer.get("system_version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise MigrationPreflightError("Verified active pointer has no valid version")
    status = _normalise_status(pointer.get("status") or pointer.get("activation_status"))
    if status is not None and status != "active":
        raise MigrationPreflightError(f"Verified active pointer is not active: {status}")
    activated_at = pointer.get("activated_at")
    activated_by = pointer.get("activated_by")
    if not isinstance(activated_at, str) or not activated_at:
        raise MigrationPreflightError("Verified active pointer is missing activated_at")
    if not isinstance(activated_by, str) or not activated_by:
        raise MigrationPreflightError("Verified active pointer is missing activated_by")
    manifest_sha = pointer.get("manifest_sha256")
    if not isinstance(manifest_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", manifest_sha):
        raise MigrationPreflightError("Verified active pointer is missing manifest_sha256")
    manifest = _manifest_for_active_pointer(pointer_path, version)
    if manifest.get("manifest_sha256") != manifest_sha:
        raise MigrationPreflightError("Verified active pointer and manifest disagree")
    pointer_digest = _file_digest(pointer_path)[0]
    for digest_key in ("source_sha256", "active_pointer_sha256", "pointer_sha256"):
        supplied = pointer.get(digest_key)
        if supplied is not None and supplied != pointer_digest:
            raise MigrationPreflightError(
                f"Verified active pointer source digest disagrees: {digest_key}"
            )
    if state is not None:
        live_version = state["system_version"]
        if _normalise_status(live_version.get("status")) != "active":
            raise MigrationPreflightError("Verified live runtime System Version is not active")
        expected = {
            "version": version,
            "activated_at": activated_at,
            "activated_by": activated_by,
            "manifest_sha256": manifest_sha,
        }
        for key, expected_value in expected.items():
            if live_version.get(key) != expected_value:
                raise MigrationPreflightError(
                    f"Verified live runtime state disagrees with active pointer: {key}"
                )
        source_path = live_version.get("source_path")
        if not isinstance(source_path, str) or (
            Path(source_path).expanduser().absolute() != pointer_path
        ):
            raise MigrationPreflightError(
                "Verified live runtime state active pointer path disagrees"
            )
        if live_version.get("source_sha256") != pointer_digest:
            raise MigrationPreflightError(
                "Verified live runtime state active pointer digest disagrees"
            )
    return {
        "version": version,
        "activated_at": activated_at,
        "activated_by": activated_by,
        "manifest_sha256": manifest_sha,
        "pointer_sha256": pointer_digest,
        "pointer": copy.deepcopy(pointer),
        "manifest": copy.deepcopy(manifest),
        "live_state_verified": state is not None,
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.migration-{os.getpid()}")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _portable_path(path: Path, root: Path) -> str:
    try:
        return f"<root>/{path.relative_to(root).as_posix()}"
    except ValueError:
        return "<external>"


def _iter_files(root: Path, *, include_git: bool = False) -> Iterable[Path]:
    if not root.exists():
        return ()
    values: list[Path] = []
    for path in root.rglob("*"):
        if not include_git and ".git" in path.relative_to(root).parts:
            continue
        if path.is_file() or path.is_symlink():
            values.append(path)
    return sorted(values, key=lambda item: item.relative_to(root).as_posix())


def _file_digest(path: Path) -> tuple[str, int, bool]:
    if path.is_symlink():
        return _sha256_bytes(("symlink:" + os.readlink(path)).encode("utf-8")), 0, True
    raw = path.read_bytes()
    return _sha256_bytes(raw), len(raw), False


def inventory_workplace(
    root: str | os.PathLike[str] | Path,
    *,
    include_git: bool = False,
) -> dict[str, Any]:
    """Return a deterministic path/digest inventory without changing ``root``."""

    root_path = _path(root)
    files: dict[str, dict[str, Any]] = {}
    for path in _iter_files(root_path, include_git=include_git):
        relative = path.relative_to(root_path).as_posix()
        digest, size, symlink = _file_digest(path)
        files[relative] = {"sha256": digest, "bytes": size, "symlink": symlink}
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "root": "<root>",
        "file_count": len(files),
        "files": files,
        "sha256": _sha256_bytes(encoded),
    }


def _canonical_workplace() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "record_type": "fractal-workplace",
        "record_version": 1,
        "workplace_id": "neutral",
        "identity": {"kind": "neutral"},
        "locations": copy.deepcopy(LOGICAL_LOCATIONS),
        "system": {
            "version_records": "workplace://system/versions",
            "active_pointer": "workplace://system/active-version.json",
            "candidate_pointer": "workplace://system/candidate-version.json",
            "decisions": "workplace://system/decisions",
            "reviews": "workplace://system/reviews",
            "components": "workplace://system/components",
        },
        "runtime": {"storage_class": "local-ephemeral", "committed": False, "rebuildable": True},
    }


def _is_project_id(value: str) -> bool:
    return _PROJECT_ID_RE.fullmatch(value) is not None


def _history_copy(stage: Path, relative: str | Path, raw: bytes) -> Path:
    target = stage / "system" / "history" / Path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != raw:
            raise MigrationPreflightError(f"Historical provenance collision: {target}")
    else:
        target.write_bytes(raw)
    return target


def _tree_bytes_equal(left: Path, right: Path) -> bool:
    left_files = {p.relative_to(left).as_posix(): p for p in _iter_files(left, include_git=True)}
    right_files = {p.relative_to(right).as_posix(): p for p in _iter_files(right, include_git=True)}
    if set(left_files) != set(right_files):
        return False
    return all(
        _file_digest(left_files[key]) == _file_digest(right_files[key]) for key in left_files
    )


def _copy_tree_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        target = destination / entry.name
        if entry.is_symlink():
            target.symlink_to(os.readlink(entry), target_is_directory=entry.is_dir())
        elif entry.is_dir():
            shutil.copytree(entry, target, symlinks=True)
        else:
            shutil.copy2(entry, target)


def _project_sources(stage: Path) -> list[tuple[str, Path]]:
    values: list[tuple[str, Path]] = []
    for bucket in ("active", "completed"):
        directory = stage / "projects" / bucket
        if not directory.is_dir() or directory.is_symlink():
            continue
        for item in sorted(directory.iterdir(), key=lambda path: path.name):
            if item.is_dir() and not item.is_symlink() and _is_project_id(item.name):
                values.append((bucket, item))
    return values


def _project_lock_sources(stage: Path) -> list[tuple[str, Path]]:
    """Return rebuildable ProjectStore lock files from legacy status buckets."""

    values: list[tuple[str, Path]] = []
    for bucket in ("active", "completed"):
        directory = stage / "projects" / bucket
        if not directory.is_dir() or directory.is_symlink():
            continue
        for item in sorted(directory.iterdir(), key=lambda path: path.name):
            if (
                item.is_file()
                and not item.is_symlink()
                and item.name.startswith(".")
                and item.name.endswith(".lock")
            ):
                values.append((bucket, item))
    return values


def _migrate_project_locks(
    stage: Path,
    operations: list[str],
) -> None:
    """Move legacy lock files into ignored, rebuildable runtime state."""

    sources = _project_lock_sources(stage)
    if not sources:
        return
    runtime = stage / RUNTIME_RELATIVE
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    for bucket, source in sources:
        target = runtime / "raw" / "project-locks" / bucket / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if target.exists() and target.read_bytes() != raw:
            raise MigrationPreflightError(f"Project lock collision: {target}")
        if not target.exists():
            target.write_bytes(raw)
        source.unlink()
    operations.append("project-lock-files-to-ignored-runtime")


def _copy_event_journals(stage: Path, event_root: Path | None) -> None:
    """Copy explicit event journals into the staged local runtime."""

    if event_root is None:
        return
    source = event_root / "events" if (event_root / "events").is_dir() else event_root
    if not source.is_dir():
        return
    destination = stage / RUNTIME_RELATIVE / "events"
    for path in _iter_files(source, include_git=True):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = path.read_bytes()
        if target.exists() and not _event_journal_compatible(raw, target.read_bytes()):
            raise MigrationPreflightError(f"Event journal collision: {target}")
        if not target.exists():
            target.write_bytes(raw)


def _event_journal_compatible(source: bytes, target: bytes) -> bool:
    """Allow an already-migrated target journal to extend its source prefix."""

    return target == source or target.startswith(source)


def _migrate_project_schema(
    stage: Path,
    project_id: str,
    project_path: Path,
    event_root: Path | None,
) -> bool:
    """Upgrade one legacy Project and append its verified migration event."""

    record_path = project_path / "record.json"
    digest_path = project_path / "record.sha256"
    record = _read_json(record_path, label=f"Project {project_id}")
    if not digest_path.is_file() or digest_path.is_symlink():
        raise MigrationValidationError(f"Project record/sidecar missing: {project_id}")
    expected_digest = digest_path.read_text(encoding="ascii").strip()
    if expected_digest != value_sha256(record):
        raise MigrationValidationError(f"Project sidecar mismatch: {project_id}")
    if record.get("schema_version") == CURRENT_PROJECT_SCHEMA_VERSION:
        return False
    if not isinstance(record.get("schema_version"), str):
        raise MigrationValidationError(f"Project schema version is missing: {project_id}")
    try:
        migrated, applied = migrate_project_record(record)
    except Exception as error:
        raise MigrationValidationError(f"Project schema migration failed: {project_id}") from error
    if not applied:
        return False
    event_path = stage / RUNTIME_RELATIVE / "events" / f"{project_id}.jsonl"
    if event_root is None or not event_path.is_file():
        raise MigrationValidationError(
            f"Explicit copied event journal unavailable for Project: {project_id}"
        )
    events = _read_event_values(event_path, project_id)
    _validate_event_chain(event_path, project_id, int(record["revision"]))
    before_version = record["schema_version"]
    migrated["revision"] = int(record["revision"]) + 1
    migrated["updated_at"] = utc_now()
    migrated["project_id"] = project_id
    try:
        validate_project_record(migrated)
    except Exception as error:
        raise MigrationValidationError(f"Migrated Project schema invalid: {project_id}") from error
    _write_json(record_path, migrated)
    digest_path.write_text(value_sha256(migrated) + "\n", encoding="ascii")
    previous_hash = events[-1].get("event_hash") if events else None
    event: dict[str, Any] = {
        "event_id": f"migration-{project_id}-{migrated['revision']}",
        "project_id": project_id,
        "base_revision": record["revision"],
        "new_revision": migrated["revision"],
        "actor": "workplace-migration",
        "platform": "fractal",
        "action": "migrate-project-schema",
        "changes": [
            {
                "migration": migration,
                "from_version": before_version,
                "to_version": CURRENT_PROJECT_SCHEMA_VERSION,
            }
            for migration in applied
        ],
        "occurred_at": utc_now(),
        "previous_event_hash": previous_hash,
    }
    event["event_hash"] = value_sha256(event)
    with event_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _read_event_values(path: Path, project_id: str) -> list[dict[str, Any]]:
    try:
        values = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationValidationError(f"Event journal is unreadable: {project_id}") from error
    if not all(isinstance(value, dict) for value in values):
        raise MigrationValidationError(f"Event journal contains a non-object: {project_id}")
    return values


def _migrate_projects(
    stage: Path,
    operations: list[str],
    status_map: dict[str, str],
    event_root: Path | None = None,
) -> None:
    _migrate_project_locks(stage, operations)
    _copy_event_journals(stage, event_root)
    sources = _project_sources(stage)
    canonical_root = stage / "projects"
    canonical_root.mkdir(parents=True, exist_ok=True)
    migrated_schema_projects: list[str] = []
    if sources:
        operations.append("projects-active-completed-to-canonical")
        for bucket, source in sources:
            project_id = source.name
            destination = canonical_root / project_id
            if destination.exists() and destination != source:
                if not _tree_bytes_equal(source, destination):
                    raise MigrationPreflightError(
                        f"Project collision with different bytes: {project_id}"
                    )
                shutil.rmtree(source)
            elif destination == source:
                continue
            else:
                shutil.copytree(source, destination, symlinks=True)
                shutil.rmtree(source)
            record_path = destination / "record.json"
            if not record_path.is_file() or record_path.is_symlink():
                raise MigrationValidationError(f"Project record is missing: {project_id}")
            record = _read_json(record_path, label=f"Project {project_id}")
            if record.get("project_id") != project_id:
                raise MigrationValidationError(f"Project id/path mismatch: {project_id}")
            record_status = record.get("status")
            derived = (
                "completed"
                if bucket == "completed"
                else (str(record_status) if record_status else "in_progress")
            )
            status_map[project_id] = derived
            if _migrate_project_schema(stage, project_id, destination, event_root):
                migrated_schema_projects.append(project_id)
    # A partially migrated tree may already have direct canonical Project
    # directories.  Upgrade those too, while keeping schema 1.2 a no-op.
    for directory in sorted(canonical_root.iterdir(), key=lambda item: item.name):
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or directory.name in {"active", "completed"}
            or not _is_project_id(directory.name)
        ):
            continue
        if _migrate_project_schema(stage, directory.name, directory, event_root):
            migrated_schema_projects.append(directory.name)
    if migrated_schema_projects:
        operations.append("legacy-project-schema-upgrade")
    for bucket in ("active", "completed"):
        directory = stage / "projects" / bucket
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def _context_path(source: str, catalogue: Path) -> Path:
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = catalogue.parent / candidate
    return candidate.absolute()


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _context_locator(
    source: Mapping[str, Any],
    catalogue: Path,
    stage: Path,
    context_roots: Mapping[str, Path],
) -> str:
    root_id = str(source.get("root_id", "source"))
    raw_path = source.get("path")
    if raw_path is None:
        locator = source.get("locator")
        if not isinstance(locator, str):
            raise MigrationPreflightError(f"Context source has no path or locator: {root_id}")
        return locator
    candidate = _context_path(str(raw_path), catalogue)
    # A copied legacy tree often contains the original absolute Workplace path.
    # Known source ids are mapped by meaning, and a final path component match
    # handles synthetic copies with a different temporary parent.
    if root_id == "project-records":
        # Canonical Project records are status-neutral. Active/completed are
        # derived views, not storage buckets, so retrieval must cover the
        # canonical collection rather than retain the legacy active path.
        return "workplace://projects"
    if root_id == "current-policy":
        return "workplace://policies/current.json"
    if root_id == "current-profile":
        return "workplace://profile/current.json"
    if root_id == "profile":
        return "workplace://profile/current.json"
    if _inside(candidate, stage):
        relative = candidate.resolve(strict=False).relative_to(stage.resolve(strict=False))
        return f"workplace://{relative.as_posix()}" if relative.parts else "workplace://"
    for marker in ("Fractal Workspace", stage.name):
        parts = candidate.parts
        if marker in parts:
            index = parts.index(marker)
            relative = PurePosixPath(*parts[index + 1 :])
            return f"workplace://{relative.as_posix()}" if relative.parts else "workplace://"
    for root_name, root in context_roots.items():
        if root.is_file() and candidate.resolve(strict=False) == root.resolve(strict=False):
            # Context rebuild roots are directories. Preserve an explicitly
            # mapped file as a child of its named local root so callers can
            # map that root to the file's parent without losing the filename.
            return f"local://{root_name}/{root.name}"
        if _inside(candidate, root):
            relative = candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
            # The context catalogue schema accepts ``local://`` as the
            # portable external-root namespace.  The mapped root id remains
            # explicit so a rebuild can resolve it without guessing.
            return (
                f"local://{root_name}/{relative.as_posix()}"
                if relative.parts
                else f"local://{root_name}"
            )
    raise MigrationPreflightError(
        f"Context source is external and has no explicit context_roots mapping: {root_id}"
    )


def _migrate_context(
    stage: Path,
    operations: list[str],
    context_roots: Mapping[str, Path],
) -> None:
    destination = stage / CONTEXT_CANONICAL_RELATIVE
    candidates = [stage / CONTEXT_LEGACY_RELATIVE, stage / "context-catalogue.json"]
    source = next((item for item in candidates if item.is_file()), None)
    if source is None:
        if destination.is_file():
            # Validate an already canonical catalogue even when no legacy input
            # remains.  This is important for the idempotent second run.
            load_context_catalogue(destination)
        return
    legacy_bytes = source.read_bytes()
    legacy = _read_json(source, label="Legacy context catalogue")
    sources = legacy.get("sources")
    if not isinstance(sources, list):
        raise MigrationPreflightError("Legacy context catalogue sources must be a list")
    canonical_sources: list[dict[str, Any]] = []
    legacy_ids: list[str] = []
    for item in sources:
        if not isinstance(item, Mapping):
            raise MigrationPreflightError("Legacy context source must be an object")
        migrated = dict(item)
        migrated.pop("path", None)
        migrated.pop("legacy", None)
        if "path" in item:
            legacy_ids.append(str(item.get("root_id", "source")))
            migrated["locator"] = _context_locator(item, source, stage, context_roots)
        if "locator" not in migrated:
            raise MigrationPreflightError(
                f"Context source has no logical locator: {item.get('root_id')}"
            )
        canonical_sources.append(migrated)
    canonical: dict[str, Any] = {
        key: value
        for key, value in legacy.items()
        if key not in {"sources", "migration"}
    }
    canonical["record_type"] = "context-catalogue"
    canonical["record_version"] = 1
    canonical["sources"] = canonical_sources
    canonical["migration"] = {
        "source_record_type": str(legacy.get("record_type", "context-catalogue")),
        "source_record_version": int(legacy.get("record_version", 1)),
        "source_sha256": _sha256_bytes(legacy_bytes),
        "legacy_path_sources": sorted(set(legacy_ids)),
        "destination": "context/sources.json",
        "read_compatibility": True,
    }
    schema_path = Path(__file__).parent / "schemas" / "context-catalogue.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(canonical)
    if destination.exists():
        existing = load_context_catalogue(destination)
        if existing != canonical:
            raise MigrationPreflightError(f"Canonical context destination differs: {destination}")
    else:
        _write_json(destination, canonical)
    _history_copy(stage, "context/context-catalogue.json", legacy_bytes)
    if source != destination:
        source.unlink()
    operations.append("context-catalogue-to-logical-sources")


def _migrate_policy(stage: Path, operations: list[str]) -> None:
    policy_path = stage / POLICY_RELATIVE
    authority_path = stage / AUTHORITY_RELATIVE
    if not policy_path.is_file():
        return
    source_bytes = policy_path.read_bytes()
    source = _read_json(policy_path, label="Workplace policy")
    if source.get("record_type") == "user-policy" and authority_path.is_file():
        return
    authorities = source.get("authorities", {})
    lifecycle = source.get("lifecycle")
    if not isinstance(authorities, Mapping):
        authorities = {}
    bindings: dict[str, Any] = {
        "$schema": source.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "record_type": "authority-bindings",
        "record_version": 1,
        "policy_id": str(source.get("policy_id", "primary-authority")),
        "bindings": copy.deepcopy(dict(authorities)),
        # ``authorities`` is a readable compatibility spelling; both maps are
        # intentionally equal and do not contain lifecycle invariants.
        "authorities": copy.deepcopy(dict(authorities)),
        "provenance": {
            "source_record_type": str(source.get("record_type", "authority-policy")),
            "source_sha256": _sha256_bytes(source_bytes),
        },
    }
    user_policy = {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key not in {"authorities", "lifecycle"}
    }
    user_policy["record_type"] = "user-policy"
    user_policy["record_version"] = 1
    user_policy["authority_bindings"] = "workplace://authority/bindings.json"
    user_policy["provenance"] = {
        "source_record_type": str(source.get("record_type", "authority-policy")),
        "source_sha256": _sha256_bytes(source_bytes),
        "lifecycle_moved_to_history": lifecycle is not None,
    }
    if authority_path.exists():
        existing = _read_json(authority_path, label="Authority bindings")
        if existing != bindings:
            raise MigrationPreflightError(f"Authority bindings collision: {authority_path}")
    else:
        _write_json(authority_path, bindings)
    if policy_path.exists() and _read_json(policy_path, label="User policy") != user_policy:
        _history_copy(stage, "policy/current-authority-policy.json", source_bytes)
        _write_json(policy_path, user_policy)
    else:
        _write_json(policy_path, user_policy)
    _history_copy(stage, "policy/current-authority-policy.json", source_bytes)
    operations.append("mixed-policy-split-authority-and-user-policy")


def _version_from(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    candidate = value.get("version") or value.get("system_version")
    if not isinstance(candidate, str) or not _VERSION_RE.fullmatch(candidate):
        return None
    return candidate


def _candidate_status(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("candidate_status", "status", "activation_status", "state"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate.lower().replace(" ", "-")
    return None


def _version_payload(
    version: str,
    status: str,
    raw: Mapping[str, Any] | None,
    raw_bytes: bytes | None,
    *,
    source: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "record_type": "system-version",
        "record_version": 1,
        "version": _version_id(version),
        "status": status,
    }
    if raw_bytes is not None:
        payload["source_sha256"] = _sha256_bytes(raw_bytes)
    if source is not None:
        payload["source"] = source
    if raw is not None:
        payload["legacy"] = {"record": copy.deepcopy(dict(raw))}
    if provenance is not None:
        payload.setdefault("legacy", {})["provenance"] = copy.deepcopy(dict(provenance))
    _validate_version_record(payload)
    return payload


def _pointer_version(root: Path, kind: str) -> str | None:
    filename = "active-version.json" if kind == "active" else "candidate-version.json"
    path = root / "system" / filename
    if not path.is_file():
        return None
    value = _read_json(path, label=f"{kind} System Version record")
    direct = _version_from(value)
    if direct:
        return direct
    if value.get("record_type") == "system-version-pointer":
        uri = value.get("record_uri")
        if isinstance(uri, str):
            return (
                Path(uri.split("/")[-1]).stem
                if uri.endswith(".json")
                else uri.rsplit("/", 1)[-1]
            )
    return None


def _copy_external_version_history(stage: Path, runtime_root: Path | None) -> None:
    if runtime_root is None:
        return
    source = runtime_root / "system-version" / "versions"
    if not source.is_dir():
        return
    destination = stage / "system" / "history" / "runtime-versions"
    for path in _iter_files(source, include_git=True):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise MigrationPreflightError(f"Runtime version history collision: {target}")
        if not target.exists():
            shutil.copy2(path, target)


def _migrate_versions(
    stage: Path,
    operations: list[str],
    runtime_root: Path | None,
    verified_active_state: Mapping[str, Any] | None = None,
) -> None:
    workspace_path = stage / "workspace.json"
    workspace = (
        _read_json(workspace_path, label="Legacy Workplace") if workspace_path.is_file() else {}
    )
    workspace_system = (
        workspace.get("system")
        if isinstance(workspace.get("system"), Mapping)
        else {}
    )
    active_path = stage / "system" / "active-version.json"
    candidate_path = stage / "system" / "candidate-version.json"
    active_raw = (
        _read_json(active_path, label="Active System Version")
        if active_path.is_file()
        else None
    )
    candidate_raw = (
        _read_json(candidate_path, label="Candidate System Version")
        if candidate_path.is_file()
        else None
    )
    active_bytes = active_path.read_bytes() if active_path.is_file() else None
    candidate_bytes = candidate_path.read_bytes() if candidate_path.is_file() else None
    # On a second run these files are already canonical logical pointers.  Do
    # not treat the pointer bytes as a new legacy record or create a provenance
    # collision against the first run's historical source copy.
    if (
        isinstance(active_raw, Mapping)
        and active_raw.get("record_type") == "system-version-pointer"
    ):
        active_bytes = None
    if (
        isinstance(candidate_raw, Mapping)
        and candidate_raw.get("record_type") == "system-version-pointer"
    ):
        candidate_bytes = None
    legacy_active = (
        _version_from(active_raw)
        or _pointer_version(stage, "active")
        or workspace_system.get("active_version")
    )
    legacy_candidate = (
        _version_from(candidate_raw)
        or _pointer_version(stage, "candidate")
        or workspace_system.get("candidate_version")
    )
    if legacy_active is not None and (
        not isinstance(legacy_active, str) or not _VERSION_RE.fullmatch(legacy_active)
    ):
        raise MigrationPreflightError(f"Invalid active System Version: {legacy_active}")
    if legacy_candidate is not None and (
        not isinstance(legacy_candidate, str) or not _VERSION_RE.fullmatch(legacy_candidate)
    ):
        raise MigrationPreflightError(f"Invalid candidate System Version: {legacy_candidate}")
    active = (
        str(verified_active_state["version"])
        if verified_active_state is not None
        else legacy_active
    )
    candidate = legacy_candidate
    _copy_external_version_history(stage, runtime_root)
    versions_dir = stage / "system" / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    # Existing canonical version records are retained verbatim.  Their bytes
    # are the historical source of truth, so do not rewrite them here.
    existing_versions: set[str] = set()
    for path in sorted(versions_dir.glob("*.json")):
        value = _read_json(path, label="System Version history")
        version = _version_from(value)
        if version:
            existing_versions.add(version)
    candidate_state = _candidate_status(candidate_raw)
    reconciled = (
        verified_active_state is not None
        and legacy_active is not None
        and legacy_active != active
    )

    def ensure_version(
        version: str,
        status: str,
        raw: Mapping[str, Any] | None,
        raw_bytes: bytes | None,
        *,
        source: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        if version not in existing_versions:
            payload = _version_payload(
                version,
                status,
                raw,
                raw_bytes,
                source=source,
                provenance=provenance,
            )
            _write_json(versions_dir / f"{version}.json", payload)
            existing_versions.add(version)
            return
        existing = _read_json(
            versions_dir / f"{version}.json", label=f"System Version record {version}"
        )
        changed = False
        if status == "active" and existing.get("status") != "active":
            existing["status"] = "active"
            changed = True
        if status == "previously-active" and existing.get("status") == "candidate":
            existing["status"] = "previously-active"
            changed = True
        if changed:
            _write_json(versions_dir / f"{version}.json", existing)

    if reconciled:
        # The old Workplace records are evidence of what it believed, not a
        # second activation.  Preserve their exact bytes and a compact
        # reconciliation receipt before replacing the active projection.
        old_provenance = {
            "kind": "stale-workplace-active-state",
            "verified_active_version": active,
            "reconciled": True,
        }
        if legacy_active is not None:
            ensure_version(
                legacy_active,
                "previously-active",
                active_raw,
                active_bytes,
                source="legacy-workplace/system/active-version.json",
                provenance=old_provenance,
            )
        if active_bytes is not None:
            _history_copy(stage, "legacy-version-pointers/active-version.json", active_bytes)
        if candidate_bytes is not None:
            _history_copy(stage, "legacy-version-pointers/candidate-version.json", candidate_bytes)
        _write_json(
            stage / "system" / "history" / "verified-active-state.json",
            {
                "record_type": "verified-active-state-reconciliation",
                "record_version": 1,
                "verified_version": active,
                "activated_at": verified_active_state["activated_at"],
                "activated_by": verified_active_state["activated_by"],
                "manifest_sha256": verified_active_state["manifest_sha256"],
                "pointer_sha256": verified_active_state["pointer_sha256"],
                "legacy_active_version": legacy_active,
                "legacy_candidate_version": legacy_candidate,
            },
        )

    if active:
        if verified_active_state is not None:
            verified_pointer = verified_active_state["pointer"]
            ensure_version(
                active,
                "active",
                verified_pointer,
                None,
                source="verified-active-state/system-version/active.json",
                provenance={
                    "kind": "verified-active-state",
                    "activated_at": verified_active_state["activated_at"],
                    "activated_by": verified_active_state["activated_by"],
                    "manifest_sha256": verified_active_state["manifest_sha256"],
                },
            )
        else:
            ensure_version(active, "active", active_raw, active_bytes)
    if candidate is not None and candidate != active:
        candidate_is_resolved = candidate_state in _RESOLVED_CANDIDATE_STATUSES
        candidate_status = "previously-active" if candidate_is_resolved else "candidate"
        ensure_version(candidate, candidate_status, candidate_raw, candidate_bytes)
    if not reconciled:
        if active_bytes is not None:
            _history_copy(stage, "legacy-version-pointers/active-version.json", active_bytes)
        if candidate_bytes is not None:
            _history_copy(stage, "legacy-version-pointers/candidate-version.json", candidate_bytes)
    if active:
        pointer = _pointer_record("active", f"workplace://system/versions/{active}.json")
        _write_json(active_path, pointer)
    elif active_path.exists():
        active_path.unlink()
    # A matching activated candidate is historical, not an unresolved pointer.
    # A matching unresolved candidate is also omitted because active + candidate
    # would be an impossible state.  A distinct unresolved candidate remains
    # addressable through the canonical candidate pointer.
    if candidate and candidate != active and candidate_state not in _RESOLVED_CANDIDATE_STATUSES:
        pointer = _pointer_record("candidate", f"workplace://system/versions/{candidate}.json")
        _write_json(candidate_path, pointer)
    elif candidate_path.exists():
        candidate_path.unlink()
    if active or candidate:
        operations.append("active-candidate-version-normalisation")
    if reconciled:
        operations.append("verified-active-state-reconciliation")


def _status_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, Mapping):
        identifier = value.get("id")
        if isinstance(identifier, str) and (
            "status" in value
            or "execution" in value
            or "primary_element" in value
            or "supporting_elements" in value
        ):
            yield dict(value)
        for child in value.values():
            yield from _status_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _status_objects(child)


def _method_status(source: Mapping[str, Any], source_sha256: str) -> dict[str, Any]:
    methods: dict[str, dict[str, Any]] = {}
    for item in _status_objects(source):
        identifier = str(item["id"])
        status: dict[str, Any] = {"id": identifier}
        for key in (
            "status",
            "execution",
            "primary_element",
            "supporting_elements",
            "open_design",
            "evidence_ids",
        ):
            if key in item:
                status[key] = copy.deepcopy(item[key])
        status["provenance"] = {"source_sha256": source_sha256}
        existing = methods.get(identifier)
        if existing is None:
            methods[identifier] = status
        else:
            # Keep the first stable ordering but merge non-conflicting status
            # fields.  Conflicting definitions are retained as provenance, not
            # allowed to become competing Workplace authority.
            for key, value in status.items():
                if key not in existing:
                    existing[key] = value
                elif existing[key] != value and key != "provenance":
                    conflicts = existing.setdefault("definition_conflicts", [])
                    if value not in conflicts:
                        conflicts.append(value)
    return {
        "record_type": "workplace-method-status",
        "record_version": 1,
        "methods": [methods[key] for key in sorted(methods)],
        "source": {
            "record_type": str(source.get("record_type", "method-registry")),
            "source_sha256": source_sha256,
        },
    }


def _migrate_methods(stage: Path, operations: list[str]) -> None:
    source_path = stage / "system" / "method-registry.json"
    destination = stage / METHOD_STATUS_RELATIVE
    if not source_path.is_file():
        if destination.is_file():
            value = _read_json(destination, label="Workplace method status")
            ids = [item.get("id") for item in value.get("methods", []) if isinstance(item, Mapping)]
            if len(ids) != len(set(ids)):
                raise MigrationValidationError("Duplicate Workplace method status ids")
        return
    raw = source_path.read_bytes()
    source = _read_json(source_path, label="Legacy method registry")
    status = _method_status(source, _sha256_bytes(raw))
    if destination.exists() and _read_json(destination, label="Workplace method status") != status:
        raise MigrationPreflightError(f"Method status collision: {destination}")
    if not destination.exists():
        _write_json(destination, status)
    _history_copy(stage, "method-registry.json", raw)
    source_path.unlink()
    operations.append("duplicate-method-definitions-to-workplace-status")


def _strip_local_endpoints(value: Any, path: str, endpoints: list[dict[str, Any]]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            if key_text in {
                "base_url",
                "endpoint",
                "socket_path",
                "socket",
                "discovered_endpoint",
            } and isinstance(child, str):
                endpoints.append({"path": path + "/" + key_text, "endpoint": child})
                continue
            result[key_text] = _strip_local_endpoints(child, path + "/" + key_text, endpoints)
        return result
    if isinstance(value, list):
        return [
            _strip_local_endpoints(child, f"{path}/{index}", endpoints)
            for index, child in enumerate(value)
        ]
    return copy.deepcopy(value)


def _migrate_adapters(stage: Path, operations: list[str]) -> None:
    adapters_root = stage / "adapters"
    if not adapters_root.is_dir():
        return
    source_files = [
        path
        for path in sorted(adapters_root.rglob("*.json"))
        if path.relative_to(stage).as_posix() not in {ADAPTER_PREFERENCES_RELATIVE.as_posix()}
        and not path.is_symlink()
    ]
    if not source_files:
        return
    preferences: dict[str, Any] = {
        "record_type": "adapter-preferences",
        "record_version": 1,
        "adapters": {},
    }
    endpoints: list[dict[str, Any]] = []
    for path in source_files:
        raw = path.read_bytes()
        value = _read_json(path, label="Adapter route")
        local: list[dict[str, Any]] = []
        cleaned = _strip_local_endpoints(value, path.relative_to(stage).as_posix(), local)
        key = path.relative_to(adapters_root).with_suffix("").as_posix()
        preferences["adapters"][key] = cleaned
        for endpoint in local:
            endpoint["adapter"] = key
            endpoint["source_sha256"] = _sha256_bytes(raw)
            endpoints.append(endpoint)
        _history_copy(stage, Path("adapters") / path.relative_to(adapters_root), raw)
        path.unlink()
    destination = stage / ADAPTER_PREFERENCES_RELATIVE
    if destination.exists() and _read_json(destination, label="Adapter preferences") != preferences:
        raise MigrationPreflightError(f"Adapter preferences collision: {destination}")
    _write_json(destination, preferences)
    if endpoints:
        endpoint_path = stage / RUNTIME_RELATIVE / "adapters" / "endpoints.json"
        _write_json(
            endpoint_path,
            {
                "record_type": "discovered-local-endpoints",
                "record_version": 1,
                "endpoints": sorted(
                    endpoints, key=lambda item: (item["adapter"], item["path"])
                ),
            },
        )
    operations.append("adapter-preferences-separated-from-local-endpoints")


def _is_legitimate_component_record(relative: str, value: Any = None) -> bool:
    parts = PurePosixPath(relative).parts
    if len(parts) < 3 or parts[0:2] != ("system", "components"):
        return False
    stem = PurePosixPath(parts[-1]).stem.lower()
    record_type = value.get("record_type") if isinstance(value, Mapping) else None
    if stem in _LEGITIMATE_COMPONENT_STEMS:
        return True
    if any(
        stem.endswith(f"-{suffix}")
        for suffix in ("registry", "selection", "status", "evidence")
    ):
        return True
    if not isinstance(record_type, str):
        return False
    marker = record_type.lower().replace("_", "-")
    return not (
        marker in _RAW_RECORD_MARKERS
        or any(
            token in marker
            for token in ("raw-session", "raw-tool", "socket-inventory", "tool-inventory")
        )
    )


def _is_raw_relative(relative: str, value: Any = None) -> bool:
    parts = PurePosixPath(relative).parts
    if bool(parts and parts[0] in _RAW_TOP_LEVEL_DIRS):
        return True
    if any(relative.startswith(prefix) for prefix in _RAW_RELATIVE_PREFIXES):
        return True
    filename = PurePosixPath(relative).name
    if any(pattern.search(filename) for pattern in _RAW_FILENAME_PATTERNS):
        return True
    record_type = value.get("record_type") if isinstance(value, Mapping) else None
    if isinstance(record_type, str):
        marker = record_type.lower().replace("_", "-")
        if marker in _RAW_RECORD_MARKERS or any(
            token in marker
            for token in ("raw-session", "raw-tool", "socket-inventory", "tool-inventory")
        ):
            return True
    if isinstance(value, Mapping) and not _is_legitimate_component_record(relative, value):
        keys = {str(key).lower() for key in value}
        if keys.intersection(_RAW_KEY_MARKERS) and (
            "system/components" in relative or parts[0] in {"discovery", "imports", "system"}
        ):
            return True
    return False


def _normalise_receipt(path: Path, raw: bytes, value: Any, relative: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if not (
        _RECEIPT_KEYS.intersection(value)
        or "receipt" in str(value.get("record_type", "")).lower()
    ):
        return None
    summary = {
        key: copy.deepcopy(value[key])
        for key in sorted(_RECEIPT_KEYS)
        if key in value and isinstance(value[key], (str, int, float, bool, type(None), list))
    }
    for key, child in list(summary.items()):
        if isinstance(child, str) and _privacy_kinds(child, key=key):
            summary[key] = _portable_privacy_value(child, key=key)
    summary["source_path"] = relative
    summary["source_sha256"] = _sha256_bytes(raw)
    return summary


def _privacy_kinds(value: str, *, key: str | None = None) -> tuple[str, ...]:
    if value.startswith(("historical://", "<redacted-")):
        return ()
    kinds: list[str] = []
    key_marker = re.sub(r"[^a-z0-9_]", "_", key.lower()) if key else ""
    if key_marker in _SECRET_KEY_MARKERS or key_marker.endswith(
        ("_token", "_password", "_secret", "_credential", "_api_key")
    ):
        kinds.append("credential")
    if key_marker == "socket" or "socket" in key_marker:
        kinds.append("socket")
    if _PATH_RE.search(value):
        kinds.append("path")
    if _SOCKET_RE.search(value):
        kinds.append("socket")
    for match in _IP_RE.findall(value):
        try:
            address = ipaddress.ip_address(match)
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local:
            kinds.append("private-address")
            break
    return tuple(dict.fromkeys(kinds))


def _portable_privacy_value(value: str, *, key: str | None = None) -> str:
    kinds = _privacy_kinds(value, key=key)
    if "credential" in kinds:
        return "<redacted-credential>"
    if "socket" in kinds:
        return f"historical://socket/{_portable_digest(value)}"
    if "private-address" in kinds:
        return f"historical://private-address/{_portable_digest(value)}"
    if "path" in kinds:
        return f"historical://path/{_portable_digest(value)}"
    return value


def _privacy_historical_path(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if relative.startswith(("system/history/", "system/reviews/", "imports/")):
        return True
    if relative.startswith(
        (".runtime/events/", "system/versions/", "system/decisions/", "system/legacy/")
    ):
        return True
    if relative.startswith("memory/"):
        return True
    if relative.startswith("system/adapters/"):
        return True
    if len(parts) >= 3 and parts[:2] == ("system", "components"):
        stem = PurePosixPath(parts[-1]).stem.lower()
        return stem not in {
            "registry",
            "selection",
            "status",
            "evidence",
            "discovery-policy",
            "user-surface",
        } and not any(
            stem.endswith(f"-{suffix}") for suffix in ("-registry", "-selection", "-status")
        )
    return False


def _sanitise_privacy_value(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitise_privacy_value(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitise_privacy_value(child, key=key) for child in value]
    if isinstance(value, str):
        return _portable_privacy_value(value, key=key)
    return copy.deepcopy(value)


def _sanitise_canonical_privacy(stage: Path, operations: list[str]) -> None:
    """Remove machine-local values from active records while preserving history."""

    changed = False
    for path in _iter_files(stage):
        relative = path.relative_to(stage).as_posix()
        if relative.startswith((".runtime/", "system/history/")) or _privacy_historical_path(
            relative
        ):
            continue
        if path.name == ".gitignore":
            # Ignore patterns may intentionally name sockets, local caches,
            # credentials, or absolute-path shapes. They are controls, not
            # retained values, and rewriting the whole file would disable the
            # privacy boundary it enforces.
            continue
        if relative.startswith("projects/") and path.name in {"record.json", "record.sha256"}:
            # Project evidence/provenance source strings are immutable and their
            # sidecar hash must never be rewritten as a privacy convenience.
            continue
        raw = path.read_bytes()
        value: Any = None
        is_json = path.suffix.lower() in {".json", ".jsonl", ".ndjson"}
        if is_json:
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            cleaned = _sanitise_privacy_value(value)
            if cleaned == value:
                continue
            _history_copy(stage, Path("privacy") / relative, raw)
            _write_json(path, cleaned)
            changed = True
            continue
        # Plain-text canonical files are validated below but never rewritten
        # wholesale. A single path-like token must not collapse an entire
        # control file or human document into one redaction marker.
    if changed:
        operations.append("canonical-runtime-paths-portabilised")


def _migrate_raw_runtime(stage: Path, operations: list[str], event_root: Path | None) -> None:
    runtime = stage / RUNTIME_RELATIVE
    candidates: list[Path] = []
    for path in _iter_files(stage):
        relative = path.relative_to(stage).as_posix()
        if path.name == ".gitignore":
            continue
        if relative.startswith(".runtime/") or relative.startswith("system/history/"):
            continue
        value: Any = None
        if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                value = None
        if _is_raw_relative(relative, value):
            # The root ``runtime/events`` stream is canonical local evidence,
            # not an opaque dump: it receives the explicit event-root treatment.
            if relative.startswith("runtime/events/"):
                continue
            candidates.append(path)
    if not candidates and event_root is None and not runtime.exists():
        return
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    receipts: list[dict[str, Any]] = []
    for path in candidates:
        relative = path.relative_to(stage).as_posix()
        raw = path.read_bytes()
        value: Any = None
        if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = None
        receipt = _normalise_receipt(path, raw, value, relative)
        if receipt:
            receipts.append(receipt)
        target = runtime / "raw" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise MigrationPreflightError(f"Raw runtime collision: {target}")
        if not target.exists():
            shutil.copy2(path, target)
        path.unlink()
    # Remove now-empty legacy containers while leaving any non-raw sibling
    # content untouched.  The bytes themselves live below ``.runtime/raw``.
    raw_directories = sorted(
        {
            path.parent
            for path in candidates
            if path.parent.exists()
        },
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for directory in raw_directories:
        current = directory
        while current != stage:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
    if event_root is not None:
        source = event_root / "events" if (event_root / "events").is_dir() else event_root
        if source.is_dir():
            destination = runtime / "events"
            destination.mkdir(parents=True, exist_ok=True)
            for path in _iter_files(source, include_git=True):
                relative = path.relative_to(source)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = path.read_bytes()
                if target.exists() and not _event_journal_compatible(raw, target.read_bytes()):
                    raise MigrationPreflightError(f"Event journal collision: {target}")
                if not target.exists():
                    target.write_bytes(raw)
    if receipts:
        receipt_path = stage / "evidence" / "runtime-receipts.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = {
            "record_type": "runtime-receipts",
            "record_version": 1,
            "receipts": sorted(receipts, key=lambda item: item["source_path"]),
        }
        if receipt_path.exists() and _read_json(receipt_path, label="Runtime receipts") != encoded:
            raise MigrationPreflightError(f"Runtime receipt collision: {receipt_path}")
        _write_json(receipt_path, encoded)
    if candidates or event_root is not None:
        operations.append("raw-live-session-tool-state-to-ignored-runtime")


def _validate_event_chain(path: Path, project_id: str, revision: int) -> dict[str, Any]:
    if not path.is_file():
        return {"project_id": project_id, "event_chain_present": False, "event_chain_valid": None}
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise MigrationValidationError(f"Event journal item is not an object: {path}")
            events.append(value)
    previous: str | None = None
    for expected, event in enumerate(events):
        stored = event.get("event_hash")
        body = dict(event)
        body.pop("event_hash", None)
        if event.get("previous_event_hash") != previous:
            raise MigrationValidationError(f"Event chain link mismatch: {project_id}")
        if event.get("new_revision") != expected:
            raise MigrationValidationError(f"Event revision gap: {project_id}")
        if not isinstance(stored, str) or value_sha256(body) != stored:
            raise MigrationValidationError(f"Event digest mismatch: {project_id}")
        previous = stored
    if not events or events[-1].get("new_revision") != revision:
        raise MigrationValidationError(f"Event chain does not reach Project revision: {project_id}")
    return {
        "project_id": project_id,
        "event_count": len(events),
        "record_revision": revision,
        "event_chain_present": True,
        "event_chain_valid": True,
    }


def _scan_privacy_value(
    value: Any,
    path: str,
    findings: list[dict[str, str]],
    *,
    project_record: bool = False,
    allow_project_evidence_source: bool = False,
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}/{key_text}"
            allow_source = (
                project_record
                and key_text == "source"
                and ("/evidence/" in child_path or "/provenance/" in child_path)
            )
            if allow_source and allow_project_evidence_source:
                continue
            if key_text.lower() in _SECRET_KEY_MARKERS and child != "<redacted-credential>":
                findings.append({"path": child_path, "kind": "credential-key"})
            if (
                "socket" in key_text.lower()
                and not (isinstance(child, str) and child.startswith("historical://socket/"))
            ):
                findings.append({"path": child_path, "kind": "socket-key"})
            _scan_privacy_value(
                child,
                child_path,
                findings,
                project_record=project_record,
                allow_project_evidence_source=allow_project_evidence_source,
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_privacy_value(
                child,
                f"{path}/{index}",
                findings,
                project_record=project_record,
                allow_project_evidence_source=allow_project_evidence_source,
            )
        return
    if isinstance(value, str):
        for kind in _privacy_kinds(value):
            findings.append({"path": path, "kind": kind})


def _validate_canonical_privacy(stage: Path) -> dict[str, Any]:
    """Scan every canonical file, separating history/runtime from dependencies."""

    findings: list[dict[str, str]] = []
    scanned_files = 0
    historical_files: list[str] = []
    ephemeral_files: list[str] = []
    for path in _iter_files(stage):
        relative = path.relative_to(stage).as_posix()
        if path.name == ".gitignore":
            # Pattern names are privacy controls, not retained secret, socket,
            # or machine-path values.
            continue
        if relative.startswith(".runtime/raw/") or relative.startswith(".runtime/adapters/"):
            ephemeral_files.append(relative)
            continue
        if relative.startswith(".runtime/events/"):
            historical_files.append(relative)
            continue
        if _privacy_historical_path(relative):
            historical_files.append(relative)
            continue
        scanned_files += 1
        value: Any
        if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # A non-JSON active file is still checked as text; a binary
                # file cannot be a portable active record and is reported.
                try:
                    value = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    findings.append({"path": relative, "kind": "unreadable-active-file"})
                    continue
        else:
            try:
                value = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        _scan_privacy_value(
            value,
            relative,
            findings,
            project_record=relative.startswith("projects/") and path.name == "record.json",
            allow_project_evidence_source=True,
        )
    # Keep deterministic output and avoid repeating the same key/path finding.
    unique_findings = sorted(
        { (item["path"], item["kind"]): item for item in findings }.values(),
        key=lambda item: (item["path"], item["kind"]),
    )
    return {
        "valid": not unique_findings,
        "scanned_file_count": scanned_files,
        "historical_evidence_file_count": len(historical_files),
        "historical_evidence_paths": historical_files,
        "ephemeral_runtime_file_count": len(ephemeral_files),
        "ephemeral_runtime_paths": ephemeral_files,
        "findings": unique_findings,
    }


def _validate_candidate(
    stage: Path,
    *,
    runtime_root: Path | None = None,
    expected_active_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workplace_path = stage / "workplace.json"
    if not workplace_path.is_file():
        raise MigrationValidationError("Candidate workplace.json is missing")
    workplace_value = _read_json(workplace_path, label="Canonical Workplace")
    try:
        validate_workplace(workplace_value)
    except Exception as error:
        raise MigrationValidationError("Canonical Workplace schema validation failed") from error
    workplace = Workplace(root=stage, record=workplace_value)
    workplace.validate_version_state()
    active_version = _pointer_version(stage, "active")
    active_state_valid = True
    if expected_active_state is not None:
        active_state_valid = active_version == expected_active_state.get("version")
        if not active_state_valid:
            raise MigrationValidationError(
                "Candidate active pointer does not match the verified active System Version"
            )
    projects: list[dict[str, Any]] = []
    event_status: list[dict[str, Any]] = []
    project_root = stage / "projects"
    if project_root.is_dir():
        for directory in sorted(project_root.iterdir(), key=lambda item: item.name):
            if (
                not directory.is_dir()
                or directory.is_symlink()
                or not _is_project_id(directory.name)
            ):
                continue
            record_path = directory / "record.json"
            digest_path = directory / "record.sha256"
            if not record_path.is_file() or not digest_path.is_file():
                raise MigrationValidationError(f"Project record/sidecar missing: {directory.name}")
            value = _read_json(record_path, label=f"Project {directory.name}")
            try:
                validate_project_record(value)
            except Exception as error:
                raise MigrationValidationError(
                    f"Project schema invalid: {directory.name}"
                ) from error
            expected = digest_path.read_text(encoding="ascii").strip()
            actual = value_sha256(value)
            if expected != actual:
                raise MigrationValidationError(f"Project sidecar mismatch: {directory.name}")
            projects.append(
                {
                    "project_id": directory.name,
                    "status": value.get("status"),
                    "value_sha256": actual,
                }
            )
            event_status.append(
                _validate_event_chain(
                    stage / ".runtime" / "events" / f"{directory.name}.jsonl",
                    directory.name,
                    int(value["revision"]),
                )
            )
    context_path = stage / CONTEXT_CANONICAL_RELATIVE
    context_valid = None
    if context_path.is_file():
        context_value = load_context_catalogue(context_path)
        context_valid = all(
            "path" not in item and isinstance(item.get("locator"), str)
            for item in context_value["sources"]
        )
        if not context_valid:
            raise MigrationValidationError(
                "Canonical context catalogue still contains a path source"
            )
    policy_path = stage / POLICY_RELATIVE
    authority_path = stage / AUTHORITY_RELATIVE
    policy_valid = True
    if policy_path.is_file():
        policy = _read_json(policy_path, label="User policy")
        policy_valid = (
            policy.get("record_type") == "user-policy"
            and "lifecycle" not in policy
            and "authorities" not in policy
        )
    if authority_path.is_file():
        authority = _read_json(authority_path, label="Authority bindings")
        policy_valid = policy_valid and authority.get("record_type") == "authority-bindings"
    method_valid = True
    method_path = stage / METHOD_STATUS_RELATIVE
    if method_path.is_file():
        methods = _read_json(method_path, label="Workplace method status").get("methods", [])
        ids = [item.get("id") for item in methods if isinstance(item, Mapping)]
        method_valid = len(ids) == len(set(ids))
        if not method_valid:
            raise MigrationValidationError("Duplicate Workplace method status ids")
    preferences_valid = True
    preferences_path = stage / ADAPTER_PREFERENCES_RELATIVE
    if preferences_path.is_file():
        preferences = _read_json(preferences_path, label="Adapter preferences")
        serialised = json.dumps(preferences, sort_keys=True)
        preferences_valid = "base_url" not in serialised and "discovered_endpoint" not in serialised
        if not preferences_valid:
            raise MigrationValidationError("Adapter preferences contain a local endpoint")
    privacy = _validate_canonical_privacy(stage)
    if not privacy["valid"]:
        first = privacy["findings"][0]
        raise MigrationValidationError(
            f"Canonical candidate privacy failure: {first['path']} ({first['kind']})"
        )
    version_history: list[dict[str, Any]] = []
    versions_path = stage / "system" / "versions"
    if versions_path.is_dir():
        for path in sorted(versions_path.glob("*.json")):
            value = _read_json(path, label="System Version history")
            version_history.append(
                {
                    "version": value.get("version"),
                    "status": value.get("status"),
                    "source": value.get("source"),
                    "provenance_kind": (
                        value.get("legacy", {}).get("provenance", {}).get("kind")
                        if isinstance(value.get("legacy"), Mapping)
                        and isinstance(value["legacy"].get("provenance"), Mapping)
                        else None
                    ),
                }
            )
    runtime_raw_paths = [
        path.relative_to(stage).as_posix()
        for path in _iter_files(stage / RUNTIME_RELATIVE / "raw", include_git=True)
    ] if (stage / RUNTIME_RELATIVE / "raw").is_dir() else []
    return {
        "valid": True,
        "project_count": len(projects),
        "project_ids": [item["project_id"] for item in projects],
        "projects": projects,
        "event_chains": event_status,
        "context_valid": context_valid,
        "policy_valid": policy_valid,
        "method_status_valid": method_valid,
        "adapter_preferences_valid": preferences_valid,
        "runtime_ignored": (stage / RUNTIME_RELATIVE / ".gitignore").is_file(),
        "active_version": active_version,
        "active_state_valid": active_state_valid,
        "verified_active_state": (
            {
                "version": expected_active_state.get("version"),
                "activated_at": expected_active_state.get("activated_at"),
                "activated_by": expected_active_state.get("activated_by"),
                "manifest_sha256": expected_active_state.get("manifest_sha256"),
                "pointer_sha256": expected_active_state.get("pointer_sha256"),
                "live_state_verified": expected_active_state.get("live_state_verified", False),
            }
            if expected_active_state is not None
            else None
        ),
        "candidate_version": _pointer_version(stage, "candidate"),
        "version_history": version_history,
        "runtime_raw_paths": runtime_raw_paths,
        "privacy_valid": privacy["valid"],
        "canonical_privacy_valid": privacy["valid"],
        "privacy": privacy,
    }


def _plan_operations(root: Path) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    operations: list[str] = []
    warnings: list[str] = []
    if (root / "workspace.json").is_file():
        operations.append("workspace-json-to-workplace-json")
    if _project_sources(root):
        operations.append("projects-active-completed-to-canonical")
    if _project_lock_sources(root):
        operations.append("project-lock-files-to-ignored-runtime")
    if (root / CONTEXT_LEGACY_RELATIVE).is_file() or (root / "context-catalogue.json").is_file():
        operations.append("context-catalogue-to-logical-sources")
    policy = root / POLICY_RELATIVE
    if policy.is_file():
        try:
            value = _read_json(policy, label="Workplace policy")
            if "authorities" in value or "lifecycle" in value:
                operations.append("mixed-policy-split-authority-and-user-policy")
        except WorkplaceMigrationError:
            raise
    version_paths = [
        root / "system" / "active-version.json",
        root / "system" / "candidate-version.json",
    ]
    legacy_version_pointer = False
    for version_path in version_paths:
        if not version_path.is_file():
            continue
        pointer = _read_json(version_path, label="System Version pointer preflight")
        if pointer.get("record_type") != "system-version-pointer":
            legacy_version_pointer = True
            break
    if legacy_version_pointer:
        operations.append("active-candidate-version-normalisation")
    if (root / "system" / "method-registry.json").is_file():
        operations.append("duplicate-method-definitions-to-workplace-status")
    adapter_files = (
        [
            path
            for path in (root / "adapters").rglob("*.json")
            if path.relative_to(root).as_posix() != ADAPTER_PREFERENCES_RELATIVE.as_posix()
        ]
        if (root / "adapters").is_dir()
        else []
    )
    if adapter_files:
        operations.append("adapter-preferences-separated-from-local-endpoints")
    raw_files = False
    for path in _iter_files(root):
        relative = path.relative_to(root).as_posix()
        if relative.startswith((".runtime/", "system/history/")):
            continue
        value: Any = None
        if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                value = None
        if _is_raw_relative(relative, value):
            raw_files = True
            break
    if raw_files:
        operations.append("raw-live-session-tool-state-to-ignored-runtime")
    already_canonical = not operations and (root / "workplace.json").is_file()
    if already_canonical:
        warnings.append("Root already satisfies the canonical tree naming surface")
    return tuple(dict.fromkeys(operations)), tuple(warnings), already_canonical


def build_migration_plan(
    root: str | os.PathLike[str] | Path,
    *,
    include_git: bool = False,
) -> MigrationPlan:
    """Build a deterministic, non-mutating whole-tree preflight plan."""

    root_path = _path(root)
    if not root_path.is_dir():
        raise MigrationPreflightError(f"Migration root does not exist: {root_path}")
    operations, warnings, already_canonical = _plan_operations(root_path)
    inventory = inventory_workplace(root_path, include_git=include_git)
    source_sha = inventory["sha256"]
    return MigrationPlan(root_path, operations, inventory, warnings, already_canonical, source_sha)


def _copy_for_staging(source: Path, stage: Path) -> None:
    # ``stage`` is created up front so a finally block can remove it even when
    # copying fails.  ``dirs_exist_ok`` therefore matters on Python 3.12.
    shutil.copytree(source, stage, symlinks=True, dirs_exist_ok=True)


def _switch_tree(root: Path, stage: Path) -> Path:
    """Switch a verified stage and retain its backup until final read-back."""

    backup = root.with_name(f".{root.name}.migration-backup-{os.getpid()}")
    if backup.exists():
        shutil.rmtree(backup)
    os.replace(root, backup)
    try:
        os.replace(stage, root)
    except Exception as error:
        try:
            os.replace(backup, root)
        except Exception as restore_error:
            raise MigrationSwitchError(
                f"Migration switch failed and rollback failed: {restore_error}"
            ) from error
        raise MigrationSwitchError("Migration switch failed; source restored") from error
    return backup


def migrate_workplace_tree(
    root: str | os.PathLike[str] | Path,
    *,
    runtime_root: str | os.PathLike[str] | Path | None = None,
    event_root: str | os.PathLike[str] | Path | None = None,
    context_roots: Mapping[str, str | os.PathLike[str] | Path] | None = None,
    active_pointer_path: str | os.PathLike[str] | Path | None = None,
    live_state_path: str | os.PathLike[str] | Path | None = None,
    active_state_path: str | os.PathLike[str] | Path | None = None,
    active_pointer: str | os.PathLike[str] | Path | None = None,
    live_state: str | os.PathLike[str] | Path | None = None,
    failure_injector: Callable[[str], None] | None = None,
    dry_run: bool = False,
) -> MigrationResult | MigrationPlan:
    """Migrate one supplied Workplace root transactionally and idempotently."""

    root_path = _path(root)
    if not root_path.is_dir():
        raise MigrationPreflightError(f"Migration root does not exist: {root_path}")
    runtime_path = _path(runtime_root, label="runtime root") if runtime_root is not None else None
    event_path = _path(event_root, label="event root") if event_root is not None else None
    if runtime_path is not None and runtime_path == root_path:
        raise MigrationPreflightError(
            "Runtime root must be explicit and separate from Workplace root"
        )
    if event_path is not None and event_path == root_path:
        raise MigrationPreflightError(
            "Event root must be explicit and separate from Workplace root"
        )
    if active_pointer is not None:
        if active_pointer_path is not None:
            raise MigrationPreflightError("Pass active_pointer or active_pointer_path, not both")
        active_pointer_path = active_pointer
    if live_state is not None:
        if live_state_path is not None:
            raise MigrationPreflightError("Pass live_state or live_state_path, not both")
        live_state_path = live_state
    if active_state_path is not None:
        if active_pointer_path is not None or live_state_path is not None:
            raise MigrationPreflightError(
                "Pass active_state_path or active_pointer_path/live_state_path, not both"
            )
        state_candidate = _file_path(active_state_path, label="active state")
        active_state_value = _read_json(state_candidate, label="Explicit active state")
        if "system_version" in active_state_value and "state_sha256" in active_state_value:
            live_state_path = state_candidate
        else:
            active_pointer_path = state_candidate
    verified_active_state = _verify_explicit_active_state(
        active_pointer_path=active_pointer_path,
        live_state_path=live_state_path,
    )
    context_map = {
        key.rstrip(":/").lower(): _context_root_path(value, label=f"context root {key}")
        for key, value in (context_roots or {}).items()
    }
    plan = build_migration_plan(root_path)
    if dry_run:
        return plan
    before = inventory_workplace(root_path)
    parent = root_path.parent
    stage: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{root_path.name}.migration-stage-", dir=parent)
    )
    backup: Path | None = None
    status_map: dict[str, str] = {}
    operations: list[str] = []
    try:
        _copy_for_staging(root_path, stage)
        if failure_injector:
            failure_injector("staging")
        # A canonical root may be mixed with legacy records; each operation is
        # independently idempotent and therefore safe on the second pass.
        workspace_path = stage / "workspace.json"
        if workspace_path.is_file():
            raw = workspace_path.read_bytes()
            legacy = _read_json(workspace_path, label="Legacy Workplace")
            active = (
                legacy.get("system", {}).get("active_version")
                if isinstance(legacy.get("system"), Mapping)
                else None
            )
            candidate = (
                legacy.get("system", {}).get("candidate_version")
                if isinstance(legacy.get("system"), Mapping)
                else None
            )
            duplicate = isinstance(active, str) and active == candidate
            workplace = _canonical_workplace()
            workplace["migration"] = {
                "source_record_type": str(legacy.get("record_type", "fractal-workspace")),
                "source_record_version": int(legacy.get("record_version", 1)),
                "source_sha256": _sha256_bytes(raw),
                "candidate_duplicate_resolved": duplicate,
            }
            validate_workplace(workplace)
            _history_copy(stage, "legacy-workspace.json", raw)
            _write_json(stage / "workplace.json", workplace)
            workspace_path.unlink()
            operations.append("workspace-json-to-workplace-json")
        _migrate_projects(stage, operations, status_map, event_path)
        _migrate_context(stage, operations, context_map)
        _migrate_policy(stage, operations)
        _migrate_versions(stage, operations, runtime_path, verified_active_state)
        _migrate_methods(stage, operations)
        _migrate_adapters(stage, operations)
        _migrate_raw_runtime(stage, operations, event_path)
        _sanitise_canonical_privacy(stage, operations)
        if failure_injector:
            failure_injector("candidate-built")
        validation = _validate_candidate(
            stage,
            runtime_root=runtime_path,
            expected_active_state=verified_active_state,
        )
        validation["derived_project_statuses"] = status_map
        if failure_injector:
            failure_injector("candidate-validated")
        after_candidate = inventory_workplace(stage)
        changed = before["sha256"] != after_candidate["sha256"]
        preserved = {
            "project_count": validation["project_count"],
            "project_ids": validation["project_ids"],
            "canonical_project_digests": {
                item["project_id"]: item["value_sha256"] for item in validation["projects"]
            },
            "derived_project_statuses": status_map,
        }
        if changed:
            backup = _switch_tree(root_path, stage)
            # The stage directory has become the live root.  Never replace it
            # with ``Path("")``: that is the caller's cwd and would make the
            # cleanup block destructive when the caller runs from a repository.
            stage = None
            if failure_injector:
                failure_injector("switched")
        after = inventory_workplace(root_path)
        final_validation = _validate_candidate(
            root_path,
            runtime_root=runtime_path,
            expected_active_state=verified_active_state,
        )
        final_validation["derived_project_statuses"] = status_map
        if failure_injector:
            failure_injector("final-validated")
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
            backup = None
        return MigrationResult(
            root=root_path,
            changed=changed,
            operations=tuple(dict.fromkeys(operations)),
            before=before,
            after=after,
            validation=final_validation,
            rollback={"performed": False, "available": True, "source_preserved_until_switch": True},
            plan=plan,
            preserved=preserved,
        )
    except Exception:
        if backup is not None and backup.exists():
            # A failure after switch (including a final validator or injected
            # failure) restores the exact pre-switch tree before propagating.
            if root_path.exists():
                shutil.rmtree(root_path, ignore_errors=True)
            os.replace(backup, root_path)
            backup = None
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def rehearse_workplace_migration(
    source_root: str | os.PathLike[str] | Path,
    *,
    runtime_root: str | os.PathLike[str] | Path | None = None,
    event_root: str | os.PathLike[str] | Path | None = None,
    context_roots: Mapping[str, str | os.PathLike[str] | Path] | None = None,
    active_pointer_path: str | os.PathLike[str] | Path | None = None,
    live_state_path: str | os.PathLike[str] | Path | None = None,
    active_state_path: str | os.PathLike[str] | Path | None = None,
    active_pointer: str | os.PathLike[str] | Path | None = None,
    live_state: str | os.PathLike[str] | Path | None = None,
) -> dict[str, Any]:
    """Rehearse migration on a disposable copy and return path-minimised proof.

    The source tree and explicit runtime/event roots are read only.  Temporary
    copies are removed in ``finally`` and the returned report never contains an
    absolute path, which makes it safe to store in Workplace review evidence.
    """

    source = _path(source_root, label="source root")
    source_before = inventory_workplace(source)
    temporary_parent = Path(tempfile.mkdtemp(prefix="workplace-migration-rehearsal-"))
    rehearsal = temporary_parent / "copy"
    second = temporary_parent / "second"
    source_git = (source / ".git").exists()
    try:
        shutil.copytree(source, rehearsal, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        result = migrate_workplace_tree(
            rehearsal,
            runtime_root=runtime_root,
            event_root=event_root,
            context_roots=context_roots,
            active_pointer_path=active_pointer_path,
            live_state_path=live_state_path,
            active_state_path=active_state_path,
            active_pointer=active_pointer,
            live_state=live_state,
        )
        first_after = inventory_workplace(rehearsal)
        shutil.copytree(rehearsal, second, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        second_result = migrate_workplace_tree(
            second,
            runtime_root=runtime_root,
            event_root=event_root,
            context_roots=context_roots,
            active_pointer_path=active_pointer_path,
            live_state_path=live_state_path,
            active_state_path=active_state_path,
            active_pointer=active_pointer,
            live_state=live_state,
        )
        second_validation = second_result.validation
        canonical_privacy = _validate_canonical_privacy(second)
        canonical_portable = canonical_privacy["valid"]
        source_after = inventory_workplace(source)
        report = {
            "record_type": "workplace-migration-rehearsal",
            "record_version": 1,
            "candidate_only": True,
            "version_activation": False,
            "real_migration": False,
            "source_unchanged": source_before == source_after,
            "source_had_git": source_git,
            "rehearsal_git_omitted": not (rehearsal / ".git").exists(),
            # The finally block updates this after checking the actual cleanup.
            "temporary_copy_removed": False,
            "before": source_before,
            "after": first_after,
            "migration": result.to_dict(),
            "second_root": {
                "portable": canonical_portable,
                "validation": second_validation,
                "changed": second_result.changed,
                "idempotent": second_result.idempotent,
                "inventory_unchanged": second_result.before == second_result.after,
            },
            "event_root_supplied": event_root is not None,
            "runtime_root_supplied": runtime_root is not None,
            "raw_runtime_ignored": bool(result.validation.get("runtime_ignored")),
            "active_state": {
                "verified": result.validation.get("active_state_valid", False),
                "version": result.validation.get("active_version"),
                "live_state_supplied": live_state_path is not None or live_state is not None,
                "pointer_supplied": active_pointer_path is not None
                or active_pointer is not None
                or active_state_path is not None,
            },
            "privacy": canonical_privacy,
        }
        return report
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)
        if "report" in locals():
            report["temporary_copy_removed"] = not temporary_parent.exists()


# Compatibility spellings make the operation easy to discover without
# multiplying user-facing Commands.
plan_workplace_migration = build_migration_plan
preflight_workplace_migration = build_migration_plan
migrate_legacy_workplace_tree = migrate_workplace_tree
migrate_workspace_tree = migrate_workplace_tree
migrate_workplace = migrate_workplace_tree
run_workplace_migration_rehearsal = rehearse_workplace_migration
rehearse_legacy_workplace_migration = rehearse_workplace_migration


__all__ = [
    "ADAPTER_PREFERENCES_RELATIVE",
    "AUTHORITY_RELATIVE",
    "CONTEXT_CANONICAL_RELATIVE",
    "CONTEXT_LEGACY_RELATIVE",
    "METHOD_STATUS_RELATIVE",
    "MigrationPlan",
    "MigrationPreflightError",
    "MigrationResult",
    "MigrationSwitchError",
    "MigrationValidationError",
    "WorkplaceMigrationError",
    "build_migration_plan",
    "inventory_workplace",
    "migrate_legacy_workplace_tree",
    "migrate_workplace",
    "migrate_workplace_tree",
    "migrate_workspace_tree",
    "plan_workplace_migration",
    "preflight_workplace_migration",
    "rehearse_legacy_workplace_migration",
    "rehearse_workplace_migration",
    "run_workplace_migration_rehearsal",
]
