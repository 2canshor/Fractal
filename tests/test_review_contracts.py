from __future__ import annotations

import pytest

from fractal.methods import load_agentic_element_map, load_method_registry
from fractal.review_contracts import (
    EVIDENCE_STATES,
    ReviewContractError,
    architecture_lineage_receipt,
    audit_claim_set,
    build_response_units,
    semantic_lineage_shadow,
    validate_claim_receipt,
    validate_plain_handoff,
    validate_two_sided_result,
)


def claim_receipt(claim_id: str, state: str = "verified-live") -> dict[str, object]:
    proof_types = {
        "source-present": "source-inspection",
        "registered": "registry-readback",
        "discovered": "platform-discovery",
        "enabled": "platform-enabled-readback",
        "authenticated": "authentication-probe",
        "callable": "callability-probe",
        "staged-executed": "staged-execution",
        "verified-live": "representative-real-task",
    }
    return {
        "claim_id": claim_id,
        "subject_id": "candidate-a",
        "surface": "codex-skill",
        "observed_at": "2026-08-23T05:00:00Z",
        "asserted_state": state,
        "proof_type": proof_types[state],
        "evidence_ids": [f"evidence-{claim_id}"],
        "scope": {"platform": "codex", "account": "local", "task": "review project-a"},
        "version_dependencies": {"codex": "2026.08", "adapter": "0.1.0-alpha.4"},
        "actual_user_outcome": {"observed": False, "evidence_ids": []},
    }


BYPASS_CASES = [
    (f"claim-{index}", EVIDENCE_STATES[(index % 7) + 1], EVIDENCE_STATES[index % 7])
    for index in range(20)
]


@pytest.mark.parametrize(("claim_id", "asserted", "observed"), BYPASS_CASES)
def test_twenty_claim_bypass_attempts_fail_closed(
    claim_id: str,
    asserted: str,
    observed: str,
) -> None:
    receipt = claim_receipt(claim_id, asserted)
    receipt["proof_type"] = claim_receipt("lower", observed)["proof_type"]
    with pytest.raises(ReviewContractError, match="incompatible"):
        validate_claim_receipt(receipt)


def test_supported_claim_set_returns_an_exact_audit() -> None:
    receipts = [claim_receipt("claim-live"), claim_receipt("claim-registered", "registered")]
    audit = audit_claim_set(receipts)
    assert audit["passed"] is True
    assert audit["claim_count"] == 2


def test_claim_receipt_cannot_cross_surface_version_or_infer_user_outcome() -> None:
    receipt = claim_receipt("claim-scoped")
    with pytest.raises(ReviewContractError, match="different surface"):
        validate_claim_receipt(receipt, expected_surface="claude-skill")
    with pytest.raises(ReviewContractError, match="stale or different"):
        validate_claim_receipt(
            receipt,
            expected_version_dependencies={"codex": "new", "adapter": "new"},
        )
    receipt["actual_user_outcome"] = {"observed": True, "evidence_ids": []}
    with pytest.raises(ReviewContractError, match="own evidence"):
        validate_claim_receipt(receipt)


def test_every_pattern_maps_once_to_a_decision_ready_response_unit() -> None:
    units = build_response_units(
        [
            {
                "pattern_id": "pattern-a",
                "response_unit_id": "unit-a",
                "disposition": "change",
                "decision_id": "decision-a",
            },
            {
                "pattern_id": "pattern-b",
                "response_unit_id": "unit-a",
                "disposition": "experiment",
                "decision_id": "decision-b",
            },
        ]
    )
    assert units[0]["pattern_ids"] == ["pattern-a", "pattern-b"]
    with pytest.raises(ReviewContractError, match="more than once"):
        build_response_units(
            [
                {
                    "pattern_id": "pattern-a",
                    "response_unit_id": "unit-a",
                    "disposition": "change",
                    "decision_id": "decision-a",
                },
                {
                    "pattern_id": "pattern-a",
                    "response_unit_id": "unit-b",
                    "disposition": "no-change",
                    "decision_id": "decision-b",
                },
            ]
        )


def test_consequential_two_sided_review_cannot_be_skipped() -> None:
    warrant = {
        "high_impact": True,
        "hard_to_restore": False,
        "cross_project": False,
        "cross_platform": False,
        "authority_change": False,
        "evidence_conflict": False,
        "direction_reversal": False,
        "primary_user_requested": False,
    }
    with pytest.raises(ReviewContractError, match="requires Two-Sided Review"):
        validate_two_sided_result({"status": "not-warranted", "reason": "skip", "warrant": warrant})
    result = validate_two_sided_result(
        {
            "status": "completed",
            "warrant": warrant,
            "case_for_artifact_id": "case-for",
            "case_against_artifact_id": "case-against",
            "independent_cases_verified": True,
            "synthesised_by": "main-agent",
        }
    )
    assert result == {"warranted": True, "status": "completed"}


def test_newcomer_preflight_is_three_handoff_shadow_advice_only() -> None:
    receipt = validate_plain_handoff(
        {
            "problem": "The Project says two different things.",
            "solution": "Read the current canonical record before answering.",
            "decision": "Carson decides whether to build the inactive test version.",
        },
        shadow_handoff_number=3,
    )
    assert receipt["blocking"] is False
    assert receipt["shadow_advisory"]["automatic_rewrite"] is False
    with pytest.raises(ReviewContractError, match="limited to three"):
        validate_plain_handoff(
            {"problem": "p", "solution": "s", "decision": "d"},
            shadow_handoff_number=4,
        )


def test_structural_lineage_blocks_missing_changed_component_and_shadow_stays_nonblocking() -> None:
    registry = load_method_registry()
    mapping = load_agentic_element_map()
    with pytest.raises(ReviewContractError, match="coverage is incomplete"):
        architecture_lineage_receipt(
            method_registry=registry,
            agentic_element_map=mapping,
            changed_component_ids=["review-contracts", "versioning"],
            component_lineage={"review-contracts": ["system-review"]},
        )
    receipt = architecture_lineage_receipt(
        method_registry=registry,
        agentic_element_map=mapping,
        changed_component_ids=["review-contracts", "versioning"],
        component_lineage={
            "review-contracts": ["system-review", "two-sided-review"],
            "versioning": ["component-governance", "human-control"],
        },
    )
    shadow = semantic_lineage_shadow(
        structural_receipt=receipt,
        assessments=[
            {"component_id": "review-contracts", "status": "aligned"},
            {"component_id": "versioning", "status": "ambiguous"},
        ],
    )
    assert receipt["structural_gate_passed"] is True
    assert shadow["blocking"] is False
    assert shadow["result"] == "needs-review"
