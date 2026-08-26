from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fractal.adapter_hook import handle_hook
from fractal.improvement import TrialBoundary, TrialMeasurement
from fractal.reality import ExecutionGate
from fractal.storage import AuthorityError
from fractal.versioning import (
    PublicationExecutor,
    VersionError,
    VersionStore,
    decide_node_map_change,
    propose_node_map_change,
    trial_node_map_change,
    validate_publication_order,
)

PROJECT_ID = "version-project"
PROJECT_REVISION = 7


def authority_evidence(store: VersionStore, label: str) -> dict[str, str]:
    text = f"approve {label}\n"
    message_id = f"msg-{label}"
    turn_id = f"turn-{label}"
    path = store.root / f"session-{label}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": message_id,
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
                },
            }
        )
        + "\n"
    )
    return {
        "session_path": str(path),
        "turn_id": turn_id,
        "message_id": message_id,
        "message_sha256": hashlib.sha256((text + "\n").encode()).hexdigest(),
    }


def decision_batch() -> dict:
    return {"decision_batch_id": "batch-a", "included": ["decision-a"]}


def architecture_lineage() -> dict:
    return {"structural_gate_passed": True, "receipt_id": "lineage-a"}


def claim_gate_audit() -> dict:
    return {"passed": True, "claim_count": 20, "receipt_id": "claim-gate-a"}


def adapter_boundary_audit(*, live_promotion_eligible: bool = True) -> dict:
    return {
        "passed_for_candidate": True,
        "live_promotion_eligible": live_promotion_eligible,
        "platforms": {"codex": {"staged_smoke_passed": True}},
    }


def preservation_audits() -> dict:
    return {
        "phase_a_pre_build": {"passed": True, "receipt_sha256": "d" * 64},
        "phase_b_post_build_pre_activation": {
            "passed": True,
            "receipt_sha256": "e" * 64,
        },
    }


def apple_acceptance_audit() -> dict:
    value: dict[str, object] = {
        "record_type": "apple-system-version-acceptance",
        "record_version": 1,
        "apple_registry": {
            "source_count": 171,
            "source_manifest_sha256": (
                "40308dfd08e1c7ad3acdf05b659463b1984c69f7dbff96d2c752de0f169bec1c"
            ),
            "index_sha256": (
                "94b10ebc13cbb5dd7542487e8a232b315c191b6fc2e801f15cec5081c502d1d1"
            ),
        },
        "responsibilities": {
            "count": 20,
            "responsibility_ids": [f"RESP-{index:02d}" for index in range(20)],
            "deterministic_alignment_passed": True,
            "active_live_claimed": False,
        },
        "components": {
            "count": 1,
            "deterministic_audit_passed": True,
            "human_acceptance_was_pending_before_this_receipt": True,
            "audit_sha256": "1" * 64,
        },
        "user_surface": {
            "deterministic_audit_clean": True,
            "human_acceptance_was_pending_before_this_receipt": True,
            "audit_sha256": "2" * 64,
        },
        "continuous_improvement": {
            "core": "continuous-improvement",
            "sole_protagonist": "system-review",
            "parallel_lifecycle": False,
        },
        "human_delight": {
            "status": "accepted",
            "accepted_by": "primary-user",
            "scope": "exact-version-batch",
            "evidence_ids": ["primary-user-delight-acceptance"],
        },
        "authority_scope": {
            "project_id": PROJECT_ID,
            "project_revision": PROJECT_REVISION,
            "decision_batch_id": decision_batch()["decision_batch_id"],
        },
        "evidence_ids": ["apple-source-audit", "responsibility-audit", "human-walkthrough"],
    }
    value["receipt_sha256"] = value_sha256_for_test(value)
    return value


def issue_action(store: VersionStore, version: str, action: str, label: str) -> str:
    target, expected_state = store.action_authority_scope(version, action=action)
    return store.authority.issue(
        action=action,
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected_state,
        authority_evidence=authority_evidence(store, label),
    )["receipt_id"]


def verification_plan() -> list[ExecutionGate]:
    root = Path(__file__).parent
    return [
        ExecutionGate(
            gate_id=gate_id,
            command=(sys.executable, "-c", "raise SystemExit(0)"),
            cwd=root,
            materials=("test_versioning.py",),
        )
        for gate_id in sorted(VersionStore.REQUIRED_VERIFICATIONS)
    ]


def component() -> dict:
    return {
        "component_id": "recording-core",
        "version": "1.0.0",
        "sha256": "a" * 64,
        "dependencies": [],
    }


