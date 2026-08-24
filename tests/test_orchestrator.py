from __future__ import annotations

import json
from pathlib import Path

import pytest

from fractal.improvement import RepetitionRecognition, WorkSignature
from fractal.models import ProjectRecord
from fractal.orchestrator import (
    CanonicalEvidenceInvestigator,
    FractalOrchestrator,
    OrchestrationError,
)
from fractal.self_improvement import MethodCandidateStore, PostWorkLearning
from fractal.storage import ProjectStore
from fractal.system_review import start_system_review, verified_zero_pattern_reasoner


def project_store(tmp_path: Path, *, completed: bool = False) -> ProjectStore:
    store = ProjectStore(tmp_path / "projects", tmp_path / "runtime")
    record = ProjectRecord(
        project_id="runtime-project",
        title="Runtime Project",
        system_version="0.1.0-alpha.8-r1",
        status="completed" if completed else "in_progress",
        completion=(
            {
                "requested_at": "2026-08-24T00:00:00Z",
                "completed_at": "2026-08-24T00:01:00Z",
                "completed_by": "primary-user",
            }
            if completed
            else {
                "requested_at": None,
                "completed_at": None,
                "completed_by": None,
            }
        ),
    )
    store.create(
        record,
        actor="primary-user" if completed else "main-agent",
        platform="test",
        authority_write=completed,
    )
    return store


def work_signature(work_id: str = "work-c") -> WorkSignature:
    return WorkSignature(
        work_id=work_id,
        project_id="runtime-project",
        work_type="agent-turn",
        input_shape="codex-completed-turn",
        steps=("user-turn", "assistant-response"),
        tools=("exec_command",),
        outcome_category="completed-response",
        purpose_class="ordinary",
        elapsed_seconds=10,
        token_usage=100,
        completed_at="2026-08-24T00:00:00Z",
    )


def repetition() -> RepetitionRecognition:
    return RepetitionRecognition(
        status="investigation-required",
        occurrence_count=3,
        confidence="high",
        route="project-review",
        evidence_work_ids=("work-a", "work-b", "work-c"),
        supporting_action="improvement-researcher",
    )


def test_fatigue_opens_perspective_and_durable_research_action_once(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path)
    runtime = FractalOrchestrator(store)

    first = runtime.handle_fatigue(
        repetition(), work_signature(), actor="fractal-runtime", platform="codex"
    )
    second = runtime.handle_fatigue(
        repetition(), work_signature(), actor="fractal-runtime", platform="codex"
    )

    assert first["result"]["perspective_opened"] is True
    assert first["result"]["research_status"] == "ready"
    assert second["idempotent"] is True
    project = store.read("runtime-project")
    assert len(project.lifecycle["review_points"]) == 1
    assert project.lifecycle["review_points"][0]["review_kind"] == "exception"
    action = runtime.state.next_ready_action()
    assert action is not None
    assert action["action_kind"] == "curiosity-implementation-research"
    payload = json.loads(action["payload_json"])
    assert [item["effort_share"] for item in payload["routes"]] == [60, 20, 20]
    assert payload["automatic_change"] is False
    assert payload["value_activation"]["value"] == "fatigue"
    assert payload["value_activation"]["trigger"] == "verified-repetition"


def test_project_completion_starts_persists_and_queues_system_review_once(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)

    first = runtime.handle_project_completion("runtime-project")
    second = runtime.reconcile_project("runtime-project")

    assert first["review"]["status"] == "in_progress"
    assert first["action"]["stage"] == "project-assessment"
    assert second["idempotent"] is True
    saved = runtime.state.read_review(first["review"]["review_id"])
    assert saved == first["review"]
    assert runtime.state.next_ready_action(review_id=saved["review_id"])["stage"] == (
        "project-assessment"
    )


