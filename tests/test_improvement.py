from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from fractal.improvement import (
    ComponentShape,
    GreedMetricRule,
    ResearchFinding,
    TrialBoundary,
    TrialMeasurement,
    VerifiedOutcome,
    WorkSignature,
    WorkSignatureStore,
    activate_value_behavior,
    assess_trial_boundary,
    build_improvement_investigation,
    build_performance_baseline,
    challenge_candidate_criteria,
    challenge_trigger_allowed,
    classify_structural_repetition,
    combine_curiosity_findings,
    compare_trial_results,
    curiosity_routes,
    evaluate_global_outcome_trial,
    evaluate_greed_trial,
    recognise_repetition,
    route_value_evidence,
    semantic_comparison_payload,
)


def signature(work_id: str, *, purpose: str = "ordinary") -> WorkSignature:
    return WorkSignature(
        work_id=work_id,
        project_id="project-a",
        work_type="verify-package",
        input_shape="python-package",
        steps=("build", "install", "run"),
        tools=("uv",),
        outcome_category="verified",
        purpose_class=purpose,
        elapsed_seconds=None,
        token_usage=None,
        completed_at="2026-08-22T00:00:00Z",
    )


def test_completion_hook_captures_compact_signature_once(tmp_path: Path) -> None:
    store = WorkSignatureStore(tmp_path / "work-signatures.jsonl")
    item = signature("work-a")
    assert store.capture_completion(item) is True
    assert store.capture_completion(item) is False
    captured = store.read_all()
    assert len(captured) == 1
    assert captured[0]["elapsed_seconds"] is None
    assert captured[0]["token_usage"] is None
    assert "prompt" not in captured[0]
    assert "conversation" not in captured[0]


def test_stop_signature_can_be_enriched_once_with_final_turn_evidence(tmp_path: Path) -> None:
    store = WorkSignatureStore(tmp_path / "work-signatures.jsonl")
    lightweight = signature("work-a")
    assert store.capture_completion(lightweight)
    final = replace(
        lightweight,
        outcome_category="completed",
        elapsed_seconds=1.25,
        token_usage=42,
        thread_id="thread-a",
        turn_id="turn-a",
        tool_evidence=("exec_command:completed:0",),
        evidence_state="turn-completed",
    )
    assert store.enrich_completion(final)
    assert store.enrich_completion(final) is False
    assert len(store.read_all()) == 1
    assert store.read_all()[0]["token_usage"] == 42
    with pytest.raises(ValueError, match="cannot be changed"):
        store.enrich_completion(replace(final, token_usage=43))


def test_repetition_trigger_and_necessary_repetition_rules() -> None:
    first = signature("work-1")
    second = signature("work-2")
    third = signature("work-3")
    assert recognise_repetition([], first).status == "first-occurrence"
    assert recognise_repetition([first], second).status == "possible-repetition"
    result = recognise_repetition([first, second], third)
    assert result.status == "investigation-required"
    assert result.occurrence_count == 3
    assert result.route == "project-review"
    assert result.supporting_action == "improvement-researcher"
    assert recognise_repetition([first], second, high_avoidable_cost=True).status == (
        "investigation-required"
    )
    verification = replace(third, purpose_class="verification")
    assert (
        recognise_repetition(
            [
                replace(first, purpose_class="verification"),
                replace(second, purpose_class="verification"),
            ],
            verification,
        ).status
        == "necessary-repetition"
    )


def test_uncertain_match_uses_minimal_semantic_payload() -> None:
    first = signature("work-1")
    different_steps = replace(signature("work-2"), steps=("build", "smoke"))
    result = recognise_repetition([first], different_steps)
    assert result.status == "semantic-comparison-required"
    payload = semantic_comparison_payload(first, different_steps)
    assert payload["raw_work_included"] is False
    assert "elapsed_seconds" not in payload["first"]
    assert "token_usage" not in payload["first"]


def test_curiosity_allocation_provenance_relevance_and_no_finding() -> None:
    routes = curiosity_routes("improvement-investigation")
    assert [item["effort_share"] for item in routes] == [60, 20, 20]
    assert sum(item["effort_share"] for item in routes) == 100
    findings = [
        ResearchFinding(
            "research-latest-findings",
            "A dated technique may reduce repeated work",
            "https://example.test/current",
            "2026-08-22T00:00:00Z",
            source_date="2026-08-01",
        ),
        ResearchFinding(
            "explore-related-fields",
            "A compiler cache idea may transfer",
            "https://example.test/related",
            "2026-08-22T00:00:00Z",
            related_field="compiler design",
            relationship="Both reuse verified intermediate results",
        ),
    ]
    combined = combine_curiosity_findings("improvement-investigation", findings)
    assert combined["automatic_adoption"] is False
    assert combined["status"] == "candidate-findings"
    assert combine_curiosity_findings("success-criteria-challenge-post", [])["status"] == (
        "no-finding"
    )
    with pytest.raises(ValueError, match="source date"):
        combine_curiosity_findings(
            "improvement-investigation",
            [
                ResearchFinding(
                    "research-latest-findings",
                    "Missing freshness evidence",
                    "https://example.test",
                    "2026-08-22T00:00:00Z",
                )
            ],
        )


