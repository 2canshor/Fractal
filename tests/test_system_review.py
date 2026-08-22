from __future__ import annotations

from pathlib import Path

import pytest

from fractal.improvement import TrialBoundary, TrialMeasurement
from fractal.models import ProjectRecord
from fractal.storage import AuthorityError
from fractal.system_review import (
    SYSTEM_REVIEW_STAGES,
    AgentCandidate,
    ExperimentRunner,
    IndependentBranch,
    SystemReviewError,
    build_change_proposal,
    decide_change_proposal,
    detect_reversals,
    order_improvement_options,
    record_system_review_stage,
    review_feedback,
    select_lightest_capable_agent,
    start_system_review,
    verify_branch_independence,
    warrants_two_sided_review,
)


def completed_project() -> ProjectRecord:
    return ProjectRecord(
        project_id="completed-project",
        title="Completed Project",
        system_version="0.1.0-alpha.1",
        status="completed",
        completion={
            "requested_at": "2026-08-22T00:00:00Z",
            "completed_at": "2026-08-22T00:01:00Z",
            "completed_by": "primary-user",
        },
    )


def safe_boundary(**overrides: bool) -> TrialBoundary:
    values = {
        "small": True,
        "reversible": True,
        "isolated": True,
        "restore_verified": True,
        "no_real_data_change": True,
        "no_external_action": True,
        "no_new_recipient": True,
        "no_cost": True,
        "no_goal_change": True,
        "no_scope_expansion": True,
        "no_persistent_change": True,
        "same_representative_work": True,
    }
    values.update(overrides)
    return TrialBoundary(**values)


def stage_result(stage: str) -> dict:
    values = {
        "project-assessment": {
            "what_went_well": ["The approved outcome was achieved"],
            "what_could_be_better": ["One verification step was repeated"],
        },
        "issue-scan": {
            "scan_mode": "high-recall",
            "observations": ["Repeated verification", "Late restore proof"],
        },
        "cross-project-patterns": {
            "history_status": "insufficient",
            "summary": "Only one comparable Project exists",
        },
        "cause-research": {"status": "not-needed", "reason": "Cause is directly observed"},
        "two-sided-review": {
            "status": "not-warranted",
            "reason": "No consequential proposal",
        },
        "final-assessment": {
            "recommendation": "no-change",
            "confidence": "high",
            "synthesised_by": "main-agent",
        },
        "biggest-remaining-concern": {
            "summary": "There is only one completed Project in the comparison set"
        },
        "result": {"outcome": "no-change", "reason": "Mutation lacks evidence"},
        "your-decision": {"decision": "accept-result"},
    }
    return values.get(stage, {"summary": f"Observed {stage}"})


def test_full_system_review_accepts_no_change_as_a_real_result() -> None:
    review = start_system_review(completed_project())
    for stage in SYSTEM_REVIEW_STAGES:
        review = record_system_review_stage(
            review,
            stage=stage,
            result=stage_result(stage),
            evidence_ids=["evidence-a"],
            actor="primary-user" if stage == "your-decision" else None,
            human_action=stage == "your-decision",
        )
    assert review["status"] == "completed"
    assert review["result"]["outcome"] == "no-change"
    assert len(review["stages"]) == len(SYSTEM_REVIEW_STAGES)


def test_system_review_cannot_start_before_primary_user_completion() -> None:
    project = completed_project()
    project.status = "in_progress"
    project.completion["completed_by"] = None
    with pytest.raises(AuthorityError, match="Project Completion"):
        start_system_review(project)


def test_system_review_stages_cannot_skip_order() -> None:
    review = start_system_review(completed_project())
    with pytest.raises(SystemReviewError, match="Expected stage project-assessment"):
        record_system_review_stage(
            review,
            stage="issue-scan",
            result={"summary": "Too early"},
            evidence_ids=[],
        )


