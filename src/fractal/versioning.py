"""Human-approved System Version, Node-map change, and restore paths."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fractal.improvement import TrialBoundary, TrialMeasurement, compare_trial_results
from fractal.models import utc_now
from fractal.storage import AuthorityError, value_sha256


class VersionError(RuntimeError):
    """Raised when a System Version cannot be built, activated, or restored."""


class VersionStore:
    """Persist immutable candidate manifests and an evented active pointer."""

    REQUIRED_VERIFICATIONS = {
        "clean_build",
        "tests_passed",
        "adapter_hashes_verified",
        "migrations_verified",
        "restore_verified",
    }

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.events = self.root / "version-events.jsonl"
        self.active_pointer = self.root / "active.json"

    def build_candidate(
        self,
        *,
        version: str,
        public_commit: str,
        private_commit: str,
        components: list[dict[str, Any]],
        adapter_hashes: dict[str, str],
        migrations: list[str],
        restore_point: dict[str, Any],
        verification: dict[str, bool],
    ) -> dict[str, Any]:
        """Build an immutable candidate only when every executable gate passes."""
        self._validate_version(version)
        if set(verification) != self.REQUIRED_VERIFICATIONS or not all(verification.values()):
            raise VersionError("Every build, test, adapter, migration, and restore gate must pass")
        if re.fullmatch(r"[a-f0-9]{40}", public_commit) is None or re.fullmatch(
            r"[a-f0-9]{40}", private_commit
        ) is None:
            raise VersionError("System Version commits must be full Git object ids")
        if not restore_point:
            raise VersionError("System Version requires a restore point")
        for adapter_id, digest in adapter_hashes.items():
            if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise VersionError(f"Invalid adapter digest: {adapter_id}")
        component_ids = [item["component_id"] for item in components]
        if len(component_ids) != len(set(component_ids)):
            raise VersionError("System Version component ids must be unique")
        for component in components:
            if re.fullmatch(r"[a-f0-9]{64}", component["sha256"]) is None:
                raise VersionError(f"Invalid component digest: {component['component_id']}")
        manifest_content = {
            "record_type": "system-version-manifest",
            "record_version": 1,
            "version": version,
            "public_commit": public_commit,
            "private_commit": private_commit,
            "components": components,
            "adapter_hashes": adapter_hashes,
            "migrations": migrations,
            "restore_point": restore_point,
            "verification": verification,
            "status": "candidate",
        }
        path = self._manifest_path(version)
        if path.exists():
            existing = self.read_manifest(version)
            comparable = {
                key: value
                for key, value in existing.items()
                if key not in {"built_at", "manifest_sha256"}
            }
            if comparable != manifest_content:
                raise VersionError(
                    f"System Version already exists with different content: {version}"
                )
            return existing
        manifest = {
            **manifest_content,
            "manifest_sha256": None,
            "built_at": utc_now(),
        }
        manifest["manifest_sha256"] = value_sha256(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
        self._atomic_json_write(path, manifest)
        self._append_event("build-candidate", version, "system-builder")
        return manifest

    def activate(
        self,
        version: str,
        *,
        actor: str,
        human_action: bool,
    ) -> dict[str, Any]:
        """Activate a verified candidate only after primary-user approval."""
        self._require_primary_user(actor, human_action)
        manifest = self.read_manifest(version)
        state = self.version_state(version)
        if state != "candidate":
            raise VersionError(f"Only a candidate can be activated; current state is {state}")
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".activation.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                previous = self.read_active()
                pointer = {
                    "version": version,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "activated_by": actor,
                    "activated_at": utc_now(),
                    "previous_version": previous["version"] if previous else None,
                }
                self._atomic_json_write(self.active_pointer, pointer)
                self._append_event("activate", version, actor)
                self._refresh_live_system_version(pointer)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return pointer

    def reject(
        self,
        version: str,
        *,
        actor: str,
        human_action: bool,
    ) -> None:
        """Reject a candidate without moving the active pointer."""
        self._require_primary_user(actor, human_action)
        self.read_manifest(version)
        if self.version_state(version) != "candidate":
            raise VersionError("Only a candidate can be rejected")
        self._append_event("reject", version, actor)

    def restore(
        self,
        version: str,
        *,
        actor: str,
        human_action: bool,
    ) -> dict[str, Any]:
        """Restore a previously activated verified manifest."""
        self._require_primary_user(actor, human_action)
        manifest = self.read_manifest(version)
        if not manifest["verification"]["restore_verified"]:
            raise VersionError("Target System Version has no verified restore path")
        if not any(
            event["action"] in {"activate", "restore"} and event["version"] == version
            for event in self.read_events()
        ):
            raise VersionError("Only a previously active System Version can be restored")
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".activation.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                previous = self.read_active()
                pointer = {
                    "version": version,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "activated_by": actor,
                    "activated_at": utc_now(),
                    "previous_version": previous["version"] if previous else None,
                }
                self._atomic_json_write(self.active_pointer, pointer)
                self._append_event("restore", version, actor)
                self._refresh_live_system_version(pointer)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return pointer

    def read_manifest(self, version: str) -> dict[str, Any]:
        """Read and verify one immutable candidate manifest."""
        self._validate_version(version)
        path = self._manifest_path(version)
        if not path.exists():
            raise VersionError(f"Unknown System Version: {version}")
        value = json.loads(path.read_text(encoding="utf-8"))
        expected = value["manifest_sha256"]
        unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
        actual = value_sha256(unsigned)
        if actual != expected:
            raise VersionError(f"System Version manifest integrity failure: {version}")
        return value

    def read_active(self) -> dict[str, Any] | None:
        """Read the active version pointer, if one exists."""
        if not self.active_pointer.exists():
            return None
        pointer = json.loads(self.active_pointer.read_text(encoding="utf-8"))
        manifest = self.read_manifest(pointer["version"])
        if manifest["manifest_sha256"] != pointer["manifest_sha256"]:
            raise VersionError("Active System Version pointer integrity failure")
        return pointer

    def version_state(self, version: str) -> str:
        """Derive lifecycle state from append-only version events."""
        actions = [event["action"] for event in self.read_events() if event["version"] == version]
        if "reject" in actions:
            return "rejected"
        if any(action in {"activate", "restore"} for action in actions):
            active = self.read_active()
            return "active" if active and active["version"] == version else "previously-active"
        return "candidate"

    def read_events(self) -> list[dict[str, Any]]:
        """Read version lifecycle evidence."""
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines() if line.strip()]

    def _append_event(self, action: str, version: str, actor: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".version-events.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                event = {
                    "event_id": f"version-event-{uuid.uuid4()}",
                    "action": action,
                    "version": version,
                    "actor": actor,
                    "occurred_at": utc_now(),
                }
                with self.events.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _manifest_path(self, version: str) -> Path:
        return self.versions / f"{version}.json"

    def _refresh_live_system_version(self, pointer: dict[str, Any]) -> None:
        """Refresh rebuildable live state only after an authority pointer write."""
        from fractal.live_state import LiveRuntimeStateStore

        LiveRuntimeStateStore(self.root.parent).update_system_version(
            pointer,
            self.active_pointer,
        )

    @staticmethod
    def _validate_version(version: str) -> None:
        pattern = (
            r"(?:0|[1-9]\d*)\."
            r"(?:0|[1-9]\d*)\."
            r"(?:0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z.-]+)?"
        )
        if re.fullmatch(pattern, version) is None:
            raise VersionError(f"Invalid Semantic Version: {version}")

    @staticmethod
    def _require_primary_user(actor: str, human_action: bool) -> None:
        if actor != "primary-user" or not human_action:
            raise AuthorityError("System Version action requires the primary user")

    @staticmethod
    def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def propose_node_map_change(
    *,
    change_type: str,
    target_ids: list[str],
    active_map: dict[str, Any],
    candidate_map: dict[str, Any],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """Propose Add, Remove, Replace, Merge, or Split without self-activation."""
    if change_type not in {"add", "remove", "replace", "merge", "split"}:
        raise ValueError(f"Unsupported Node map change: {change_type}")
    if not target_ids or not evidence_ids:
        raise VersionError("Node map proposals require targets and evidence")
    return {
        "record_type": "node-map-change-proposal",
        "proposal_id": f"node-map-proposal-{uuid.uuid4()}",
        "change_type": change_type,
        "target_ids": target_ids,
        "before_sha256": value_sha256(active_map),
        "candidate_sha256": value_sha256(candidate_map),
        "active_map": active_map,
        "candidate_map": candidate_map,
        "restore_map": active_map,
        "evidence_ids": evidence_ids,
        "decision_status": "proposed",
        "trial_status": "not-run",
        "active": False,
    }


def trial_node_map_change(
    proposal: dict[str, Any],
    *,
    boundary: TrialBoundary,
    baseline: TrialMeasurement,
    candidate: TrialMeasurement,
) -> dict[str, Any]:
    """Trial a candidate mapping while leaving the active map untouched."""
    updated = json.loads(json.dumps(proposal))
    result = compare_trial_results(boundary, baseline, candidate)
    updated["trial_status"] = result["status"]
    updated["trial_result"] = result
    updated["active"] = False
    return updated


def decide_node_map_change(
    proposal: dict[str, Any],
    *,
    decision: str,
    actor: str,
    human_action: bool,
) -> dict[str, Any]:
    """Approve for a future System Version or reject without modifying the active map."""
    if actor != "primary-user" or not human_action:
        raise AuthorityError("Node map decisions require the primary user")
    if decision not in {"approve", "reject"}:
        raise ValueError(f"Unsupported Node map decision: {decision}")
    updated = json.loads(json.dumps(proposal))
    updated["decision_status"] = "approved-for-version" if decision == "approve" else "rejected"
    updated["decided_by"] = actor
    updated["decided_at"] = utc_now()
    updated["active"] = False
    return updated
