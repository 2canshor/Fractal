from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from fractal.capability_dot import (
    CapabilityDotError,
    select_compatible_implementation,
    validate_candidate_execution,
    validate_candidate_execution_authority,
    validate_capability_dot,
    validate_implementation,
    validate_version_update,
)


def implementation(
    *,
    implementation_id: str = "implementation-python",
    verification_status: str = "unverified",
    compatible: bool = True,
    dot_version: str = "1.0.0",
    provider: str = "python-runtime",
) -> dict:
    return {
        "implementation_id": implementation_id,
        "version": "1.0.0",
        "provider": provider,
        "dependencies": [],
        "capability_requirements": ["input-normalisation"],
        "permissions": {"operations": ["read-input", "emit-output"]},
        "executable_target": {"kind": "python", "ref": "tests.fixture:run"},
        "provenance": {
            "origin": "bounded-candidate",
            "evidence_ids": [f"provenance-{implementation_id}"],
        },
        "compatibility": {"compatible": compatible, "dot_version": dot_version},
        "verification": {
            "status": verification_status,
            "evidence_ids": (
                [f"verification-{implementation_id}"]
                if verification_status == "verified"
                else []
            ),
        },
        "recovery": {
            "strategy": "disable this implementation and restore the predecessor",
            "evidence_ids": [f"recovery-{implementation_id}"],
        },
        "evidence": [f"implementation-{implementation_id}"],
    }


def dot(*, state: str = "candidate", implementations: list[dict] | None = None) -> dict:
    return {
        "record_type": "capability-dot",
        "record_version": 1,
        "dot_id": "bounded-transform",
        "version": "1.0.0",
        "human_name": "Bounded Transform",
        "responsibility": "Transform one bounded input into one auditable output.",
        "inputs": ["bounded input"],
        "outputs": ["auditable output"],
        "preconditions": ["the input is present and within the declared boundary"],
        "side_effects": ["emit one output to the caller"],
        "lifecycle": {
            "state": state,
            "transition_evidence": ["lifecycle-evidence"],
        },
        "evidence": {"evidence_ids": ["dot-evidence"]},
        "recovery": {
            "strategy": "disable the Dot and use the retained predecessor",
            "evidence_ids": ["dot-recovery"],
        },
        "coherence": {
            "coherent_responsibility": True,
            "reuse_rationale": "The same bounded transformation is reused across matching inputs.",
            "boundary_evidence_hooks": [
                {
                    "hook_id": "responsibility-boundary",
                    "type": "boundary",
                    "reason": "The output boundary is explicit and testable.",
                    "evidence_ids": ["boundary-evidence"],
                }
            ],
        },
        "implementations": implementations or [implementation()],
        "trial": {"status": "pending"},
        "verification": {"status": "unverified"},
        "system_review": {"status": "pending"},
        "human_decision": {"status": "pending"},
        "activation": {"status": "inactive"},
    }


def active_dot(*, implementations: list[dict] | None = None) -> dict:
    value = dot(
        state="active",
        implementations=implementations
        or [implementation(verification_status="verified")],
    )
    value["trial"] = {"status": "passed", "evidence_ids": ["trial-evidence"]}
    value["verification"] = {
        "status": "verified",
        "evidence_ids": ["dot-verification"],
        "verified_implementation_ids": [value["implementations"][0]["implementation_id"]],
    }
    value["system_review"] = {"status": "completed", "evidence_ids": ["system-review"]}
    value["human_decision"] = {
        "status": "approved",
        "decision_id": "decision-1",
        "decided_by": "primary-user",
        "evidence_ids": ["decision-evidence"],
    }
    value["activation"] = {
        "status": "active",
        "authorised": True,
        "activated_version": "1.0.0",
        "activation_evidence": [
            {
                "evidence_id": "activation-evidence",
                "dot_version": "1.0.0",
                "authorised": True,
            }
        ],
    }
    return value