def candidate_inputs(
    version: str,
    *,
    live_promotion_eligible: bool = True,
    plan: list[ExecutionGate] | None = None,
    components: list[dict] | None = None,
) -> dict:
    return {
        "version": version,
        "public_commit": "a" * 40,
        "private_commit": "b" * 40,
        "components": components or [component()],
        "adapter_hashes": {"codex": "c" * 64},
        "migrations": ["project-1.0-to-1.1"],
        "restore_point": {"kind": "manifest", "version": "previous"},
        "verification_plan": plan or verification_plan(),
        "project_id": PROJECT_ID,
        "project_revision": PROJECT_REVISION,
        "decision_batch": decision_batch(),
        "architecture_lineage": architecture_lineage(),
        "claim_gate_audit": claim_gate_audit(),
        "adapter_boundary_audit": adapter_boundary_audit(
            live_promotion_eligible=live_promotion_eligible
        ),
        "preservation_audits": preservation_audits(),
        "apple_acceptance_audit": apple_acceptance_audit(),
    }


def build(
    store: VersionStore,
    version: str,
    *,
    live_promotion_eligible: bool = True,
) -> dict:
    inputs = candidate_inputs(
        version, live_promotion_eligible=live_promotion_eligible
    )
    candidate = store.candidate_input(**inputs)
    target, expected_state = store.build_authority_scope(candidate)
    receipt_id = store.authority.issue(
        action="build",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected_state,
        authority_evidence=authority_evidence(store, f"build-{version}"),
    )["receipt_id"]
    return store.build_candidate(**inputs, authority_receipt_id=receipt_id)


