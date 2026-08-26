from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from fractal.capability_workflow import (
    CompositionValidationError,
    WorkflowError,
    WorkflowValidationError,
    build_workflow,
    compose_execution,
    record_execution_evidence,
    revise_workflow,
    validate_execution_composition,
    validate_workflow,
    validate_workflow_version_update,
)

ROOT = Path(__file__).parents[1]
EXPIRY = "2099-01-01T00:00:00Z"


def dot(dot_id: str, state: str = "active") -> dict:
    return {
        "record_type": "capability-dot",
        "record_version": 1,
        "dot_id": dot_id,
        "version": "1.0.0",
        "lifecycle": {"status": state},
    }


def workflow(*, status: str = "candidate", dot_state: str | None = None) -> dict:
    dot_state = dot_state or status
    active_dimensions = (
        {
            "trial": {
                "status": "passed",
                "workflow_version": "1.0.0",
                "evidence_ids": ["workflow-trial"],
            },
            "verification": {
                "status": "verified",
                "evidence_ids": ["workflow-verification"],
            },
            "system_review": {
                "status": "completed",
                "review_id": "system-review-1",
                "workflow_version": "1.0.0",
                "evidence_ids": ["system-review-evidence"],
            },
            "human_decision": {
                "status": "approved",
                "decision_id": "decision-1",
                "workflow_version": "1.0.0",
                "decided_by": "primary-user",
                "evidence_ids": ["decision-evidence"],
            },
            "activation": {
                "status": "active",
                "authorised": True,
                "activated_version": "1.0.0",
                "activation_evidence": [
                    {
                        "evidence_id": "activation-evidence",
                        "workflow_version": "1.0.0",
                        "system_version": "1.0.0-test",
                        "authority": "system-version",
                        "authorised": True,
                    }
                ],
            },
            "transition_evidence": ["workflow-transition"],
        }
        if status == "active"
        else {}
    )
    return build_workflow(
        workflow_id="verify-evidence",
        version="1.0.0",
        human_name="Verify evidence",
        match_contract={"intent": "verify", "required_signals": ["evidence"]},
        inputs=["request"],
        outputs=["verified-result"],
        dot_refs=[
            {
                "sequence": 1,
                "dot_id": "verify-dot",
                "version": "1.0.0",
                "lifecycle": dot_state,
            }
        ],
        success_contract={"checks": ["result is evidenced"]},
        side_effect_contract={"allowed": ["read"], "forbidden": ["send"]},
        recovery={"failure": "return evidence and stop"},
        provenance={"evidence_refs": ["extraction-1"]},
        trial=active_dimensions.get("trial"),
        verification=active_dimensions.get("verification"),
        system_review=active_dimensions.get("system_review"),
        human_decision=active_dimensions.get("human_decision"),
        activation=active_dimensions.get("activation"),
        transition_evidence=active_dimensions.get("transition_evidence"),
        status=status,
    )


def composition(
    *,
    composition_id: str = "trial-1",
    candidate_dot: bool = False,
    workflows: object = None,
    dots: object = None,
    execution_authority: dict | None = None,
) -> dict:
    state = "candidate" if candidate_dot else "active"
    return compose_execution(
        composition_id=composition_id,
        project_id="project-a",
        task_id="task-a",
        inputs=["request"],
        outputs=["result"],
        steps=[
            {
                "step_id": "step-1",
                "sequence": 1,
                "kind": "workflow",
                "ref": {
                    "workflow_id": "verify-evidence",
                    "version": "1.0.0",
                    "lifecycle": "active",
                },
            },
            *(
                [
                    {
                        "step_id": "step-2",
                        "sequence": 2,
                        "kind": "dot",
                        "ref": {
                            "dot_id": "trial-dot",
                            "version": "1.0.0",
                            "lifecycle": state,
                        },
                    }
                ]
                if candidate_dot
                else []
            ),
        ],
        verification={"checks": ["output matches contract"]},
        expiry={"expires_at": EXPIRY},
        allowed_side_effects=["read"],
        recovery={"failure": "stop and retain evidence"},
        evidence_refs=["trial-evidence"],
        execution_authority=execution_authority,
        workflows=workflows,
        dots=dots,
    )


