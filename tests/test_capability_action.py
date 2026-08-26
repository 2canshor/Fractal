from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from fractal.capability_action import (
    ActionValidationError,
    revise_action,
    validate_action,
    validate_action_graph,
    validate_action_version_update,
)

ROOT = Path(__file__).parents[1]


def induction(
    workflow_ids: list[tuple[str, str]],
    *,
    digest: str = "digest-a",
    verb: str = "review",
) -> dict:
    return {
        "new_workflow_clusters": [
            {
                "cluster_id": f"cluster-{workflow_id}",
                "workflow_id": workflow_id,
                "version": version,
            }
            for workflow_id, version in workflow_ids
        ],
        "human_intent_analysis": {
            "observed_intent": "The person wants one bounded, auditable result."
        },
        "compression_decision": {
            "decision": "one-action",
            "reason": "The Workflow cluster has one coherent human outcome.",
        },
        "naming_system": {
            "proposal": verb,
            "rationale": "Use the familiar human outcome and keep provider terms out.",
            "alternatives": ["review" if verb == "inspect" else "inspect"],
            "language": "en",
            "part_of_speech": "verb",
            "provenance": {
                "component_id": "naming-system",
                "version": "0.1.0",
                "evidence_ids": [f"naming-{digest}"],
            },
        },
        "anti_seed_attestation": {
            "attested": True,
            "independent": True,
            "evidence_ids": [f"anti-seed-{digest}"],
        },
        "input_digest": digest,
    }


def action(
    *,
    action_id: str = "review",
    version: str = "1.0.0",
    status: str = "candidate",
    workflow_ids: list[tuple[str, str]] | None = None,
    digest: str = "digest-a",
    human_name: str = "review",
    human_intent: str = "Review one result and return an auditable answer.",
) -> dict:
    workflow_ids = workflow_ids or [("review-workflow", "1.0.0")]
    ref_state = "active" if status == "active" else "candidate"
    value = {
        "record_type": "capability-action",
        "record_version": 1,
        "action_id": action_id,
        "version": version,
        "human_name": human_name,
        "human_intent": {
            "statement": human_intent,
            "familiar": human_name,
            "stable": True,
        },
        "match_contract": {"intent": "review", "signals": ["result"]},
        "inputs": ["request"],
        "outputs": ["auditable-answer"],
        "workflow_refs": [
            {
                "sequence": index,
                "workflow_id": workflow_id,
                "version": version_ref,
                "lifecycle": ref_state,
            }
            for index, (workflow_id, version_ref) in enumerate(workflow_ids, start=1)
        ],
        "success_family": {"family": "auditable-answer", "checks": ["answer has evidence"]},
        "lifecycle": {
            "status": status,
            "state": status,
            "candidate": status == "candidate",
            "active": status == "active",
            "active_surface": status == "active",
            **({"transition_evidence": ["action-transition"]} if status == "active" else {}),
        },
        "verification": {
            "status": "verified" if status == "active" else "unverified",
            **({"evidence_ids": ["action-verification"]} if status == "active" else {}),
        },
        "system_review": {
            "status": "completed" if status == "active" else "pending",
            **({"evidence_ids": ["action-system-review"]} if status == "active" else {}),
        },
        "human_decision": {
            "status": "approved" if status == "active" else "pending",
            **(
                {
                    "decision_id": "action-decision",
                    "decided_by": "primary-user",
                    "evidence_ids": ["action-decision-evidence"],
                }
                if status == "active"
                else {}
            ),
        },
        "activation": (
            {
                "status": "active",
                "authorised": True,
                "activated_version": version,
                "activation_evidence": [
                    {
                        "evidence_id": "action-activation",
                        "action_version": version,
                        "authorised": True,
                    }
                ],
            }
            if status == "active"
            else {"status": "inactive"}
        ),
        "recovery": {
            "strategy": "Disable this Action and restore the retained predecessor.",
            "evidence_ids": ["action-recovery"],
        },
        "induction_evidence": induction(workflow_ids, digest=digest, verb=action_id),
    }
    return value