def test_investigation_is_candidate_analysis_and_routes_by_scope() -> None:
    recognition = recognise_repetition(
        [signature("work-1"), signature("work-2")],
        signature("work-3"),
    )
    local = build_improvement_investigation(
        recognition,
        project_id="project-a",
        method_summary="Build, install, and run a smoke test",
        alternatives=[
            {
                "summary": "Cache the built wheel",
                "expected_saving": "one build",
                "implementation_cost": "low",
                "risk": "stale cache",
                "outcome_equivalence": "requires trial",
            }
        ],
        persistent_scope=False,
        findings=[],
    )
    assert local["permissions"] == ["read-only"]
    assert local["context"]["raw_history_included"] is False
    assert local["automatic_change"] is False
    assert local["next_route"] == "project-review"
    persistent = build_improvement_investigation(
        recognition,
        project_id="project-a",
        method_summary="Build, install, and run a smoke test",
        alternatives=[],
        persistent_scope=True,
        findings=[],
    )
    assert persistent["next_route"] == "project-review"
    assert persistent["later_system_review_evidence"] is True


def shape(
    component_id: str,
    *,
    source: str = "source-a",
    platform: str = "codex",
    layer: str = "adapter",
    digest: str = "a" * 64,
    guardrail: bool = False,
    high_risk: bool = False,
) -> ComponentShape:
    return ComponentShape(
        component_id=component_id,
        canonical_responsibility="authority-check",
        canonical_source_id=source,
        platform=platform,
        layer=layer,
        content_sha256=digest,
        guardrail=guardrail,
        high_risk_boundary=high_risk,
    )


@pytest.mark.parametrize(
    ("first", "second", "expected", "candidate"),
    [
        (shape("a"), shape("b"), "accidental-duplication", True),
        (shape("a"), shape("b", platform="claude"), "platform-projection", False),
        (
            shape("a", layer="policy", guardrail=True),
            shape("b", layer="runtime", guardrail=True),
            "independent-guardrail",
            False,
        ),
        (
            shape("a", layer="policy", high_risk=True),
            shape("b", layer="runtime", high_risk=True),
            "deliberate-high-risk-reinforcement",
            False,
        ),
        (shape("a"), shape("b", digest="b" * 64), "drift", True),
    ],
)
def test_structural_repetition_classification(
    first: ComponentShape,
    second: ComponentShape,
    expected: str,
    candidate: bool,
) -> None:
    result = classify_structural_repetition(first, second)
    assert result["classification"] == expected
    assert result["removal_candidate"] is candidate
    assert result["automatic_removal"] is False


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


def test_safe_trial_is_isolated_quality_first_and_never_auto_adopts() -> None:
    baseline = TrialMeasurement(True, 1.0, 10.0, 1000, 0.99, False)
    faster = TrialMeasurement(True, 1.0, 5.0, 700, 0.99, False)
    result = compare_trial_results(safe_boundary(), baseline, faster)
    assert result["status"] == "candidate-for-review"
    assert result["persistent_adoption"] is False
    worse_quality = TrialMeasurement(True, 0.9, 1.0, 100, 0.99, False)
    assert compare_trial_results(safe_boundary(), baseline, worse_quality)["status"] == (
        "no-adoption-quality-first"
    )
    blocked = assess_trial_boundary(safe_boundary(no_external_action=False))
    assert blocked["allowed"] is False
    assert blocked["next_route"] == "request-primary-user-decision"
    unknown_measurements = TrialMeasurement(True, 1.0, None, None, None, False)
    assert (
        compare_trial_results(safe_boundary(), unknown_measurements, unknown_measurements)["status"]
        == "no-adoption-no-improvement"
    )


