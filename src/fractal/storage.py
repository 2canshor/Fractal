"""Atomic, conflict-aware storage for canonical Project records."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fractal.migrations import CURRENT_PROJECT_SCHEMA_VERSION, migrate_project_record
from fractal.models import Change, ProjectRecord, WriteResult, utc_now
from fractal.validation import validate_project_record


class FractalStorageError(RuntimeError):
    """Base error for Project storage failures."""


class ProjectAlreadyExistsError(FractalStorageError):
    """Raised when a Project identity is already present."""


class ProjectNotFoundError(FractalStorageError):
    """Raised when a Project identity is not present."""


class IntegrityError(FractalStorageError):
    """Raised when canonical state does not match its stored digest."""


class AuthorityError(FractalStorageError):
    """Raised when an ordinary write attempts an authority-controlled change."""


def canonical_json_bytes(value: Any) -> bytes:
    """Encode a value deterministically for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def value_sha256(value: Any) -> str:
    """Return a digest of canonical JSON data."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_project_id(project_id: str) -> None:
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", project_id) is None:
        raise ValueError(f"Invalid Project id: {project_id}")


def _split_pointer(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise ValueError(f"Invalid record path: {path}")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _get_value(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in _split_pointer(path):
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        raise ValueError(f"Unknown record path: {path}")
    return value


def _set_value(record: dict[str, Any], path: str, value: Any) -> None:
    parts = _split_pointer(path)
    parent: Any = record
    for part in parts[:-1]:
        if not isinstance(parent, dict) or part not in parent:
            raise ValueError(f"Unknown record path: {path}")
        parent = parent[part]
    if not isinstance(parent, dict) or parts[-1] not in parent:
        raise ValueError(f"Unknown record path: {path}")
    parent[parts[-1]] = copy.deepcopy(value)


def _append_value(record: dict[str, Any], path: str, value: Any) -> bool:
    target = _get_value(record, path)
    if not isinstance(target, list):
        raise ValueError(f"Append target is not a list: {path}")
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        raise ValueError("Appended records require a stable string id")
    existing = next((item for item in target if item.get("id") == value["id"]), None)
    if existing is None:
        target.append(copy.deepcopy(value))
        return True
    if existing == value:
        return False
    raise ValueError(f"Stable id already exists with different content: {value['id']}")


class ProjectStore:
    """Persist canonical Projects and local append-only event evidence."""

    def __init__(self, project_root: Path, runtime_root: Path) -> None:
        self.project_root = Path(project_root)
        self.runtime_root = Path(runtime_root)
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        record: ProjectRecord,
        *,
        actor: str,
        platform: str,
        authority_write: bool = False,
    ) -> ProjectRecord:
        """Create a new stable Project identity and verify its read-back."""
        _validate_project_id(record.project_id)
        if not authority_write:
            self._guard_initial_authority(record)
        with self._project_lock(record.project_id):
            if self._record_path(record.project_id).exists():
                raise ProjectAlreadyExistsError(record.project_id)
            value = record.to_dict()
            validate_project_record(value)
            self._write_record(record.project_id, value)
            self._append_event(
                record.project_id,
                {
                    "event_id": f"event-{uuid.uuid4()}",
                    "project_id": record.project_id,
                    "base_revision": None,
                    "new_revision": 0,
                    "actor": actor,
                    "platform": platform,
                    "action": "create-project",
                    "changes": [],
                    "occurred_at": utc_now(),
                },
            )
            return self.read(record.project_id)

    def read(self, project_id: str) -> ProjectRecord:
        """Read, integrity-check, validate, and type a canonical Project."""
        value = self._read_raw(project_id)
        validate_project_record(value)
        return ProjectRecord.from_dict(value)

    def migrate(self, project_id: str, *, actor: str, platform: str) -> ProjectRecord:
        """Migrate canonical state through a deterministic verified schema path."""
        _validate_project_id(project_id)
        with self._project_lock(project_id):
            current = self._read_raw(project_id)
            migrated, applied = migrate_project_record(current)
            if not applied:
                validate_project_record(migrated)
                return ProjectRecord.from_dict(migrated)
            before_version = current["schema_version"]
            migrated["revision"] = current["revision"] + 1
            migrated["updated_at"] = utc_now()
            validate_project_record(migrated)
            self._write_record(project_id, migrated)
            self._append_event(
                project_id,
                {
                    "event_id": f"event-{uuid.uuid4()}",
                    "project_id": project_id,
                    "base_revision": current["revision"],
                    "new_revision": migrated["revision"],
                    "actor": actor,
                    "platform": platform,
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
                },
            )
            return self.read(project_id)

    def apply_changes(
        self,
        project_id: str,
        *,
        expected_revision: int,
        changes: list[Change],
        actor: str,
        platform: str,
        authority_write: bool = False,
        action: str = "apply-changes",
    ) -> WriteResult:
        """Apply compatible changes or persist a Request Decision for a real conflict."""
        if not changes:
            raise ValueError("At least one change is required")
        _validate_project_id(project_id)
        with self._project_lock(project_id):
            current = self.read(project_id).to_dict()
            self._guard_authority(
                changes,
                authority_write=authority_write,
                current=current,
            )
            stale = current["revision"] != expected_revision
            candidate = copy.deepcopy(current)
            changed = False
            event_changes: list[dict[str, Any]] = []
            for change in changes:
                event_change = change.to_dict()
                if change.operation == "append":
                    target = _get_value(current, change.path)
                    event_change["observed_item_ids"] = [
                        item.get("id") for item in target if isinstance(item, dict)
                    ]
                    try:
                        changed = _append_value(candidate, change.path, change.value) or changed
                    except ValueError:
                        return self._record_conflict(
                            current,
                            change,
                            actor=actor,
                            platform=platform,
                        )
                    event_changes.append(event_change)
                    continue
                current_value = _get_value(candidate, change.path)
                event_change["observed_value"] = copy.deepcopy(
                    _get_value(current, change.path)
                )
                if stale and current_value not in (change.base_value, change.value):
                    return self._record_conflict(
                        current,
                        change,
                        actor=actor,
                        platform=platform,
                    )
                if current_value != change.value:
                    _set_value(candidate, change.path, change.value)
                    changed = True
                event_changes.append(event_change)

            if not changed:
                return WriteResult(
                    applied=True,
                    merged=stale,
                    revision=current["revision"],
                )
            candidate["revision"] = current["revision"] + 1
            candidate["updated_at"] = utc_now()
            validate_project_record(candidate)
            self._write_record(project_id, candidate)
            self._append_event(
                project_id,
                {
                    "event_id": f"event-{uuid.uuid4()}",
                    "project_id": project_id,
                    "base_revision": expected_revision,
                    "new_revision": candidate["revision"],
                    "actor": actor,
                    "platform": platform,
                    "action": action,
                    "changes": event_changes,
                    "occurred_at": utc_now(),
                },
            )
            read_back = self.read(project_id)
            return WriteResult(
                applied=True,
                merged=stale,
                revision=read_back.revision,
            )

    def verify(self, project_id: str) -> dict[str, Any]:
        """Return observed integrity and event-chain status for a Project."""
        _validate_project_id(project_id)
        record = self.read(project_id)
        events = self._read_events(project_id)
        previous_hash: str | None = None
        for expected_revision, event in enumerate(events):
            stored_hash = event.pop("event_hash")
            if event["previous_event_hash"] != previous_hash:
                raise IntegrityError(f"Event chain link mismatch: {event['event_id']}")
            if event["new_revision"] != expected_revision:
                raise IntegrityError(f"Event revision gap: {event['event_id']}")
            actual_hash = value_sha256(event)
            if actual_hash != stored_hash:
                raise IntegrityError(f"Event digest mismatch: {event['event_id']}")
            previous_hash = stored_hash
        if not events or events[-1]["new_revision"] != record.revision:
            raise IntegrityError("Event chain does not reach the canonical record revision")
        return {
            "project_id": project_id,
            "record_revision": record.revision,
            "record_sha256": value_sha256(record.to_dict()),
            "event_count": len(events),
            "event_chain_valid": True,
        }

    def _record_conflict(
        self,
        current: dict[str, Any],
        change: Change,
        *,
        actor: str,
        platform: str,
    ) -> WriteResult:
        request_id = f"request-{uuid.uuid4()}"
        try:
            current_value = _get_value(current, change.path)
        except ValueError:
            current_value = None
        request = {
            "id": request_id,
            "kind": "request_decision",
            "status": "pending",
            "path": change.path,
            "base_value": change.base_value,
            "current_value": current_value,
            "proposed_value": change.value,
            "created_at": utc_now(),
        }
        current["requests"].append(request)
        current["revision"] += 1
        current["updated_at"] = utc_now()
        validate_project_record(current)
        self._write_record(current["project_id"], current)
        self._append_event(
            current["project_id"],
            {
                "event_id": f"event-{uuid.uuid4()}",
                "project_id": current["project_id"],
                "base_revision": current["revision"] - 1,
                "new_revision": current["revision"],
                "actor": actor,
                "platform": platform,
                "action": "request-decision",
                "changes": [change.to_dict()],
                "occurred_at": utc_now(),
            },
        )
        return WriteResult(
            applied=False,
            merged=False,
            revision=current["revision"],
            conflict_request_id=request_id,
        )

    def _guard_authority(
        self,
        changes: list[Change],
        *,
        authority_write: bool,
        current: dict[str, Any],
    ) -> None:
        if authority_write:
            return
        protected_paths = {
            "/project_id",
            "/system_version",
            "/revision",
            "/completion",
            "/completion/completed_at",
            "/completion/completed_by",
        }
        authority_candidate = copy.deepcopy(current)
        for change in changes:
            if (
                change.path in protected_paths or change.path.startswith("/completion/")
            ) and change.path != "/completion/requested_at":
                raise AuthorityError(f"Authority-controlled path: {change.path}")
            if change.path == "/status" and change.value == "completed":
                raise AuthorityError("Project Completion requires an authority action")
            if (
                change.path == "/decisions"
                and isinstance(change.value, dict)
                and change.value.get("status") in {"approved", "rejected"}
            ):
                raise AuthorityError(
                    "Decision approval or rejection requires an authority action"
                )
            try:
                if change.operation == "set":
                    _set_value(authority_candidate, change.path, change.value)
                else:
                    _append_value(authority_candidate, change.path, change.value)
            except ValueError:
                continue

        current_lifecycle = current["lifecycle"]
        candidate_lifecycle = authority_candidate["lifecycle"]
        current_direction = current_lifecycle["direction"]
        candidate_direction = candidate_lifecycle["direction"]
        if (
            current_direction != candidate_direction
            and candidate_direction["status"] != "provisional"
        ):
            raise AuthorityError("Project Direction confirmation requires an authority action")
        if current_lifecycle["goal"] != candidate_lifecycle["goal"]:
            raise AuthorityError("Goal changes require an authority action")
        if current_lifecycle["priorities"] != candidate_lifecycle["priorities"]:
            raise AuthorityError("Priority changes require an authority action")
        current_criteria = self._criteria_definition(current_lifecycle["success_criteria"])
        candidate_criteria = self._criteria_definition(candidate_lifecycle["success_criteria"])
        if current_criteria != candidate_criteria:
            raise AuthorityError("Success Criteria definition changes require an authority action")

    @staticmethod
    def _criteria_definition(criteria: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": criteria["version"],
            "status": criteria["status"],
            "items": [
                {
                    "id": item["id"],
                    "summary": item["summary"],
                    "evidence_required": item["evidence_required"],
                }
                for item in criteria["items"]
            ],
        }

    @staticmethod
    def _guard_initial_authority(record: ProjectRecord) -> None:
        if record.status == "completed" or any(record.completion.values()):
            raise AuthorityError("Project Completion requires an authority action")
        if record.lifecycle["direction"].get("status") == "confirmed":
            raise AuthorityError("Project Direction confirmation requires an authority action")
        if any(item.get("status") in {"approved", "rejected"} for item in record.decisions):
            raise AuthorityError("Decision approval or rejection requires an authority action")

    @contextmanager
    def _project_lock(self, project_id: str) -> Iterator[None]:
        self.project_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.project_root / f".{project_id}.lock"
        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _record_path(self, project_id: str) -> Path:
        return self.project_root / project_id / "record.json"

    def _digest_path(self, project_id: str) -> Path:
        return self.project_root / project_id / "record.sha256"

    def _event_path(self, project_id: str) -> Path:
        return self.runtime_root / "events" / f"{project_id}.jsonl"

    def _read_raw(self, project_id: str) -> dict[str, Any]:
        _validate_project_id(project_id)
        record_path = self._record_path(project_id)
        digest_path = self._digest_path(project_id)
        if not record_path.exists() or not digest_path.exists():
            raise ProjectNotFoundError(project_id)
        value = json.loads(record_path.read_text(encoding="utf-8"))
        expected = digest_path.read_text(encoding="ascii").strip()
        actual = value_sha256(value)
        if actual != expected:
            raise IntegrityError(f"Project digest mismatch: {project_id}")
        return value

    def _write_record(self, project_id: str, value: dict[str, Any]) -> None:
        project_path = self.project_root / project_id
        project_path.mkdir(parents=True, exist_ok=True)
        record_text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self._atomic_write(self._record_path(project_id), record_text.encode("utf-8"))
        digest = value_sha256(value) + "\n"
        self._atomic_write(self._digest_path(project_id), digest.encode("ascii"))

    def _append_event(self, project_id: str, event: dict[str, Any]) -> None:
        event_path = self._event_path(project_id)
        event_path.parent.mkdir(parents=True, exist_ok=True)
        events = self._read_events(project_id)
        previous_hash = events[-1]["event_hash"] if events else None
        event["previous_event_hash"] = previous_hash
        event["event_hash"] = value_sha256(event)
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _read_events(self, project_id: str) -> list[dict[str, Any]]:
        event_path = self._event_path(project_id)
        if not event_path.exists():
            return []
        return [
            json.loads(line)
            for line in event_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary_path.unlink(missing_ok=True)