def test_final_suggestion_and_change_result_fail_closed() -> None:
    review = start_system_review(completed_project())
    for stage in SYSTEM_REVIEW_STAGES[:12]:
        review = record_system_review_stage(
            review,
            stage=stage,
            result=stage_result(stage),
            evidence_ids=["evidence-a"],
        )
    with pytest.raises(SystemReviewError, match="Main Agent"):
        record_system_review_stage(
            review,
            stage="final-assessment",
            result={
                "recommendation": "change",
                "confidence": "medium",
                "synthesised_by": "review-subagent",
            },
            evidence_ids=["evidence-a"],
        )
    review = record_system_review_stage(
        review,
        stage="final-assessment",
        result={
            "recommendation": "change",
            "confidence": "medium",
            "synthesised_by": "main-agent",
        },
        evidence_ids=["evidence-a"],
    )
    review = record_system_review_stage(
        review,
        stage="biggest-remaining-concern",
        result={"summary": "The global effect is not yet proven live"},
        evidence_ids=["evidence-a"],
    )
    with pytest.raises(SystemReviewError, match="proposal id"):
        record_system_review_stage(
            review,
            stage="result",
            result={"outcome": "change-proposal"},
            evidence_ids=["evidence-a"],
        )


def test_lightest_agent_and_independent_research_and_debate_branches() -> None:
    candidates = [
        AgentCandidate("large", 3, 3, True, frozenset({"web", "citations"})),
        AgentCandidate("small", 1, 1, True, frozenset({"web", "citations"})),
        AgentCandidate("failed", 0, 0, False, frozenset({"web", "citations"})),
    ]
    selected = select_lightest_capable_agent(
        candidates,
        required_capabilities={"web", "citations"},
    )
    assert selected.agent_id == "small"
    branches = [
        IndependentBranch(
            "branch-external",
            "external-research",
            "a" * 64,
            ("project-brief",),
            "external-output",
            ("source-a",),
            "small",
            "External finding",
        ),
        IndependentBranch(
            "branch-internal",
            "internal-review",
            "b" * 64,
            ("project-record",),
            "internal-output",
            ("record-a",),
            "small",
            "Internal finding",
        ),
    ]
    verified = verify_branch_independence(
        branches,
        required_roles={"external-research", "internal-review"},
    )
    assert verified["independent"] is True
    assert warrants_two_sided_review({"evidence_conflict": True}) is True
    debate = [
        IndependentBranch(
            "case-for",
            "case-for",
            "c" * 64,
            ("proposal",),
            "for-output",
            ("evidence-a",),
            "small",
            "Strongest case for",
        ),
        IndependentBranch(
            "case-against",
            "case-against",
            "d" * 64,
            ("proposal",),
            "against-output",
            ("evidence-b",),
            "small",
            "Strongest case against",
        ),
    ]
    assert verify_branch_independence(
        debate,
        required_roles={"case-for", "case-against"},
    )["independent"]
    contaminated = [
        branches[0],
        IndependentBranch(
            "branch-internal",
            "internal-review",
            "b" * 64,
            ("external-output",),
            "internal-output",
            ("record-a",),
            "small",
            "Contaminated",
        ),
    ]
    with pytest.raises(SystemReviewError, match="another branch output"):
        verify_branch_independence(
            contaminated,
            required_roles={"external-research", "internal-review"},
        )