def test_performance_baseline_and_greed_use_evidence_without_fabrication() -> None:
    outcome = VerifiedOutcome(
        project_id="completed-project",
        comparable_key="analysis-task",
        project_status="completed",
        independently_verified=True,
        representative=True,
        metrics={
            "tokens": {"value": 1500, "direction": "minimize", "unit": "tokens"},
            "accuracy": {"value": 97, "direction": "maximize", "unit": "percent"},
        },
    )
    baseline = build_performance_baseline(
        [outcome],
        comparable_key="analysis-task",
        requested_dimensions=["tokens", "accuracy", "reliability"],
    )
    assert baseline["unknown_dimensions"] == ["reliability"]
    challenge = challenge_candidate_criteria(
        {
            "tokens": {"threshold": 2000, "direction": "minimize", "unit": "tokens"},
            "accuracy": {"threshold": 95, "direction": "maximize", "unit": "percent"},
            "reliability": {"threshold": 99, "direction": "maximize", "unit": "percent"},
        },
        baseline,
        materiality_rules=[
            GreedMetricRule("tokens", "minimize", min_absolute_change=100),
            GreedMetricRule("accuracy", "maximize", min_absolute_change=1),
        ],
        architecture_options=[
            {"summary": "Restructure the retrieval layer", "evidence_ids": ["evidence-a"]},
            {"summary": "Unsupported architecture idea", "evidence_ids": []},
        ],
    )
    suggestions = {
        item["dimension"]: item["suggested_threshold"] for item in challenge["ambitious_options"]
    }
    assert suggestions == {"tokens": 1400, "accuracy": 98}
    assert len(challenge["architecture_options"]) == 1
    assert challenge["fabricated_targets"] is False
    assert challenge["automatic_approval"] is False
    assert all(item["dimension"] != "reliability" for item in challenge["ambitious_options"])


def test_greed_ratchet_requires_material_verified_improvement_and_keeps_baseline() -> None:
    baseline = {
        "metrics": {
            "quality": {
                "value": 0.8,
                "direction": "maximize",
                "unit": "score",
                "provenance_project_ids": ["project-a"],
            },
            "cost": {
                "value": 100,
                "direction": "minimize",
                "unit": "tokens",
                "provenance_project_ids": ["project-a"],
            },
        }
    }
    rules = [
        GreedMetricRule("quality", "maximize", min_relative_change=0.1),
        GreedMetricRule("cost", "minimize", min_absolute_change=10),
    ]
    passed = evaluate_greed_trial(
        baseline=baseline,
        candidate_metrics={
            "quality": {
                "value": 0.89,
                "direction": "maximize",
                "evidence_ids": ["evidence-quality"],
            },
            "cost": {
                "value": 90,
                "direction": "minimize",
                "evidence_ids": ["evidence-cost"],
            },
        },
        materiality_rules=rules,
        original_success_preserved=True,
        representative_trial=True,
    )
    assert passed["status"] == "candidate-for-flow-5"
    assert passed["hands_off_to_flow"] == "find-global-pattern-solutions"
    assert passed["automatic_approval"] is False
    assert passed["persistent_change"] is False

    weak = evaluate_greed_trial(
        baseline=baseline,
        candidate_metrics={
            "quality": {
                "value": 0.87,
                "direction": "maximize",
                "evidence_ids": ["evidence-quality"],
            },
            "cost": {
                "value": 95,
                "direction": "minimize",
                "evidence_ids": ["evidence-cost"],
            },
        },
        materiality_rules=rules,
        original_success_preserved=True,
        representative_trial=True,
    )
    assert weak["status"] == "retain-original-success"
    assert weak["hands_off_to_flow"] is None


def test_greed_ratchet_fails_closed_without_evidence_or_original_success() -> None:
    baseline = {
        "metrics": {
            "quality": {
                "value": 10,
                "direction": "maximize",
                "unit": "points",
                "provenance_project_ids": ["project-a"],
            }
        }
    }
    rule = GreedMetricRule("quality", "maximize", min_absolute_change=1)
    with pytest.raises(ValueError, match="original successful result"):
        evaluate_greed_trial(
            baseline=baseline,
            candidate_metrics={},
            materiality_rules=[rule],
            original_success_preserved=False,
            representative_trial=True,
        )
    with pytest.raises(ValueError, match="requires evidence"):
        evaluate_greed_trial(
            baseline=baseline,
            candidate_metrics={
                "quality": {"value": 11, "direction": "maximize", "evidence_ids": []}
            },
            materiality_rules=[rule],
            original_success_preserved=True,
            representative_trial=True,
        )


