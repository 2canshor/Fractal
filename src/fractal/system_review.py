"""Deterministic orchestration contracts for full System Review."""

from __future__ import annotations

import copy
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fractal.blueprint_mapping import validate_candidate_mapping
from fractal.improvement import (
    TrialBoundary,
    TrialMeasurement,
    assess_trial_boundary,
    compare_trial_results,
    curiosity_routes,
)
from fractal.models import ProjectRecord, utc_now
from fractal.review_contracts import (
    ReviewContractError,
    validate_review_ready,
    validate_two_sided_result,
)
from fractal.storage import AuthorityError, value_sha256

SYSTEM_REVIEW_STAGES = [
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
    "blueprint-mapping",
    "two-sided-review",
    "final-assessment",
    "biggest-remaining-concern",
    "result",
    "your-decision",
]

BACKBONE_FLOW_BY_STAGE = {
    "project-assessment": 1,
    "issue-scan": 1,
    "project-patterns": 2,
    "cross-project-patterns": 3,
    "reversal-check": 3,
    "cause-research": 4,
    "reconciliation": 4,
    "improvement-options": 5,
    "expected-effect": None,
    "local-effect": None,
    "global-effect": None,
    "blueprint-mapping": 6,
    "two-sided-review": 7,
    "final-assessment": 7,
    "biggest-remaining-concern": 8,
    "result": 8,
    "your-decision": 8,
}

ISSUE_SCAN_HISTORY_SECTIONS = {
    "project-direction",
    "project-plan-history",
    "project-reviews",
    "work-records",
    "decisions-and-corrections",
    "outcome-evidence",
    "resource-use",
}

HISTORICAL_RECORD_TYPES = {
    "projects",
    "system-reviews",
    "change-proposals",
    "hypotheses",
    "system-versions",
    "interventions",
    "outcomes",
}

IMPROVEMENT_OPTION_KINDS = (
    "delete",
    "shorten",
    "merge",
    "simplify",
    "reconfigure",
    "modify",
    "add",
    "no-change",
)


class SystemReviewError(RuntimeError):
    """Raised when a System Review violates stage or evidence contracts."""


@dataclass(frozen=True, slots=True)
class AgentCandidate:
    """One evaluated source or debate agent option."""

    agent_id: str
    capability_level: int
    cost_rank: int
    eval_passed: bool
    capabilities: frozenset[str]


def select_lightest_capable_agent(
    candidates: list[AgentCandidate],
    *,
    required_capabilities: set[str],
) -> AgentCandidate:
    """Select the cheapest passed agent that covers the exact branch requirements."""
    eligible = [
        item
        for item in candidates
        if item.eval_passed and required_capabilities.issubset(item.capabilities)
    ]
    if not eligible:
        raise SystemReviewError("No evaluated agent satisfies the branch requirements")
    return min(eligible, key=lambda item: (item.capability_level, item.cost_rank, item.agent_id))


@dataclass(frozen=True, slots=True)
class IndependentBranch:
    """Provenance needed to verify source or debate branch independence."""

    branch_id: str
    role: str
    initial_context_sha256: str
    input_artifact_ids: tuple[str, ...]
    output_artifact_id: str
    source_ids: tuple[str, ...]
    selected_agent_id: str
    result_summary: str


def verify_branch_independence(
    branches: list[IndependentBranch],
    *,
    required_roles: set[str],
) -> dict[str, Any]:
    """Verify that branches did not receive each other's initial output."""
    roles = {branch.role for branch in branches}
    if roles != required_roles or len(branches) != len(required_roles):
        raise SystemReviewError("Independent branches do not match the required roles")
    if len({branch.branch_id for branch in branches}) != len(branches):
        raise SystemReviewError("Independent branch ids must be unique")
    if len({branch.initial_context_sha256 for branch in branches}) != len(branches):
        raise SystemReviewError("Independent branches require distinct initial context manifests")
    outputs = {branch.output_artifact_id for branch in branches}
    for branch in branches:
        other_outputs = outputs.difference({branch.output_artifact_id})
        if other_outputs.intersection(branch.input_artifact_ids):
            raise SystemReviewError("A branch received another branch output before synthesis")
    return {
        "independent": True,
        "roles": sorted(roles),
        "branch_ids": [branch.branch_id for branch in branches],
        "selected_agent_ids": [branch.selected_agent_id for branch in branches],
    }


