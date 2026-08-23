"""Human-approved System Version, Node-map change, and restore paths."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fractal.authority import AuthorityReceiptStore, ReceiptError
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

    def __init__(
        self,
        root: Path,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.events = self.root / "version-events.jsonl"
        self.active_pointer = self.root / "active.json"
        self.authority = AuthorityReceiptStore(self.root / "authority")
        self.fault_injector = fault_injector

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
        project_id: str,
        project_revision: int,
        decision_batch: dict[str, Any],
        architecture_lineage: dict[str, Any],
        claim_gate_audit: dict[str, Any],
        adapter_boundary_audit: dict[str, Any],
        preservation_audits: dict[str, Any],
        authority_receipt_id: str,
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
        if architecture_lineage.get("structural_gate_passed") is not True:
            raise VersionError("System Version requires a passed architecture-lineage gate")
        if claim_gate_audit.get("passed") is not True:
            raise VersionError("System Version requires a passed Claim Gate audit")
        if (
            adapter_boundary_audit.get("passed_for_candidate") is not True
            or not isinstance(adapter_boundary_audit.get("platforms"), dict)
            or not adapter_boundary_audit["platforms"]
        ):
            raise VersionError("System Version requires a passed staged adapter boundary audit")
        if set(preservation_audits) != {
            "phase_a_pre_build",
            "phase_b_post_build_pre_activation",
        } or any(
            audit.get("passed") is not True or not audit.get("receipt_sha256")
            for audit in preservation_audits.values()
        ):
            raise VersionError("System Version requires both passed preservation audits")
        if not decision_batch.get("decision_batch_id"):
            raise VersionError("System Version requires an exact approved decision batch")
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
            "record_version": 2,
            "version": version,
            "public_commit": public_commit,
            "private_commit": private_commit,
            "components": components,
            "adapter_hashes": adapter_hashes,
            "migrations": migrations,
            "restore_point": restore_point,
            "verification": verification,
            "project_id": project_id,
            "project_revision": project_revision,
            "decision_batch": decision_batch,
            "architecture_lineage": architecture_lineage,
            "claim_gate_audit": claim_gate_audit,
            "adapter_boundary_audit": adapter_boundary_audit,
            "preservation_audits": preservation_audits,
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
            if not self._has_build_event(version, existing["manifest_sha256"]):
                raise VersionError("Existing candidate lacks a governed build event")
            return existing
        target, expected_state = self.build_authority_scope(
            version=version,
            public_commit=public_commit,
            private_commit=private_commit,
            decision_batch=decision_batch,
        )
        try:
            self.authority.claim(
                authority_receipt_id,
                action="build",
                project_id=project_id,
                project_revision=project_revision,
                target=target,
                expected_state=expected_state,
            )
        except ReceiptError as error:
            raise AuthorityError(str(error)) from error
        manifest = {
            **manifest_content,
            "manifest_sha256": None,
            "built_at": utc_now(),
        }
        manifest["manifest_sha256"] = value_sha256(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
        self._atomic_json_write(path, manifest)
        try:
            self._inject("after-manifest-before-build-event")
            self._append_event(
                "build-candidate",
                version,
                "primary-user",
                authority_receipt_id=authority_receipt_id,
                manifest_sha256=manifest["manifest_sha256"],
                decision_batch_sha256=value_sha256(decision_batch),
            )
        except Exception as error:
            path.unlink(missing_ok=True)
            self.authority.finish(
                authority_receipt_id,
                succeeded=False,
                failure=str(error),
            )
            raise
        self.authority.finish(authority_receipt_id, succeeded=True)
        return manifest

    def build_authority_scope(
        self,
        *,
        version: str,
        public_commit: str,
        private_commit: str,
        decision_batch: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the exact scope a primary-user build receipt must bind."""
        active = self.read_active()
        return (
            {
                "version": version,
                "public_commit": public_commit,
                "private_commit": private_commit,
                "decision_batch_sha256": value_sha256(decision_batch),
            },
            {
                "active_version": active["version"] if active else None,
                "candidate_absent": not self._manifest_path(version).exists(),
            },
        )

    def activate(
        self,
        version: str,
        *,
        project_id: str,
        project_revision: int,
        authority_receipt_id: str,
    ) -> dict[str, Any]:
        """Activate a verified candidate only through an exact single-use receipt."""
        manifest = self.read_manifest(version)
        state = self.version_state(version)
        if state != "candidate":
            raise VersionError(f"Only a candidate can be activated; current state is {state}")
        if not self._has_build_event(version, manifest["manifest_sha256"]):
            raise VersionError("Candidate has no verified governed build event")
        if manifest.get("adapter_boundary_audit", {}).get("live_promotion_eligible") is not True:
            raise VersionError(
                "Candidate cannot activate until exact live adapter boundary proof passes"
            )
        target, expected_state = self.action_authority_scope(version, action="activate")
        self._claim_action_receipt(
            authority_receipt_id,
            action="activate",
            project_id=project_id,
            project_revision=project_revision,
            target=target,
            expected_state=expected_state,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".activation.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                previous = self.read_active()
                if (previous["version"] if previous else None) != expected_state["active_version"]:
                    raise VersionError("Active System Version changed after authority was issued")
                pointer = {
                    "version": version,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "activated_by": "primary-user",
                    "activated_at": utc_now(),
                    "previous_version": previous["version"] if previous else None,
                }
                self._atomic_json_write(self.active_pointer, pointer)
                try:
                    self._inject("after-pointer-before-event")
                    self._append_event(
                        "activate",
                        version,
                        "primary-user",
                        authority_receipt_id=authority_receipt_id,
                        manifest_sha256=manifest["manifest_sha256"],
                    )
                except Exception:
                    self._restore_pointer(previous)
                    raise
                self._refresh_live_system_version(pointer)
            except Exception as error:
                self.authority.finish(
                    authority_receipt_id,
                    succeeded=False,
                    failure=str(error),
                )
                raise
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self.authority.finish(authority_receipt_id, succeeded=True)
        return pointer

    def reject(
        self,
        version: str,
        *,
        project_id: str,
        project_revision: int,
        authority_receipt_id: str,
    ) -> None:
        """Reject a candidate without moving the active pointer."""
        manifest = self.read_manifest(version)
        if self.version_state(version) != "candidate":
            raise VersionError("Only a candidate can be rejected")
        if not self._has_build_event(version, manifest["manifest_sha256"]):
            raise VersionError("Candidate has no verified governed build event")
        target, expected_state = self.action_authority_scope(version, action="reject")
        self._claim_action_receipt(
            authority_receipt_id,
            action="reject",
            project_id=project_id,
            project_revision=project_revision,
            target=target,
            expected_state=expected_state,
        )
        try:
            self._append_event(
                "reject",
                version,
                "primary-user",
                authority_receipt_id=authority_receipt_id,
                manifest_sha256=manifest["manifest_sha256"],
            )
        except Exception as error:
            self.authority.finish(authority_receipt_id, succeeded=False, failure=str(error))
            raise
        self.authority.finish(authority_receipt_id, succeeded=True)

    def restore(
        self,
        version: str,
        *,
        project_id: str,
        project_revision: int,
        authority_receipt_id: str,
    ) -> dict[str, Any]:
        """Restore a previously activated verified manifest."""
        manifest = self.read_manifest(version)
        if not manifest["verification"]["restore_verified"]:
            raise VersionError("Target System Version has no verified restore path")
        if not any(
            event["action"] in {"activate", "restore"} and event["version"] == version
            for event in self.read_events()
        ):
            raise VersionError("Only a previously active System Version can be restored")
        target, expected_state = self.action_authority_scope(version, action="restore")
        self._claim_action_receipt(
            authority_receipt_id,
            action="restore",
            project_id=project_id,
            project_revision=project_revision,
            target=target,
            expected_state=expected_state,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".activation.lock"
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                previous = self.read_active()
                if (previous["version"] if previous else None) != expected_state["active_version"]:
                    raise VersionError("Active System Version changed after authority was issued")
                pointer = {
                    "version": version,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "activated_by": "primary-user",
                    "activated_at": utc_now(),
                    "previous_version": previous["version"] if previous else None,
                }
                self._atomic_json_write(self.active_pointer, pointer)
                try:
                    self._inject("after-pointer-before-event")
                    self._append_event(
                        "restore",
                        version,
                        "primary-user",
                        authority_receipt_id=authority_receipt_id,
                        manifest_sha256=manifest["manifest_sha256"],
                    )
                except Exception:
                    self._restore_pointer(previous)
                    raise
                self._refresh_live_system_version(pointer)
            except Exception as error:
                self.authority.finish(
                    authority_receipt_id,
                    succeeded=False,
                    failure=str(error),
                )
                raise
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self.authority.finish(authority_receipt_id, succeeded=True)
        return pointer

    def action_authority_scope(
        self,
        version: str,
        *,
        action: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the exact scope for activate, reject, or restore authority."""
        if action not in {"activate", "reject", "restore"}:
            raise VersionError(f"Unsupported local lifecycle action: {action}")
        manifest = self.read_manifest(version)
        active = self.read_active()
        return (
            {"version": version, "manifest_sha256": manifest["manifest_sha256"]},
            {
                "active_version": active["version"] if active else None,
                "version_state": self.version_state(version),
            },
        )

    def _claim_action_receipt(
        self,
        receipt_id: str,
        *,
        action: str,
        project_id: str,
        project_revision: int,
        target: dict[str, Any],
        expected_state: dict[str, Any],
    ) -> None:
        try:
            self.authority.claim(
                receipt_id,
                action=action,
                project_id=project_id,
                project_revision=project_revision,
                target=target,
                expected_state=expected_state,
            )
        except ReceiptError as error:
            raise AuthorityError(str(error)) from error

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

    def _append_event(
        self,
        action: str,
        version: str,
        actor: str,
        **details: Any,
    ) -> None:
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
                    **details,
                }
                with self.events.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, sort_keys=True) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _has_build_event(self, version: str, manifest_sha256: str) -> bool:
        return any(
            event.get("action") == "build-candidate"
            and event.get("version") == version
            and event.get("manifest_sha256") == manifest_sha256
            and event.get("authority_receipt_id")
            for event in self.read_events()
        )

    def _restore_pointer(self, previous: dict[str, Any] | None) -> None:
        """Compensate a failed pointer transition before any success is claimed."""
        if previous is None:
            self.active_pointer.unlink(missing_ok=True)
        else:
            self._atomic_json_write(self.active_pointer, previous)

    def _inject(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

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


def validate_publication_order(order: dict[str, Any]) -> dict[str, Any]:
    """Validate the exact Git publication scope without performing any remote action."""
    required = {
        "version",
        "repository_id",
        "remote",
        "ref",
        "commit",
        "expected_remote_commit",
        "force",
    }
    if set(order) != required:
        raise VersionError("Publication order fields are incomplete or unexpected")
    for field in ("version", "repository_id", "remote", "ref", "commit"):
        if not str(order[field]).strip():
            raise VersionError(f"Publication order requires {field}")
    if re.fullmatch(r"[a-f0-9]{40}", order["commit"]) is None:
        raise VersionError("Publication order requires a full commit id")
    expected = order["expected_remote_commit"]
    if expected is not None and re.fullmatch(r"[a-f0-9]{40}", expected) is None:
        raise VersionError("Publication order has an invalid expected remote commit")
    if order["force"] is not False:
        raise VersionError("Fractal publication orders cannot authorise force push")
    return {
        "record_type": "publication-order-preflight",
        "record_version": 1,
        "order_sha256": value_sha256(order),
        "route": "governed-publication-command",
        "raw-route-enforcement": "partial-until-bypass-audit-proves-closure",
        "automatic_retry": False,
        "lost_acknowledgement_behavior": "inspect-remote-and-stop",
        "passed": True,
    }


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