def test_global_outcome_rejects_a_local_win_that_regresses_a_protected_dimension() -> None:
    baseline = {
        "metrics": {
            "quality": {
                "value": 80,
                "direction": "maximize",
                "unit": "score",
                "provenance_project_ids": ["project-a"],
            },
            "reliability": {
                "value": 99,
                "direction": "maximize",
                "unit": "percent",
                "provenance_project_ids": ["project-a"],
            },
        }
    }
    rules = [
        GreedMetricRule("quality", "maximize", min_absolute_change=5),
        GreedMetricRule("reliability", "maximize", min_absolute_change=0.5),
    ]
    harmful = evaluate_global_outcome_trial(
        baseline=baseline,
        candidate_metrics={
            "quality": {
                "value": 90,
                "direction": "maximize",
                "evidence_ids": ["quality-trial"],
            },
            "reliability": {
                "value": 98,
                "direction": "maximize",
                "evidence_ids": ["reliability-trial"],
            },
        },
        materiality_rules=rules,
        protected_dimensions=["reliability"],
        local_hypothesis_supported=True,
        representative_trial=True,
    )
    assert harmful["improvement_status"] == "harmful-local-optimisation"
    assert harmful["global_outcome_improved"] is False
    assert harmful["automatic_approval"] is False

    genuine = evaluate_global_outcome_trial(
        baseline=baseline,
        candidate_metrics={
            "quality": {
                "value": 90,
                "direction": "maximize",
                "evidence_ids": ["quality-trial"],
            },
            "reliability": {
                "value": 99,
                "direction": "maximize",
                "evidence_ids": ["reliability-trial"],
            },
        },
        materiality_rules=rules,
        protected_dimensions=["reliability"],
        local_hypothesis_supported=True,
        representative_trial=True,
    )
    assert genuine["improvement_status"] == "genuine-improvement"
    assert genuine["global_outcome_improved"] is True


def test_greed_does_not_move_mid_project_goalposts() -> None:
    assert challenge_trigger_allowed(
        trigger="pre_work",
        project_status="planning",
        original_criteria_achieved=False,
    )
    assert not challenge_trigger_allowed(
        trigger="pre_work",
        project_status="in_progress",
        original_criteria_achieved=False,
    )
    assert challenge_trigger_allowed(
        trigger="post_work",
        project_status="in_progress",
        original_criteria_achieved=True,
    )
    assert not challenge_trigger_allowed(
        trigger="post_work",
        project_status="in_progress",
        original_criteria_achieved=False,
    )


@pytest.mark.parametrize(
    ("value", "flow_inputs"),
    [
        ("fatigue", ["find-problems", "find-local-patterns"]),
        ("curiosity", ["find-global-pattern-reasons", "find-global-pattern-solutions"]),
        ("greed", ["find-problems", "find-global-pattern-solutions"]),
    ],
)
def test_three_values_route_into_the_existing_review_backbone(
    value: str,
    flow_inputs: list[str],
) -> None:
    active = route_value_evidence(
        value,
        project_id="project-a",
        project_status="in_progress",
        summary="A bounded signal from completed work",
        evidence_ids=["evidence-a"],
        persistent_system_observation=True,
    )
    assert active["primary_route"] == "project-review"
    assert active["system_review_evidence"] is True
    assert active["system_review_flow_inputs"] == flow_inputs
    assert active["decision_mechanism"] == "project-review"
    assert active["competing_improvement_loop"] is False

    completed = route_value_evidence(
        value,
        project_id="project-a",
        project_status="completed",
        summary="A bounded signal from the completed Project",
        evidence_ids=["evidence-a"],
    )
    assert completed["primary_route"] == "system-review"
    assert completed["system_review_evidence"] is True


def test_three_values_activate_only_at_their_blueprint_lifecycle_boundaries() -> None:
    fatigue = activate_value_behavior(
        "fatigue",
        trigger="verified-repetition",
        project_id="project-a",
        project_status="in_progress",
        evidence_ids=["work-a", "work-b", "work-c"],
    )
    curiosity = activate_value_behavior(
        "curiosity",
        trigger="solution-needed",
        project_id="project-a",
        project_status="completed",
        evidence_ids=["pattern-a"],
    )
    greed = activate_value_behavior(
        "greed",
        trigger="verified-success",
        project_id="project-a",
        project_status="completed",
        evidence_ids=["verified-outcome-a"],
        original_success_preserved=True,
    )
    assert fatigue["next_action"] == "open-perspective-and-research"
    assert curiosity["next_action"] == "run-evidence-exploration-and-optional-steal"
    assert greed["next_action"] == "run-outcome-ratchet-and-bounded-experiment"
    assert all(
        activation["automatic_adoption"] is False
        and activation["persistent_change"] is False
        for activation in (fatigue, curiosity, greed)
    )

    with pytest.raises(ValueError, match="cannot activate"):
        activate_value_behavior(
            "curiosity",
            trigger="verified-repetition",
            project_id="project-a",
            project_status="in_progress",
            evidence_ids=["evidence-a"],
        )
    with pytest.raises(ValueError, match="verified original success"):
        activate_value_behavior(
            "greed",
            trigger="verified-success",
            project_id="project-a",
            project_status="completed",
            evidence_ids=["evidence-a"],
            original_success_preserved=False,
        )


def test_value_evidence_requires_real_provenance() -> None:
    with pytest.raises(ValueError, match="evidence ids"):
        route_value_evidence(
            "fatigue",
            project_id="project-a",
            project_status="in_progress",
            summary="Repeated effort",
            evidence_ids=[],
        )
