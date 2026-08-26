# ruff: noqa: E501

from __future__ import annotations

import copy
import json

import pytest

from fractal.capability_workflow_synthesis import (
    WorkflowInputError,
    synthesize_candidate_workflows,
)


def dot(
    dot_id: str,
    *,
    inputs: list[str],
    outputs: list[str],
    state: str = "candidate",
    side_effects: list[str] | None = None,
    provider_specific: dict | None = None,
) -> dict:
    implementation_id = f"{dot_id}-implementation"
    value = {
        "record_type": "capability-dot",
        "record_version": 1,
        "dot_id": dot_id,
        "version": "1.0.0",
        "human_name": f"{dot_id} responsibility",
        "responsibility": f"Perform the {dot_id} responsibility.",
        "inputs": inputs,
        "outputs": outputs,
        "preconditions": ["The input is present."],
        "side_effects": side_effects or ["emit one bounded output."],
        "lifecycle": {
            "state": state,
            "candidate": state == "candidate",
            "active": state == "active",
            "active_surface": state == "active",
            "transition_evidence": [f"transition-{dot_id}"],
        },
        "evidence": {"evidence_ids": [f"dot-evidence-{dot_id}"], "provenance": []},
        "recovery": {"strategy": "Stop and restore the prior bounded step.", "evidence_ids": [f"recover-{dot_id}"]},
        "coherence": {
            "coherent_responsibility": True,
            "reuse_rationale": "The responsibility remains reusable independently.",
            "boundary_evidence_hooks": [
                {
                    "hook_id": f"boundary-{dot_id}",
                    "type": "boundary",
                    "reason": "The input and output boundary remains explicit.",
                    "evidence_ids": [f"boundary-{dot_id}"],
                }
            ],
        },
        "implementations": [
            {
                "implementation_id": implementation_id,
                "version": "1.0.0",
                "provider": "portable-runtime",
                "dependencies": [],
                "capability_requirements": ["bounded-portable-responsibility"],
                "permissions": {"operations": ["read-bounded-input", "emit-bounded-output"]},
                "procedure_ref": {"kind": "portable-procedure", "ref": f"tests:{dot_id}"},
                "provenance": {"origin": "test", "evidence_ids": [f"impl-{dot_id}"]},
                "compatibility": {"compatible": True, "dot_version": "1.0.0"},
                "verification": {"status": "unverified", "evidence_ids": []},
                "recovery": {"strategy": "Restore the prior procedure.", "evidence_ids": [f"impl-recover-{dot_id}"]},
                "evidence": [f"impl-evidence-{dot_id}"],
            }
        ],
        "trial": {"status": "pending"},
        "verification": {"status": "unverified"},
        "system_review": {"status": "pending"},
        "human_decision": {"status": "pending"},
        "activation": {"status": "inactive", "authorised": False},
    }
    if provider_specific is not None:
        value["provider_specific"] = provider_specific
    return value


def reusable(outcome: str = "auditable report", *, path: list[str] | None = None, evidence: str = "reuse-proof") -> dict:
    return {
        "outcome": outcome,
        "dot_ids": path or [],
        "coherent_outcome": True,
        "reusable": True,
        "occurrences": 2,
        "evidence_ids": [evidence],
    }


def chain() -> list[dict]:
    return [
        dot("parse", inputs=["request"], outputs=["parsed"]),
        dot("report", inputs=["parsed"], outputs=["report"]),
    ]


def test_coherent_reusable_chain_builds_canonical_inactive_workflow() -> None:
    result = synthesize_candidate_workflows(chain(), reusable_outcome_evidence=reusable())

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["record_type"] == "capability-workflow"
    assert [item["dot_id"] for item in candidate["dot_refs"]] == ["parse", "report"]
    assert candidate["lifecycle"] == {
        "status": "candidate",
        "state": "candidate",
        "candidate": True,
        "active": False,
        "active_surface": False,
        "supersedes": None,
        "material_change": False,
    }
    assert candidate["outputs"][0]["id"] == "report"
    assert "reuse-proof" in candidate["provenance"]["evidence_refs"]


def test_technically_connectable_but_side_effect_incompatible_path_is_rejected() -> None:
    dots = chain()
    dots[1]["side_effects"] = ["write external record."]
    result = synthesize_candidate_workflows(
        dots,
        verification_boundaries={"allowed_side_effects": ["emit one bounded output"]},
        reusable_outcome_evidence=reusable(),
    )

    assert result["candidates"] == []
    assert any(item["kind"] == "compatibility" for item in result["rejected"])


