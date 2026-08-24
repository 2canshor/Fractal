from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from fractal.self_improvement import (
    ImprovementSignal,
    MethodCandidateStore,
    PostWorkLearning,
    SelfImprovementError,
    classify_signal,
    load_learning_method_sources,
    validate_improvement_candidate,
    validate_local_improvement_review,
)


def project() -> dict:
    return {
        "project_id": "learning-project",
        "status": "in_progress",
        "revision": 3,
    }


def action_payload(*, work_count: int = 3, signals: list[dict] | None = None) -> dict:
    return {
        "trigger": "improvement-investigation",
        "work_signature_ids": [f"work-{index}" for index in range(work_count)],
        "current_method": {
            "work_type": "agent-turn",
            "input_shape": "codex-completed-turn",
            "steps": ["user-turn", "assistant-response"],
            "tools": ["exec_command"],
        },
        "signals": signals or [],
        "automatic_change": False,
    }


def test_learning_methods_use_fractal_names_and_replaceable_donor_sources() -> None:
    methods = load_learning_method_sources()
    assert methods["runtime_dependency_on_upstream"] is False
    assert methods["selection_policy"]["donor_set_fixed"] is False
    assert methods["selection_policy"]["select_from_current_need"] is True
    assert methods["selection_policy"]["multiple_donors_per_method_allowed"] is True
    assert methods["selection_policy"]["replacement_allowed"] is True
    assert {item["method_id"] for item in methods["methods"]} == {
        "post-work-learning",
        "blind-spot-review",
        "weakness-triage",
        "work-method-extraction",
        "checkpoint-inspection",
        "learning-evidence",
        "outcome-ratchet",
        "evidence-exploration",
    }
    assert all(
        item["source_binding"] == "replaceable-current-source"
        for item in methods["methods"]
    )
    assert all(
        all(
            source["source_url"].startswith("https://github.com/")
            for source in item.get("sources", [item])
        )
        for item in methods["methods"]
    )
    ratchet = next(item for item in methods["methods"] if item["method_id"] == "outcome-ratchet")
    assert ratchet["donor_id"] == "mlflow"
    assert ratchet["blueprint_mapping"] == {
        "implements": "greed",
        "serves_flows": ["find-global-pattern-solutions"],
        "hands_off_to_element": "experiment",
    }
    exploration = next(
        item for item in methods["methods"] if item["method_id"] == "evidence-exploration"
    )
    assert [source["donor_id"] for source in exploration["sources"]] == [
        "storm",
        "gpt-researcher",
    ]


@pytest.mark.parametrize(
    ("summary", "error", "expected_category", "expected_fixable"),
    [
        ("Provider login failed", "invalid credentials", "authentication", False),
        ("Service unavailable", "connection refused", "infrastructure", False),
        ("Quota pressure", "429 rate limit", "rate-limit", False),
        ("Tool absent", "command not found", "transient", False),
        ("Call failed", "field required: path", "missing-parameter", True),
        ("Repeated manual work", "", "repetition", True),
    ],
)
def test_weakness_triage_does_not_turn_temporary_failures_into_methods(
    summary: str,
    error: str,
    expected_category: str,
    expected_fixable: bool,
) -> None:
    category, fixable = classify_signal(
        ImprovementSignal(
            signal_id="signal-a",
            summary=summary,
            evidence_ids=("evidence-a",),
            occurrences=3,
            error=error,
            signal_kind="repetition",
        )
    )
    assert (category, fixable) == (expected_category, expected_fixable)


def test_post_work_learning_produces_local_candidate_with_60_20_20_and_blind_spots(
    tmp_path: Path,
) -> None:
    store = MethodCandidateStore(tmp_path / "learning")
    reviewer = PostWorkLearning(
        constraint_history=[{"blind_spots": ["cross-device behaviour"]}]
    )
    review = reviewer.review(project=project(), action_payload=action_payload())
    manifest = store.stage(review, source_action_id="action-a")

    assert review["status"] == "candidate"
    assert [
        (item["action_id"], item["effort_share"])
        for item in review["research_routes"]
    ] == [
        ("improve-current-method", 60),
        ("research-latest-findings", 20),
        ("explore-related-fields", 20),
    ]
    assert review["automatic_apply"] is False
    assert review["runtime_dependency_on_upstream"] is False
    assert review["donor_selection_fixed"] is False
    assert review["constraint_report"]["prior_blind_spots_addressed"] == [
        "cross-device behaviour"
    ]
    assert manifest["status"] == "staged-not-active"
    assert manifest["runtime_dependency_on_upstream"] is False
    assert manifest["donor_selection_fixed"] is False
    assert manifest["next_step"] == "map-implementations-to-blueprint"
    candidate_dir = store.candidate_path(manifest["candidate_id"])
    skill = (candidate_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: improve-agent-turn" in skill
    assert "candidate-not-active" in skill
    assert "Hermes" not in skill
    assert store.read(manifest["candidate_id"]) == manifest
    assert store.recent_constraint_reports()[0] == review["constraint_report"]


def test_one_occurrence_is_honest_no_change_and_writes_no_candidate(tmp_path: Path) -> None:
    review = PostWorkLearning().review(
        project=project(),
        action_payload=action_payload(work_count=1),
    )
    store = MethodCandidateStore(tmp_path / "learning")
    assert review["status"] == "no-change"
    assert review["candidate_changes"] == []
    assert store.stage(review, source_action_id="action-one") is None
    assert not store.candidates.exists()


def test_candidate_integrity_and_authority_fail_closed(tmp_path: Path) -> None:
    store = MethodCandidateStore(tmp_path / "learning")
    review = PostWorkLearning().review(project=project(), action_payload=action_payload())
    manifest = store.stage(review, source_action_id="action-a")
    candidate_dir = store.candidate_path(manifest["candidate_id"])
    tampered = json.loads(json.dumps(manifest))
    tampered["automatic_apply"] = True
    with pytest.raises(SelfImprovementError, match="cannot apply itself"):
        validate_improvement_candidate(tampered, candidate_dir=candidate_dir)
    malformed = json.loads(json.dumps(review))
    malformed["runtime_dependency_on_upstream"] = True
    with pytest.raises(SelfImprovementError, match="donor runtime"):
        validate_local_improvement_review(malformed)


def test_candidate_publish_is_atomic_and_retryable(tmp_path: Path) -> None:
    review = PostWorkLearning().review(project=project(), action_payload=action_payload())

    def inject(point: str) -> None:
        if point == "after-candidate-files-before-publish":
            raise RuntimeError("injected candidate publish failure")

    store = MethodCandidateStore(tmp_path / "learning", fault_injector=inject)
    with pytest.raises(RuntimeError, match="publish failure"):
        store.stage(review, source_action_id="action-a")
    assert list(store.candidates.iterdir()) == []

    store.fault_injector = None
    manifest = store.stage(review, source_action_id="action-a")
    assert store.read(manifest["candidate_id"]) == manifest


def test_learning_review_still_runs_when_all_network_access_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(*_args, **_kwargs):
        raise OSError("network deliberately unavailable")

    monkeypatch.setattr(socket, "create_connection", offline)
    review = PostWorkLearning().review(project=project(), action_payload=action_payload())
    store = MethodCandidateStore(tmp_path / "learning")
    manifest = store.stage(review, source_action_id="offline-action")
    assert review["runtime_dependency_on_upstream"] is False
    assert review["donor_selection_fixed"] is False
    assert store.read(manifest["candidate_id"])["status"] == "staged-not-active"
