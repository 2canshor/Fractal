from __future__ import annotations

from pathlib import Path

import pytest

from fractal.improvement import (
    ResearchFinding,
    TrialBoundary,
    TrialMeasurement,
    combine_curiosity_findings,
)
from fractal.models import ProjectRecord
from fractal.storage import AuthorityError
from fractal.system_review import (
    SYSTEM_REVIEW_STAGES,
    AgentCandidate,
    ExperimentRunner,
    IndependentBranch,
    SystemReviewError,
    build_change_proposal,
    classify_outcome_evidence,
    decide_change_proposal,
    detect_reversals,
    order_improvement_options,
    record_later_outcome_evaluation,
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
            "comparison_baseline": "The approved Plan and previous comparable result",
            "positive_delta": [
                {
                    "delta_id": "positive-a",
                    "summary": "The approved outcome was achieved",
                    "baseline": "The previous Project missed one criterion",
                    "evidence_ids": ["evidence-a"],
                }
            ],
            "negative_delta": [
                {
                    "delta_id": "negative-a",
                    "summary": "One verification step was repeated",
                    "baseline": "The Plan expected one verification pass",
                    "evidence_ids": ["evidence-a"],
                }
            ],
        },
        "issue-scan": {
            "collection_mode": "quantity-over-quality",
            "causal_filtering_applied": False,
            "deduplication_status": "deferred-to-step-2",
            "whole_project_history_manifest": {
                "covered_sections": [
                    "project-direction",
                    "project-plan-history",
                    "project-reviews",
                    "work-records",
                    "decisions-and-corrections",
                    "outcome-evidence",
                    "resource-use",
                ],
                "snapshot_sha256": "a" * 64,
                "source_artifact_ids": ["project-snapshot"],
            },
            "observations": [
                {
                    "observation_id": "observation-a",
                    "summary": "Verification repeated",
                    "uncertainty": "The extra pass may have been necessary",
                    "delta": "possible",
                    "evidence_ids": ["evidence-a"],
                },
                {
                    "observation_id": "observation-b",
                    "summary": "Restore proof arrived late",
                    "uncertainty": "The sequencing cause is not established",
                    "delta": "negative",
                    "evidence_ids": ["evidence-a"],
                },
            ],
        },
        "project-patterns": {
            "status": "completed",
            "observation_ids_considered": ["observation-a", "observation-b"],
            "patterns": [
                {
                    "pattern_id": "pattern-a",
                    "observation_ids": ["observation-a", "observation-b"],
                    "symptom_summary": "Verification evidence was assembled more than once",
                    "possible_cause": "Evidence requirements were staged too late",
                    "causal_status": "plausible",
                    "confidence": "medium",
                    "counterevidence": ["One repeat may be an independent safety check"],
                }
            ],
        },
        "cross-project-patterns": {
            "history_status": "insufficient",
            "history_manifest": {
                "projects": ["completed-project"],
                "system-reviews": [],
                "change-proposals": [],
                "hypotheses": [],
                "system-versions": [],
                "interventions": [],
                "outcomes": [],
            },
            "pattern_types_seen": [],
            "comparisons": [],
            "reason": "Only one comparable Project exists",
        },
        "reversal-check": {
            "direction_history": [],
            "reversals": [],
            "problem_dimension_status": "not-enough-history",
            "cause_research_warranted": False,
        },
        "cause-research": {"status": "not-needed", "reason": "Cause is directly observed"},
        "reconciliation": {"status": "not-needed", "reason": "Cause Research was not needed"},
        "improvement-options": {
            "existing_capability_assessment": {
                "checked": True,
                "sufficient": True,
                "component_ids": ["existing-review"],
                "evidence_ids": ["evidence-a"],
            },
            "options": [
                {
                    "kind": kind,
                    "status": "viable" if kind == "no-change" else "not-applicable",
                    "reason": (
                        "The evidence supports the current system"
                        if kind == "no-change"
                        else "No evidence supports this response"
                    ),
                    "evidence_ids": ["evidence-a"],
                }
                for kind in (
                    "delete",
                    "shorten",
                    "merge",
                    "simplify",
                    "reconfigure",
                    "modify",
                    "add",
                    "no-change",
                )
            ],
            "preferred_kind": "no-change",
            "complexity_delta": 0,
            "curiosity": {
                "trigger": "solution-needed",
                "status": "not-needed",
                "reason": "No solution is needed because no-change is preferred",
                "automatic_adoption": False,
            },
        },
        "expected-effect": {
            "hypothesis_id": "hypothesis-a",
            "problem_summary": "The possible repetition lacks enough history",
            "causal_hypothesis": "The current evidence does not justify a system change",
            "expected_local_effect": "No local behaviour changes",
            "expected_global_effect": "System complexity stays stable",
            "possible_downside": "A useful improvement may be delayed",
            "uncertainty": "Only one completed Project exists",
            "evaluation_horizon": "the next comparable Project",
        },
        "local-effect": {
            "status": "not-applicable",
            "hypothesis_id": "hypothesis-a",
            "evidence_ids": [],
            "reason": "No system change is proposed",
        },
        "global-effect": {
            "status": "not-applicable",
            "hypothesis_id": "hypothesis-a",
            "evidence_ids": [],
            "reason": "No system change is proposed",
        },
        "two-sided-review": {
            "status": "not-warranted",
            "reason": "No consequential proposal",
        },
        "final-assessment": {
            "recommendation": "no-change",
            "confidence": "high",
            "synthesised_by": "main-agent",
            "before_after_compared": True,
            "history_checked": True,
            "improvement_status": "not-applicable",
        },
        "biggest-remaining-concern": {
            "summary": "There is only one completed Project in the comparison set"
        },
        "result": {"outcome": "no-change", "reason": "Mutation lacks evidence"},
        "your-decision": {"decision": "accept-result"},
    }
    return values[stage]


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
    assert [item["backbone_step"] for item in review["stages"]] == [
        1,
        1,
        2,
        3,
        3,
        3,
        3,
        4,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
        5,
    ]