def test_subtraction_first_reversal_and_change_proposal_authority() -> None:
    options = order_improvement_options(
        [
            {"kind": "add", "summary": "Add a layer"},
            {"kind": "merge", "summary": "Merge two components"},
            {"kind": "delete", "summary": "Delete obsolete logic"},
        ]
    )
    assert [item["kind"] for item in options] == ["delete", "merge", "add", "no-change"]
    reversals = detect_reversals(
        [
            {
                "component_id": "router",
                "direction": "add",
                "recorded_at": "2026-01-01",
                "outcome": "slow",
            },
            {
                "component_id": "router",
                "direction": "remove",
                "recorded_at": "2026-02-01",
                "outcome": "simple",
            },
        ]
    )
    assert reversals[0]["hidden_dimension"] == "unknown"
    proposal = build_change_proposal(
        title="Simplify a route",
        change_kind="remove",
        baseline={"routes": 2},
        candidate={"routes": 1},
        expected_effect={"summary": "Less duplication"},
        local_effect={"risk": "low"},
        global_effect={"coverage": "unchanged"},
        evidence_ids=["evidence-a"],
        restore_plan={"action": "restore previous manifest"},
    )
    assert proposal["active"] is False
    with pytest.raises(AuthorityError, match="primary user"):
        decide_change_proposal(
            proposal,
            decision="approve",
            actor="main-agent",
            human_action=False,
        )
    approved = decide_change_proposal(
        proposal,
        decision="approve",
        actor="primary-user",
        human_action=True,
    )
    assert approved["decision_status"] == "approved-for-version"
    assert approved["active"] is False
    rejected = decide_change_proposal(
        proposal,
        decision="reject",
        actor="primary-user",
        human_action=True,
    )
    assert rejected["decision_status"] == "rejected"


def test_experiment_runner_safe_and_approval_required_paths() -> None:
    runner = ExperimentRunner()

    def baseline(path: Path) -> TrialMeasurement:
        path.mkdir(parents=True)
        (path / "result.txt").write_text("same outcome")
        return TrialMeasurement(True, 1.0, 10.0, None, 0.99, False)

    def candidate(path: Path) -> TrialMeasurement:
        path.mkdir(parents=True)
        (path / "result.txt").write_text("same outcome")
        return TrialMeasurement(True, 1.0, 5.0, None, 0.99, False)

    safe = runner.run(
        boundary=safe_boundary(),
        baseline_runner=baseline,
        candidate_runner=candidate,
    )
    assert safe["status"] == "candidate-for-review"
    assert safe["restore_verified"] is True
    assert safe["persistent_adoption"] is False
    blocked = runner.run(
        boundary=safe_boundary(no_external_action=False),
        baseline_runner=baseline,
        candidate_runner=candidate,
    )
    assert blocked["status"] == "approval-required-before-trial"
    assert blocked["executed"] is False


def test_feedback_is_evaluated_without_becoming_automatic_instruction() -> None:
    evidence = review_feedback(
        feedback="Make the system shorter",
        source="user-feedback",
        accepted_scope=None,
        supporting_reasons=["The current route repeats one stage"],
        challenging_reasons=["Removing it may hide evidence"],
        updated_final_assessment="Shorten the wording and retain the evidence contract",
        biggest_remaining_concern="The shorter wording still needs a newcomer test",
    )
    assert evidence["instruction_authority"] == "evidence-only"
    assert evidence["automatic_system_change"] is False
    accepted = review_feedback(
        feedback="Use Cantonese for this status update",
        source="typed-user-action",
        accepted_scope="current-status-update",
        supporting_reasons=["It matches the user's stated language"],
        challenging_reasons=["Technical identifiers must remain exact"],
        updated_final_assessment="Use Cantonese prose and preserve technical identifiers",
        biggest_remaining_concern="No material concern remains for this update",
    )
    assert accepted["instruction_authority"] == "accepted"
    assert accepted["accepted_scope"] == "current-status-update"
    assert accepted["next_route"] == "your-decision"


def test_result_waits_for_primary_user_your_decision() -> None:
    review = start_system_review(completed_project())
    for stage in SYSTEM_REVIEW_STAGES[:-1]:
        review = record_system_review_stage(
            review,
            stage=stage,
            result=stage_result(stage),
            evidence_ids=["evidence-a"],
        )
    assert review["status"] == "awaiting-primary-user-decision"
    with pytest.raises(AuthorityError, match="primary user"):
        record_system_review_stage(
            review,
            stage="your-decision",
            result={"decision": "accept-result"},
            evidence_ids=["evidence-a"],
            actor="main-agent",
        )