def authority(*, expires_at: datetime | None = None, **changes: object) -> dict:
    value = {
        "authority_id": "candidate-execution-1",
        "project_id": "project-a",
        "project_revision": 7,
        "task_id": "task-a",
        "allowed_side_effects": ["emit one output to the caller"],
        "expires_at": (expires_at or datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "persistence_state_change": False,
    }
    value.update(changes)
    return value


def test_candidate_is_valid_but_cannot_claim_active_without_all_dimensions() -> None:
    candidate = validate_capability_dot(dot())
    assert candidate["lifecycle"]["state"] == "candidate"
    assert candidate["activation"]["status"] == "inactive"
    with pytest.raises(CapabilityDotError, match="active Dot requires|transition_evidence"):
        validate_capability_dot({**dot(), "lifecycle": {"state": "active"}})


def test_active_dot_requires_versioned_authorised_activation_and_verified_executable() -> None:
    active = validate_capability_dot(active_dot(), require_active=True)
    assert active["activation"]["activation_evidence"][0]["dot_version"] == "1.0.0"
    assert active["implementations"][0]["executable_target"]

    missing_verification = active_dot(implementations=[implementation()])
    with pytest.raises(CapabilityDotError, match="verified executable"):
        validate_capability_dot(missing_verification)


def test_human_approval_is_independent_from_activation() -> None:
    candidate = dot()
    candidate["human_decision"] = {
        "status": "approved",
        "decision_id": "decision-1",
        "decided_by": "primary-user",
        "evidence_ids": ["decision-evidence"],
    }
    validated = validate_capability_dot(candidate)
    assert validated["human_decision"]["status"] == "approved"
    assert validated["activation"]["status"] == "inactive"
    with pytest.raises(CapabilityDotError, match="active lifecycle"):
        candidate["activation"] = {"status": "active", "authorised": True}
        validate_capability_dot(candidate)


def test_candidate_execution_scope_is_exact_expiring_and_non_persistent() -> None:
    candidate = dot()
    before = deepcopy(candidate)
    plan = validate_candidate_execution(
        candidate,
        authority(),
        project_id="project-a",
        project_revision=7,
        task_id="task-a",
    )
    assert plan["persistence_state_change"] is False
    assert candidate == before

    with pytest.raises(CapabilityDotError, match="different Project"):
        validate_candidate_execution_authority(
            authority(), project_id="project-b", project_revision=7, task_id="task-a"
        )
    with pytest.raises(CapabilityDotError, match="bounded"):
        validate_candidate_execution_authority(
            authority(allowed_side_effects=["*"]),
            project_id="project-a",
            project_revision=7,
            task_id="task-a",
        )
    with pytest.raises(CapabilityDotError, match="persistence"):
        validate_candidate_execution_authority(
            authority(allowed_side_effects=["persist candidate"]),
            project_id="project-a",
            project_revision=7,
            task_id="task-a",
        )
    with pytest.raises(CapabilityDotError, match="expired"):
        validate_candidate_execution_authority(
            authority(expires_at=datetime(2000, 1, 1, tzinfo=UTC)),
            project_id="project-a",
            project_revision=7,
            task_id="task-a",
        )


def test_provider_is_below_dot_unless_intrinsic_responsibility_is_evidenced() -> None:
    provider_independent = validate_capability_dot(dot())
    assert "provider" not in provider_independent
    assert provider_independent["implementations"][0]["provider"] == "python-runtime"

    provider_specific = dot()
    provider_specific["provider_specific"] = {
        "provider_id": "python-runtime",
        "intrinsic_provider_responsibility": {
            "reason_code": "required-native-sandbox",
            "evidence_ids": ["provider-evidence"],
        },
    }
    assert validate_capability_dot(provider_specific)["provider_specific"]

    dishonest = dot()
    dishonest["provider"] = "python-runtime"
    with pytest.raises(CapabilityDotError, match="Provider identity"):
        validate_capability_dot(dishonest)
    missing_reason = dot()
    missing_reason["provider_specific"] = {"provider_id": "python-runtime"}
    with pytest.raises(CapabilityDotError, match="intrinsic_provider_responsibility"):
        validate_capability_dot(missing_reason)


def test_material_change_requires_new_candidate_version_lineage_and_recovery() -> None:
    previous = dot()
    unchanged = validate_version_update(previous, deepcopy(previous))
    assert unchanged["version"] == "1.0.0"

    changed_same_version = deepcopy(previous)
    changed_same_version["responsibility"] = "Transform a different bounded input into output."
    with pytest.raises(CapabilityDotError, match="new version"):
        validate_version_update(previous, changed_same_version)

    next_version = deepcopy(previous)
    next_version["version"] = "1.1.0"
    next_version["lineage"] = {
        "predecessor_dot_id": "bounded-transform",
        "predecessor_version": "1.0.0",
        "change_type": "material",
        "reason": "Make the boundary explicit for a new input shape.",
        "evidence_ids": ["lineage-evidence"],
        "recovery_evidence_ids": ["lineage-recovery"],
    }
    assert validate_version_update(previous, next_version)["version"] == "1.1.0"

    active_previous = active_dot()
    active_next = deepcopy(active_previous)
    active_next["human_name"] = "Changed Active Dot"
    with pytest.raises(CapabilityDotError, match="new version"):
        validate_version_update(active_previous, active_next)


def test_implementation_contract_is_selection_ready_and_method_ref_is_forbidden() -> None:
    verified = validate_implementation(implementation(verification_status="verified"))
    assert verified["executable_target"]["ref"] == "tests.fixture:run"
    assert verified["compatibility"]["dot_version"] == "1.0.0"
    assert verified["verification"]["evidence_ids"]

    invalid = implementation()
    invalid["method_ref"] = "provider-method"
    with pytest.raises(CapabilityDotError, match="method_ref"):
        validate_implementation(invalid)
    missing_target = implementation()
    del missing_target["executable_target"]
    with pytest.raises(CapabilityDotError, match="procedure_ref or executable_target"):
        validate_implementation(missing_target)


def test_no_action_workflow_or_source_authority_can_be_embedded() -> None:
    for forbidden in ("action_authority", "workflow_authority", "source_authority"):
        invalid = dot()
        invalid[forbidden] = {"approved": True}
        with pytest.raises(CapabilityDotError, match="cannot own Dot authority"):
            validate_capability_dot(invalid)


def test_selection_uses_verified_compatible_implementation_only() -> None:
    value = dot(
        implementations=[
            implementation(
                implementation_id="implementation-unverified",
                verification_status="unverified",
            ),
            implementation(
                implementation_id="implementation-verified",
                verification_status="verified",
            ),
        ]
    )
    selected = select_compatible_implementation(value)
    assert selected["implementation_id"] == "implementation-verified"
    with pytest.raises(CapabilityDotError, match="selection-ready"):
        select_compatible_implementation(
            dot(implementations=[implementation(compatible=False)])
        )
