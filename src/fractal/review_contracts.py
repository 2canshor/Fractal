"""Cross-cutting truth, handoff, response-unit, and lineage review contracts."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from fractal.storage import value_sha256


class ReviewContractError(RuntimeError):
    """Raised when a review cannot honestly be described as ready."""


EVIDENCE_STATES = (
    "source-present",
    "registered",
    "discovered",
    "enabled",
    "authenticated",
    "callable",
    "staged-executed",
    "verified-live",
)

PROOF_TYPES_BY_STATE = {
    "source-present": "source-inspection",
    "registered": "registry-readback",
    "discovered": "platform-discovery",
    "enabled": "platform-enabled-readback",
    "authenticated": "authentication-probe",
    "callable": "callability-probe",
    "staged-executed": "staged-execution",
    "verified-live": "representative-real-task",
}

TWO_SIDED_WARRANT_FIELDS = {
    "high_impact",
    "hard_to_restore",
    "cross_project",
    "cross_platform",
    "authority_change",
    "evidence_conflict",
    "direction_reversal",
    "primary_user_requested",
}

PATTERN_DISPOSITIONS = {
    "change",
    "experiment",
    "need-more-evidence",
    "no-change",
    "deferred",
    "rejected",
}


def validate_claim_receipt(
    receipt: dict[str, Any],
    *,
    expected_surface: str | None = None,
    expected_scope: dict[str, str] | None = None,
    expected_version_dependencies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Require exact, same-surface proof without treating states as a linear ladder."""
    required = {
        "claim_id",
        "subject_id",
        "surface",
        "observed_at",
        "asserted_state",
        "proof_type",
        "evidence_ids",
        "scope",
        "version_dependencies",
        "actual_user_outcome",
    }
    derived_fields = {"human_label", "reason_code", "claim_supported"}
    if not required <= set(receipt) or frozenset(set(receipt).difference(required)) not in {
        frozenset(),
        frozenset(derived_fields),
    }:
        raise ReviewContractError("Claim receipt fields are incomplete or unexpected")
    if receipt["asserted_state"] not in EVIDENCE_STATES:
        raise ReviewContractError("Claim receipt uses an unknown evidence state")
    expected_proof_type = PROOF_TYPES_BY_STATE[receipt["asserted_state"]]
    if receipt["proof_type"] != expected_proof_type:
        raise ReviewContractError(
            "Claim proof is incompatible with the asserted state: "
            f"asserted={receipt['asserted_state']}, proof={receipt['proof_type']}"
        )
    if not isinstance(receipt["surface"], str) or not receipt["surface"].strip():
        raise ReviewContractError("Claim receipt requires an exact surface")
    try:
        observed_at = datetime.fromisoformat(str(receipt["observed_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise ReviewContractError("Claim receipt observed time is invalid") from error
    if observed_at.tzinfo is None:
        raise ReviewContractError("Claim receipt observed time must include a timezone")
    if (
        not isinstance(receipt["evidence_ids"], list)
        or not receipt["evidence_ids"]
        or any(not str(item).strip() for item in receipt["evidence_ids"])
    ):
        raise ReviewContractError("Claim receipt requires direct evidence ids")
    scope = receipt["scope"]
    if (
        not isinstance(scope, dict)
        or set(scope) != {"platform", "account", "task"}
        or any(not isinstance(value, str) for value in scope.values())
        or not scope["platform"].strip()
    ):
        raise ReviewContractError(
            "Claim receipt requires an exact platform, account, and task scope"
        )
    versions = receipt["version_dependencies"]
    if (
        not isinstance(versions, dict)
        or not versions
        or any(not str(key).strip() or not str(value).strip() for key, value in versions.items())
    ):
        raise ReviewContractError("Claim receipt requires version dependencies")
    if receipt["asserted_state"] == "verified-live" and not scope["task"].strip():
        raise ReviewContractError("Verified-live requires a representative real task")
    outcome = receipt["actual_user_outcome"]
    if not isinstance(outcome, dict) or set(outcome) != {"observed", "evidence_ids"}:
        raise ReviewContractError("Actual user outcome must remain an independent dimension")
    if not isinstance(outcome["observed"], bool) or not isinstance(outcome["evidence_ids"], list):
        raise ReviewContractError("Actual user outcome evidence is invalid")
    if outcome["observed"] and not outcome["evidence_ids"]:
        raise ReviewContractError("An observed user outcome requires its own evidence")
    if not outcome["observed"] and outcome["evidence_ids"]:
        raise ReviewContractError("User outcome evidence cannot exist without direct observation")
    if expected_surface is not None and receipt["surface"] != expected_surface:
        raise ReviewContractError("Claim receipt is for a different surface")
    if expected_scope is not None and scope != expected_scope:
        raise ReviewContractError("Claim receipt is for a different scope")
    if (
        expected_version_dependencies is not None
        and versions != expected_version_dependencies
    ):
        raise ReviewContractError("Claim receipt has stale or different version dependencies")
    validated = {
        **copy.deepcopy(receipt),
        "human_label": receipt["asserted_state"],
        "reason_code": f"proof:{receipt['proof_type']}",
        "claim_supported": True,
    }
    if derived_fields <= set(receipt) and any(
        receipt[field] != validated[field] for field in derived_fields
    ):
        raise ReviewContractError("Claim receipt derived label or reason is inconsistent")
    return validated


def audit_claim_set(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate a bounded claim set and return an exact audit receipt."""
    if not receipts:
        raise ReviewContractError("Claim audit requires at least one receipt")
    validated = [validate_claim_receipt(item) for item in receipts]
    claim_ids = [item["claim_id"] for item in validated]
    if len(claim_ids) != len(set(claim_ids)):
        raise ReviewContractError("Claim audit ids must be unique")
    return {
        "record_type": "claim-gate-audit",
        "record_version": 1,
        "claim_count": len(validated),
        "claim_ids": claim_ids,
        "claims_sha256": value_sha256(receipts),
        "passed": True,
    }


def build_response_units(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group every discovered Pattern into one explicit, decision-ready response unit."""
    if not patterns:
        raise ReviewContractError("Response coverage requires discovered Patterns")
    seen: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pattern in patterns:
        required = {"pattern_id", "response_unit_id", "disposition", "decision_id"}
        if set(pattern) != required:
            raise ReviewContractError("Pattern response fields are incomplete or unexpected")
        pattern_id = pattern["pattern_id"]
        if pattern_id in seen:
            raise ReviewContractError(f"Pattern appears more than once: {pattern_id}")
        if pattern["disposition"] not in PATTERN_DISPOSITIONS:
            raise ReviewContractError(f"Pattern has no valid disposition: {pattern_id}")
        if not all(str(pattern[field]).strip() for field in required):
            raise ReviewContractError(f"Pattern response is blank: {pattern_id}")
        seen.add(pattern_id)
        grouped.setdefault(pattern["response_unit_id"], []).append(copy.deepcopy(pattern))
    return [
        {
            "response_unit_id": response_unit_id,
            "patterns": sorted(items, key=lambda item: item["pattern_id"]),
            "pattern_ids": sorted(item["pattern_id"] for item in items),
            "decision_ids": sorted(item["decision_id"] for item in items),
        }
        for response_unit_id, items in sorted(grouped.items())
    ]


def validate_two_sided_result(result: dict[str, Any]) -> dict[str, Any]:
    """Require Two-Sided Review automatically whenever its exact warrant is true."""
    warrant = result.get("warrant")
    if not isinstance(warrant, dict) or set(warrant) != TWO_SIDED_WARRANT_FIELDS:
        raise ReviewContractError("Two-Sided Review requires the complete warrant")
    if any(not isinstance(value, bool) for value in warrant.values()):
        raise ReviewContractError("Two-Sided Review warrant values must be boolean")
    warranted = any(warrant.values())
    if warranted and result.get("status") != "completed":
        raise ReviewContractError("A consequential warrant requires Two-Sided Review")
    if not warranted and result.get("status") != "not-warranted":
        raise ReviewContractError("Two-Sided Review cannot claim completion without a warrant")
    if warranted:
        for field in (
            "case_for_artifact_id",
            "case_against_artifact_id",
            "independent_cases_verified",
            "synthesised_by",
        ):
            if not result.get(field):
                raise ReviewContractError(f"Two-Sided Review requires {field}")
        if result["independent_cases_verified"] is not True:
            raise ReviewContractError("Case For and Case Against were not independently verified")
        if result["synthesised_by"] != "main-agent":
            raise ReviewContractError("Only the Main Agent may synthesise Two-Sided Review")
    elif not str(result.get("reason", "")).strip():
        raise ReviewContractError("A not-warranted result requires a reason")
    return {"warranted": warranted, "status": result["status"]}


def validate_plain_handoff(
    handoff: dict[str, Any],
    *,
    shadow_handoff_number: int | None = None,
) -> dict[str, Any]:
    """Validate the three-part ordinary-language handoff and record shadow advice."""
    required = {"problem", "solution", "decision"}
    if set(handoff) != required or any(not str(handoff[key]).strip() for key in required):
        raise ReviewContractError("Public handoff requires problem, solution, and decision")
    result = {
        "record_type": "plain-handoff-preflight",
        "record_version": 1,
        "handoff_sha256": value_sha256(handoff),
        "required_order": ["problem", "solution", "decision"],
        "passed": True,
        "blocking": False,
    }
    if shadow_handoff_number is not None:
        if shadow_handoff_number not in {1, 2, 3}:
            raise ReviewContractError("Newcomer shadow experiment is limited to three handoffs")
        result["shadow_advisory"] = {
            "handoff_number": shadow_handoff_number,
            "experiment_horizon": 3,
            "automatic_rewrite": False,
            "advice_only": True,
        }
    return result


def validate_review_ready(review: dict[str, Any]) -> dict[str, Any]:
    """Fail closed before presentation when any required review mechanism is missing."""
    stages = {item.get("stage"): item.get("result") for item in review.get("stages", [])}
    required = {
        "project-assessment",
        "issue-scan",
        "project-patterns",
        "cross-project-patterns",
        "reversal-check",
        "cause-research",
        "reconciliation",
        "improvement-options",
        "expected-effect",
        "local-effect",
        "global-effect",
        "two-sided-review",
        "final-assessment",
        "biggest-remaining-concern",
        "result",
    }
    missing = sorted(required.difference(stages))
    if missing:
        raise ReviewContractError(f"review_incomplete: missing mechanisms {missing}")
    two_sided = validate_two_sided_result(stages["two-sided-review"])
    result = stages["result"]
    response_units = result.get("response_units")
    if not isinstance(response_units, list) or not response_units:
        raise ReviewContractError("review_incomplete: response coverage is missing")
    pattern_ids = [
        pattern_id
        for unit in response_units
        for pattern_id in unit.get("pattern_ids", [])
    ]
    if not pattern_ids or len(pattern_ids) != len(set(pattern_ids)):
        raise ReviewContractError("review_incomplete: Pattern coverage is empty or duplicated")
    if result.get("unmapped_pattern_ids") != []:
        raise ReviewContractError("review_incomplete: an observed Pattern has no disposition")
    handoff_receipt = validate_plain_handoff(
        result.get("plain_handoff", {}),
        shadow_handoff_number=result.get("newcomer_shadow_handoff_number"),
    )
    return {
        "record_type": "system-review-readiness",
        "record_version": 1,
        "ready": True,
        "pattern_ids": sorted(pattern_ids),
        "response_unit_ids": sorted(unit["response_unit_id"] for unit in response_units),
        "two_sided": two_sided,
        "plain_handoff_preflight": handoff_receipt,
        "review_sha256": value_sha256(review),
    }


def architecture_lineage_receipt(
    *,
    method_registry: dict[str, Any],
    agentic_element_map: dict[str, Any],
    changed_component_ids: list[str],
    component_lineage: dict[str, list[str]],
) -> dict[str, Any]:
    """Prove each changed component traces to existing architecture meaning."""
    method_ids = {
        item["id"]
        for section in (
            "core_philosophies",
            "protagonist_mechanisms",
            "methodologies",
            "secondary_mechanisms",
            "mechanisms",
        )
        for item in method_registry[section]
    }
    mapped_ids = {item["node_id"] for item in agentic_element_map["mappings"]}
    if method_ids != mapped_ids:
        raise ReviewContractError("Architecture map does not cover the canonical hierarchy")
    if set(component_lineage) != set(changed_component_ids):
        raise ReviewContractError("Changed-component lineage coverage is incomplete")
    for component_id, node_ids in component_lineage.items():
        if not node_ids or not set(node_ids) <= method_ids:
            raise ReviewContractError(f"Invalid architecture lineage: {component_id}")
    return {
        "record_type": "architecture-lineage-receipt",
        "record_version": 1,
        "changed_component_ids": sorted(changed_component_ids),
        "component_lineage": {
            key: sorted(value) for key, value in sorted(component_lineage.items())
        },
        "method_registry_sha256": value_sha256(method_registry),
        "agentic_element_map_sha256": value_sha256(agentic_element_map),
        "structural_gate_passed": True,
    }


def semantic_lineage_shadow(
    *,
    structural_receipt: dict[str, Any],
    assessments: list[dict[str, str]],
) -> dict[str, Any]:
    """Record non-blocking semantic agreement, disagreement, and ambiguity honestly."""
    allowed = {"aligned", "misaligned", "ambiguous"}
    if not assessments or any(item.get("status") not in allowed for item in assessments):
        raise ReviewContractError("Semantic shadow assessments are incomplete")
    counts = {status: sum(item["status"] == status for item in assessments) for status in allowed}
    return {
        "record_type": "semantic-lineage-shadow",
        "record_version": 1,
        "structural_receipt_sha256": value_sha256(structural_receipt),
        "assessment_count": len(assessments),
        "counts": counts,
        "blocking": False,
        "automatic_architecture_change": False,
        "result": "needs-review" if counts["misaligned"] or counts["ambiguous"] else "aligned",
    }