def test_schema_and_basic_candidate_are_valid() -> None:
    schema = json.loads((ROOT / "src/fractal/schemas/capability-action.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    assert validate_action(action())["record_type"] == "capability-action"


@pytest.mark.parametrize(
    ("action_id", "human_name"),
    [
        ("Review", "Review"),
        ("review-result", "review-result"),
        ("review result", "review result"),
        ("review", "inspect"),
        ("/review", "/review"),
    ],
)
def test_action_name_and_id_must_be_the_same_lowercase_one_word_verb(
    action_id: str, human_name: str
) -> None:
    invalid = action(action_id=action_id, human_name=human_name)
    with pytest.raises(
        ActionValidationError,
        match="lowercase English one-word verb|same lowercase",
    ):
        validate_action(invalid)


def test_raw_provider_source_dot_and_implementation_identity_is_rejected() -> None:
    for forbidden in ("provider", "source_id", "dot_id", "implementation_id", "platform_skill"):
        invalid = action()
        invalid[forbidden] = "not-canonical"
        with pytest.raises(ActionValidationError, match="Raw Source/Dot/Implementation"):
            validate_action(invalid)


def test_workflow_references_are_unique_and_active_actions_need_active_workflows() -> None:
    duplicate = action(workflow_ids=[("review-workflow", "1.0.0"), ("review-workflow", "1.1.0")])
    with pytest.raises(ActionValidationError, match="Duplicate Workflow"):
        validate_action(duplicate)

    active = action(status="active")
    active["workflow_refs"][0]["lifecycle"] = "candidate"
    with pytest.raises(ActionValidationError, match="only active Workflows"):
        validate_action(active)


def test_candidate_dimensions_are_independent_and_approval_is_not_activation() -> None:
    candidate = action()
    candidate["human_decision"] = {
        "status": "approved",
        "decision_id": "decision-1",
        "decided_by": "primary-user",
        "evidence_ids": ["decision-evidence"],
    }
    validated = validate_action(candidate)
    assert validated["human_decision"]["status"] == "approved"
    assert validated["activation"]["status"] == "inactive"

    candidate["activation"] = {
        "status": "active",
        "authorised": True,
        "activation_evidence": [
            {"evidence_id": "e", "action_version": "1.0.0", "authorised": True}
        ],
    }
    with pytest.raises(ActionValidationError, match="candidate Action"):
        validate_action(candidate)


def test_active_action_requires_verification_review_decision_and_authorised_version() -> None:
    active = action(status="active")
    assert validate_action(active)["activation"]["status"] == "active"

    missing_verification = action(status="active")
    missing_verification["verification"] = {"status": "unverified"}
    with pytest.raises(ActionValidationError, match="verified Action"):
        validate_action(missing_verification)

    wrong_version = action(status="active")
    wrong_version["activation"]["activation_evidence"][0]["action_version"] = "2.0.0"
    with pytest.raises(ActionValidationError, match="exact Action version"):
        validate_action(wrong_version)


def test_anti_seed_is_required_and_inheritance_keys_cannot_be_smuggled() -> None:
    missing = action()
    del missing["induction_evidence"]["anti_seed_attestation"]
    with pytest.raises(ActionValidationError, match="anti-seed"):
        validate_action(missing)

    inherited = action()
    inherited["induction_evidence"]["inherited_action"] = "old-action"
    with pytest.raises(ActionValidationError, match="inherited Action"):
        validate_action(inherited)


def test_shared_workflow_requires_distinct_intent_evidence() -> None:
    first = action(action_id="review", human_name="review", digest="review-digest")
    second = action(action_id="teach", human_name="teach", digest="teach-digest")
    with pytest.raises(ActionValidationError, match="distinct-intent evidence"):
        validate_action_graph([first, second])

    first["human_intent"]["statement"] = "Review one result for an audit."
    second["human_intent"]["statement"] = "Transform one result into a teaching explanation."
    first["induction_evidence"]["distinct_intent_evidence"] = {
        "changed_output": True,
        "independent": True,
        "evidence_ids": ["distinct-first"],
    }
    second["induction_evidence"]["distinct_intent_evidence"] = {
        "changed_output": True,
        "independent": True,
        "evidence_ids": ["distinct-second"],
    }
    assert len(validate_action_graph([first, second])) == 2


def test_same_verb_cannot_identify_unrelated_actions_or_gain_a_suffix() -> None:
    first = action(digest="digest-first")
    second = action(digest="digest-second")
    second["human_intent"]["statement"] = "Review a different unrelated outcome."

    with pytest.raises(ActionValidationError, match="Duplicate Action id"):
        validate_action_graph([first, second])


def test_material_change_requires_candidate_new_version_lineage_and_recovery() -> None:
    previous = action()
    updated = copy.deepcopy(previous)
    updated["version"] = "1.1.0"
    updated["outputs"] = ["changed-answer"]
    updated["lifecycle"] = {
        "status": "candidate",
        "state": "candidate",
        "candidate": True,
        "active": False,
        "active_surface": False,
    }
    updated["lineage"] = {
        "predecessor_action_id": "review",
        "predecessor_version": "1.0.0",
        "change_type": "material",
        "reason": "The output boundary changed.",
        "evidence_ids": ["lineage-evidence"],
        "recovery_evidence_ids": ["lineage-recovery"],
    }
    updated["name_reuse_evidence"] = {
        "changed_output": True,
        "independent": True,
        "evidence_ids": ["same-verb-new-output"],
    }
    assert validate_action_version_update(previous, updated)["version"] == "1.1.0"

    same_version = copy.deepcopy(previous)
    same_version["outputs"] = ["another-answer"]
    with pytest.raises(ActionValidationError, match="new version"):
        validate_action_version_update(previous, same_version)


def test_candidate_first_action_rename_changes_name_and_id_together() -> None:
    current = action(status="active")
    renamed_induction = induction(
        [("review-workflow", "1.0.0")], digest="rename-digest", verb="inspect"
    )
    renamed = revise_action(
        current,
        {
            "action_id": "inspect",
            "human_name": "inspect",
            "induction_evidence": renamed_induction,
        },
    )

    assert renamed["action_id"] == renamed["human_name"] == "inspect"
    assert renamed["lifecycle"]["status"] == "candidate"
    assert renamed["activation"] == {"status": "inactive"}
    assert renamed["system_review"] == {"status": "pending"}
    assert renamed["human_decision"] == {"status": "pending"}
    assert renamed["lineage"]["change_type"] == "rename"
    assert validate_action_version_update(current, renamed) == renamed


def test_platform_projection_is_derived_not_canonical() -> None:
    value = action()
    value["platform_projections"] = [
        {
            "projection_id": "codex-review",
            "platform": "codex",
            "platform_skill": "review-result",
            "derived_from": {"action_id": "review", "version": "1.0.0"},
            "canonical": False,
            "authority": "derived",
        }
    ]
    assert validate_action(value)["platform_projections"][0]["canonical"] is False
    value["platform_projections"][0]["canonical"] = True
    with pytest.raises(ActionValidationError, match="cannot be canonical"):
        validate_action(value)


def test_no_fixed_action_or_workflow_count_is_imposed() -> None:
    refs = [(f"workflow-{index}", "1.0.0") for index in range(101)]
    value = action(workflow_ids=refs)
    assert len(validate_action(value)["workflow_refs"]) == 101