def test_project_completion_reconciles_after_a_failed_pre_persist_attempt(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    project = store.read("runtime-project")
    snapshot = start_system_review(project)["project_snapshot_sha256"]
    execution, _ = runtime.state.claim_execution(
        idempotency_key=f"project-completion:{project.project_id}:{snapshot}",
        execution_kind="project-completion-to-system-review",
        project_id=project.project_id,
        project_revision=project.revision,
    )
    runtime.state.finish_execution(
        execution["execution_id"], success=False, error="injected before persist"
    )

    recovered = runtime.reconcile_project(project.project_id)

    assert recovered["review"]["status"] == "in_progress"
    assert recovered["action"]["stage"] == "project-assessment"


def test_project_completion_transaction_rolls_back_review_without_first_action(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)

    def inject(point: str) -> None:
        if point == "after-review-before-first-action":
            raise RuntimeError("injected review/action boundary failure")

    runtime.state.fault_injector = inject
    with pytest.raises(RuntimeError, match="boundary failure"):
        runtime.handle_project_completion("runtime-project")
    snapshot = start_system_review(store.read("runtime-project"))["project_snapshot_sha256"]
    assert runtime.state.review_for_snapshot(snapshot) is None

    runtime.state.fault_injector = None
    recovered = runtime.reconcile_project("runtime-project")
    assert recovered["review"]["status"] == "in_progress"
    assert recovered["action"]["stage"] == "project-assessment"


def test_fatigue_reconciles_failed_dispatch_without_duplicate_perspective(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path)
    runtime = FractalOrchestrator(store)
    signature = work_signature()
    execution, _ = runtime.state.claim_execution(
        idempotency_key=f"fatigue:{signature.project_id}:{signature.work_id}",
        execution_kind="fatigue-to-perspective",
        project_id=signature.project_id,
        project_revision=0,
    )
    runtime.state.finish_execution(
        execution["execution_id"], success=False, error="injected before persist"
    )

    first = runtime.handle_fatigue(
        repetition(), signature, actor="fractal-runtime", platform="codex"
    )
    second = runtime.handle_fatigue(
        repetition(), signature, actor="fractal-runtime", platform="codex"
    )

    assert first["result"]["perspective_opened"] is True
    assert second["idempotent"] is True
    assert len(store.read(signature.project_id).lifecycle["review_points"]) == 1


def test_fatigue_runs_local_learning_and_stages_method_before_perspective(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path)
    runtime = FractalOrchestrator(store)
    fatigue = runtime.handle_fatigue(
        repetition(), work_signature(), actor="fractal-runtime", platform="codex"
    )
    candidate_store = MethodCandidateStore(tmp_path / "runtime" / "learning")

    outcome = runtime.run_learning_review(
        fatigue["result"]["research_action_id"],
        reviewer=PostWorkLearning(),
        candidate_store=candidate_store,
    )

    assert outcome["action"]["status"] == "completed"
    assert outcome["result"]["status"] == "candidate"
    assert outcome["result"]["runtime_dependency_on_upstream"] is False
    assert outcome["next_action"]["action_kind"] == "perspective-review"
    assert outcome["next_action"]["status"] == "ready"
    manifest = outcome["result"]["candidate_manifest"]
    assert candidate_store.read(manifest["candidate_id"]) == manifest
    project = store.read("runtime-project")
    evidence_id = outcome["result"]["canonical_evidence_id"]
    assert any(item["id"] == evidence_id for item in project.evidence)


def test_learning_result_and_perspective_handoff_are_atomic_and_retryable(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path)
    runtime = FractalOrchestrator(store)
    fatigue = runtime.handle_fatigue(
        repetition(), work_signature(), actor="fractal-runtime", platform="codex"
    )
    action_id = fatigue["result"]["research_action_id"]
    candidate_store = MethodCandidateStore(tmp_path / "runtime" / "learning")

    def inject(point: str) -> None:
        if point == "after-learning-result-before-successor":
            raise RuntimeError("injected learning handoff failure")

    runtime.state.fault_injector = inject
    with pytest.raises(RuntimeError, match="learning handoff failure"):
        runtime.run_learning_review(
            action_id,
            reviewer=PostWorkLearning(),
            candidate_store=candidate_store,
        )
    assert runtime.state.read_action(action_id)["status"] == "failed"
    assert not store.read("runtime-project").evidence

    runtime.state.fault_injector = None
    retry = runtime.retry_failed_learning_review(action_id)
    recovered = runtime.run_learning_review(
        retry["action_id"],
        reviewer=PostWorkLearning(),
        candidate_store=candidate_store,
    )
    assert recovered["action"]["status"] == "completed"
    assert recovered["next_action"]["action_kind"] == "perspective-review"
    assert len(list(candidate_store.candidates.iterdir())) == 1


def test_runtime_obtains_validates_persists_and_advances_one_stage(tmp_path: Path) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]
    calls: list[str] = []

    def investigator(stage: str, context: dict) -> tuple[dict, list[str]]:
        calls.append(stage)
        assert context["project"]["status"] == "completed"
        return (
            {
                "comparison_baseline": "Approved Project Direction and Plan",
                "positive_delta": [],
                "no_positive_delta_reason": "No positive delta was observed.",
                "negative_delta": [],
                "no_negative_delta_reason": "No negative delta was observed.",
            },
            ["evidence-project-snapshot"],
        )

    outcome = runtime.run_next_system_review_stage(review["review_id"], investigator)

    assert calls == ["project-assessment"]
    assert outcome["review"]["stages"][0]["stage"] == "project-assessment"
    assert outcome["next_action"]["stage"] == "issue-scan"
    assert runtime.state.read_review(review["review_id"])["stages"] == (
        outcome["review"]["stages"]
    )