def run_two_sided_review(
    *,
    warrant: dict[str, bool],
    evidence: list[dict[str, Any]],
    case_reasoner: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    synthesizer: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Run isolated Case For and Case Against before Main Agent synthesis."""
    if not warrants_two_sided_review(warrant):
        result = {
            "status": "not-warranted",
            "warrant": warrant,
            "reason": "No consequential Two-Sided Review warrant is present.",
        }
        validate_two_sided_result(result)
        return result
    evidence_ids = [item.get("evidence_id") for item in evidence]
    if (
        not evidence
        or any(not isinstance(item, str) or not item.strip() for item in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        raise SystemReviewError("Two-Sided Review requires unique evidence artifacts")
    reports = {}
    branches = []
    for role in ("case-for", "case-against"):
        initial_context_sha256 = value_sha256(
            {"role": role, "evidence_ids": evidence_ids}
        )
        report = case_reasoner(role, copy.deepcopy(evidence))
        used_evidence_ids = report.get("evidence_ids")
        if (
            not str(report.get("summary", "")).strip()
            or not isinstance(used_evidence_ids, list)
            or not used_evidence_ids
            or not set(used_evidence_ids).issubset(set(evidence_ids))
        ):
            raise SystemReviewError(f"Two-Sided Review branch used unavailable evidence: {role}")
        artifact_id = f"two-sided:{value_sha256(report)}"
        reports[role] = {
            **report,
            "role": role,
            "artifact_id": artifact_id,
            "initial_context_sha256": initial_context_sha256,
        }
        branches.append(
            IndependentBranch(
                branch_id=f"branch:{role}",
                role=role,
                initial_context_sha256=initial_context_sha256,
                input_artifact_ids=tuple(used_evidence_ids),
                output_artifact_id=artifact_id,
                source_ids=tuple(used_evidence_ids),
                selected_agent_id=str(report.get("reasoner_id", "registered-reasoner")),
                result_summary=report["summary"],
            )
        )
    independence = verify_branch_independence(
        branches,
        required_roles={"case-for", "case-against"},
    )
    synthesis = synthesizer(reports["case-for"], reports["case-against"])
    if (
        not str(synthesis.get("recommendation", "")).strip()
        or not str(synthesis.get("biggest_remaining_concern", "")).strip()
    ):
        raise SystemReviewError("Two-Sided Review synthesis is incomplete")
    result = {
        "status": "completed",
        "warrant": warrant,
        "case_for_artifact_id": reports["case-for"]["artifact_id"],
        "case_against_artifact_id": reports["case-against"]["artifact_id"],
        "independent_cases_verified": independence["independent"],
        "synthesised_by": "main-agent",
        "case_reports": reports,
        "synthesis": synthesis,
        "automatic_decision": False,
    }
    try:
        validate_two_sided_result(result)
    except ReviewContractError as error:
        raise SystemReviewError(str(error)) from error
    return result


def verified_zero_pattern_reasoner(
    stage: str,
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Produce the deterministic No Change path only from a complete zero-pattern receipt."""
    receipts = [
        artifact
        for artifact in bundle.get("artifacts", [])
        if artifact.get("payload", {}).get("record_type")
        == "zero-pattern-coverage-receipt"
    ]
    if len(receipts) != 1:
        raise SystemReviewError("Zero-pattern review requires one complete coverage receipt")
    artifact = receipts[0]
    receipt = artifact["payload"]
    if (
        receipt.get("coverage_complete") is not True
        or receipt.get("observation_count") != 0
        or not str(receipt.get("reason", "")).strip()
        or receipt.get("project_snapshot_sha256")
        != bundle.get("project_snapshot_sha256")
    ):
        raise SystemReviewError(
            "Zero-pattern coverage receipt is incomplete or for another Project"
        )
    evidence_ids = [artifact["evidence_id"]]
    hypothesis_id = f"hypothesis-no-change:{bundle['project_snapshot_sha256'][:16]}"
    common_reason = receipt["reason"]
    values = {
        "project-assessment": {
            "comparison_baseline": receipt["comparison_baseline"],
            "positive_delta": [],
            "no_positive_delta_reason": common_reason,
            "negative_delta": [],
            "no_negative_delta_reason": common_reason,
        },
        "issue-scan": {
            "collection_mode": "quantity-over-quality",
            "causal_filtering_applied": False,
            "deduplication_status": "deferred-to-flow-2",
            "whole_project_history_manifest": {
                "covered_sections": sorted(ISSUE_SCAN_HISTORY_SECTIONS),
                "snapshot_sha256": bundle["project_snapshot_sha256"],
                "source_artifact_ids": evidence_ids,
            },
            "observations": [],
            "coverage_complete": True,
            "no_observation_reason": common_reason,
        },
        "project-patterns": {
            "status": "no-pattern",
            "observation_ids_considered": [],
            "patterns": [],
            "reason": "No observations exist to group into a Local Pattern.",
        },
        "cross-project-patterns": {
            "history_status": "insufficient",
            "history_manifest": {
                key: list(receipt.get("history_manifest", {}).get(key, []))
                for key in HISTORICAL_RECORD_TYPES
            },
            "pattern_types_seen": [],
            "comparisons": [],
            "reason": "No Local Pattern exists for cross-history comparison.",
        },
        "reversal-check": {
            "direction_history": [],
            "reversals": [],
            "problem_dimension_status": "not-enough-history",
            "cause_research_warranted": False,
        },
        "cause-research": {
            "status": "not-needed",
            "reason": "No Global Pattern exists to explain.",
        },
        "reconciliation": {
            "status": "not-needed",
            "reason": "Cause Research was not needed.",
        },
        "improvement-options": {
            "existing_capability_assessment": {
                "checked": True,
                "sufficient": True,
                "component_ids": [],
                "evidence_ids": evidence_ids,
            },
            "options": [
                {
                    "kind": kind,
                    "status": "viable" if kind == "no-change" else "not-applicable",
                    "reason": (
                        "No verified Pattern supports this response."
                        if kind != "no-change"
                        else "Complete coverage supports retaining the current system."
                    ),
                    "evidence_ids": evidence_ids,
                }
                for kind in IMPROVEMENT_OPTION_KINDS
            ],
            "preferred_kind": "no-change",
            "complexity_delta": 0,
            "curiosity": {
                "trigger": "solution-needed",
                "status": "not-needed",
                "reason": "No solution is needed because no Pattern exists.",
                "automatic_adoption": False,
            },
        },
        "expected-effect": {
            "hypothesis_id": hypothesis_id,
            "problem_summary": "No verified improvement problem was observed.",
            "causal_hypothesis": "The current evidence does not justify a system change.",
            "expected_local_effect": "No local behaviour changes.",
            "expected_global_effect": "System complexity remains stable.",
            "possible_downside": "A subtle opportunity may remain undiscovered.",
            "uncertainty": "The conclusion is limited to the completed coverage receipt.",
            "evaluation_horizon": "the next comparable completed Project",
        },
        "local-effect": {
            "status": "not-applicable",
            "hypothesis_id": hypothesis_id,
            "evidence_ids": [],
            "reason": "No system change is proposed.",
        },
        "global-effect": {
            "status": "not-applicable",
            "hypothesis_id": hypothesis_id,
            "evidence_ids": [],
            "reason": "No system change is proposed.",
        },
        "blueprint-mapping": {
            "status": "no-candidates",
            "candidate_mappings": [],
            "unmapped_candidate_ids": [],
            "reason": "No Pattern or solution created an implementation Candidate.",
        },
        "two-sided-review": {
            "status": "not-warranted",
            "reason": "No consequential Candidate exists.",
            "warrant": {
                "high_impact": False,
                "hard_to_restore": False,
                "cross_project": False,
                "cross_platform": False,
                "authority_change": False,
                "evidence_conflict": False,
                "direction_reversal": False,
                "primary_user_requested": False,
            },
        },
        "final-assessment": {
            "recommendation": "no-change",
            "confidence": "bounded",
            "synthesised_by": "main-agent",
            "before_after_compared": True,
            "history_checked": True,
            "improvement_status": "not-applicable",
        },
        "biggest-remaining-concern": {
            "summary": "A later Project may reveal evidence absent from this complete snapshot."
        },
        "result": {
            "outcome": "no-change",
            "reason": common_reason,
            "response_units": [],
            "zero_pattern_coverage_receipt": {
                "coverage_complete": True,
                "observation_count": 0,
                "pattern_status": "no-pattern",
                "reason": common_reason,
            },
            "unmapped_pattern_ids": [],
            "plain_handoff": {
                "problem": "No material improvement problem was observed.",
                "solution": "Keep the current system unchanged.",
                "decision": "Carson decides whether to accept No Change.",
            },
            "newcomer_shadow_handoff_number": 1,
        },
    }
    result = values.get(stage)
    if result is None:
        raise SystemReviewError(f"Zero-pattern reasoner cannot execute stage: {stage}")
    return result, evidence_ids


def warrants_two_sided_review(warrant: dict[str, bool]) -> bool:
    """Use independent debate only for an approved consequential trigger."""
    fields = {
        "high_impact",
        "hard_to_restore",
        "cross_project",
        "cross_platform",
        "authority_change",
        "evidence_conflict",
        "direction_reversal",
        "primary_user_requested",
    }
    unknown = set(warrant).difference(fields)
    if unknown:
        raise ValueError(f"Unknown Two-Sided Review warrant: {sorted(unknown)}")
    return any(warrant.values())


def start_system_review(project: ProjectRecord) -> dict[str, Any]:
    """Start only after typed primary-user Project Completion."""
    if project.status != "completed" or project.completion["completed_by"] != "primary-user":
        raise AuthorityError("System Review requires primary-user Project Completion")
    return {
        "record_type": "system-review",
        "record_version": 2,
        "review_id": f"system-review-{uuid.uuid4()}",
        "project_id": project.project_id,
        "project_snapshot_sha256": value_sha256(project.to_dict()),
        "trigger": "project-completion",
        "started_at": utc_now(),
        "status": "in_progress",
        "backbone": {
            "protagonist": "system-review",
            "workflow": "new-blueprint-eight-flows",
            "required_flows": [
                "find-problems",
                "find-local-patterns",
                "find-global-patterns",
                "find-global-pattern-reasons",
                "find-global-pattern-solutions",
                "map-implementations-to-blueprint",
                "debate-global-pattern-solutions",
                "present-decisions-one-by-one",
            ],
            "three_values": ["fatigue", "curiosity", "greed"],
        },
        "stages": [],
        "result": None,
    }


def record_system_review_stage(
    review: dict[str, Any],
    *,
    stage: str,
    result: dict[str, Any],
    evidence_ids: list[str],
    actor: str | None = None,
    human_action: bool = False,
) -> dict[str, Any]:
    """Record exactly one next stage in the required order."""
    updated = copy.deepcopy(review)
    position = len(updated["stages"])
    if position >= len(SYSTEM_REVIEW_STAGES):
        raise SystemReviewError("System Review already contains every stage")
    expected = SYSTEM_REVIEW_STAGES[position]
    if stage != expected:
        raise SystemReviewError(f"Expected stage {expected}, received {stage}")
    if not result:
        raise SystemReviewError("A System Review stage requires an observed result")
    _validate_system_review_stage(stage, result)
    _validate_system_review_transition(updated, stage=stage, result=result)
    if stage == "your-decision" and (actor != "primary-user" or not human_action):
        raise AuthorityError("Your Decision requires the primary user")
    stage_record = {
        "stage": stage,
        "blueprint_flow": BACKBONE_FLOW_BY_STAGE[stage],
        "result": copy.deepcopy(result),
        "evidence_ids": list(evidence_ids),
        "recorded_at": utc_now(),
    }
    if stage in {"expected-effect", "local-effect", "global-effect"}:
        stage_record["supporting_infrastructure"] = "reality-check"
    updated["stages"].append(stage_record)
    if stage == "result":
        try:
            readiness = validate_review_ready(updated)
        except ReviewContractError as error:
            raise SystemReviewError(str(error)) from error
        updated["status"] = "awaiting-primary-user-decision"
        updated["result"] = copy.deepcopy(result)
        updated["readiness"] = readiness
    if stage == "your-decision":
        updated["status"] = "completed"
        updated["decision"] = copy.deepcopy(result)
        updated["completed_at"] = utc_now()
    return updated


def _validate_system_review_stage(stage: str, result: dict[str, Any]) -> None:
    """Fail closed when a core review stage omits its defining evidence."""
    if stage == "project-assessment":
        if not str(result.get("comparison_baseline", "")).strip():
            raise SystemReviewError("Project Assessment requires a comparison baseline")
        for field in ("positive_delta", "negative_delta"):
            deltas = result.get(field)
            if not isinstance(deltas, list):
                raise SystemReviewError(
                    "Project Assessment requires explicit Positive Delta and Negative Delta lists"
                )
            if not deltas and not str(result.get(f"no_{field}_reason", "")).strip():
                raise SystemReviewError(f"An empty {field} requires an observed reason")
            for delta in deltas:
                _validate_delta(delta, field=field)
    elif stage == "issue-scan":
        if result.get("collection_mode") != "quantity-over-quality":
            raise SystemReviewError("Issue Scan requires the Quantity over Quality collection mode")
        if result.get("causal_filtering_applied") is not False:
            raise SystemReviewError("Issue Scan must preserve observations before causal filtering")
        if result.get("deduplication_status") != "deferred-to-flow-2":
            raise SystemReviewError("Issue Scan must defer deduplication and grouping to Flow 2")
        _validate_whole_project_history_manifest(result.get("whole_project_history_manifest"))
        observations = result.get("observations")
        if not isinstance(observations, list):
            raise SystemReviewError("Issue Scan requires an observation list")
        if not observations and (
            result.get("coverage_complete") is not True
            or not str(result.get("no_observation_reason", "")).strip()
        ):
            raise SystemReviewError(
                "An empty Issue Scan requires complete coverage and an observed reason"
            )
        for observation in observations:
            _validate_raw_observation(observation)
    elif stage == "project-patterns":
        _validate_project_patterns(result)
    elif stage == "cross-project-patterns":
        _validate_cross_project_patterns(result)
    elif stage == "reversal-check":
        _validate_reversal_check(result)
    elif stage == "cause-research":
        status = result.get("status")
        if status not in {"completed", "not-needed"}:
            raise SystemReviewError("Cause Research must be completed or explicitly not needed")
        if status == "completed":
            if result.get("independent_branches_verified") is not True:
                raise SystemReviewError(
                    "Cause Research requires independently verified external and internal branches"
                )
            for field in ("external_research_artifact_id", "internal_review_artifact_id"):
                if not str(result.get(field, "")).strip():
                    raise SystemReviewError(f"Cause Research requires {field}")
        elif not str(result.get("reason", "")).strip():
            raise SystemReviewError("Cause Research marked not needed requires a reason")
    elif stage == "reconciliation":
        if result.get("status") not in {"completed", "not-needed"}:
            raise SystemReviewError("Reconciliation must be completed or explicitly not needed")
        if result["status"] == "completed":
            for field in ("agreements", "disagreements"):
                if not isinstance(result.get(field), list):
                    raise SystemReviewError(f"Reconciliation requires {field}")
            if not str(result.get("local_diagnosis", "")).strip():
                raise SystemReviewError("Reconciliation requires a local diagnosis")
        elif not str(result.get("reason", "")).strip():
            raise SystemReviewError("Reconciliation marked not needed requires a reason")
    elif stage == "improvement-options":
        _validate_improvement_options(result)
    elif stage == "expected-effect":
        for field in (
            "hypothesis_id",
            "problem_summary",
            "causal_hypothesis",
            "expected_local_effect",
            "expected_global_effect",
            "possible_downside",
            "uncertainty",
            "evaluation_horizon",
        ):
            if not str(result.get(field, "")).strip():
                raise SystemReviewError(f"Expected Effect requires {field}")
    elif stage == "local-effect":
        _validate_effect_record(result, effect="local")
    elif stage == "global-effect":
        _validate_effect_record(result, effect="global")
    elif stage == "blueprint-mapping":
        status = result.get("status")
        mappings = result.get("candidate_mappings")
        if status not in {"completed", "no-candidates"} or not isinstance(mappings, list):
            raise SystemReviewError(
                "Blueprint Mapping must be completed or record an honest no-candidates result"
            )
        if status == "completed":
            if not mappings:
                raise SystemReviewError("Completed Blueprint Mapping requires Candidates")
            for mapping in mappings:
                try:
                    validate_candidate_mapping(mapping)
                except ValueError as error:
                    raise SystemReviewError(str(error)) from error
        elif mappings or not str(result.get("reason", "")).strip():
            raise SystemReviewError(
                "No-candidates Blueprint Mapping requires an empty list and reason"
            )
        if result.get("unmapped_candidate_ids") != []:
            raise SystemReviewError("Blueprint Mapping cannot leave Candidates unmapped")
    elif stage == "two-sided-review":
        try:
            validate_two_sided_result(result)
        except ReviewContractError as error:
            raise SystemReviewError(str(error)) from error
    elif stage == "final-assessment":
        if result.get("synthesised_by") != "main-agent":
            raise SystemReviewError(
                "Only the Main Agent can make the final System Review suggestion"
            )
        if not result.get("recommendation") or not result.get("confidence"):
            raise SystemReviewError("Final Assessment requires a recommendation and confidence")
        if result.get("before_after_compared") is not True:
            raise SystemReviewError("Final Assessment requires an explicit before-and-after check")
        if result.get("history_checked") is not True:
            raise SystemReviewError("Final Assessment requires previous attempts to be checked")
        if result.get("improvement_status") not in {
            "genuine-improvement",
            "harmful-local-optimisation",
            "helpful-wrong-causal-model",
            "failed-intervention",
            "pending-later-outcome",
            "not-applicable",
        }:
            raise SystemReviewError("Final Assessment requires an honest improvement status")
    elif stage == "biggest-remaining-concern":
        if not str(result.get("summary", "")).strip():
            raise SystemReviewError("Biggest Remaining Concern requires a concrete summary")
    elif stage == "result":
        outcome = result.get("outcome")
        allowed = {
            "change-proposal",
            "experiment",
            "need-more-evidence",
            "no-change",
        }
        if outcome not in allowed:
            raise SystemReviewError(
                "System Review result must be Change Proposal, Experiment, "
                "Need More Evidence, or No Change"
            )
        if outcome == "change-proposal" and not result.get("proposal_id"):
            raise SystemReviewError("Change Proposal result requires a proposal id")
        if outcome == "experiment" and not result.get("experiment_id"):
            raise SystemReviewError("Experiment result requires an experiment id")
        if (
            outcome in {"need-more-evidence", "no-change"}
            and not str(result.get("reason", "")).strip()
        ):
            raise SystemReviewError(f"{outcome} result requires a reason")
        response_units = result.get("response_units")
        if not isinstance(response_units, list):
            raise SystemReviewError("System Review result requires response-unit coverage")
        if not response_units:
            zero = result.get("zero_pattern_coverage_receipt")
            if outcome != "no-change" or not isinstance(zero, dict):
                raise SystemReviewError(
                    "An empty response-unit set requires a No Change zero-pattern receipt"
                )
            if (
                zero.get("coverage_complete") is not True
                or zero.get("observation_count") != 0
                or zero.get("pattern_status") != "no-pattern"
                or not str(zero.get("reason", "")).strip()
            ):
                raise SystemReviewError("Zero-pattern coverage receipt is incomplete")
        if result.get("unmapped_pattern_ids") != []:
            raise SystemReviewError("System Review result cannot leave Patterns unmapped")
        if not isinstance(result.get("plain_handoff"), dict):
            raise SystemReviewError("System Review result requires a plain-language handoff")
    elif stage == "your-decision" and result.get("decision") not in {
        "accept-result",
        "reject-result",
        "give-feedback",
    }:
        raise SystemReviewError("Your Decision must accept the result, reject it, or give feedback")


def _validate_delta(delta: Any, *, field: str) -> None:
    if not isinstance(delta, dict):
        raise SystemReviewError(f"{field} items must be typed records")
    for required in ("delta_id", "summary", "baseline"):
        if not str(delta.get(required, "")).strip():
            raise SystemReviewError(f"{field} item requires {required}")
    if not isinstance(delta.get("evidence_ids"), list) or not delta["evidence_ids"]:
        raise SystemReviewError(f"{field} item requires evidence ids")


def _validate_whole_project_history_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise SystemReviewError("Issue Scan requires a whole-Project history manifest")
    covered = manifest.get("covered_sections")
    if not isinstance(covered, list) or set(covered) != ISSUE_SCAN_HISTORY_SECTIONS:
        missing = sorted(ISSUE_SCAN_HISTORY_SECTIONS.difference(covered or []))
        extra = sorted(set(covered or []).difference(ISSUE_SCAN_HISTORY_SECTIONS))
        raise SystemReviewError(
            f"Whole-Project history coverage mismatch; missing={missing}, extra={extra}"
        )
    digest = str(manifest.get("snapshot_sha256", ""))
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise SystemReviewError("Whole-Project history manifest requires a SHA-256 snapshot")
    if not isinstance(manifest.get("source_artifact_ids"), list):
        raise SystemReviewError("Whole-Project history manifest requires source artifact ids")


def _validate_raw_observation(observation: Any) -> None:
    if not isinstance(observation, dict):
        raise SystemReviewError("Issue Scan observations must be typed records")
    for required in ("observation_id", "summary", "uncertainty"):
        if not str(observation.get(required, "")).strip():
            raise SystemReviewError(f"Issue Scan observation requires {required}")
    if observation.get("delta") not in {"positive", "negative", "neutral", "possible"}:
        raise SystemReviewError("Issue Scan observation requires a valid delta classification")
    if not isinstance(observation.get("evidence_ids"), list) or not observation["evidence_ids"]:
        raise SystemReviewError("Issue Scan observation requires evidence ids")
    premature_fields = {
        "root_cause",
        "pattern_id",
        "priority",
        "proposed_change",
        "recommended_component",
    }
    found = sorted(premature_fields.intersection(observation))
    if found:
        raise SystemReviewError(f"Issue Scan performed Flow 2 or Flow 4 work too early: {found}")


def _validate_project_patterns(result: dict[str, Any]) -> None:
    if result.get("status") not in {"completed", "no-pattern"}:
        raise SystemReviewError("Project Patterns must be completed or record no pattern")
    if not isinstance(result.get("observation_ids_considered"), list):
        raise SystemReviewError("Project Patterns must identify the observations considered")
    patterns = result.get("patterns")
    if not isinstance(patterns, list):
        raise SystemReviewError("Project Patterns requires a pattern list")
    if result["status"] == "completed" and not patterns:
        raise SystemReviewError("Completed Project Patterns requires at least one Local Pattern")
    if result["status"] == "no-pattern" and not str(result.get("reason", "")).strip():
        raise SystemReviewError("No-pattern result requires a reason")
    if result["status"] == "completed" and not result["observation_ids_considered"]:
        raise SystemReviewError("Completed Project Patterns requires observations")
    for pattern in patterns:
        if not isinstance(pattern, dict):
            raise SystemReviewError("Local Patterns must be typed records")
        for required in ("pattern_id", "symptom_summary", "possible_cause"):
            if not str(pattern.get(required, "")).strip():
                raise SystemReviewError(f"Local Pattern requires {required}")
        if not isinstance(pattern.get("observation_ids"), list) or not pattern["observation_ids"]:
            raise SystemReviewError("Local Pattern requires linked observations")
        if pattern.get("causal_status") not in {"unknown", "plausible", "confirmed"}:
            raise SystemReviewError("Local Pattern requires a causal status")
        if pattern.get("confidence") not in {"low", "medium", "high"}:
            raise SystemReviewError("Local Pattern requires confidence")
        if not isinstance(pattern.get("counterevidence"), list):
            raise SystemReviewError("Local Pattern requires a counterevidence list")
        if pattern["causal_status"] == "confirmed" and not pattern.get("confirmation_evidence_ids"):
            raise SystemReviewError("A confirmed cause requires confirmation evidence")


def _validate_cross_project_patterns(result: dict[str, Any]) -> None:
    if result.get("history_status") not in {"sufficient", "insufficient"}:
        raise SystemReviewError("Cross-Project Patterns must state history sufficiency")
    manifest = result.get("history_manifest")
    if not isinstance(manifest, dict) or set(manifest) != HISTORICAL_RECORD_TYPES:
        raise SystemReviewError(
            "Cross-Project Patterns requires the complete analysis and intervention "
            "history manifest"
        )
    if any(not isinstance(records, list) for records in manifest.values()):
        raise SystemReviewError("Every historical record type must be represented as a list")
    allowed_types = {"recurrence", "contradiction", "overcorrection", "pattern-of-patterns"}
    pattern_types = result.get("pattern_types_seen")
    if not isinstance(pattern_types, list) or not set(pattern_types) <= allowed_types:
        raise SystemReviewError(
            "Cross-Project Patterns contains an invalid historical pattern type"
        )
    comparisons = result.get("comparisons")
    if not isinstance(comparisons, list):
        raise SystemReviewError("Cross-Project Patterns requires comparison records")
    if result["history_status"] == "sufficient" and not comparisons:
        raise SystemReviewError("Sufficient history requires at least one comparison")
    if result["history_status"] == "insufficient" and not str(result.get("reason", "")).strip():
        raise SystemReviewError("Insufficient history requires a reason")


def _validate_reversal_check(result: dict[str, Any]) -> None:
    if not isinstance(result.get("direction_history"), list) or not isinstance(
        result.get("reversals"), list
    ):
        raise SystemReviewError("Reversal Check requires direction history and reversal records")
    if result.get("problem_dimension_status") not in {
        "credible",
        "challenged",
        "not-enough-history",
    }:
        raise SystemReviewError("Reversal Check requires a problem-dimension status")
    if not isinstance(result.get("cause_research_warranted"), bool):
        raise SystemReviewError("Reversal Check requires a Cause Research decision")
    if result["reversals"] and (
        result["problem_dimension_status"] != "challenged"
        or result["cause_research_warranted"] is not True
    ):
        raise SystemReviewError(
            "Directional reversal must challenge the problem dimension and warrant Cause Research"
        )


def _validate_improvement_options(result: dict[str, Any]) -> None:
    assessment = result.get("existing_capability_assessment")
    if not isinstance(assessment, dict) or assessment.get("checked") is not True:
        raise SystemReviewError("Improvement Options requires an existing Capability Check")
    if not isinstance(assessment.get("component_ids"), list) or not isinstance(
        assessment.get("evidence_ids"), list
    ):
        raise SystemReviewError("Capability Check requires component and evidence ids")
    if not isinstance(assessment.get("sufficient"), bool):
        raise SystemReviewError(
            "Capability Check must state whether the current system is sufficient"
        )
    options = result.get("options")
    if not isinstance(options, list):
        raise SystemReviewError("Improvement Options requires typed options")
    kinds = [option.get("kind") for option in options if isinstance(option, dict)]
    if len(options) != len(IMPROVEMENT_OPTION_KINDS) or set(kinds) != set(IMPROVEMENT_OPTION_KINDS):
        raise SystemReviewError(
            "Improvement Options must cover delete, shorten, merge, simplify, "
            "reconfigure, modify, add, and no-change"
        )
    if kinds != list(IMPROVEMENT_OPTION_KINDS):
        raise SystemReviewError("Improvement Options must preserve Subtraction First order")
    for option in options:
        if option.get("status") not in {"viable", "rejected", "not-applicable"}:
            raise SystemReviewError("Each Improvement Option requires a status")
        if not str(option.get("reason", "")).strip():
            raise SystemReviewError("Each Improvement Option requires a reason")
        if not isinstance(option.get("evidence_ids"), list):
            raise SystemReviewError("Each Improvement Option requires an evidence list")
    add_option = next(option for option in options if option["kind"] == "add")
    if add_option["status"] == "viable" and assessment["sufficient"] is True:
        raise SystemReviewError(
            "A new component cannot be viable when existing capability suffices"
        )
    if not str(result.get("preferred_kind", "")).strip():
        raise SystemReviewError("Improvement Options requires a preferred response kind")
    if result["preferred_kind"] not in set(IMPROVEMENT_OPTION_KINDS):
        raise SystemReviewError("Preferred response is not a recognised option kind")
    preferred_index = IMPROVEMENT_OPTION_KINDS.index(result["preferred_kind"])
    preferred_option = options[preferred_index]
    if preferred_option["status"] != "viable":
        raise SystemReviewError("Preferred response must be a viable Improvement Option")
    for earlier in options[:preferred_index]:
        if earlier["status"] == "viable" and not str(
            earlier.get("not_selected_reason", "")
        ).strip():
            raise SystemReviewError(
                "Selecting a later option requires evidence for bypassing every earlier viable "
                "Subtraction First response"
            )
    if not isinstance(result.get("complexity_delta"), int):
        raise SystemReviewError("Improvement Options requires a complexity delta")
    _validate_solution_curiosity(
        result.get("curiosity"),
        solution_needed=result["preferred_kind"] != "no-change",
    )


def _validate_solution_curiosity(value: Any, *, solution_needed: bool) -> None:
    """Require auditable Curiosity whenever Improvement Options selects a solution."""
    if not isinstance(value, dict):
        raise SystemReviewError("Improvement Options requires Curiosity 60/20/20 evidence")
    if value.get("automatic_adoption") is not False:
        raise SystemReviewError("Curiosity findings cannot be adopted automatically")
    if not solution_needed:
        if value.get("status") != "not-needed" or not str(value.get("reason", "")).strip():
            raise SystemReviewError("No-change must explicitly record why Curiosity is not needed")
        return
    if value.get("trigger") != "solution-needed":
        raise SystemReviewError("Solution selection requires the Curiosity solution-needed trigger")
    expected_allocation = curiosity_routes("solution-needed")
    if value.get("allocation") != expected_allocation:
        raise SystemReviewError(
            "Solution selection requires the exact Curiosity 60/20/20 allocation"
        )
    if value.get("status") not in {"candidate-findings", "no-finding"}:
        raise SystemReviewError("Curiosity requires an honest finding status")
    findings = value.get("findings")
    if not isinstance(findings, list):
        raise SystemReviewError("Curiosity requires a candidate findings list")
    expected_status = "candidate-findings" if findings else "no-finding"
    if value["status"] != expected_status:
        raise SystemReviewError("Curiosity finding status does not match its evidence")
    route_results = value.get("route_results")
    expected_actions = [item["action_id"] for item in expected_allocation]
    if (
        not isinstance(route_results, list)
        or [item.get("action_id") for item in route_results if isinstance(item, dict)]
        != expected_actions
    ):
        raise SystemReviewError("Curiosity 60/20/20 requires one recorded outcome for every route")
    flattened_findings = []
    for action_id, route_result in zip(expected_actions, route_results, strict=True):
        if route_result.get("status") not in {"candidate-findings", "no-finding"}:
            raise SystemReviewError("Each Curiosity route requires an honest outcome")
        route_findings = route_result.get("findings")
        if not isinstance(route_findings, list):
            raise SystemReviewError("Each Curiosity route requires a findings list")
        route_status = "candidate-findings" if route_findings else "no-finding"
        if route_result["status"] != route_status:
            raise SystemReviewError("Curiosity route status does not match its evidence")
        for finding in route_findings:
            if not isinstance(finding, dict) or finding.get("action_id") != action_id:
                raise SystemReviewError("Curiosity finding is attached to the wrong route")
            if (
                not str(finding.get("summary", "")).strip()
                or not str(finding.get("observed_at", "")).strip()
            ):
                raise SystemReviewError("Curiosity finding requires summary and provenance")
            if action_id == "research-latest-findings" and (
                not str(finding.get("source", "")).strip()
                or not str(finding.get("source_date", "")).strip()
            ):
                raise SystemReviewError("Curiosity latest finding requires source and source date")
            if action_id == "explore-related-fields" and (
                not str(finding.get("related_field", "")).strip()
                or not str(finding.get("relationship", "")).strip()
            ):
                raise SystemReviewError(
                    "Curiosity related-field finding requires a concrete relationship"
                )
        flattened_findings.extend(route_findings)
    if flattened_findings != findings:
        raise SystemReviewError("Curiosity route outcomes and combined findings do not match")


def _validate_effect_record(result: dict[str, Any], *, effect: str) -> None:
    if result.get("status") not in {"observed", "pending", "not-applicable"}:
        raise SystemReviewError(f"{effect.title()} Effect requires an evidence status")
    if not str(result.get("hypothesis_id", "")).strip():
        raise SystemReviewError(f"{effect.title()} Effect requires a hypothesis id")
    if not isinstance(result.get("evidence_ids"), list):
        raise SystemReviewError(f"{effect.title()} Effect requires evidence ids")
    if result["status"] == "observed":
        if not isinstance(result.get("before"), dict) or not isinstance(result.get("after"), dict):
            raise SystemReviewError(f"Observed {effect} effect requires before and after")
        decision_field = "hypothesis_supported" if effect == "local" else "system_improved"
        if not isinstance(result.get(decision_field), bool):
            raise SystemReviewError(f"Observed {effect} effect requires {decision_field}")
        if not result["evidence_ids"]:
            raise SystemReviewError(f"Observed {effect} effect requires evidence")
    elif not str(result.get("reason", "")).strip():
        raise SystemReviewError(f"{effect.title()} Effect status requires a reason")


def _validate_system_review_transition(
    review: dict[str, Any],
    *,
    stage: str,
    result: dict[str, Any],
) -> None:
    previous = {item["stage"]: item["result"] for item in review["stages"]}
    if stage == "cause-research":
        reversal = previous["reversal-check"]
        if reversal["cause_research_warranted"] and result["status"] != "completed":
            raise SystemReviewError("A warranted Cause Research cannot be skipped")
    elif stage == "reconciliation":
        cause = previous["cause-research"]
        expected = "completed" if cause["status"] == "completed" else "not-needed"
        if result["status"] != expected:
            raise SystemReviewError("Reconciliation status must match the Cause Research route")
    elif stage in {"local-effect", "global-effect"}:
        expected = previous["expected-effect"]["hypothesis_id"]
        if result["hypothesis_id"] != expected:
            raise SystemReviewError("Effect record does not match the Expected Effect hypothesis")
    elif stage == "blueprint-mapping":
        preferred = previous["improvement-options"]["preferred_kind"]
        expected = "no-candidates" if preferred == "no-change" else "completed"
        if result["status"] != expected:
            raise SystemReviewError(
                "Blueprint Mapping status must match whether the selected response changes anything"
            )
    elif stage == "two-sided-review":
        if "blueprint-mapping" not in previous:
            raise SystemReviewError("Debate cannot begin before Blueprint Mapping")
    elif stage == "final-assessment":
        local = previous["local-effect"]
        global_effect = previous["global-effect"]
        expected = classify_outcome_evidence(local, global_effect)
        if result["improvement_status"] != expected:
            raise SystemReviewError(
                "Final Assessment improvement status does not match Local and Global evidence"
            )


def classify_outcome_evidence(
    local_effect: dict[str, Any],
    global_effect: dict[str, Any],
) -> str:
    """Classify real before-and-after evidence without confusing change with improvement."""
    statuses = {local_effect.get("status"), global_effect.get("status")}
    if "pending" in statuses:
        return "pending-later-outcome"
    if statuses == {"not-applicable"}:
        return "not-applicable"
    if statuses != {"observed"}:
        raise SystemReviewError("Local and Global Effect statuses are not comparable")
    local_supported = local_effect["hypothesis_supported"]
    globally_improved = global_effect["system_improved"]
    return {
        (True, True): "genuine-improvement",
        (True, False): "harmful-local-optimisation",
        (False, True): "helpful-wrong-causal-model",
        (False, False): "failed-intervention",
    }[(local_supported, globally_improved)]


def order_improvement_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply Subtraction First and retain No Change as a valid option."""
    order = {kind: position for position, kind in enumerate(IMPROVEMENT_OPTION_KINDS)}
    unknown = [item["kind"] for item in options if item.get("kind") not in order]
    if unknown:
        raise ValueError(f"Unknown improvement option kind: {unknown}")
    if not any(item["kind"] == "no-change" for item in options):
        options = [*options, {"kind": "no-change", "summary": "Keep the current version"}]
    return sorted(copy.deepcopy(options), key=lambda item: order[item["kind"]])


def detect_reversals(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find opposite component directions without guessing the hidden dimension."""
    opposites = {
        ("add", "remove"),
        ("remove", "add"),
        ("enable", "disable"),
        ("disable", "enable"),
        ("expand", "reduce"),
        ("reduce", "expand"),
    }
    reversals = []
    by_component: dict[str, list[dict[str, Any]]] = {}
    for item in history:
        by_component.setdefault(item["component_id"], []).append(item)
    for component_id, items in by_component.items():
        ordered = sorted(items, key=lambda item: item["recorded_at"])
        for first, second in zip(ordered, ordered[1:], strict=False):
            if (first["direction"], second["direction"]) in opposites:
                reversals.append(
                    {
                        "component_id": component_id,
                        "from": first["direction"],
                        "to": second["direction"],
                        "first_outcome": first.get("outcome"),
                        "second_outcome": second.get("outcome"),
                        "hidden_dimension": "unknown",
                        "problem_representation_challenged": True,
                        "next_question": "are-we-solving-the-wrong-problem",
                    }
                )
    return reversals


def build_change_proposal(
    *,
    title: str,
    change_kind: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    expected_effect: dict[str, Any],
    local_effect: dict[str, Any],
    global_effect: dict[str, Any],
    evidence_ids: list[str],
    restore_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build a reviewable proposal that cannot activate itself."""
    if change_kind not in {"add", "remove", "replace", "merge", "split", "modify"}:
        raise ValueError(f"Unsupported Change Proposal kind: {change_kind}")
    if not evidence_ids or not restore_plan:
        raise SystemReviewError("A Change Proposal requires evidence and a restore plan")
    _validate_system_review_stage("expected-effect", expected_effect)
    _validate_effect_record(local_effect, effect="local")
    _validate_effect_record(global_effect, effect="global")
    hypothesis_id = expected_effect["hypothesis_id"]
    if {
        local_effect["hypothesis_id"],
        global_effect["hypothesis_id"],
    } != {hypothesis_id}:
        raise SystemReviewError("Proposal effects must reference the same hypothesis")
    proposal = {
        "record_type": "change-proposal",
        "proposal_id": f"proposal-{uuid.uuid4()}",
        "title": title,
        "change_kind": change_kind,
        "baseline": baseline,
        "candidate": candidate,
        "diff_sha256": value_sha256({"baseline": baseline, "candidate": candidate}),
        "expected_effect": expected_effect,
        "local_effect": local_effect,
        "global_effect": global_effect,
        "evidence_ids": evidence_ids,
        "restore_plan": restore_plan,
        "learning_record": {
            "hypothesis_id": hypothesis_id,
            "problem_summary": expected_effect["problem_summary"],
            "causal_hypothesis": expected_effect["causal_hypothesis"],
            "outcome_classification": classify_outcome_evidence(
                local_effect,
                global_effect,
            ),
            "later_evaluations": [],
        },
        "decision_status": "proposed",
        "active": False,
    }
    return proposal


def record_later_outcome_evaluation(
    proposal: dict[str, Any],
    *,
    local_effect: dict[str, Any],
    global_effect: dict[str, Any],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """Attach later real outcomes while preserving the original causal hypothesis."""
    if not evidence_ids:
        raise SystemReviewError("Later outcome evaluation requires evidence")
    _validate_effect_record(local_effect, effect="local")
    _validate_effect_record(global_effect, effect="global")
    if local_effect["status"] != "observed" or global_effect["status"] != "observed":
        raise SystemReviewError(
            "Later outcome evaluation requires observed Local and Global Effect"
        )
    hypothesis_id = proposal["learning_record"]["hypothesis_id"]
    if {
        local_effect["hypothesis_id"],
        global_effect["hypothesis_id"],
    } != {hypothesis_id}:
        raise SystemReviewError("Later outcome evidence references a different hypothesis")
    updated = copy.deepcopy(proposal)
    evaluation = {
        "evaluation_id": f"outcome-evaluation-{uuid.uuid4()}",
        "hypothesis_id": hypothesis_id,
        "local_effect": copy.deepcopy(local_effect),
        "global_effect": copy.deepcopy(global_effect),
        "outcome_classification": classify_outcome_evidence(local_effect, global_effect),
        "evidence_ids": list(evidence_ids),
        "recorded_at": utc_now(),
    }
    updated["learning_record"]["later_evaluations"].append(evaluation)
    updated["learning_record"]["outcome_classification"] = evaluation["outcome_classification"]
    return updated


def decide_change_proposal(
    proposal: dict[str, Any],
    *,
    decision: str,
    actor: str,
    human_action: bool,
) -> dict[str, Any]:
    """Record a human proposal decision without directly changing the active version."""
    if actor != "primary-user" or not human_action:
        raise AuthorityError("Change Proposal decisions require the primary user")
    if decision not in {"approve", "reject"}:
        raise ValueError(f"Unsupported proposal decision: {decision}")
    updated = copy.deepcopy(proposal)
    updated["decision_status"] = "approved-for-version" if decision == "approve" else "rejected"
    updated["decided_by"] = actor
    updated["decided_at"] = utc_now()
    updated["active"] = False
    return updated


def review_feedback(
    *,
    feedback: str,
    source: str,
    accepted_scope: str | None,
    supporting_reasons: list[str],
    challenging_reasons: list[str],
    updated_final_assessment: str,
    biggest_remaining_concern: str,
) -> dict[str, Any]:
    """Evaluate feedback from both sides and return it to Your Decision."""
    if not feedback.strip() or not updated_final_assessment.strip():
        raise SystemReviewError("Feedback Review requires feedback and an updated assessment")
    if not supporting_reasons or not challenging_reasons:
        raise SystemReviewError("Feedback Review requires supporting and challenging reasons")
    if not biggest_remaining_concern.strip():
        raise SystemReviewError("Feedback Review requires the biggest remaining concern")
    return {
        "record_type": "feedback-review",
        "feedback": feedback,
        "source": source,
        "instruction_authority": "accepted" if accepted_scope else "evidence-only",
        "accepted_scope": accepted_scope,
        "supporting_reasons": list(supporting_reasons),
        "challenging_reasons": list(challenging_reasons),
        "updated_final_assessment": updated_final_assessment.strip(),
        "biggest_remaining_concern": biggest_remaining_concern.strip(),
        "automatic_system_change": False,
        "next_route": "your-decision",
    }


class ExperimentRunner:
    """Run same-work baseline and candidate functions in a disposable directory."""

    def run(
        self,
        *,
        boundary: TrialBoundary,
        baseline_runner: Callable[[Path], TrialMeasurement],
        candidate_runner: Callable[[Path], TrialMeasurement],
    ) -> dict[str, Any]:
        """Run only after every deterministic trial boundary passes."""
        boundary_result = assess_trial_boundary(boundary)
        if not boundary_result["allowed"]:
            return {
                "status": "approval-required-before-trial",
                "boundary": boundary_result,
                "executed": False,
            }
        temporary_path: Path | None = None
        with tempfile.TemporaryDirectory(prefix="fractal-experiment-") as directory:
            temporary_path = Path(directory)
            baseline = baseline_runner(temporary_path / "baseline")
            candidate = candidate_runner(temporary_path / "candidate")
            comparison = compare_trial_results(boundary, baseline, candidate)
        return {
            "status": comparison["status"],
            "boundary": boundary_result,
            "executed": True,
            "restore_verified": temporary_path is not None and not temporary_path.exists(),
            "comparison": comparison,
            "persistent_adoption": False,
        }