def test_candidate_requires_human_activation_rejection_and_restore(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    first = build(store, "0.1.0-alpha.1")
    assert first["status"] == "candidate"
    assert store.read_active() is None
    wrong_receipt = issue_action(store, "0.1.0-alpha.1", "reject", "wrong-action")
    with pytest.raises(AuthorityError, match="scope"):
        store.activate(
            "0.1.0-alpha.1",
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=wrong_receipt,
        )
    activate_first = issue_action(store, "0.1.0-alpha.1", "activate", "activate-first")
    store.activate(
        "0.1.0-alpha.1",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=activate_first,
    )
    assert store.read_active()["version"] == "0.1.0-alpha.1"

    build(store, "0.1.0-alpha.2")
    reject_second = issue_action(store, "0.1.0-alpha.2", "reject", "reject-second")
    store.reject(
        "0.1.0-alpha.2",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=reject_second,
    )
    assert store.version_state("0.1.0-alpha.2") == "rejected"
    assert store.read_active()["version"] == "0.1.0-alpha.1"
    with pytest.raises(VersionError, match="rejected"):
        store.activate(
            "0.1.0-alpha.2",
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=wrong_receipt,
        )

    build(store, "0.1.0-alpha.3")
    activate_third = issue_action(store, "0.1.0-alpha.3", "activate", "activate-third")
    store.activate(
        "0.1.0-alpha.3",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=activate_third,
    )
    assert store.read_active()["version"] == "0.1.0-alpha.3"
    restore_first = issue_action(store, "0.1.0-alpha.1", "restore", "restore-first")
    store.restore(
        "0.1.0-alpha.1",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=restore_first,
    )
    assert store.read_active()["version"] == "0.1.0-alpha.1"
    assert store.version_state("0.1.0-alpha.3") == "previously-active"


def test_build_runs_reality_checks_and_binds_full_candidate_scope(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "reality")
    manifest = build(store, "0.1.0-alpha.1")
    assert set(manifest["verification"]) == VersionStore.REQUIRED_VERIFICATIONS
    assert len(manifest["verification_receipts"]) == 5
    assert all(
        receipt["byproducts"]["exit_code"] == 0
        for receipt in manifest["verification_receipts"]
    )

    approved = candidate_inputs("0.1.0-alpha.2")
    target, expected_state = store.build_authority_scope(store.candidate_input(**approved))
    receipt = store.authority.issue(
        action="build",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected_state,
        authority_evidence=authority_evidence(store, "full-candidate-scope"),
    )
    changed = candidate_inputs(
        "0.1.0-alpha.2",
        components=[{**component(), "sha256": "f" * 64}],
    )
    with pytest.raises(AuthorityError, match="scope"):
        store.build_candidate(
            **changed,
            authority_receipt_id=receipt["receipt_id"],
        )


def test_failed_reality_gate_prevents_manifest_and_consumes_build_attempt(
    tmp_path: Path,
) -> None:
    store = VersionStore(tmp_path / "failed-reality")
    plan = verification_plan()
    failing_gate = next(item for item in plan if item.gate_id == "tests_passed")
    plan[plan.index(failing_gate)] = ExecutionGate(
        gate_id="tests_passed",
        command=(sys.executable, "-c", "raise SystemExit(9)"),
        cwd=failing_gate.cwd,
        materials=failing_gate.materials,
    )
    inputs = candidate_inputs("0.1.0-alpha.1", plan=plan)
    target, expected_state = store.build_authority_scope(store.candidate_input(**inputs))
    receipt = store.authority.issue(
        action="build",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected_state,
        authority_evidence=authority_evidence(store, "failed-reality"),
    )
    with pytest.raises(VersionError, match="gate failed"):
        store.build_candidate(**inputs, authority_receipt_id=receipt["receipt_id"])
    assert not store._manifest_path("0.1.0-alpha.1").exists()


def test_candidate_build_fails_closed_when_any_gate_is_missing(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    with pytest.raises(VersionError, match="Every build"):
        store.build_candidate(
            version="0.1.0-alpha.1",
            public_commit="a" * 40,
            private_commit="b" * 40,
            components=[component()],
            adapter_hashes={},
            migrations=[],
            restore_point={},
            verification_plan=verification_plan()[:-1],
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            decision_batch=decision_batch(),
            architecture_lineage=architecture_lineage(),
            claim_gate_audit=claim_gate_audit(),
            adapter_boundary_audit=adapter_boundary_audit(),
            preservation_audits=preservation_audits(),
            apple_acceptance_audit=apple_acceptance_audit(),
            authority_receipt_id="missing",
        )


def test_candidate_build_requires_exact_primary_user_apple_acceptance(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "apple-gate")
    inputs = candidate_inputs("0.1.0-alpha.1")
    inputs["apple_acceptance_audit"]["human_delight"]["status"] = "pending"
    unsigned = {
        key: value
        for key, value in inputs["apple_acceptance_audit"].items()
        if key != "receipt_sha256"
    }
    inputs["apple_acceptance_audit"]["receipt_sha256"] = value_sha256_for_test(unsigned)
    with pytest.raises(VersionError, match="Human Delight"):
        store.build_candidate(**inputs, authority_receipt_id="missing")

    inputs = candidate_inputs("0.1.0-alpha.1")
    inputs["apple_acceptance_audit"]["authority_scope"]["project_revision"] += 1
    unsigned = {
        key: value
        for key, value in inputs["apple_acceptance_audit"].items()
        if key != "receipt_sha256"
    }
    inputs["apple_acceptance_audit"]["receipt_sha256"] = value_sha256_for_test(unsigned)
    with pytest.raises(VersionError, match="exact Project decision batch"):
        store.build_candidate(**inputs, authority_receipt_id="missing")


def test_candidate_build_is_idempotent_but_version_content_is_immutable(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    first = build(store, "0.1.0-alpha.1")
    second = build(store, "0.1.0-alpha.1")
    assert second == first
    assert len(store.read_events()) == 1
    with pytest.raises(VersionError, match="different content"):
        store.build_candidate(
            version="0.1.0-alpha.1",
            public_commit="a" * 40,
            private_commit="b" * 40,
            components=[{**component(), "sha256": "d" * 64}],
            adapter_hashes={"codex": "c" * 64},
            migrations=["project-1.0-to-1.1"],
            restore_point={"kind": "manifest", "version": "previous"},
            verification_plan=verification_plan(),
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            decision_batch=decision_batch(),
            architecture_lineage=architecture_lineage(),
            claim_gate_audit=claim_gate_audit(),
            adapter_boundary_audit=adapter_boundary_audit(),
            preservation_audits=preservation_audits(),
            apple_acceptance_audit=apple_acceptance_audit(),
            authority_receipt_id="unused-because-content-diff-fails-first",
        )


def test_manifest_and_active_pointer_integrity_fail_closed(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "versions")
    build(store, "0.1.0-alpha.1")
    manifest_path = store.versions / "0.1.0-alpha.1.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["public_commit"] = "f" * 40
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(VersionError, match="manifest integrity"):
        store.read_manifest("0.1.0-alpha.1")

    store = VersionStore(tmp_path / "pointer")
    build(store, "0.1.0-alpha.1")
    receipt = issue_action(store, "0.1.0-alpha.1", "activate", "pointer-integrity")
    store.activate(
        "0.1.0-alpha.1",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=receipt,
    )
    pointer = json.loads(store.active_pointer.read_text())
    pointer["manifest_sha256"] = "0" * 64
    store.active_pointer.write_text(json.dumps(pointer))
    with pytest.raises(VersionError, match="pointer integrity"):
        store.read_active()


def test_imported_candidate_without_governed_build_event_cannot_activate(
    tmp_path: Path,
) -> None:
    store = VersionStore(tmp_path / "imported")
    manifest = build(store, "0.1.0-alpha.1")
    store.events.write_text("")
    target = {
        "version": "0.1.0-alpha.1",
        "manifest_sha256": manifest["manifest_sha256"],
    }
    receipt = store.authority.issue(
        action="activate",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state={"active_version": None, "version_state": "candidate"},
        authority_evidence=authority_evidence(store, "activate-imported"),
    )
    with pytest.raises(VersionError, match="governed build event"):
        store.activate(
            "0.1.0-alpha.1",
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=receipt["receipt_id"],
        )
    assert store.read_active() is None


def test_pointer_is_compensated_when_event_write_boundary_fails(tmp_path: Path) -> None:
    def inject(point: str) -> None:
        if point == "after-pointer-before-event":
            raise RuntimeError("injected pointer event boundary failure")

    store = VersionStore(tmp_path / "fault", fault_injector=inject)
    build(store, "0.1.0-alpha.1")
    receipt = issue_action(store, "0.1.0-alpha.1", "activate", "activate-fault")
    with pytest.raises(RuntimeError, match="injected"):
        store.activate(
            "0.1.0-alpha.1",
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=receipt,
        )
    assert store.read_active() is None
    assert all(event["action"] != "activate" for event in store.read_events())
    assert store.authority.read(receipt)["state"] == "failed"


def test_candidate_manifest_is_removed_when_build_event_boundary_fails(
    tmp_path: Path,
) -> None:
    def inject(point: str) -> None:
        if point == "after-manifest-before-build-event":
            raise RuntimeError("injected candidate event boundary failure")

    store = VersionStore(tmp_path / "build-fault", fault_injector=inject)
    with pytest.raises(RuntimeError, match="injected"):
        build(store, "0.1.0-alpha.1")
    assert not (store.versions / "0.1.0-alpha.1.json").exists()
    assert store.read_active() is None
    assert store.read_events() == []


def test_staged_adapter_evidence_cannot_promote_candidate_live(tmp_path: Path) -> None:
    store = VersionStore(tmp_path / "staged-only")
    build(store, "0.1.0-alpha.1", live_promotion_eligible=False)
    with pytest.raises(VersionError, match="exact live adapter boundary proof"):
        store.activate(
            "0.1.0-alpha.1",
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id="not-consumed",
        )


def publication_order() -> dict[str, object]:
    return {
        "version": "0.1.0-alpha.4-candidate",
        "repository_id": "2canshor/fractal",
        "remote": "origin",
        "ref": "refs/heads/main",
        "commit": "a" * 40,
        "expected_remote_commit": "b" * 40,
        "force": False,
    }


def publication_bypass_audit(order: dict[str, object]) -> dict[str, object]:
    approved_batch_sha256 = "c" * 64
    active_manifest_sha256 = "d" * 64
    previous_manifest_sha256 = "e" * 64

    def common_receipts() -> dict[str, str]:
        return {
            "approved_batch_receipt_id": "batch-receipt-a",
            "authority_receipt_id": "authority-receipt-a",
            "order_receipt_id": "order-receipt-a",
        }

    return {
        "record_type": "version-route-bypass-audit",
        "record_version": 1,
        "approved_batch_sha256": approved_batch_sha256,
        "active_manifest_sha256": active_manifest_sha256,
        "previous_manifest_sha256": previous_manifest_sha256,
        "routes": [
            {
                "route_id": "version-store-build-candidate",
                "operation": "build",
                "route_class": "governed",
                "enforcement": "receipt-gated",
                "receipts": {
                    **common_receipts(),
                    "approved_batch_sha256": approved_batch_sha256,
                    "candidate_manifest_sha256": active_manifest_sha256,
                },
            },
            {
                "route_id": "version-store-activate",
                "operation": "activate",
                "route_class": "governed",
                "enforcement": "receipt-gated",
                "receipts": {
                    **common_receipts(),
                    "approved_batch_sha256": approved_batch_sha256,
                    "candidate_manifest_sha256": active_manifest_sha256,
                    "activation_receipt_id": "activation-receipt-a",
                },
            },
            {
                "route_id": "governed-publication-command",
                "operation": "publish",
                "route_class": "governed",
                "enforcement": "receipt-gated",
                "receipts": {
                    **common_receipts(),
                    "approved_batch_sha256": approved_batch_sha256,
                    "publication_order_sha256": value_sha256_for_test(order),
                    "preflight_receipt_id": "preflight-receipt-a",
                    "activation_receipt_id": "activation-receipt-a",
                    "active_manifest_sha256": active_manifest_sha256,
                    "fresh_session_receipt_id": "fresh-session-receipt-a",
                },
            },
            {
                "route_id": "raw-git-transport",
                "operation": "publish",
                "route_class": "raw",
                "enforcement": "disabled-fail-closed",
                "receipts": {},
            },
            {
                "route_id": "low-level-publication-api",
                "operation": "publish",
                "route_class": "low-level",
                "enforcement": "disabled-fail-closed",
                "receipts": {},
            },
            {
                "route_id": "version-store-restore",
                "operation": "restore",
                "route_class": "governed",
                "enforcement": "receipt-gated",
                "receipts": {
                    "restore_authority_receipt_id": "restore-authority-a",
                    "restore_order_receipt_id": "restore-order-a",
                    "active_manifest_sha256": active_manifest_sha256,
                    "previous_manifest_sha256": previous_manifest_sha256,
                },
            },
        ],
    }


def value_sha256_for_test(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def activated_publication_store(tmp_path: Path, version: str) -> tuple[VersionStore, dict]:
    store = VersionStore(tmp_path / "runtime" / "system-version")
    manifest = build(store, version)
    store.activate(
        version,
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=issue_action(store, version, "activate", f"activate-{version}"),
    )
    return store, manifest


def trusted_observation_ids(
    store: VersionStore,
    order: dict[str, object],
    repository_root: Path,
) -> tuple[str, str]:
    context = {
        "system_version": order["version"],
        "active_project": {
            "project_id": PROJECT_ID,
            "status": "in_progress",
            "revision": PROJECT_REVISION,
            "current_phase": 14,
        },
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": []},
        "publication_governance": {
            "repository_roots": [str(repository_root)],
            "repository_ids": [order["repository_id"]],
            "trust_receipt_id": "trusted-live-hook-a",
        },
    }
    fresh_output = handle_hook(
        "session-start", context, {"source": "startup", "session_id": "fresh-session-a"}
    )
    fresh = store.record_fresh_session_observation(fresh_output)
    tool_input = {
        "command": (
            "fractal version publish --order order.json "
            f"--order-sha256 {value_sha256_for_test(order)}"
        ),
        "workdir": str(repository_root),
    }
    route_output = handle_hook(
        "pre-tool-use",
        context,
        {"tool_name": "exec_command", "tool_input": tool_input},
    )
    trust = store.record_hook_trust_evidence(hook_trust_evidence())
    route = store.record_publication_route_observation(
        route_output,
        order=order,
        hook_trust_receipt_id=trust["receipt_id"],
    )
    return fresh["receipt_id"], route["receipt_id"]


def hook_trust_evidence() -> dict[str, object]:
    return {
        "record_type": "codex-hook-trust-evidence",
        "status": "verified",
        "hook_count": 1,
        "hook_events": ["PreToolUse"],
        "trusted_hashes": ["a" * 64],
        "transaction": {
            "record_type": "codex-config-transaction-evidence",
            "status": "verified",
            "changed_key_paths": ['hooks.state."pretool".trusted_hash'],
            "before_sha256": "b" * 64,
            "after_sha256": "c" * 64,
            "written_version": "config-version-a",
            "recovery_path": "recovery.json",
            "secret_values_recorded_in_evidence": False,
        },
        "persistent_system_version_activated": False,
    }


class FakeGitRunner:
    def __init__(self, repository_root: Path, *, remote_commit: str | None) -> None:
        self.repository_root = repository_root.resolve()
        self.remote_commit = remote_commit
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.push_count = 0

    def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        self.calls.append((argv, kwargs))
        if "--show-toplevel" in argv:
            return SimpleNamespace(stdout=f"{self.repository_root}\n", stderr="", returncode=0)
        if "get-url" in argv:
            return SimpleNamespace(
                stdout="git@github.com:2canshor/fractal.git\n", stderr="", returncode=0
            )
        if "rev-parse" in argv:
            return SimpleNamespace(stdout=f"{argv[-1]}\n", stderr="", returncode=0)
        if "ls-remote" in argv:
            stdout = "" if self.remote_commit is None else f"{self.remote_commit}\t{argv[-1]}\n"
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if "push" in argv:
            self.push_count += 1
            self.remote_commit = argv[-1].split(":", 1)[0]
            return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)
        raise AssertionError(argv)


def issue_publication_authority(
    store: VersionStore,
    order: dict[str, object],
    fresh_session_receipt_id: str,
    runtime_route_receipt_id: str,
) -> str:
    target, expected = store.publication_authority_scope(
        order,
        fresh_session_receipt_id=fresh_session_receipt_id,
        runtime_route_receipt_id=runtime_route_receipt_id,
    )
    return store.authority.issue(
        action="publish",
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        target=target,
        expected_state=expected,
        authority_evidence=authority_evidence(store, "publish"),
    )["receipt_id"]


def test_publication_order_never_infers_scope_or_force(tmp_path: Path) -> None:
    del tmp_path
    order = publication_order()
    result = validate_publication_order(order, bypass_audit=publication_bypass_audit(order))
    assert result["order_preflight_passed"] is True
    assert result["bypass_audit"]["receipt_schema_passed"] is True
    assert result["raw-route-enforcement"] == "pending-target-real-runtime-proof"
    assert result["publication_allowed"] is False
    assert result["passed"] is False
    with pytest.raises(VersionError, match="incomplete or unexpected"):
        validate_publication_order(
            {key: value for key, value in order.items() if key != "ref"},
            bypass_audit=publication_bypass_audit(order),
        )
    with pytest.raises(VersionError, match="cannot authorise force push"):
        validate_publication_order(
            {**order, "force": True},
            bypass_audit=publication_bypass_audit(order),
        )


def test_publication_bypass_audit_rejects_unknown_missing_and_duplicate_routes() -> None:
    order = publication_order()
    audit = publication_bypass_audit(order)
    routes = audit["routes"]
    assert isinstance(routes, list)

    unknown = {**audit, "routes": [*routes, {**routes[0], "route_id": "shell-push"}]}
    with pytest.raises(VersionError, match="incomplete or contains unknown"):
        validate_publication_order(order, bypass_audit=unknown)

    missing = {**audit, "routes": routes[:-1]}
    with pytest.raises(VersionError, match="incomplete or contains unknown"):
        validate_publication_order(order, bypass_audit=missing)

    duplicate = {**audit, "routes": [*routes, routes[0]]}
    with pytest.raises(VersionError, match="unique records"):
        validate_publication_order(order, bypass_audit=duplicate)

    malformed = {**audit, "routes": [*routes[:-1], {"route_id": ["not", "hashable"]}]}
    with pytest.raises(VersionError, match="require exact route ids"):
        validate_publication_order(order, bypass_audit=malformed)


@pytest.mark.parametrize(
    "missing_receipt",
    [
        "preflight_receipt_id",
        "activation_receipt_id",
        "fresh_session_receipt_id",
    ],
)
def test_raw_publication_route_cannot_skip_lifecycle_proof(
    missing_receipt: str,
) -> None:
    order = publication_order()
    audit = publication_bypass_audit(order)
    routes = audit["routes"]
    assert isinstance(routes, list)
    raw_route = next(route for route in routes if route["route_id"] == "raw-git-transport")
    governed_route = next(
        route for route in routes if route["route_id"] == "governed-publication-command"
    )
    raw_route["enforcement"] = "receipt-gated"
    raw_route["receipts"] = {
        key: value
        for key, value in governed_route["receipts"].items()
        if key != missing_receipt
    }
    with pytest.raises(VersionError, match="receipt family is incomplete"):
        validate_publication_order(order, bypass_audit=audit)


def test_publication_routes_bind_exact_order_batch_and_active_manifest() -> None:
    order = publication_order()
    for field, wrong_value, message in (
        ("approved_batch_sha256", "f" * 64, "approved batch mismatch"),
        ("publication_order_sha256", "f" * 64, "order mismatch"),
        ("active_manifest_sha256", "f" * 64, "active manifest mismatch"),
    ):
        audit = publication_bypass_audit(order)
        routes = audit["routes"]
        assert isinstance(routes, list)
        route = next(
            item for item in routes if item["route_id"] == "governed-publication-command"
        )
        route["receipts"][field] = wrong_value
        with pytest.raises(VersionError, match=message):
            validate_publication_order(order, bypass_audit=audit)


def test_restore_route_binds_exact_active_and_previous_manifests() -> None:
    order = publication_order()
    for field, message in (
        ("active_manifest_sha256", "exact active manifest"),
        ("previous_manifest_sha256", "exact previous manifest"),
    ):
        audit = publication_bypass_audit(order)
        routes = audit["routes"]
        assert isinstance(routes, list)
        restore = next(item for item in routes if item["route_id"] == "version-store-restore")
        restore["receipts"][field] = "f" * 64
        with pytest.raises(VersionError, match=message):
            validate_publication_order(order, bypass_audit=audit)


def test_disabled_raw_route_must_be_empty_and_fail_closed() -> None:
    order = publication_order()
    audit = publication_bypass_audit(order)
    routes = audit["routes"]
    assert isinstance(routes, list)
    raw_route = next(route for route in routes if route["route_id"] == "raw-git-transport")
    raw_route["receipts"] = {"claimed": "but-not-enforced"}
    with pytest.raises(VersionError, match="cannot claim receipts"):
        validate_publication_order(order, bypass_audit=audit)


def test_target_real_route_receipt_is_required_and_cannot_be_forged(tmp_path: Path) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
    result = validate_publication_order(
        order,
        version_store=store,
        fresh_session_receipt_id=fresh_id,
        runtime_route_receipt_id=route_id,
    )
    assert result["runtime_route_closure"] is True
    assert result["publication_allowed"] is True
    assert result["runtime_route"]["target_real"] is True
    with pytest.raises(VersionError, match="not stored"):
        validate_publication_order(
            order,
            version_store=store,
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id="trusted-runtime-receipt-fabricated",
        )
    fabricated = handle_hook(
        "session-start",
        {
            "system_version": version,
            "active_project": {
                "project_id": PROJECT_ID,
                "status": "in_progress",
                "revision": PROJECT_REVISION,
                "current_phase": 14,
            },
            "protected_legacy_roots": [],
            "authority": {"legacy_removal_enabled": True},
        },
        {"source": "startup"},
    )
    fabricated["hookSpecificOutput"]["fractalObservation"]["session_id"] = "forged"
    with pytest.raises(VersionError, match="integrity"):
        store.record_fresh_session_observation(fabricated)


def test_arbitrary_receipt_dict_is_not_an_executor_input(tmp_path: Path) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    with pytest.raises(VersionError, match="Invalid trusted runtime receipt id"):
        store.publication_authority_scope(
            order,
            fresh_session_receipt_id={"fabricated": True},  # type: ignore[arg-type]
            runtime_route_receipt_id="trusted-runtime-receipt-fabricated",
        )


@pytest.mark.parametrize(
    "trust_evidence",
    [
        {},
        {
            "record_type": "codex-hook-trust-evidence",
            "status": "verified",
            "hook_events": ["PreToolUse"],
            "trusted_hashes": [],
        },
    ],
)
def test_publication_observation_rejects_missing_or_minimal_trust_report(
    tmp_path: Path, trust_evidence: dict[str, object]
) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    context = {
        "system_version": version,
        "protected_legacy_roots": [],
        "authority": {"legacy_removal_enabled": True},
        "component_governance": {"managed_roots": []},
        "publication_governance": {
            "repository_roots": [str(repository_root)],
            "repository_ids": [order["repository_id"]],
        },
    }
    output = handle_hook(
        "pre-tool-use",
        context,
        {
            "tool_name": "exec_command",
            "tool_input": {
                "command": (
                    "fractal version publish --order order.json "
                    f"--order-sha256 {value_sha256_for_test(order)}"
                ),
                "workdir": str(repository_root),
            },
        },
    )
    assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert (
        output["hookSpecificOutput"]["fractalObservation"]["trust_status"]
        == "requires-version-store-validation"
    )
    with pytest.raises(VersionError, match="trust evidence"):
        store.record_hook_trust_evidence(trust_evidence)
    with pytest.raises(VersionError, match="not stored"):
        store.record_publication_route_observation(
            output,
            order=order,
            hook_trust_receipt_id="trusted-runtime-receipt-fabricated",
        )


def test_trusted_runtime_ledger_detects_tamper_and_recomputed_event_content(
    tmp_path: Path,
) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    trusted_observation_ids(store, order, repository_root)
    original_lines = store.trusted_observations.read_text().splitlines()
    assert len(original_lines) >= 3

    tampered = [json.loads(line) for line in original_lines]
    tampered[0]["payload"]["receipt"]["version"] = "9.9.9"
    store.trusted_observations.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in tampered) + "\n"
    )
    with pytest.raises(VersionError, match="event hash"):
        store._read_trusted_observation_ledger()

    recomputed = [json.loads(line) for line in original_lines]
    recomputed[0]["occurred_at"] = "recomputed-content"
    unsigned = {key: value for key, value in recomputed[0].items() if key != "event_hash"}
    recomputed[0]["event_hash"] = value_sha256_for_test(unsigned)
    store.trusted_observations.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in recomputed) + "\n"
    )
    with pytest.raises(VersionError, match="chain is broken"):
        store._read_trusted_observation_ledger()


