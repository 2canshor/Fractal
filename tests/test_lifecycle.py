from __future__ import annotations

from pathlib import Path

import pytest

from fractal.lifecycle import (
    DirectionSummary,
    LifecycleController,
    LifecycleError,
    SuccessCriterion,
)
from fractal.models import Change, ProjectRecord, utc_now
from fractal.storage import AuthorityError, ProjectStore


@pytest.fixture
def lifecycle_project(tmp_path: Path) -> tuple[LifecycleController, ProjectStore, str]:
    store = ProjectStore(tmp_path / "projects", tmp_path / "runtime")
    project_id = "lifecycle-project"
    store.create(
        ProjectRecord(
            project_id=project_id,
            title="Lifecycle Project",
            system_version="0.1.0-alpha.1",
        ),
        actor="main-agent",
        platform="test-adapter",
    )
    evidence = {
        "id": "evidence-done",
        "kind": "test",
        "claim": "The criterion was observed",
        "source": "local-test",
        "observed_at": utc_now(),
        "sha256": None,
    }
    store.apply_changes(
        project_id,
        expected_revision=0,
        changes=[Change("append", "/evidence", evidence)],
        actor="main-agent",
        platform="test-adapter",
    )
    return LifecycleController(store), store, project_id


def direction() -> DirectionSummary:
    return DirectionSummary(
        intended_outcome="Deliver the verified outcome",
        deliverable="A tested local system",
        completion_standard="All approved criteria have evidence",
        exclusions="No unapproved external action",
    )


def pre_work_challenge() -> dict:
    return {
        "id": "challenge-pre-work",
        "criteria_version": 1,
        "trigger": "pre_work",
        "status": "completed",
        "original_achievement_preserved": False,
        "higher_target": {"summary": "Add an integrity check", "status": "tested"},
        "recorded_at": utc_now(),
        "evidence_ids": [],
    }


def approve_project(
    controller: LifecycleController, store: ProjectStore, project_id: str
) -> None:
    record = store.read(project_id)
    controller.confirm_direction(
        project_id,
        expected_revision=record.revision,
        summary=direction(),
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
        authority_source="test-human-action",
    )
    record = store.read(project_id)
    controller.approve_outcome(
        project_id,
        expected_revision=record.revision,
        goal="Deliver the verified outcome",
        criteria=[SuccessCriterion("criterion-a", "Pass verification", "test evidence")],
        priorities=["quality", "safety"],
        pre_work_challenge=pre_work_challenge(),
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
    )


