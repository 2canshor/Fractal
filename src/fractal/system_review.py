"""Deterministic orchestration contracts for full System Review."""

from __future__ import annotations

import copy
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fractal.improvement import (
    TrialBoundary,
    TrialMeasurement,
    assess_trial_boundary,
    compare_trial_results,
)
from fractal.models import ProjectRecord, utc_now
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
    "two-sided-review",
    "final-assessment",
    "biggest-remaining-concern",
    "result",
    "your-decision",
]


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
        "record_version": 1,
        "review_id": f"system-review-{uuid.uuid4()}",
        "project_id": project.project_id,
        "project_snapshot_sha256": value_sha256(project.to_dict()),
        "trigger": "project-completion",
        "started_at": utc_now(),
        "status": "in_progress",
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
    if stage == "your-decision" and (actor != "primary-user" or not human_action):
        raise AuthorityError("Your Decision requires the primary user")
    updated["stages"].append(
        {
            "stage": stage,
            "result": copy.deepcopy(result),
            "evidence_ids": list(evidence_ids),
            "recorded_at": utc_now(),
        }
    )
    if stage == "result":
        updated["status"] = "awaiting-primary-user-decision"
        updated["result"] = copy.deepcopy(result)
    if stage == "your-decision":
        updated["status"] = "completed"
        updated["decision"] = copy.deepcopy(result)
        updated["completed_at"] = utc_now()
    return updated


def _validate_system_review_stage(stage: str, result: dict[str, Any]) -> None:
    """Fail closed when a core review stage omits its defining evidence."""
    if stage == "project-assessment":
        for field in ("what_went_well", "what_could_be_better"):
            value = result.get(field)
            if not isinstance(value, list) or not value:
                raise SystemReviewError(
                    "Project Assessment requires what went well and what could be better"
                )
    elif stage == "issue-scan":
        if result.get("scan_mode") != "high-recall" or not isinstance(
            result.get("observations"), list
        ):
            raise SystemReviewError(
                "Issue Scan requires a high-recall observation list before prioritisation"
            )
    elif stage == "cross-project-patterns":
        if result.get("history_status") not in {"sufficient", "insufficient"}:
            raise SystemReviewError(
                "Cross-Project Patterns must state whether history is sufficient"
            )
    elif stage == "cause-research":
        status = result.get("status")
        if status not in {"completed", "not-needed"}:
            raise SystemReviewError("Cause Research must be completed or explicitly not needed")
        if status == "completed" and result.get("independent_branches_verified") is not True:
            raise SystemReviewError(
                "Cause Research requires independently verified external and internal branches"
            )
    elif stage == "two-sided-review":
        if result.get("status") not in {"completed", "not-warranted"}:
            raise SystemReviewError(
                "Two-Sided Review must be completed or explicitly not warranted"
            )
        if (
            result.get("status") == "completed"
            and result.get("independent_cases_verified") is not True
        ):
            raise SystemReviewError("Case For and Case Against must be independently verified")
    elif stage == "final-assessment":
        if result.get("synthesised_by") != "main-agent":
            raise SystemReviewError(
                "Only the Main Agent can make the final System Review suggestion"
            )
        if not result.get("recommendation") or not result.get("confidence"):
            raise SystemReviewError("Final Assessment requires a recommendation and confidence")
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
    elif stage == "your-decision" and result.get("decision") not in {
        "accept-result",
        "reject-result",
        "give-feedback",
    }:
        raise SystemReviewError("Your Decision must accept the result, reject it, or give feedback")


def order_improvement_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply Subtraction First and retain No Change as a valid option."""
    order = {
        "delete": 0,
        "shorten": 1,
        "merge": 2,
        "simplify": 3,
        "modify": 4,
        "add": 5,
        "no-change": 6,
    }
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
        "decision_status": "proposed",
        "active": False,
    }
    return proposal


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