def test_step_one_requires_deltas_whole_history_and_no_early_cause() -> None:
    review = start_system_review(completed_project())
    with pytest.raises(SystemReviewError, match="Positive Delta and Negative Delta"):
        record_system_review_stage(
            review,
            stage="project-assessment",
            result={"comparison_baseline": "Project Plan"},
            evidence_ids=["evidence-a"],
        )
    review = record_system_review_stage(
        review,
        stage="project-assessment",
        result=stage_result("project-assessment"),
        evidence_ids=["evidence-a"],
    )
    incomplete = stage_result("issue-scan")
    incomplete["whole_project_history_manifest"]["covered_sections"].remove("resource-use")
    with pytest.raises(SystemReviewError, match="history coverage mismatch"):
        record_system_review_stage(
            review,
            stage="issue-scan",
            result=incomplete,
            evidence_ids=["evidence-a"],
        )
    premature = stage_result("issue-scan")
    premature["observations"][0]["root_cause"] = "late planning"
    with pytest.raises(SystemReviewError, match="too early"):
        record_system_review_stage(
            review,
            stage="issue-scan",
            result=premature,
            evidence_ids=["evidence-a"],
        )


def test_step_two_keeps_plausible_and_confirmed_causes_distinct() -> None:
    review = start_system_review(completed_project())
    for stage in ("project-assessment", "issue-scan"):
        review = record_system_review_stage(
            review,
            stage=stage,
            result=stage_result(stage),
            evidence_ids=["evidence-a"],
        )
    unsupported = stage_result("project-patterns")
    unsupported["patterns"][0]["causal_status"] = "confirmed"
    with pytest.raises(SystemReviewError, match="confirmation evidence"):
        record_system_review_stage(
            review,
            stage="project-patterns",
            result=unsupported,
            evidence_ids=["evidence-a"],
        )


def test_reversal_challenges_dimension_and_forces_cause_research() -> None:
    review = start_system_review(completed_project())
    for stage in SYSTEM_REVIEW_STAGES[:4]:
        review = record_system_review_stage(
            review,
            stage=stage,
            result=stage_result(stage),
            evidence_ids=["evidence-a"],
        )
    reversal = {
        "direction_history": ["detailed", "concise", "detailed"],
        "reversals": [{"from": "detailed", "to": "concise"}],
        "problem_dimension_status": "challenged",
        "cause_research_warranted": True,
    }
    review = record_system_review_stage(
        review,
        stage="reversal-check",
        result=reversal,
        evidence_ids=["evidence-a"],
    )
    with pytest.raises(SystemReviewError, match="cannot be skipped"):
        record_system_review_stage(
            review,
            stage="cause-research",
            result={"status": "not-needed", "reason": "Use the previous explanation"},
            evidence_ids=["evidence-a"],
        )


def test_step_four_requires_complete_subtraction_first_and_capability_check() -> None:
    result = stage_result("improvement-options")
    result["options"].pop(4)
    with pytest.raises(SystemReviewError, match="must cover"):
        from fractal.system_review import _validate_system_review_stage

        _validate_system_review_stage("improvement-options", result)
    add = stage_result("improvement-options")
    next(item for item in add["options"] if item["kind"] == "add")["status"] = "viable"
    with pytest.raises(SystemReviewError, match="existing capability suffices"):
        _validate_system_review_stage("improvement-options", add)


def test_solution_needed_fails_closed_without_curiosity_60_20_20() -> None:
    from fractal.system_review import _validate_system_review_stage

    result = stage_result("improvement-options")
    result["preferred_kind"] = "modify"
    result["complexity_delta"] = 0
    result["existing_capability_assessment"]["sufficient"] = True
    del result["curiosity"]
    with pytest.raises(SystemReviewError, match="Curiosity 60/20/20"):
        _validate_system_review_stage("improvement-options", result)