def test_one_off_composition_is_not_persisted() -> None:
    result = synthesize_candidate_workflows(
        chain(),
        reusable_outcome_evidence={
            "outcome": "auditable report",
            "coherent_outcome": True,
            "evidence_ids": ["single-observation"],
        },
        observed_compositions=[
            {"dot_ids": ["parse", "report"], "signature": "one-run", "succeeded": True}
        ],
    )

    assert result["candidates"] == []
    assert any(item["kind"] == "one-off" for item in result["rejected"])


def test_repeated_observed_signature_supplies_reuse_evidence_but_stays_candidate() -> None:
    result = synthesize_candidate_workflows(
        chain(),
        reusable_outcome_evidence={"outcome": "auditable report", "coherent_outcome": True},
        observed_compositions=[
            {"dot_ids": ["parse", "report"], "signature": "same-run", "succeeded": True},
            {"dot_ids": ["parse", "report"], "signature": "same-run", "succeeded": True},
        ],
    )

    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["lifecycle"]["active"] is False
    assert result["candidates"][0]["lifecycle"]["candidate"] is True


def test_provider_implementation_details_do_not_leak_without_explicit_outcome_semantics() -> None:
    provider_dot = dot(
        "native",
        inputs=["request"],
        outputs=["native-result"],
        provider_specific={
            "provider_id": "native-runtime",
            "intrinsic_provider_responsibility": {
                "reason_code": "native-result-is-the-outcome",
                "evidence_ids": ["native-boundary"],
            },
        },
    )
    result = synthesize_candidate_workflows(
        [provider_dot], reusable_outcome_evidence=reusable("native result")
    )

    assert result["candidates"] == []
    assert any("provider-specific" in reason for item in result["conflicts"] for reason in item["reasons"])


def test_explicit_provider_outcome_is_the_only_provider_specific_workflow() -> None:
    provider_dot = dot(
        "native",
        inputs=["request"],
        outputs=["native-result"],
        provider_specific={
            "provider_id": "native-runtime",
            "intrinsic_provider_responsibility": {
                "reason_code": "native-result-is-the-outcome",
                "evidence_ids": ["native-boundary"],
            },
        },
    )
    result = synthesize_candidate_workflows(
        [provider_dot],
        reusable_outcome_evidence=reusable("native result"),
        provider_semantics={"is_outcome": True, "outcome": "native result"},
    )

    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["provider_semantics"]["is_outcome"] is True
    assert result["candidates"][0]["provider_specific"]["provider_id"] == "native-runtime"


def test_distinct_outcomes_survive_duplicate_path_collapse() -> None:
    dots = [
        dot("a", inputs=["request"], outputs=["a-result"]),
        dot("b", inputs=["request"], outputs=["b-result"]),
    ]
    result = synthesize_candidate_workflows(
        dots,
        reusable_outcome_evidence=[
            reusable("first result", path=["a"], evidence="first-reuse"),
            reusable("second result", path=["b"], evidence="second-reuse"),
        ],
    )

    assert len(result["candidates"]) == 2
    assert {item["success_contract"]["outcome"] for item in result["candidates"]} == {
        "first result",
        "second result",
    }


def test_conflicting_outcome_evidence_is_recorded_not_guessed() -> None:
    result = synthesize_candidate_workflows(
        chain(),
        reusable_outcome_evidence=[
            reusable("first outcome"),
            reusable("second outcome", evidence="other-reuse"),
        ],
    )

    assert result["candidates"] == []
    assert any(item["kind"] == "semantic" for item in result["conflicts"])


def test_source_action_old_workflow_and_dot_group_inputs_are_rejected() -> None:
    with pytest.raises(WorkflowInputError):
        synthesize_candidate_workflows({"record_type": "capability-source", "source_id": "s"})
    with pytest.raises(WorkflowInputError):
        synthesize_candidate_workflows([{"record_type": "action", "action_id": "a"}])
    with pytest.raises(WorkflowInputError):
        synthesize_candidate_workflows([{"record_type": "capability-workflow", "workflow_id": "w"}])
    with pytest.raises(WorkflowInputError):
        synthesize_candidate_workflows({"dot_group": "old"})


def test_order_and_repeated_synthesis_are_idempotent() -> None:
    dots = chain()
    evidence = reusable()
    first = synthesize_candidate_workflows(dots, reusable_outcome_evidence=evidence)
    second = synthesize_candidate_workflows(list(reversed(dots)), reusable_outcome_evidence=evidence)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(first, sort_keys=True) == json.dumps(
        synthesize_candidate_workflows(dots, reusable_outcome_evidence=copy.deepcopy(evidence)),
        sort_keys=True,
    )
    assert all("action" not in json.dumps(item).casefold() for item in first["candidates"])