def test_runtime_collects_canonical_evidence_and_advances_multiple_flows(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]
    observed_bundles = []

    def collector(stage: str, _context: dict) -> list[dict]:
        return [
            {
                "evidence_id": f"collector:{stage}",
                "source": "read-only-stage-collector",
                "payload": {"stage": stage, "observed": True},
            }
        ]

    def reasoner(stage: str, bundle: dict) -> tuple[dict, list[str]]:
        observed_bundles.append(bundle)
        used = [bundle["artifacts"][0]["evidence_id"], f"collector:{stage}"]
        if stage == "project-assessment":
            return (
                {
                    "comparison_baseline": "Approved Direction and Plan",
                    "positive_delta": [],
                    "no_positive_delta_reason": "No positive delta was observed.",
                    "negative_delta": [],
                    "no_negative_delta_reason": "No negative delta was observed.",
                },
                used,
            )
        assert stage == "issue-scan"
        return (
            {
                "collection_mode": "quantity-over-quality",
                "causal_filtering_applied": False,
                "deduplication_status": "deferred-to-flow-2",
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
                    "snapshot_sha256": review["project_snapshot_sha256"],
                    "source_artifact_ids": used,
                },
                "observations": [],
                "coverage_complete": True,
                "no_observation_reason": "The complete staged scan found no observation.",
            },
            used,
        )

    investigator = CanonicalEvidenceInvestigator(reasoner, collectors=[collector])
    outcome = runtime.run_system_review_until_human(
        review["review_id"], investigator, max_stage_iterations=2
    )

    assert outcome["status"] == "stage-budget-exhausted"
    assert outcome["completed_stages"] == ["project-assessment", "issue-scan"]
    assert outcome["next_stage"] == "project-patterns"
    assert len(observed_bundles) == 2
    assert observed_bundles[1]["prior_results"]["project-assessment"]
    assert all(
        bundle["authority"] == "investigate-validate-persist-only"
        for bundle in observed_bundles
    )


def test_canonical_investigator_rejects_unavailable_evidence(tmp_path: Path) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]

    def reasoner(_stage: str, _bundle: dict) -> tuple[dict, list[str]]:
        return ({"result": "invented"}, ["not-in-the-evidence-bundle"])

    investigator = CanonicalEvidenceInvestigator(reasoner)
    with pytest.raises(OrchestrationError, match="unavailable evidence"):
        runtime.run_system_review_until_human(
            review["review_id"], investigator, max_stage_iterations=1
        )


def test_verified_zero_pattern_project_travels_all_eight_flows_to_human_control(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]

    def zero_pattern_receipt(_stage: str, _context: dict) -> list[dict]:
        return [
            {
                "evidence_id": "evidence-complete-zero-pattern-coverage",
                "source": "staged-whole-project-coverage",
                "payload": {
                    "record_type": "zero-pattern-coverage-receipt",
                    "coverage_complete": True,
                    "observation_count": 0,
                    "reason": "The complete staged Project history contained no material signal.",
                    "comparison_baseline": "Approved Direction, Plan and outcome evidence",
                    "project_snapshot_sha256": review["project_snapshot_sha256"],
                    "history_manifest": {
                        "projects": ["runtime-project"],
                        "system-reviews": [],
                        "change-proposals": [],
                        "hypotheses": [],
                        "system-versions": [],
                        "interventions": [],
                        "outcomes": [],
                    },
                },
            }
        ]

    investigator = CanonicalEvidenceInvestigator(
        verified_zero_pattern_reasoner,
        collectors=[zero_pattern_receipt],
    )
    outcome = runtime.run_system_review_until_human(
        review["review_id"], investigator, max_stage_iterations=16
    )

    assert outcome["status"] == "awaiting-primary-user-decision"
    assert outcome["budget_exhausted"] is False
    assert len(outcome["completed_stages"]) == 16
    assert "your-decision" not in outcome["completed_stages"]
    assert {item["blueprint_flow"] for item in outcome["review"]["stages"]} == {
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        None,
    }
    assert outcome["review"]["result"]["outcome"] == "no-change"