def test_trusted_claim_pair_is_fsync_durable_and_replay_fails_after_crash(
    tmp_path: Path,
) -> None:
    def inject(point: str) -> None:
        if point == "after-trusted-claim-pair-event-fsync":
            raise RuntimeError("crash-after-fsync")

    version = "0.1.0-alpha.4-candidate"
    store = VersionStore(tmp_path / "runtime" / "system-version", fault_injector=inject)
    build(store, version)
    store.activate(
        version,
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=issue_action(store, version, "activate", "activate-ledger-crash"),
    )
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
    with pytest.raises(RuntimeError, match="crash-after-fsync"):
        store.claim_publication_observations(
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id=route_id,
            authority_receipt_id="authority-receipt-crash",
        )
    ledger = store._read_trusted_observation_ledger()
    assert ledger["receipts"][fresh_id]["state"] == "claimed"
    assert ledger["receipts"][route_id]["state"] == "claimed"
    with pytest.raises(VersionError, match="not reusable"):
        store.claim_publication_observations(
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id=route_id,
            authority_receipt_id="authority-receipt-retry",
        )


def test_governed_publication_cas_ack_replay_and_subprocess_boundary(tmp_path: Path) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
    authority = issue_publication_authority(store, order, fresh_id, route_id)
    runner = FakeGitRunner(repository_root, remote_commit=order["expected_remote_commit"])
    executor = PublicationExecutor(store, repository_root, runner=runner)
    ack = executor.publish(
        order,
        fresh_session_receipt_id=fresh_id,
        runtime_route_receipt_id=route_id,
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=authority,
    )
    assert ack["action"] == "publish-ack"
    assert ack["commit"] == order["commit"]
    assert store.authority.read(authority)["state"] == "succeeded"
    assert runner.push_count == 1
    assert all(kwargs["shell"] is False for _argv, kwargs in runner.calls)
    push = next(argv for argv, _kwargs in runner.calls if "push" in argv)
    assert not any("force" in value for value in push)
    runner.remote_commit = order["expected_remote_commit"]
    with pytest.raises(VersionError, match="not reusable"):
        executor.publish(
            order,
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id=route_id,
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=authority,
        )
    assert runner.push_count == 1