def test_workflow_and_composition_schemas_are_valid_and_distinct() -> None:
    workflow_schema = json.loads(
        (ROOT / "src/fractal/schemas/capability-workflow.schema.json").read_text()
    )
    composition_schema = json.loads(
        (ROOT / "src/fractal/schemas/execution-composition.schema.json").read_text()
    )
    Draft202012Validator.check_schema(workflow_schema)
    Draft202012Validator.check_schema(composition_schema)
    assert workflow()["record_type"] == "capability-workflow"
    assert composition()["record_type"] == "execution-composition"


def test_ordered_dot_refs_and_duplicate_rules_are_fail_closed() -> None:
    invalid = workflow()
    invalid["dot_refs"][0]["sequence"] = 2
    with pytest.raises(WorkflowValidationError, match="contiguous"):
        validate_workflow(invalid)

    duplicate = workflow()
    duplicate["dot_refs"].append(copy.deepcopy(duplicate["dot_refs"][0]))
    with pytest.raises(WorkflowValidationError, match="Duplicate"):
        validate_workflow(duplicate)


def test_raw_sources_providers_actions_and_blueprint_flows_are_not_workflow_refs() -> None:
    source = workflow()
    source["match_contract"]["source_ref"] = "source-1"
    with pytest.raises(WorkflowValidationError, match="Source/provider"):
        validate_workflow(source)

    provider = workflow()
    provider["provider_semantics"] = {"is_outcome": False, "outcome": "selection"}
    with pytest.raises(WorkflowValidationError, match="provider semantics"):
        validate_workflow(provider)

    flow = workflow()
    flow["flow_id"] = "blueprint-flow-1"
    with pytest.raises(WorkflowValidationError):
        validate_workflow(flow)

    flow_step = composition()
    flow_step["steps"][0]["kind"] = "flow"
    with pytest.raises(CompositionValidationError):
        validate_execution_composition(flow_step)


def test_active_workflow_requires_active_dot_refs_and_candidate_is_isolated() -> None:
    with pytest.raises(WorkflowValidationError, match="active Dots"):
        workflow(status="active", dot_state="candidate")

    active = workflow(status="active")
    validate_workflow(active, dot_records=[dot("verify-dot", "active")])
    with pytest.raises(WorkflowValidationError, match="active Dots"):
        validate_workflow(active, dot_records=[dot("verify-dot", "candidate")])

    candidate = workflow()
    candidate["lifecycle"]["active_surface"] = True
    with pytest.raises(WorkflowValidationError, match="dimensions"):
        validate_workflow(candidate)


def test_workflow_governance_dimensions_are_independent_until_activation() -> None:
    candidate = workflow()
    assert candidate["trial"]["status"] == "pending"
    assert candidate["verification"]["status"] == "unverified"
    assert candidate["system_review"]["status"] == "pending"
    assert candidate["human_decision"]["status"] == "pending"
    assert candidate["activation"]["status"] == "inactive"

    candidate["trial"] = {
        "status": "passed",
        "workflow_version": "1.0.0",
        "evidence_ids": ["trial-evidence"],
    }
    candidate["verification"] = {
        "status": "verified-staged",
        "evidence_ids": ["verification-evidence"],
    }
    candidate["system_review"] = {
        "status": "completed",
        "review_id": "review-1",
        "workflow_version": "1.0.0",
        "evidence_ids": ["review-evidence"],
    }
    candidate["human_decision"] = {
        "status": "approved",
        "decision_id": "decision-1",
        "workflow_version": "1.0.0",
        "decided_by": "primary-user",
        "evidence_ids": ["decision-evidence"],
    }
    validated = validate_workflow(candidate)
    assert validated["human_decision"]["status"] == "approved"
    assert validated["activation"]["status"] == "inactive"


def test_active_workflow_requires_exact_version_governance_and_system_version_authority() -> None:
    active = workflow(status="active")
    assert validate_workflow(active)["activation"]["status"] == "active"

    cases = [
        ("trial", "status", "pending", "passed trial"),
        ("verification", "status", "verified-staged", "verified evidence"),
        ("system_review", "workflow_version", "1.0.1", "exact Workflow version"),
        ("human_decision", "decided_by", "main-agent", "primary-user"),
        ("human_decision", "workflow_version", "1.0.1", "exact Workflow version"),
        ("activation", "activated_version", "1.0.1", "equal Workflow version"),
    ]
    for dimension, field, value, message in cases:
        invalid = workflow(status="active")
        invalid[dimension][field] = value
        with pytest.raises(WorkflowValidationError, match=message):
            validate_workflow(invalid)

    wrong_activation_version = workflow(status="active")
    wrong_activation_version["activation"]["activation_evidence"][0][
        "workflow_version"
    ] = "1.0.1"
    with pytest.raises(WorkflowValidationError, match="exact Workflow version"):
        validate_workflow(wrong_activation_version)

    wrong_authority = workflow(status="active")
    wrong_authority["activation"]["activation_evidence"][0]["authority"] = "main-agent"
    with pytest.raises(WorkflowValidationError, match="system-version|System Version"):
        validate_workflow(wrong_authority)