def test_happy_path_requires_human_completion(
    lifecycle_project: tuple[LifecycleController, ProjectStore, str]
) -> None:
    controller, store, project_id = lifecycle_project
    approve_project(controller, store, project_id)
    record = store.read(project_id)
    controller.record_criterion_achievement(
        project_id,
        expected_revision=record.revision,
        criterion_id="criterion-a",
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.record_post_work_challenge(
        project_id,
        expected_revision=record.revision,
        higher_target_summary="A higher target was safe to test",
        higher_target_status="not_achieved",
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.mark_awaiting_completion(
        project_id,
        expected_revision=record.revision,
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    with pytest.raises(AuthorityError, match="primary user"):
        controller.declare_project_completion(
            project_id,
            expected_revision=record.revision,
            actor="main-agent",
            platform="test-adapter",
            human_action=False,
        )
    controller.declare_project_completion(
        project_id,
        expected_revision=record.revision,
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
    )
    completed = store.read(project_id)
    assert completed.status == "completed"
    assert completed.completion["completed_by"] == "primary-user"
    challenge = completed.lifecycle["success_criteria"]["post_work_challenges"][0]
    assert challenge["original_achievement_preserved"] is True


def test_primary_user_can_reopen_awaiting_completion_after_correction(
    lifecycle_project: tuple[LifecycleController, ProjectStore, str]
) -> None:
    controller, store, project_id = lifecycle_project
    approve_project(controller, store, project_id)
    record = store.read(project_id)
    controller.record_criterion_achievement(
        project_id,
        expected_revision=record.revision,
        criterion_id="criterion-a",
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.record_post_work_challenge(
        project_id,
        expected_revision=record.revision,
        higher_target_summary="No higher safe target",
        higher_target_status="no_finding",
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    record.plan["items"] = [
        {"id": "phase-8", "summary": "Phase 8", "status": "completed", "evidence_ids": []},
        {"id": "phase-9", "summary": "Phase 9", "status": "completed", "evidence_ids": []},
    ]
    record.plan["current_phase"] = 9
    store.apply_changes(
        project_id,
        expected_revision=record.revision,
        changes=[Change("set", "/plan", record.plan, store.read(project_id).plan)],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.mark_awaiting_completion(
        project_id,
        expected_revision=record.revision,
        actor="main-agent",
        platform="test-adapter",
    )
    awaiting = store.read(project_id)
    with pytest.raises(AuthorityError, match="primary user"):
        controller.reopen_after_correction(
            project_id,
            expected_revision=awaiting.revision,
            reopen_phase=8,
            criterion_ids=["criterion-a"],
            reason="Architecture correction",
            actor="main-agent",
            platform="test-adapter",
            human_action=False,
        )
    controller.reopen_after_correction(
        project_id,
        expected_revision=awaiting.revision,
        reopen_phase=8,
        criterion_ids=["criterion-a"],
        reason="Architecture correction",
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
    )
    reopened = store.read(project_id)
    assert reopened.status == "in_progress"
    assert reopened.completion["requested_at"] is None
    assert reopened.plan["current_phase"] == 8
    assert [item["status"] for item in reopened.plan["items"]] == [
        "in_progress",
        "pending",
    ]
    assert reopened.lifecycle["success_criteria"]["version"] == 2
    assert reopened.lifecycle["success_criteria"]["items"][0]["achieved"] is False


def test_post_work_challenge_runs_once_per_criteria_version(
    lifecycle_project: tuple[LifecycleController, ProjectStore, str]
) -> None:
    controller, store, project_id = lifecycle_project
    approve_project(controller, store, project_id)
    record = store.read(project_id)
    controller.record_criterion_achievement(
        project_id,
        expected_revision=record.revision,
        criterion_id="criterion-a",
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.record_post_work_challenge(
        project_id,
        expected_revision=record.revision,
        higher_target_summary="First challenge",
        higher_target_status="no_finding",
        evidence_ids=[],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    with pytest.raises(LifecycleError, match="already recorded"):
        controller.record_post_work_challenge(
            project_id,
            expected_revision=record.revision,
            higher_target_summary="Second challenge",
            higher_target_status="no_finding",
            evidence_ids=[],
            actor="main-agent",
            platform="test-adapter",
        )


def test_direction_is_provisional_then_requires_material_reconfirmation(
    lifecycle_project: tuple[LifecycleController, ProjectStore, str]
) -> None:
    controller, store, project_id = lifecycle_project
    assert store.read(project_id).lifecycle["direction"]["status"] == "provisional"
    controller.confirm_direction(
        project_id,
        expected_revision=1,
        summary=direction(),
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
        authority_source="test-human-action",
    )
    changed = DirectionSummary(
        intended_outcome="Deliver a materially wider outcome",
        deliverable=direction().deliverable,
        completion_standard=direction().completion_standard,
        exclusions=direction().exclusions,
    )
    record = store.read(project_id)
    with pytest.raises(LifecycleError, match="reconfirmation reason"):
        controller.confirm_direction(
            project_id,
            expected_revision=record.revision,
            summary=changed,
            actor="primary-user",
            platform="test-adapter",
            human_action=True,
            authority_source="test-human-action",
        )
    controller.confirm_direction(
        project_id,
        expected_revision=record.revision,
        summary=changed,
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
        authority_source="test-human-action",
        material_change_reason="The approved deliverable changed",
    )
    updated = store.read(project_id)
    assert updated.lifecycle["direction"]["version"] == 2
    assert len(updated.lifecycle["direction"]["confirmations"]) == 2


def test_deviation_review_failure_and_goal_change_paths(
    lifecycle_project: tuple[LifecycleController, ProjectStore, str]
) -> None:
    controller, store, project_id = lifecycle_project
    controller.record_deviation(
        project_id,
        expected_revision=1,
        summary="A low-level implementation detail changed",
        dimensions=["implementation"],
        evidence_ids=[],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    assert record.lifecycle["review_points"] == []
    controller.record_deviation(
        project_id,
        expected_revision=record.revision,
        summary="A delivery risk changed materially",
        dimensions=["risk", "delivery"],
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    assert len(record.lifecycle["review_points"]) == 1
    controller.record_project_review(
        project_id,
        expected_revision=record.revision,
        conclusion="Continue after containing the risk",
        confidence="high",
        plan_delta="Add a restore test",
        concern="The restore path still needs user-level proof",
        evidence_ids=["evidence-done"],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.record_review_point(
        project_id,
        expected_revision=record.revision,
        trigger="failure",
        reason="A verification command failed",
        evidence_ids=[],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.record_project_review(
        project_id,
        expected_revision=record.revision,
        conclusion="Retry with the corrected input",
        confidence="medium",
        plan_delta="No Goal change",
        concern="A repeated failure would require escalation",
        evidence_ids=[],
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    controller.request_goal_change(
        project_id,
        expected_revision=record.revision,
        proposed_goal="Expand the outcome",
        actor="main-agent",
        platform="test-adapter",
    )
    final = store.read(project_id)
    assert len(final.lifecycle["reviews"]) == 2
    assert all(point["status"] == "reviewed" for point in final.lifecycle["review_points"])
    assert final.requests[-1]["path"] == "/lifecycle/goal"
    assert final.lifecycle["goal"]["status"] == "provisional"


def test_plan_history_and_material_authority(
    lifecycle_project: tuple[LifecycleController, ProjectStore, str]
) -> None:
    controller, store, project_id = lifecycle_project
    record = store.read(project_id)
    routine_plan = {**record.plan, "current_phase": 2}
    controller.record_plan_update(
        project_id,
        expected_revision=record.revision,
        plan=routine_plan,
        reason="Advance after verified phase evidence",
        material=False,
        actor="main-agent",
        platform="test-adapter",
    )
    record = store.read(project_id)
    material_plan = {**record.plan, "criteria_version": 2}
    with pytest.raises(AuthorityError, match="primary user"):
        controller.record_plan_update(
            project_id,
            expected_revision=record.revision,
            plan=material_plan,
            reason="Change the approved completion boundary",
            material=True,
            actor="main-agent",
            platform="test-adapter",
        )
    controller.record_plan_update(
        project_id,
        expected_revision=record.revision,
        plan=material_plan,
        reason="Change the approved completion boundary",
        material=True,
        actor="primary-user",
        platform="test-adapter",
        human_action=True,
    )
    final = store.read(project_id)
    assert len(final.lifecycle["plan_history"]) == 2
    assert final.lifecycle["plan_history"][0]["material"] is False
    assert final.lifecycle["plan_history"][1]["authority"] == "primary-user"