def test_publication_authority_rejects_wrong_ref_without_mutation(tmp_path: Path) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    changed = {**order, "ref": "refs/heads/other"}
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
    authority = issue_publication_authority(store, order, fresh_id, route_id)
    _changed_fresh_id, changed_route_id = trusted_observation_ids(
        store, changed, repository_root
    )
    runner = FakeGitRunner(repository_root, remote_commit=order["expected_remote_commit"])
    with pytest.raises(AuthorityError, match="scope"):
        PublicationExecutor(store, repository_root, runner=runner).publish(
            changed,
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id=changed_route_id,
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=authority,
        )
    assert runner.push_count == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"force": True}, "cannot authorise force push"),
        ({"commit": "f" * 40}, "active manifest"),
    ],
)
def test_publication_rejects_force_and_wrong_manifest(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = {**publication_order(), **mutation}
    if order["force"] is True:
        with pytest.raises(VersionError, match=message):
            validate_publication_order(order)
    else:
        repository_root = tmp_path / "Fractal"
        repository_root.mkdir()
        fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
        with pytest.raises(VersionError, match=message):
            store.publication_authority_scope(
                order,
                fresh_session_receipt_id=fresh_id,
                runtime_route_receipt_id=route_id,
            )


def test_publication_wrong_repo_and_changed_remote_fail_before_push(tmp_path: Path) -> None:
    version = "0.1.0-alpha.4-candidate"
    store, _manifest = activated_publication_store(tmp_path, version)
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
    authority = issue_publication_authority(store, order, fresh_id, route_id)
    runner = FakeGitRunner(repository_root, remote_commit="c" * 40)
    executor = PublicationExecutor(store, repository_root, runner=runner)
    with pytest.raises(VersionError, match="remote ref changed"):
        executor.publish(
            order,
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id=route_id,
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=authority,
        )
    assert runner.push_count == 0
    wrong_repo = {**order, "repository_id": "someone/else"}
    with pytest.raises(VersionError, match="repository identity"):
        executor._verify_repository(wrong_repo)


@pytest.mark.parametrize(
    "fault_point",
    [
        "after-publication-push-before-verification",
        "after-publication-verification-before-ack",
        "after-publication-ack-before-authority-finish",
    ],
)
def test_lost_acknowledgement_inspects_then_reconciles_without_retry(
    tmp_path: Path, fault_point: str
) -> None:
    def inject(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("crash")

    version = "0.1.0-alpha.4-candidate"
    store = VersionStore(tmp_path / "runtime" / "system-version", fault_injector=inject)
    _manifest = build(store, version)
    store.activate(
        version,
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=issue_action(store, version, "activate", "activate-crash"),
    )
    order = publication_order()
    repository_root = tmp_path / "Fractal"
    repository_root.mkdir()
    fresh_id, route_id = trusted_observation_ids(store, order, repository_root)
    authority = issue_publication_authority(store, order, fresh_id, route_id)
    runner = FakeGitRunner(repository_root, remote_commit=order["expected_remote_commit"])
    executor = PublicationExecutor(store, repository_root, runner=runner)
    with pytest.raises(VersionError, match="indeterminate"):
        executor.publish(
            order,
            fresh_session_receipt_id=fresh_id,
            runtime_route_receipt_id=route_id,
            project_id=PROJECT_ID,
            project_revision=PROJECT_REVISION,
            authority_receipt_id=authority,
        )
    assert runner.push_count == 1
    ack = executor.reconcile(
        order,
        fresh_session_receipt_id=fresh_id,
        runtime_route_receipt_id=route_id,
        project_id=PROJECT_ID,
        project_revision=PROJECT_REVISION,
        authority_receipt_id=authority,
    )
    assert ack["commit"] == order["commit"]
    assert runner.push_count == 1
    assert store.authority.read(authority)["state"] == "succeeded"


def safe_boundary() -> TrialBoundary:
    return TrialBoundary(*([True] * 12))


@pytest.mark.parametrize("change_type", ["add", "remove", "replace", "merge", "split"])
def test_node_map_changes_require_trial_decision_and_future_version(
    change_type: str,
) -> None:
    active = {"nodes": [{"id": "node-a", "method": "program-a"}]}
    candidate = {"nodes": [{"id": "node-a", "method": "program-b"}]}
    proposal = propose_node_map_change(
        change_type=change_type,
        target_ids=["node-a"],
        active_map=active,
        candidate_map=candidate,
        evidence_ids=["evidence-a"],
    )
    assert proposal["active"] is False
    trialled = trial_node_map_change(
        proposal,
        boundary=safe_boundary(),
        baseline=TrialMeasurement(True, 1.0, 10.0, 1000, 0.99, False),
        candidate=TrialMeasurement(True, 1.0, 8.0, 900, 0.99, False),
    )
    assert trialled["trial_status"] == "candidate-for-review"
    with pytest.raises(AuthorityError, match="primary user"):
        decide_node_map_change(
            trialled,
            decision="approve",
            actor="main-agent",
            human_action=False,
        )
    approved = decide_node_map_change(
        trialled,
        decision="approve",
        actor="primary-user",
        human_action=True,
    )
    assert approved["decision_status"] == "approved-for-version"
    assert approved["active"] is False
    assert approved["restore_map"] == active