def test_failed_investigator_is_durable_and_does_not_advance(tmp_path: Path) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]

    def failing(_stage: str, _context: dict) -> tuple[dict, list[str]]:
        raise RuntimeError("investigator unavailable")

    with pytest.raises(RuntimeError, match="investigator unavailable"):
        runtime.run_next_system_review_stage(review["review_id"], failing)
    assert runtime.state.next_ready_action(review_id=review["review_id"]) is None
    assert runtime.state.read_review(review["review_id"])["stages"] == []
    failed = runtime.state.latest_action(review["review_id"])
    retry = runtime.retry_failed_system_review_stage(review["review_id"])
    assert retry["status"] == "ready"
    assert retry["stage"] == "project-assessment"
    assert retry["idempotency_key"].endswith(f":retry:{failed['action_id']}")
    with pytest.raises(OrchestrationError, match="no failed stage"):
        runtime.retry_failed_system_review_stage(review["review_id"])


@pytest.mark.parametrize(
    "failure_point",
    [
        "after-review-before-action-completion",
        "after-action-completion-before-next-action",
    ],
)
def test_stage_transaction_rolls_back_every_persist_boundary(
    tmp_path: Path,
    failure_point: str,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]

    def investigator(_stage: str, _context: dict) -> tuple[dict, list[str]]:
        return (
            {
                "comparison_baseline": "Approved Project Direction and Plan",
                "positive_delta": [],
                "no_positive_delta_reason": "No positive delta was observed.",
                "negative_delta": [],
                "no_negative_delta_reason": "No negative delta was observed.",
            },
            ["evidence-project-snapshot"],
        )

    def inject(point: str) -> None:
        if point == failure_point:
            raise RuntimeError(f"injected {failure_point}")

    runtime.state.fault_injector = inject
    with pytest.raises(RuntimeError, match="injected"):
        runtime.run_next_system_review_stage(review["review_id"], investigator)
    assert runtime.state.read_review(review["review_id"])["stages"] == []
    assert runtime.state.next_ready_action(review_id=review["review_id"]) is None

    runtime.state.fault_injector = None
    retry = runtime.retry_failed_system_review_stage(review["review_id"])
    assert retry["stage"] == "project-assessment"
    recovered = runtime.run_next_system_review_stage(review["review_id"], investigator)
    assert recovered["review"]["stages"][0]["stage"] == "project-assessment"
    assert recovered["next_action"]["stage"] == "issue-scan"


def test_terminal_execution_cannot_be_rewritten(tmp_path: Path) -> None:
    store = project_store(tmp_path)
    runtime = FractalOrchestrator(store)
    execution, _ = runtime.state.claim_execution(
        idempotency_key="one",
        execution_kind="test",
        project_id="runtime-project",
        project_revision=0,
    )
    runtime.state.finish_execution(execution["execution_id"], success=True, result={})
    with pytest.raises(OrchestrationError, match="immutable"):
        runtime.state.finish_execution(execution["execution_id"], success=False)


def test_dead_stage_action_owner_recovers_independently_of_completed_execution(
    tmp_path: Path,
) -> None:
    store = project_store(tmp_path, completed=True)
    runtime = FractalOrchestrator(store)
    review = runtime.handle_project_completion("runtime-project")["review"]
    action = runtime.state.next_ready_action(review_id=review["review_id"])
    running = runtime.state.mark_action_running(action["action_id"])
    with runtime.state.transaction() as connection:
        connection.execute(
            """UPDATE actions SET owner_process_id='dead-owner', owner_pid=?
               WHERE action_id=?""",
            (999_999_999, running["action_id"]),
        )

    recovered = runtime.state.recover_interrupted()
    latest = runtime.state.latest_action(review["review_id"])

    assert recovered == {"executions": 0, "actions": 1}
    assert latest["status"] == "unknown"
    retry = runtime.retry_failed_system_review_stage(review["review_id"])
    assert retry["stage"] == "project-assessment"