def test_composition_is_project_local_and_candidate_dot_needs_exact_authority() -> None:
    active = workflow(status="active")
    with pytest.raises(CompositionValidationError, match="missing Workflow"):
        composition(workflows=[])
    with pytest.raises(CompositionValidationError, match="active Workflows"):
        composition(workflows=[workflow(status="candidate")])

    with pytest.raises(CompositionValidationError, match="bounded authority"):
        composition(candidate_dot=True, workflows=[active], dots=[dot("trial-dot", "candidate")])

    authority = {
        "authority_id": "authority-1",
        "scope": {
            "kind": "project-task-local",
            "project_id": "project-a",
            "task_id": "task-a",
        },
        "allowed_step_ids": ["step-1", "step-2"],
        "candidate_dot_execution": True,
        "candidate_dot_ids": ["trial-dot"],
        "human_approved": True,
        "expires_at": EXPIRY,
        "persistent": False,
    }
    trial = composition(
        candidate_dot=True,
        workflows=[active],
        dots=[dot("trial-dot", "candidate")],
        execution_authority=authority,
    )
    assert trial["persistent"] is False
    authority["scope"]["project_id"] = "other-project"
    with pytest.raises(CompositionValidationError, match="scope"):
        composition(
            candidate_dot=True,
            workflows=[active],
            dots=[dot("trial-dot", "candidate")],
            execution_authority=authority,
        )


def test_success_is_evidence_only_and_repeated_compositions_do_not_create_workflows() -> None:
    first = composition()
    second = composition(composition_id="trial-2")
    assert first["persistent"] is False
    assert second["persistent"] is False
    first_evidence = record_execution_evidence(first, succeeded=True)
    second_evidence = record_execution_evidence(second, succeeded=True)
    assert first_evidence["persistent"] is False
    assert first_evidence["promoted"] is False
    assert first_evidence["workflow_promotion"] is None
    assert second_evidence["workflow_promotion"] is None


def test_material_workflow_change_gets_a_new_version() -> None:
    original = workflow(status="active")
    revised = revise_workflow(
        original,
        {"success_contract": {"checks": ["result and evidence both match"]}},
    )
    assert revised["version"] == "1.0.1"
    assert revised["lifecycle"]["status"] == "candidate"
    assert revised["lifecycle"]["active_surface"] is False
    assert revised["lifecycle"]["material_change"] is True
    assert revised["lifecycle"]["supersedes"] == "verify-evidence@1.0.0"
    assert revised["trial"] == {"status": "pending"}
    assert revised["verification"] == {"status": "unverified"}
    assert revised["system_review"] == {"status": "pending"}
    assert revised["human_decision"] == {"status": "pending"}
    assert revised["activation"] == {"status": "inactive"}
    validate_workflow_version_update(original, revised)


def test_material_workflow_revision_cannot_carry_prior_governance_evidence() -> None:
    original = workflow(status="active")
    revised = revise_workflow(
        original,
        {"success_contract": {"checks": ["new material outcome"]}},
    )
    carried = copy.deepcopy(revised)
    carried["human_decision"] = copy.deepcopy(original["human_decision"])
    with pytest.raises(WorkflowValidationError, match="reset human_decision"):
        validate_workflow_version_update(original, carried)

    carried = copy.deepcopy(revised)
    carried["activation"] = {
        "status": "inactive",
        "evidence_ids": ["stale-activation-evidence"],
    }
    with pytest.raises(WorkflowValidationError, match="prior activation evidence"):
        validate_workflow_version_update(original, carried)

    with pytest.raises(WorkflowError, match="strictly newer"):
        revise_workflow(
            original,
            {"success_contract": {"checks": ["new material outcome"]}},
            version="0.9.0",
        )