def test_solution_needed_accepts_complete_curiosity_60_20_20() -> None:
    from fractal.system_review import _validate_system_review_stage

    result = stage_result("improvement-options")
    result["preferred_kind"] = "modify"
    result["complexity_delta"] = 0
    result["existing_capability_assessment"]["sufficient"] = True
    result["curiosity"] = combine_curiosity_findings(
        "solution-needed",
        [
            ResearchFinding(
                "improve-current-method",
                "Move mutable live state out of immutable adapter snapshots",
                None,
                "2026-08-23T00:00:00Z",
            ),
            ResearchFinding(
                "research-latest-findings",
                "Atomic replacement keeps readers away from partial state",
                "https://docs.python.org/3/library/os.html#os.replace",
                "2026-08-23T00:00:00Z",
                source_date="2026-08-23",
            ),
            ResearchFinding(
                "explore-related-fields",
                "Materialized views must be refreshed or rejected when stale",
                "https://martinfowler.com/eaaDev/EventSourcing.html",
                "2026-08-23T00:00:00Z",
                related_field="event-sourced projections",
                relationship="Both derive fast read state from a canonical write model",
            ),
        ],
    )
    _validate_system_review_stage("improvement-options", result)


def test_solution_needed_rejects_cosmetic_or_misrouted_curiosity() -> None:
    from fractal.system_review import _validate_system_review_stage

    result = stage_result("improvement-options")
    result["preferred_kind"] = "modify"
    result["curiosity"] = combine_curiosity_findings(
        "solution-needed",
        [
            ResearchFinding(
                "improve-current-method",
                "Use a verified mutable read model",
                None,
                "2026-08-23T00:00:00Z",
            )
        ],
    )
    result["curiosity"]["allocation"][0]["effort_share"] = 59
    with pytest.raises(SystemReviewError, match="exact Curiosity 60/20/20"):
        _validate_system_review_stage("improvement-options", result)

    result["curiosity"] = combine_curiosity_findings(
        "solution-needed",
        [
            ResearchFinding(
                "improve-current-method",
                "Use a verified mutable read model",
                None,
                "2026-08-23T00:00:00Z",
            )
        ],
    )
    result["curiosity"]["route_results"][0]["findings"][0]["action_id"] = (
        "research-latest-findings"
    )
    with pytest.raises(SystemReviewError, match="wrong route"):
        _validate_system_review_stage("improvement-options", result)


@pytest.mark.parametrize(
    ("local_supported", "system_improved", "expected"),
    [
        (True, True, "genuine-improvement"),
        (True, False, "harmful-local-optimisation"),
        (False, True, "helpful-wrong-causal-model"),
        (False, False, "failed-intervention"),
    ],
)
def test_step_five_keeps_local_and_global_outcomes_separate(
    local_supported: bool,
    system_improved: bool,
    expected: str,
) -> None:
    local = {
        "status": "observed",
        "hypothesis_id": "hypothesis-a",
        "before": {"elapsed": 10},
        "after": {"elapsed": 5},
        "hypothesis_supported": local_supported,
        "evidence_ids": ["evidence-local"],
    }
    global_effect = {
        "status": "observed",
        "hypothesis_id": "hypothesis-a",
        "before": {"project_quality": 8},
        "after": {"project_quality": 9 if system_improved else 7},
        "system_improved": system_improved,
        "evidence_ids": ["evidence-global"],
    }
    assert classify_outcome_evidence(local, global_effect) == expected


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
            "before_after_compared": True,
            "history_checked": True,
            "improvement_status": "not-applicable",
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
        expected_effect={
            "hypothesis_id": "hypothesis-simplify-route",
            "problem_summary": "The route duplicates one responsibility",
            "causal_hypothesis": "Removing the duplicate route reduces repeated handling",
            "expected_local_effect": "One route handles the responsibility",
            "expected_global_effect": "Coverage remains stable with less complexity",
            "possible_downside": "A hidden caller may still use the old route",
            "uncertainty": "Caller coverage is based on current evidence",
            "evaluation_horizon": "the next two comparable Projects",
        },
        local_effect={
            "status": "pending",
            "hypothesis_id": "hypothesis-simplify-route",
            "reason": "The candidate has not been tried",
            "evidence_ids": [],
        },
        global_effect={
            "status": "pending",
            "hypothesis_id": "hypothesis-simplify-route",
            "reason": "Later Project outcomes are required",
            "evidence_ids": [],
        },
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
    later = record_later_outcome_evaluation(
        proposal,
        local_effect={
            "status": "observed",
            "hypothesis_id": "hypothesis-simplify-route",
            "before": {"routes": 2},
            "after": {"routes": 1},
            "hypothesis_supported": True,
            "evidence_ids": ["evidence-local"],
        },
        global_effect={
            "status": "observed",
            "hypothesis_id": "hypothesis-simplify-route",
            "before": {"coverage": 100},
            "after": {"coverage": 95},
            "system_improved": False,
            "evidence_ids": ["evidence-global"],
        },
        evidence_ids=["evidence-local", "evidence-global"],
    )
    assert later["learning_record"]["outcome_classification"] == (
        "harmful-local-optimisation"
    )
    assert proposal["learning_record"]["later_evaluations"] == []


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
