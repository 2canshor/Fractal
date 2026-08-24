from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from fractal.improvement import TrialBoundary, TrialMeasurement
from fractal.reality import ExecutionGate
from fractal.storage import AuthorityError
from fractal.versioning import (
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
            authority_receipt_id="missing",
        )


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


def test_publication_order_never_infers_scope_or_force(tmp_path: Path) -> None:
    del tmp_path
    order = {
        "version": "0.1.0-alpha.4-candidate",
        "repository_id": "2canshor/fractal",
        "remote": "origin",
        "ref": "refs/heads/main",
        "commit": "a" * 40,
        "expected_remote_commit": "b" * 40,
        "force": False,
    }
    assert validate_publication_order(order)["passed"] is True
    with pytest.raises(VersionError, match="incomplete or unexpected"):
        validate_publication_order({key: value for key, value in order.items() if key != "ref"})
    with pytest.raises(VersionError, match="cannot authorise force push"):
        validate_publication_order({**order, "force": True})


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
