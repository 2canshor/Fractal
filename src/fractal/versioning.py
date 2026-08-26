"""Human-approved System Version, Node-map change, and restore paths."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fractal.apple_alignment import AppleAlignmentError, validate_apple_version_acceptance
from fractal.authority import AuthorityReceiptStore, ReceiptError
from fractal.improvement import TrialBoundary, TrialMeasurement, compare_trial_results
from fractal.models import utc_now
from fractal.reality import (
    ExecutionGate,
    RealityCheckError,
    RealityCheckRunner,
    validate_execution_receipts,
    verification_plan_sha256,
)
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
        self.trusted_observations = self.root / "trusted-runtime-observations.jsonl"
        self.trusted_observations_lock = self.root / ".trusted-runtime-observations.lock"
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
        verification_plan: list[ExecutionGate],
        project_id: str,
        project_revision: int,
        decision_batch: dict[str, Any],
        architecture_lineage: dict[str, Any],
        claim_gate_audit: dict[str, Any],
        adapter_boundary_audit: dict[str, Any],
        preservation_audits: dict[str, Any],
        apple_acceptance_audit: dict[str, Any],
        authority_receipt_id: str,
    ) -> dict[str, Any]:
        """Build an immutable candidate only when every executable gate passes."""
        self._validate_version(version)
        try:
            plan_sha256 = verification_plan_sha256(verification_plan)
        except RealityCheckError as error:
            raise VersionError(str(error)) from error
        if {gate.gate_id for gate in verification_plan} != self.REQUIRED_VERIFICATIONS:
            raise VersionError("Every build, test, adapter, migration, and restore gate must run")
        if (
            re.fullmatch(r"[a-f0-9]{40}", public_commit) is None
            or re.fullmatch(r"[a-f0-9]{40}", private_commit) is None
        ):
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
        try:
            apple_acceptance = validate_apple_version_acceptance(apple_acceptance_audit)
        except AppleAlignmentError as error:
            raise VersionError(str(error)) from error
        apple_scope = apple_acceptance["authority_scope"]
        if (
            apple_scope["project_id"] != project_id
            or apple_scope["project_revision"] != project_revision
            or apple_scope["decision_batch_id"] != decision_batch["decision_batch_id"]
        ):
            raise VersionError("Apple acceptance does not bind the exact Project decision batch")
        for adapter_id, digest in adapter_hashes.items():
            if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise VersionError(f"Invalid adapter digest: {adapter_id}")
        component_ids = [item["component_id"] for item in components]
        if len(component_ids) != len(set(component_ids)):
            raise VersionError("System Version component ids must be unique")
        for component in components:
            if re.fullmatch(r"[a-f0-9]{64}", component["sha256"]) is None:
                raise VersionError(f"Invalid component digest: {component['component_id']}")
        candidate_input = self.candidate_input(
            version=version,
            public_commit=public_commit,
            private_commit=private_commit,
            components=components,
            adapter_hashes=adapter_hashes,
            migrations=migrations,
            restore_point=restore_point,
            verification_plan=verification_plan,
            project_id=project_id,
            project_revision=project_revision,
            decision_batch=decision_batch,
            architecture_lineage=architecture_lineage,
            claim_gate_audit=claim_gate_audit,
            adapter_boundary_audit=adapter_boundary_audit,
            preservation_audits=preservation_audits,
            apple_acceptance_audit=apple_acceptance,
        )
        candidate_input_sha256 = value_sha256(candidate_input)
        path = self._manifest_path(version)
        if path.exists():
            existing = self.read_manifest(version)
            if existing.get("candidate_input_sha256") != candidate_input_sha256:
                raise VersionError(
                    f"System Version already exists with different content: {version}"
                )
            if not self._has_build_event(version, existing["manifest_sha256"]):
                raise VersionError("Existing candidate lacks a governed build event")
            return existing
        target, expected_state = self.build_authority_scope(candidate_input)
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
        try:
            receipts = RealityCheckRunner().run_all(verification_plan)
            verification = validate_execution_receipts(
                receipts,
                required_gate_ids=self.REQUIRED_VERIFICATIONS,
                expected_plan_sha256=plan_sha256,
            )
        except RealityCheckError as error:
            self.authority.finish(
                authority_receipt_id,
                succeeded=False,
                failure=str(error),
            )
            raise VersionError(str(error)) from error
        manifest_content = {
            "record_type": "system-version-manifest",
            "record_version": 3,
            "version": version,
            "public_commit": public_commit,
            "private_commit": private_commit,
            "components": components,
            "adapter_hashes": adapter_hashes,
            "migrations": migrations,
            "restore_point": restore_point,
            "verification": verification,
            "verification_plan": [gate.to_dict() for gate in verification_plan],
            "verification_plan_sha256": plan_sha256,
            "verification_receipts": receipts,
            "project_id": project_id,
            "project_revision": project_revision,
            "decision_batch": decision_batch,
            "architecture_lineage": architecture_lineage,
            "claim_gate_audit": claim_gate_audit,
            "adapter_boundary_audit": adapter_boundary_audit,
            "preservation_audits": preservation_audits,
            "apple_acceptance_audit": apple_acceptance,
            "candidate_input_sha256": candidate_input_sha256,
            "status": "candidate",
        }
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

    @staticmethod
    def candidate_input(
        *,
        version: str,
        public_commit: str,
        private_commit: str,
        components: list[dict[str, Any]],
        adapter_hashes: dict[str, str],
        migrations: list[str],
        restore_point: dict[str, Any],
        verification_plan: list[ExecutionGate],
        project_id: str,
        project_revision: int,
        decision_batch: dict[str, Any],
        architecture_lineage: dict[str, Any],
        claim_gate_audit: dict[str, Any],
        adapter_boundary_audit: dict[str, Any],
        preservation_audits: dict[str, Any],
        apple_acceptance_audit: dict[str, Any],
    ) -> dict[str, Any]:
        """Return every input that primary-user build authority must bind."""
        return {
            "version": version,
            "public_commit": public_commit,
            "private_commit": private_commit,
            "components": components,
            "adapter_hashes": adapter_hashes,
            "migrations": migrations,
            "restore_point": restore_point,
            "verification_plan": [gate.to_dict() for gate in verification_plan],
            "verification_plan_sha256": verification_plan_sha256(verification_plan),
            "project_id": project_id,
            "project_revision": project_revision,
            "decision_batch": decision_batch,
            "architecture_lineage": architecture_lineage,
            "claim_gate_audit": claim_gate_audit,
            "adapter_boundary_audit": adapter_boundary_audit,
            "preservation_audits": preservation_audits,
            "apple_acceptance_audit": apple_acceptance_audit,
        }

    def build_authority_scope(
        self,
        candidate_input: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return an exact scope for the entire candidate, including execution plan."""
        active = self.read_active()
        version = candidate_input["version"]
        return (
            {
                "version": version,
                "candidate_input_sha256": value_sha256(candidate_input),
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

    def publication_authority_scope(
        self,
        order: dict[str, Any],
        *,
        fresh_session_receipt_id: str,
        runtime_route_receipt_id: str,
        allow_claimed_authority_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Bind publish authority to one active manifest and observed remote state."""
        preflight = _validate_publication_fields(order)
        active = self.read_active()
        if active is None:
            raise VersionError("Publication requires an active System Version")
        manifest = self.read_manifest(active["version"])
        if order["version"] != active["version"]:
            raise VersionError("Publication target is not the active System Version")
        repository_name = order["repository_id"].rsplit("/", 1)[-1].lower()
        commit_field = {
            "fractal": "public_commit",
            "fractal-workspace": "private_commit",
        }.get(repository_name)
        if commit_field is None:
            raise VersionError("Publication repository is not a governed Fractal repository")
        if order["commit"] != manifest.get(commit_field):
            raise VersionError("Publication commit is not bound by the active manifest")
        fresh = self._validate_trusted_observation(
            fresh_session_receipt_id,
            expected_type="fresh-session-runtime-receipt",
            version=active["version"],
            manifest_sha256=active["manifest_sha256"],
            active_pointer_sha256=value_sha256(active),
            allow_claimed_authority_id=allow_claimed_authority_id,
        )
        route = self._validate_trusted_observation(
            runtime_route_receipt_id,
            expected_type="publication-runtime-route-receipt",
            version=active["version"],
            manifest_sha256=active["manifest_sha256"],
            order_sha256=preflight["order_sha256"],
            repository_id=order["repository_id"],
            allow_claimed_authority_id=allow_claimed_authority_id,
        )
        self._validate_current_hook_trust_receipt(
            route["trust_receipt_id"],
            version=active["version"],
            manifest_sha256=active["manifest_sha256"],
        )
        if fresh.get("hook_sha256") != publication_hook_sha256():
            raise VersionError("Fresh-session receipt does not bind the loaded Hook")
        if (
            route.get("hook_sha256") != publication_hook_sha256()
            or route.get("executor_sha256") != publication_executor_sha256()
        ):
            raise VersionError("Publication route receipt does not bind loaded runtime code")
        if not str(route.get("trust_receipt_id") or "").strip():
            raise VersionError("Publication route receipt lacks live Hook trust evidence")
        return (
            {
                "version": active["version"],
                "manifest_sha256": active["manifest_sha256"],
                "publication_order_sha256": preflight["order_sha256"],
                "fresh_session_receipt_id": fresh_session_receipt_id,
                "runtime_route_receipt_id": runtime_route_receipt_id,
            },
            {
                "active_version": active["version"],
                "active_manifest_sha256": active["manifest_sha256"],
                "expected_remote_commit": order["expected_remote_commit"],
                "fresh_session_receipt_sha256": fresh["receipt_sha256"],
                "runtime_route_receipt_sha256": route["receipt_sha256"],
            },
        )

    def record_fresh_session_observation(
        self, hook_output: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist one exact, actual SessionStart Hook observation to the trusted ledger."""
        observation = self._typed_hook_observation(hook_output, "fresh-session-hook-observation")
        active = self.read_active()
        if active is None:
            raise VersionError("Fresh-session observation requires an active System Version")
        if observation.get("version") != active["version"]:
            raise VersionError("Fresh-session Hook observation does not bind the active version")
        if observation.get("decision") != "session-start-verified":
            raise VersionError("Fresh-session Hook observation is not verified")
        if not str(observation.get("session_id") or "").strip():
            raise VersionError("Fresh-session Hook observation requires a session id")
        return self._record_trusted_observation(
            {
                "record_type": "fresh-session-runtime-receipt",
                "record_version": 1,
                "issuer": "fractal-version-store",
                "observed_at": observation["observed_at"],
                "version": active["version"],
                "manifest_sha256": active["manifest_sha256"],
                "active_pointer_sha256": value_sha256(active),
                "hook_sha256": observation["hook_sha256"],
                "session_id": observation["session_id"],
                "decision": observation["decision"],
            }
        )

    def record_publication_route_observation(
        self,
        hook_output: dict[str, Any],
        *,
        order: dict[str, Any],
        hook_trust_receipt_id: str,
    ) -> dict[str, Any]:
        """Persist one allowed exact publication Tool observation; never mint via CLI."""
        preflight = _validate_publication_fields(order)
        observation = self._typed_hook_observation(
            hook_output, "publication-route-hook-observation"
        )
        active = self.read_active()
        if active is None or active["version"] != order["version"]:
            raise VersionError("Publication Hook observation is not for the active version")
        expected = {
            "version": active["version"],
            "order_sha256": preflight["order_sha256"],
            "repository_id": order["repository_id"],
            "decision": "allow-governed-publication",
        }
        if any(observation.get(key) != value for key, value in expected.items()):
            raise VersionError("Publication Hook observation does not bind the exact route")
        if observation.get("trust_status") != "requires-version-store-validation":
            raise VersionError("Publication Hook observation has an invalid trust boundary")
        active_manifest_sha256 = active["manifest_sha256"]
        trust = self._validate_current_hook_trust_receipt(
            hook_trust_receipt_id,
            version=active["version"],
            manifest_sha256=active_manifest_sha256,
        )
        _require_sha256(observation.get("tool_input_sha256"), field="Tool input digest")
        if observation.get("tool_name") not in {"exec_command", "Bash"}:
            raise VersionError("Publication Hook observation has an unsupported Tool boundary")
        return self._record_trusted_observation(
            {
                "record_type": "publication-runtime-route-receipt",
                "record_version": 1,
                "issuer": "fractal-version-store",
                "observed_at": observation["observed_at"],
                "version": active["version"],
                "manifest_sha256": active["manifest_sha256"],
                "order_sha256": preflight["order_sha256"],
                "repository_id": order["repository_id"],
                "hook_sha256": observation["hook_sha256"],
                "executor_sha256": publication_executor_sha256(),
                "trust_receipt_id": trust["receipt_id"],
                "tool_name": observation["tool_name"],
                "tool_input_sha256": observation["tool_input_sha256"],
                "decision": observation["decision"],
            }
        )

    def record_hook_trust_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Persist actual AppServer trust/readback evidence against the active manifest."""
        trust = self._validate_hook_trust_evidence(evidence)
        active = self.read_active()
        if active is None:
            raise VersionError("Hook trust evidence requires an active System Version")
        return self._record_trusted_observation(
            {
                "record_type": "hook-trust-runtime-receipt",
                "record_version": 1,
                "issuer": "fractal-version-store",
                "observed_at": utc_now(),
                "version": active["version"],
                "manifest_sha256": active["manifest_sha256"],
                "hook_sha256": publication_hook_sha256(),
                "trusted_hashes_sha256": value_sha256(trust["trusted_hashes"]),
                "trust_evidence_sha256": value_sha256(trust),
                "decision": "verified-live-hook-trust",
            }
        )

    @staticmethod
    def _validate_hook_trust_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        required = {
            "record_type",
            "status",
            "hook_count",
            "hook_events",
            "trusted_hashes",
            "transaction",
            "persistent_system_version_activated",
        }
        if not isinstance(evidence, dict) or set(evidence) != required:
            raise VersionError("Hook trust evidence fields are incomplete or unexpected")
        if (
            evidence["record_type"] != "codex-hook-trust-evidence"
            or evidence["status"] != "verified"
            or evidence["persistent_system_version_activated"] is not False
        ):
            raise VersionError("Hook trust evidence is not a verified Codex trust report")
        events = evidence["hook_events"]
        hashes = evidence["trusted_hashes"]
        if (
            not isinstance(events, list)
            or "PreToolUse" not in events
            or not isinstance(hashes, list)
            or not hashes
            or any(re.fullmatch(r"[a-f0-9]{64}", value) is None for value in hashes)
            or evidence["hook_count"] != len(hashes)
        ):
            raise VersionError("Hook trust evidence lacks the verified PreToolUse hash set")
        transaction = evidence["transaction"]
        transaction_required = {
            "record_type",
            "status",
            "changed_key_paths",
            "before_sha256",
            "after_sha256",
            "written_version",
            "recovery_path",
            "secret_values_recorded_in_evidence",
        }
        if (
            not isinstance(transaction, dict)
            or set(transaction) != transaction_required
            or transaction["record_type"] != "codex-config-transaction-evidence"
            or transaction["status"] != "verified"
            or not transaction["changed_key_paths"]
            or transaction["secret_values_recorded_in_evidence"] is not False
        ):
            raise VersionError("Hook trust transaction lacks verified write/readback evidence")
        for field in ("before_sha256", "after_sha256"):
            _require_sha256(transaction[field], field=f"Hook trust transaction {field}")
        if not str(transaction["written_version"] or "").strip():
            raise VersionError("Hook trust transaction lacks a verified written version")
        return evidence

    def claim_publication_observations(
        self,
        *,
        fresh_session_receipt_id: str,
        runtime_route_receipt_id: str,
        authority_receipt_id: str,
    ) -> None:
        """Atomically claim both trusted receipts; either replay fails closed."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.trusted_observations_lock.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                ledger = self._read_trusted_observation_ledger()
                ids = [fresh_session_receipt_id, runtime_route_receipt_id]
                if len(set(ids)) != 2:
                    raise VersionError("Publication requires distinct trusted receipt ids")
                entries = [self._trusted_entry(ledger, receipt_id) for receipt_id in ids]
                if any(entry["state"] != "issued" for entry in entries):
                    raise VersionError("Trusted runtime receipt is not reusable")
                if {entry["receipt"]["record_type"] for entry in entries} != {
                    "fresh-session-runtime-receipt",
                    "publication-runtime-route-receipt",
                }:
                    raise VersionError("Publication claim-pair has the wrong receipt types")
                self._append_trusted_event(
                    ledger,
                    "claim-pair",
                    {
                        "receipt_ids": ids,
                        "authority_receipt_id": authority_receipt_id,
                    },
                )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def finish_publication_observations(
        self,
        *,
        fresh_session_receipt_id: str,
        runtime_route_receipt_id: str,
        authority_receipt_id: str,
        outcome: str,
    ) -> None:
        if outcome not in {"succeeded", "failed", "indeterminate"}:
            raise VersionError("Unsupported trusted receipt outcome")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.trusted_observations_lock.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                ledger = self._read_trusted_observation_ledger()
                ids = [fresh_session_receipt_id, runtime_route_receipt_id]
                already_finished = True
                for receipt_id in (fresh_session_receipt_id, runtime_route_receipt_id):
                    entry = self._trusted_entry(ledger, receipt_id)
                    if (
                        entry["state"] == outcome
                        and entry.get("authority_receipt_id") == authority_receipt_id
                    ):
                        continue
                    already_finished = False
                    if entry["state"] not in {"claimed", "indeterminate"} or entry.get(
                        "authority_receipt_id"
                    ) != authority_receipt_id:
                        raise VersionError("Trusted runtime receipt claim does not match authority")
                if not already_finished:
                    self._append_trusted_event(
                        ledger,
                        "finish-pair",
                        {
                            "receipt_ids": ids,
                            "authority_receipt_id": authority_receipt_id,
                            "outcome": outcome,
                        },
                    )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _typed_hook_observation(
        self, hook_output: dict[str, Any], expected_type: str
    ) -> dict[str, Any]:
        if not isinstance(hook_output, dict) or set(hook_output) != {"hookSpecificOutput"}:
            raise VersionError("Trusted observation requires exact typed Hook output")
        specific = hook_output["hookSpecificOutput"]
        expected_specific = (
            {"hookEventName", "additionalContext", "fractalObservation"}
            if expected_type == "fresh-session-hook-observation"
            else {
                "hookEventName",
                "permissionDecision",
                "permissionDecisionReason",
                "fractalObservation",
            }
        )
        if not isinstance(specific, dict) or set(specific) != expected_specific:
            raise VersionError("Trusted observation requires the exact Hook output contract")
        if expected_type == "fresh-session-hook-observation":
            if specific["hookEventName"] != "SessionStart":
                raise VersionError("Trusted fresh-session observation has the wrong Hook event")
        elif (
            specific["hookEventName"] != "PreToolUse"
            or specific["permissionDecision"] != "allow"
        ):
            raise VersionError("Trusted publication observation was not allowed by the Hook")
        observation = specific.get("fractalObservation") if isinstance(specific, dict) else None
        if not isinstance(observation, dict) or observation.get("record_type") != expected_type:
            raise VersionError("Trusted observation has the wrong Hook output type")
        unsigned = {key: value for key, value in observation.items() if key != "observation_sha256"}
        if observation.get("observation_sha256") != value_sha256(unsigned):
            raise VersionError("Trusted Hook observation integrity failure")
        if observation.get("issuer") != "fractal-adapter-hook":
            raise VersionError("Trusted Hook observation issuer is invalid")
        if observation.get("hook_sha256") != publication_hook_sha256():
            raise VersionError("Trusted Hook observation does not bind the loaded Hook")
        return observation

    def _record_trusted_observation(self, content: dict[str, Any]) -> dict[str, Any]:
        receipt_id = f"trusted-runtime-receipt-{uuid.uuid4()}"
        receipt = {**content, "receipt_id": receipt_id}
        receipt["receipt_sha256"] = value_sha256(receipt)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.trusted_observations_lock.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                ledger = self._read_trusted_observation_ledger()
                self._append_trusted_event(ledger, "issue", {"receipt": receipt})
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return receipt

    def _validate_trusted_observation(
        self,
        receipt_id: str,
        *,
        expected_type: str,
        allow_claimed_authority_id: str | None = None,
        **scope: Any,
    ) -> dict[str, Any]:
        entry = self._trusted_entry(self._read_trusted_observation_ledger(), receipt_id)
        receipt = entry["receipt"]
        allowed_state = entry["state"] == "issued" or (
            allow_claimed_authority_id is not None
            and entry["state"] in {"claimed", "indeterminate", "succeeded"}
            and entry.get("authority_receipt_id") == allow_claimed_authority_id
        )
        if not allowed_state:
            raise VersionError("Trusted runtime receipt is not reusable")
        if receipt.get("record_type") != expected_type:
            raise VersionError("Trusted runtime receipt has the wrong type")
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if receipt.get("receipt_sha256") != value_sha256(unsigned):
            raise VersionError("Trusted runtime receipt integrity failure")
        if receipt.get("issuer") != "fractal-version-store":
            raise VersionError("Trusted runtime receipt issuer is invalid")
        if any(receipt.get(key) != value for key, value in scope.items()):
            raise VersionError("Trusted runtime receipt does not bind the exact publication")
        return receipt

    def _validate_current_hook_trust_receipt(
        self,
        receipt_id: str,
        *,
        version: str,
        manifest_sha256: str,
    ) -> dict[str, Any]:
        receipt = self._validate_trusted_observation(
            receipt_id,
            expected_type="hook-trust-runtime-receipt",
            version=version,
            manifest_sha256=manifest_sha256,
        )
        ledger = self._read_trusted_observation_ledger()
        matching_ids = [
            stored_id
            for stored_id, entry in ledger["receipts"].items()
            if entry["receipt"].get("record_type") == "hook-trust-runtime-receipt"
            and entry["receipt"].get("version") == version
            and entry["receipt"].get("manifest_sha256") == manifest_sha256
        ]
        if not matching_ids or matching_ids[-1] != receipt_id:
            raise VersionError("Hook trust receipt is stale for the active Hook set")
        if receipt.get("hook_sha256") != publication_hook_sha256():
            raise VersionError("Hook trust receipt does not bind the loaded Hook")
        return receipt

    def _read_trusted_observation_ledger(self) -> dict[str, Any]:
        ledger = {"receipts": {}, "last_event_hash": "0" * 64, "event_count": 0}
        if not self.trusted_observations.exists():
            return ledger
        try:
            lines = self.trusted_observations.read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in lines if line.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VersionError("Trusted runtime observation event stream is unreadable") from error
        for event in events:
            required = {
                "event_id",
                "event_type",
                "occurred_at",
                "payload",
                "previous_event_hash",
                "event_hash",
            }
            if not isinstance(event, dict) or set(event) != required:
                raise VersionError("Trusted runtime observation event contract is invalid")
            unsigned = {key: value for key, value in event.items() if key != "event_hash"}
            if event["previous_event_hash"] != ledger["last_event_hash"]:
                raise VersionError("Trusted runtime observation event chain is broken")
            if event["event_hash"] != value_sha256(unsigned):
                raise VersionError("Trusted runtime observation event hash is invalid")
            self._apply_trusted_event(ledger, event)
            ledger["last_event_hash"] = event["event_hash"]
            ledger["event_count"] += 1
        return ledger

    def _append_trusted_event(
        self, ledger: dict[str, Any], event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        event = {
            "event_id": f"trusted-runtime-event-{uuid.uuid4()}",
            "event_type": event_type,
            "occurred_at": utc_now(),
            "payload": payload,
            "previous_event_hash": ledger["last_event_hash"],
        }
        event["event_hash"] = value_sha256(event)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.trusted_observations.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._inject(f"after-trusted-{event_type}-event-fsync")
        return event

    @staticmethod
    def _apply_trusted_event(ledger: dict[str, Any], event: dict[str, Any]) -> None:
        payload = event["payload"]
        event_type = event["event_type"]
        if event_type == "issue":
            if not isinstance(payload, dict) or set(payload) != {"receipt"}:
                raise VersionError("Trusted issue event payload is invalid")
            receipt = payload["receipt"]
            receipt_id = receipt.get("receipt_id") if isinstance(receipt, dict) else None
            if not isinstance(receipt_id, str) or receipt_id in ledger["receipts"]:
                raise VersionError("Trusted issue event receipt id is invalid or duplicate")
            unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
            if receipt.get("receipt_sha256") != value_sha256(unsigned):
                raise VersionError("Trusted issue receipt integrity failure")
            ledger["receipts"][receipt_id] = {
                "receipt": receipt,
                "state": "issued",
                "authority_receipt_id": None,
            }
            return
        if event_type not in {"claim-pair", "finish-pair"} or not isinstance(payload, dict):
            raise VersionError("Trusted runtime observation event type is invalid")
        required = {"receipt_ids", "authority_receipt_id"}
        if event_type == "finish-pair":
            required.add("outcome")
        if set(payload) != required:
            raise VersionError("Trusted pair event payload is invalid")
        ids = payload["receipt_ids"]
        if not isinstance(ids, list) or len(ids) != 2 or len(set(ids)) != 2:
            raise VersionError("Trusted pair event requires two distinct receipt ids")
        entries = [VersionStore._trusted_entry(ledger, receipt_id) for receipt_id in ids]
        authority_receipt_id = payload["authority_receipt_id"]
        if event_type == "claim-pair":
            if any(entry["state"] != "issued" for entry in entries):
                raise VersionError("Trusted claim-pair reuses a receipt")
            for entry in entries:
                entry["state"] = "claimed"
                entry["authority_receipt_id"] = authority_receipt_id
            return
        outcome = payload["outcome"]
        if outcome not in {"succeeded", "failed", "indeterminate"}:
            raise VersionError("Trusted finish-pair outcome is invalid")
        if any(
            entry["state"] not in {"claimed", "indeterminate"}
            or entry["authority_receipt_id"] != authority_receipt_id
            for entry in entries
        ):
            raise VersionError("Trusted finish-pair authority or state is invalid")
        for entry in entries:
            entry["state"] = outcome

    @staticmethod
    def _trusted_entry(ledger: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        if not isinstance(receipt_id, str) or not receipt_id.startswith("trusted-runtime-receipt-"):
            raise VersionError("Invalid trusted runtime receipt id")
        entry = ledger["receipts"].get(receipt_id)
        if not isinstance(entry, dict):
            raise VersionError("Trusted runtime receipt is not stored")
        return entry

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


_VERSION_ROUTE_CONTRACTS: dict[str, tuple[str, str]] = {
    "version-store-build-candidate": ("build", "governed"),
    "version-store-activate": ("activate", "governed"),
    "governed-publication-command": ("publish", "governed"),
    "raw-git-transport": ("publish", "raw"),
    "low-level-publication-api": ("publish", "low-level"),
    "version-store-restore": ("restore", "governed"),
}

_COMMON_ROUTE_RECEIPTS = {
    "approved_batch_receipt_id",
    "authority_receipt_id",
    "order_receipt_id",
}

_OPERATION_ROUTE_RECEIPTS = {
    "build": {"approved_batch_sha256", "candidate_manifest_sha256"},
    "activate": {
        "approved_batch_sha256",
        "candidate_manifest_sha256",
        "activation_receipt_id",
    },
    "publish": {
        "approved_batch_sha256",
        "publication_order_sha256",
        "preflight_receipt_id",
        "activation_receipt_id",
        "active_manifest_sha256",
        "fresh_session_receipt_id",
    },
    "restore": {
        "restore_authority_receipt_id",
        "restore_order_receipt_id",
        "active_manifest_sha256",
        "previous_manifest_sha256",
    },
}


def _module_sha256(module_path: Path) -> str:
    return __import__("hashlib").sha256(module_path.read_bytes()).hexdigest()


def publication_executor_sha256() -> str:
    """Return the hash of the exact executor implementation loaded at runtime."""
    return _module_sha256(Path(__file__))


def publication_hook_sha256() -> str:
    """Return the hash of the exact publication guard implementation."""
    from fractal import adapter_hook

    return _module_sha256(Path(adapter_hook.__file__))


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise VersionError(f"Publication bypass audit requires exact {field}")
    return value


def _validate_route_bypass_audit(
    audit: dict[str, Any],
    *,
    order_sha256: str,
) -> dict[str, Any]:
    """Validate the complete known lifecycle-route inventory and its receipt bindings."""
    required = {
        "record_type",
        "record_version",
        "approved_batch_sha256",
        "active_manifest_sha256",
        "previous_manifest_sha256",
        "routes",
    }
    if not isinstance(audit, dict) or set(audit) != required:
        raise VersionError("Publication bypass audit fields are incomplete or unexpected")
    if audit["record_type"] != "version-route-bypass-audit" or audit["record_version"] != 1:
        raise VersionError("Publication bypass audit has an unsupported record contract")

    approved_batch_sha256 = _require_sha256(
        audit["approved_batch_sha256"], field="approved batch digest"
    )
    active_manifest_sha256 = _require_sha256(
        audit["active_manifest_sha256"], field="active manifest digest"
    )
    previous_manifest_sha256 = _require_sha256(
        audit["previous_manifest_sha256"], field="previous manifest digest"
    )
    routes = audit["routes"]
    if not isinstance(routes, list):
        raise VersionError("Publication bypass audit routes must be a list")

    if any(
        not isinstance(route, dict)
        or not isinstance(route.get("route_id"), str)
        or not route["route_id"].strip()
        for route in routes
    ):
        raise VersionError("Publication bypass audit routes require exact route ids")
    route_ids = [route["route_id"] for route in routes]
    if len(set(route_ids)) != len(route_ids):
        raise VersionError("Publication bypass audit route ids must be unique records")
    unknown = set(route_ids) - set(_VERSION_ROUTE_CONTRACTS)
    missing = set(_VERSION_ROUTE_CONTRACTS) - set(route_ids)
    if unknown or missing:
        raise VersionError(
            "Publication bypass audit route inventory is incomplete or contains unknown routes"
        )

    for route in routes:
        route_id = route["route_id"]
        operation, route_class = _VERSION_ROUTE_CONTRACTS[route_id]
        if set(route) != {
            "route_id",
            "operation",
            "route_class",
            "enforcement",
            "receipts",
        }:
            raise VersionError(f"Publication bypass audit route has unexpected fields: {route_id}")
        if route["operation"] != operation or route["route_class"] != route_class:
            raise VersionError(f"Publication bypass audit route identity mismatch: {route_id}")
        if route["enforcement"] not in {"receipt-gated", "disabled-fail-closed"}:
            raise VersionError(f"Publication bypass audit route is not fail closed: {route_id}")

        receipts = route["receipts"]
        if not isinstance(receipts, dict):
            raise VersionError(f"Publication bypass audit route receipts are invalid: {route_id}")
        if route["enforcement"] == "disabled-fail-closed":
            if receipts:
                raise VersionError(
                    f"Disabled publication bypass audit route cannot claim receipts: {route_id}"
                )
            continue

        required_receipts = set(_OPERATION_ROUTE_RECEIPTS[operation])
        if operation != "restore":
            required_receipts.update(_COMMON_ROUTE_RECEIPTS)
        if set(receipts) != required_receipts:
            raise VersionError(
                f"Publication bypass audit receipt family is incomplete or unexpected: {route_id}"
            )
        for field, value in receipts.items():
            if field.endswith("_sha256"):
                _require_sha256(value, field=f"{route_id} {field}")
            elif not isinstance(value, str) or not value.strip():
                raise VersionError(
                    f"Publication bypass audit requires {route_id} {field}"
                )

        if operation != "restore" and receipts["approved_batch_sha256"] != approved_batch_sha256:
            raise VersionError(f"Publication bypass audit approved batch mismatch: {route_id}")
        if operation == "publish":
            if receipts["publication_order_sha256"] != order_sha256:
                raise VersionError(f"Publication bypass audit order mismatch: {route_id}")
            if receipts["active_manifest_sha256"] != active_manifest_sha256:
                raise VersionError(f"Publication bypass audit active manifest mismatch: {route_id}")
        if operation == "restore":
            if receipts["active_manifest_sha256"] != active_manifest_sha256:
                raise VersionError("Restore route does not bind the exact active manifest")
            if receipts["previous_manifest_sha256"] != previous_manifest_sha256:
                raise VersionError("Restore route does not bind the exact previous manifest")

    return {
        "record_type": "version-route-bypass-audit-validation",
        "record_version": 1,
        "audit_sha256": value_sha256(audit),
        "route_count": len(routes),
        "known_route_count": len(_VERSION_ROUTE_CONTRACTS),
        "receipt_schema_passed": True,
        "runtime_enforcement_proven": False,
    }


def _validate_publication_fields(order: dict[str, Any]) -> dict[str, Any]:
    """Validate the immutable, no-force publication target."""
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
    return {"order_sha256": value_sha256(order)}


def validate_publication_order(
    order: dict[str, Any],
    *,
    bypass_audit: dict[str, Any] | None = None,
    version_store: VersionStore | None = None,
    fresh_session_receipt_id: str | None = None,
    runtime_route_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Derive publication readiness from preflight plus target-real runtime evidence."""
    preflight = _validate_publication_fields(order)
    order_sha256 = preflight["order_sha256"]
    audit_validation = (
        _validate_route_bypass_audit(bypass_audit, order_sha256=order_sha256)
        if bypass_audit is not None
        else None
    )
    closure_blockers: list[str] = []
    runtime_route_closure = False
    if (
        version_store is None
        or fresh_session_receipt_id is None
        or runtime_route_receipt_id is None
    ):
        closure_blockers.append(
            "Stored single-use Hook and fresh-session receipt ids are not available."
        )
    else:
        version_store.publication_authority_scope(
            order,
            fresh_session_receipt_id=fresh_session_receipt_id,
            runtime_route_receipt_id=runtime_route_receipt_id,
        )
        runtime_route_closure = True
    return {
        "record_type": "publication-order-preflight",
        "record_version": 3,
        "order_sha256": order_sha256,
        "route": "governed-publication-command",
        "order_preflight_passed": True,
        "bypass_audit": audit_validation,
        "runtime_route": (
            {
                "fresh_session_receipt_id": fresh_session_receipt_id,
                "runtime_route_receipt_id": runtime_route_receipt_id,
                "source": "version-store-trusted-runtime-ledger",
                "target_real": True,
            }
            if runtime_route_closure
            else None
        ),
        "runtime_route_closure": runtime_route_closure,
        "raw-route-enforcement": (
            "fractal-owned-routes-closed"
            if runtime_route_closure
            else "pending-target-real-runtime-proof"
        ),
        "closure_blockers": closure_blockers,
        "automatic_retry": False,
        "lost_acknowledgement_behavior": "inspect-remote-and-stop",
        "publication_allowed": runtime_route_closure,
        "passed": runtime_route_closure,
    }


class PublicationExecutor:
    """Publish one activated commit with CAS checks and a canonical acknowledgement."""

    def __init__(
        self,
        store: VersionStore,
        repository_root: Path,
        *,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.store = store
        self.repository_root = Path(repository_root).resolve()
        self.runner = runner

    def publish(
        self,
        order: dict[str, Any],
        *,
        fresh_session_receipt_id: str,
        runtime_route_receipt_id: str,
        project_id: str,
        project_revision: int,
        authority_receipt_id: str,
        bypass_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute exactly one no-force publication and verify its remote acknowledgement."""
        preflight = validate_publication_order(
            order,
            bypass_audit=bypass_audit,
            version_store=self.store,
            fresh_session_receipt_id=fresh_session_receipt_id,
            runtime_route_receipt_id=runtime_route_receipt_id,
        )
        if not preflight["publication_allowed"]:
            raise VersionError("Publication runtime route closure is not proven")
        target, expected_state = self.store.publication_authority_scope(
            order,
            fresh_session_receipt_id=fresh_session_receipt_id,
            runtime_route_receipt_id=runtime_route_receipt_id,
        )
        self._verify_repository(order)
        observed = self._remote_commit(order["remote"], order["ref"])
        if observed != order["expected_remote_commit"]:
            raise VersionError("Publication CAS failed: remote ref changed before authority claim")
        try:
            self.store.authority.claim(
                authority_receipt_id,
                action="publish",
                project_id=project_id,
                project_revision=project_revision,
                target=target,
                expected_state=expected_state,
            )
        except ReceiptError as error:
            raise AuthorityError(str(error)) from error
        try:
            self.store.claim_publication_observations(
                fresh_session_receipt_id=fresh_session_receipt_id,
                runtime_route_receipt_id=runtime_route_receipt_id,
                authority_receipt_id=authority_receipt_id,
            )
        except Exception as error:
            self.store.authority.finish(
                authority_receipt_id,
                succeeded=False,
                failure=str(error),
            )
            raise
        push_attempted = False
        try:
            self._assert_active_target(order, target)
            if self._remote_commit(order["remote"], order["ref"]) != expected_state[
                "expected_remote_commit"
            ]:
                self.store.authority.finish(
                    authority_receipt_id,
                    succeeded=False,
                    failure="Publication CAS failed after authority claim",
                )
                raise VersionError("Publication CAS failed after authority claim")
            self.store._inject("after-publication-claim-before-push")
            push_attempted = True
            completed = self._run(
                [
                    "git",
                    "-C",
                    str(self.repository_root),
                    "push",
                    "--porcelain",
                    order["remote"],
                    f"{order['commit']}:{order['ref']}",
                ],
                check=False,
            )
            self.store._inject("after-publication-push-before-verification")
            remote_after = self._remote_commit(order["remote"], order["ref"])
            if remote_after != order["commit"]:
                raise VersionError(self._command_failure(completed))
            self.store._inject("after-publication-verification-before-ack")
            acknowledgement = self._append_ack(
                order,
                authority_receipt_id=authority_receipt_id,
                reconciled=False,
                runtime_route_receipt_id=runtime_route_receipt_id,
            )
            self.store._inject("after-publication-ack-before-authority-finish")
            self.store.authority.finish(authority_receipt_id, succeeded=True)
            self.store.finish_publication_observations(
                fresh_session_receipt_id=fresh_session_receipt_id,
                runtime_route_receipt_id=runtime_route_receipt_id,
                authority_receipt_id=authority_receipt_id,
                outcome="succeeded",
            )
            return acknowledgement
        except Exception as error:
            inspected, remote = self._inspect_remote(order["remote"], order["ref"])
            if push_attempted and (
                not inspected
                or remote == order["commit"]
                or remote != order["expected_remote_commit"]
            ):
                self.store.finish_publication_observations(
                    fresh_session_receipt_id=fresh_session_receipt_id,
                    runtime_route_receipt_id=runtime_route_receipt_id,
                    authority_receipt_id=authority_receipt_id,
                    outcome="indeterminate",
                )
                raise VersionError(
                    "Publication acknowledgement is indeterminate; inspect remote and reconcile "
                    "this exact target without retrying the push"
                ) from error
            receipt = self.store.authority.read(authority_receipt_id)
            if receipt["state"] == "claimed":
                self.store.authority.finish(
                    authority_receipt_id,
                    succeeded=False,
                    failure=str(error),
                )
            self.store.finish_publication_observations(
                fresh_session_receipt_id=fresh_session_receipt_id,
                runtime_route_receipt_id=runtime_route_receipt_id,
                authority_receipt_id=authority_receipt_id,
                outcome="failed",
            )
            if isinstance(error, VersionError):
                raise
            raise VersionError(str(error)) from error

    def reconcile(
        self,
        order: dict[str, Any],
        *,
        fresh_session_receipt_id: str,
        runtime_route_receipt_id: str,
        project_id: str,
        project_revision: int,
        authority_receipt_id: str,
    ) -> dict[str, Any]:
        """Inspect only; close a lost acknowledgement when the exact target is remote."""
        target, expected_state = self.store.publication_authority_scope(
            order,
            fresh_session_receipt_id=fresh_session_receipt_id,
            runtime_route_receipt_id=runtime_route_receipt_id,
            allow_claimed_authority_id=authority_receipt_id,
        )
        receipt = self.store.authority.read(authority_receipt_id)
        expected_receipt = {
            "action": "publish",
            "project_id": project_id,
            "project_revision": project_revision,
            "target": target,
            "expected_state": expected_state,
        }
        if any(receipt.get(key) != value for key, value in expected_receipt.items()):
            raise VersionError("Publication reconciliation scope does not match authority")
        if receipt["state"] not in {"claimed", "succeeded"}:
            raise VersionError("Publication reconciliation requires a claimed exact authority")
        self._verify_repository(order)
        inspected, remote = self._inspect_remote(order["remote"], order["ref"])
        if not inspected:
            raise VersionError(
                "Publication remains indeterminate: exact remote inspection failed"
            )
        if remote != order["commit"]:
            raise VersionError("Publication remains indeterminate: remote is not the exact target")
        acknowledgement = self._find_ack(order, authority_receipt_id)
        if acknowledgement is None:
            acknowledgement = self._append_ack(
                order,
                authority_receipt_id=authority_receipt_id,
                reconciled=True,
                runtime_route_receipt_id=runtime_route_receipt_id,
            )
        if receipt["state"] == "claimed":
            self.store.authority.finish(authority_receipt_id, succeeded=True)
        self.store.finish_publication_observations(
            fresh_session_receipt_id=fresh_session_receipt_id,
            runtime_route_receipt_id=runtime_route_receipt_id,
            authority_receipt_id=authority_receipt_id,
            outcome="succeeded",
        )
        return acknowledgement

    def _verify_repository(self, order: dict[str, Any]) -> None:
        top = self._run(
            ["git", "-C", str(self.repository_root), "rev-parse", "--show-toplevel"]
        ).stdout.strip()
        if Path(top).resolve() != self.repository_root:
            raise VersionError("Publication repository root is not exact")
        remote_url = self._run(
            ["git", "-C", str(self.repository_root), "remote", "get-url", order["remote"]]
        ).stdout.strip()
        identity = re.sub(r"\.git$", "", remote_url.rstrip("/"))
        identity = re.sub(r"^(?:https?://|ssh://)?(?:git@)?github\.com[:/]", "", identity)
        if identity != order["repository_id"]:
            raise VersionError("Publication repository identity does not match the order")
        local = self._run(
            ["git", "-C", str(self.repository_root), "rev-parse", order["commit"]]
        ).stdout.strip()
        if local != order["commit"]:
            raise VersionError("Publication commit is not an exact local object")

    def _assert_active_target(self, order: dict[str, Any], target: dict[str, Any]) -> None:
        active = self.store.read_active()
        if active is None or (
            active["version"], active["manifest_sha256"]
        ) != (target["version"], target["manifest_sha256"]):
            raise VersionError("Active System Version changed after publication authority")
        if value_sha256(order) != target["publication_order_sha256"]:
            raise VersionError("Publication order changed after authority was issued")

    def _remote_commit(self, remote: str, ref: str) -> str | None:
        result = self._run(
            ["git", "-C", str(self.repository_root), "ls-remote", "--refs", remote, ref]
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return None
        if len(lines) != 1:
            raise VersionError("Publication remote inspection returned an ambiguous ref")
        commit, observed_ref = lines[0].split("\t", 1)
        if observed_ref != ref or re.fullmatch(r"[a-f0-9]{40}", commit) is None:
            raise VersionError("Publication remote inspection returned an invalid ref")
        return commit

    def _inspect_remote(self, remote: str, ref: str) -> tuple[bool, str | None]:
        try:
            return True, self._remote_commit(remote, ref)
        except Exception:
            return False, None

    def _append_ack(
        self,
        order: dict[str, Any],
        *,
        authority_receipt_id: str,
        reconciled: bool,
        runtime_route_receipt_id: str,
    ) -> dict[str, Any]:
        existing = self._find_ack(order, authority_receipt_id)
        if existing is not None:
            return existing
        self.store._append_event(
            "publish-ack",
            order["version"],
            "primary-user",
            authority_receipt_id=authority_receipt_id,
            manifest_sha256=self.store.read_active()["manifest_sha256"],
            publication_order_sha256=value_sha256(order),
            repository_id=order["repository_id"],
            remote=order["remote"],
            ref=order["ref"],
            commit=order["commit"],
            runtime_route_receipt_id=runtime_route_receipt_id,
            reconciled=reconciled,
        )
        acknowledgement = self._find_ack(order, authority_receipt_id)
        if acknowledgement is None:
            raise VersionError("Canonical publication acknowledgement was not persisted")
        return acknowledgement

    def _find_ack(
        self, order: dict[str, Any], authority_receipt_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                event
                for event in reversed(self.store.read_events())
                if event.get("action") == "publish-ack"
                and event.get("authority_receipt_id") == authority_receipt_id
                and event.get("publication_order_sha256") == value_sha256(order)
                and event.get("commit") == order["commit"]
            ),
            None,
        )

    def _run(self, argv: list[str], *, check: bool = True) -> Any:
        try:
            return self.runner(
                argv,
                check=check,
                capture_output=True,
                text=True,
                shell=False,
            )
        except subprocess.CalledProcessError as error:
            raise VersionError(
                (error.stderr or error.stdout or "Git publication command failed").strip()
            ) from error

    @staticmethod
    def _command_failure(completed: Any) -> str:
        detail = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "")).strip()
        return detail or "Git publication command did not publish the exact target"


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
