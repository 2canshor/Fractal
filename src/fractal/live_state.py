"""Verified mutable runtime state derived from canonical immutable sources."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fractal.models import utc_now


class LiveRuntimeStateError(RuntimeError):
    """Raised when current runtime state cannot be proven from canonical sources."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _value_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LiveRuntimeStateStore:
    """Maintain one rebuildable read model without mutating adapter snapshots."""

    def __init__(self, runtime_root: Path, *, state_path: Path | None = None) -> None:
        self.runtime_root = Path(runtime_root)
        self.state_path = (
            Path(state_path)
            if state_path is not None
            else self.runtime_root / "live-state" / "current.json"
        )
        self.lock_path = self.state_path.parent / ".live-state.lock"

    def read(self) -> dict[str, Any]:
        """Read and integrity-check the rebuildable state file."""
        if not self.state_path.is_file():
            raise LiveRuntimeStateError(f"Live runtime state is missing: {self.state_path}")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LiveRuntimeStateError("Live runtime state is unreadable") from error
        expected = value.get("state_sha256")
        unsigned = {key: item for key, item in value.items() if key != "state_sha256"}
        if not isinstance(expected, str) or _value_sha256(unsigned) != expected:
            raise LiveRuntimeStateError("Live runtime state integrity failure")
        return value

    def verify_current(self) -> dict[str, Any]:
        """Verify a stored read model against the canonical paths it records."""
        state = self.read()
        project = state.get("project")
        system_version = state.get("system_version")
        if not isinstance(project, dict) or not isinstance(system_version, dict):
            raise LiveRuntimeStateError("Live runtime state is incomplete")
        self._verify_sources(
            state,
            project_record_path=Path(project.get("source_path", "")),
            active_pointer_path=Path(system_version.get("source_path", "")),
        )
        return state

    def update_project(self, record: dict[str, Any], record_path: Path) -> dict[str, Any]:
        """Refresh the Project half immediately after a canonical Project write."""
        record_path = Path(record_path).resolve()
        project = self._project_state(record_path, supplied_record=record)
        return self._merge_and_write(project=project)

    def update_system_version(
        self,
        pointer: dict[str, Any],
        active_pointer_path: Path,
    ) -> dict[str, Any]:
        """Refresh the System Version half after activation or restore only."""
        active_pointer_path = Path(active_pointer_path).resolve()
        system_version = self._system_version_state(
            active_pointer_path, supplied_pointer=pointer
        )
        return self._merge_and_write(system_version=system_version)

    def reconcile(
        self,
        *,
        project_record_path: Path,
        active_pointer_path: Path,
    ) -> dict[str, Any]:
        """Rebuild current state from both canonical sources and verify read-back."""
        project_record_path = Path(project_record_path).expanduser().resolve()
        active_pointer_path = Path(active_pointer_path).expanduser().resolve()
        state = self._merge_and_write(
            project=self._project_state(project_record_path),
            system_version=self._system_version_state(active_pointer_path),
        )
        verified = self.read()
        if verified != state:
            raise LiveRuntimeStateError("Live runtime state read-back mismatch")
        self._verify_sources(
            verified,
            project_record_path=project_record_path,
            active_pointer_path=active_pointer_path,
        )
        return verified

    def _merge_and_write(
        self,
        *,
        project: dict[str, Any] | None = None,
        system_version: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                current: dict[str, Any] = {}
                if self.state_path.is_file():
                    current = self.read()
                value = {
                    "record_type": "live-runtime-state",
                    "record_version": 1,
                    "refreshed_at": utc_now(),
                }
                if "project" in current:
                    value["project"] = current["project"]
                if "system_version" in current:
                    value["system_version"] = current["system_version"]
                if project is not None:
                    value["project"] = project
                if system_version is not None:
                    value["system_version"] = system_version
                value["state_sha256"] = _value_sha256(value)
                self._atomic_json_write(self.state_path, value)
                return value
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _project_state(
        record_path: Path,
        *,
        supplied_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        digest_path = record_path.with_name("record.sha256")
        if not record_path.is_file() or not digest_path.is_file():
            raise LiveRuntimeStateError("Canonical Project record or digest is missing")
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            expected = digest_path.read_text(encoding="ascii").strip()
        except (OSError, json.JSONDecodeError) as error:
            raise LiveRuntimeStateError("Canonical Project record is unreadable") from error
        actual = _value_sha256(record)
        if actual != expected:
            raise LiveRuntimeStateError("Project digest mismatch")
        if supplied_record is not None and record != supplied_record:
            raise LiveRuntimeStateError("Project write and canonical read-back differ")
        required = {"project_id", "revision", "status", "plan"}
        if not required.issubset(record) or not isinstance(record["plan"], dict):
            raise LiveRuntimeStateError("Canonical Project record lacks live summary fields")
        return {
            "project_id": record["project_id"],
            "revision": record["revision"],
            "status": record["status"],
            "current_phase": record["plan"].get("current_phase"),
            "source_path": str(record_path),
            "source_sha256": actual,
        }

    @staticmethod
    def _system_version_state(
        active_pointer_path: Path,
        *,
        supplied_pointer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not active_pointer_path.is_file():
            raise LiveRuntimeStateError("Active System Version pointer is missing")
        try:
            pointer = json.loads(active_pointer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LiveRuntimeStateError("Active System Version pointer is unreadable") from error
        if supplied_pointer is not None and pointer != supplied_pointer:
            raise LiveRuntimeStateError("Active pointer write and canonical read-back differ")
        version = pointer.get("version")
        expected_manifest_sha = pointer.get("manifest_sha256")
        if not isinstance(version, str) or not isinstance(expected_manifest_sha, str):
            raise LiveRuntimeStateError("Active System Version pointer is incomplete")
        manifest_path = active_pointer_path.parent / "versions" / f"{version}.json"
        if not manifest_path.is_file():
            raise LiveRuntimeStateError("Active System Version manifest is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LiveRuntimeStateError("Active System Version manifest is unreadable") from error
        stored_manifest_sha = manifest.get("manifest_sha256")
        unsigned_manifest = {
            key: item for key, item in manifest.items() if key != "manifest_sha256"
        }
        if (
            stored_manifest_sha != expected_manifest_sha
            or _value_sha256(unsigned_manifest) != stored_manifest_sha
        ):
            raise LiveRuntimeStateError("Active System Version manifest integrity failure")
        return {
            "version": version,
            "status": "active",
            "activated_by": pointer.get("activated_by"),
            "activated_at": pointer.get("activated_at"),
            "manifest_sha256": expected_manifest_sha,
            "source_path": str(active_pointer_path),
            "source_sha256": _file_sha256(active_pointer_path),
        }

    def _verify_sources(
        self,
        state: dict[str, Any],
        *,
        project_record_path: Path,
        active_pointer_path: Path,
    ) -> None:
        observed_project = self._project_state(project_record_path)
        observed_version = self._system_version_state(active_pointer_path)
        if state.get("project") != observed_project:
            raise LiveRuntimeStateError("Live Project state became stale during verification")
        if state.get("system_version") != observed_version:
            raise LiveRuntimeStateError(
                "Live System Version state became stale during verification"
            )

    @staticmethod
    def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
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
