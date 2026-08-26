"""Focused tests for the pure capability-trial receipt boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from fractal.capability_trial import (
    TrialIntegrityError,
    TrialOrderError,
    TrialOutputError,
    TrialScopeError,
    build_trial_plan,
    build_trial_receipt,
    candidate_graph_content_digest,
    evaluate_candidate_trials,
    validate_trial_receipt,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _implementation(identifier: str) -> dict[str, object]:
    return {
        "implementation_id": identifier,
        "version": "1.0.0",
        "verification": {"status": "unverified"},
    }


def _graph() -> dict[str, object]:
    return {
        "record_type": "capability-candidate-graph",
        "record_version": 1,
        "input_digest": "a" * 64,
        "candidate_only": True,
        "dots": [
            {
                "dot_id": "dot-one",
                "version": "1.0.0",
                "implementations": [
                    _implementation("implementation-one"),
                    _implementation("implementation-one-alternative"),
                ],
                "trial": {"status": "not-run"},
                "verification": {"status": "unverified"},
                "system_review": {"status": "pending"},
                "human_decision": {"status": "pending"},
                "lifecycle": {"status": "candidate", "active": False},
                "activation": {"status": "inactive", "authorised": False},
            },
            {
                "dot_id": "dot-two",
                "version": "1.0.0",
                "implementations": [_implementation("implementation-two")],
                "trial": {"status": "not-run"},
                "verification": {"status": "unverified"},
                "system_review": {"status": "pending"},
                "human_decision": {"status": "pending"},
                "lifecycle": {"status": "candidate", "active": False},
                "activation": {"status": "inactive", "authorised": False},
            },
        ],
        "workflows": [
            {
                "workflow_id": "workflow-one",
                "version": "1.0.0",
                "dot_refs": [
                    {"dot_id": "dot-one", "version": "1.0.0", "sequence": 1},
                    {"dot_id": "dot-two", "version": "1.0.0", "sequence": 2},
                ],
                "trial": {"status": "pending"},
                "verification": {"status": "unverified"},
                "system_review": {"status": "pending"},
                "human_decision": {"status": "pending"},
                "lifecycle": {"status": "candidate"},
                "activation": {"status": "inactive", "authorised": False},
            }
        ],
        "actions": [
            {
                "action_id": "action-one",
                "version": "1.0.0",
                "workflow_refs": [
                    {"workflow_id": "workflow-one", "version": "1.0.0", "sequence": 1}
                ],
                "verification": {"status": "unverified"},
                "system_review": {"status": "pending"},
                "human_decision": {"status": "pending"},
                "lifecycle": {"status": "candidate"},
                "activation": {"status": "inactive", "authorised": False},
            }
        ],
    }


def _command_receipt(step: int) -> dict[str, object]:
    receipt: dict[str, object] = {
        "record_type": "command-execution-receipt",
        "record_version": 1,
        "status": "passed",
        "exit_code": 0,
        "step": step,
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return receipt


def _plan(
    graph: dict[str, object],
    *,
    full: bool = True,
    expires_at: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    expiry = expires_at or (
        datetime.now(UTC) + timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    if full:
        dots = [
            {"dot_id": "dot-one", "version": "1.0.0"},
            {"dot_id": "dot-two", "version": "1.0.0"},
        ]
        implementations = [
            {"implementation_id": "implementation-one", "version": "1.0.0"},
            {"implementation_id": "implementation-two", "version": "1.0.0"},
        ]
        action = {"action_id": "action-one", "version": "1.0.0"}
        workflow = {"workflow_id": "workflow-one", "version": "1.0.0"}
    else:
        dots = [{"dot_id": "dot-one", "version": "1.0.0"}]
        implementations = [{"implementation_id": "implementation-one", "version": "1.0.0"}]
        action = None
        workflow = None
    return build_trial_plan(
        graph,
        action_ref=action,
        workflow_ref=workflow,
        dot_refs=dots,
        implementation_refs=implementations,
        project_id="project-one",
        project_revision=4,
        task_id="task-one",
        allowed_side_effects=["emit one bounded output"],
        expires_at=expiry,
        input_contract={"type": "object"},
        output_contract={"type": "object"},
        recovery={"strategy": "restore trial workspace", "evidence_ids": ["recovery-one"]},
        **overrides,
    )


def _receipt(graph: dict[str, object], *, full: bool = True) -> dict[str, object]:
    plan = _plan(graph, full=full)
    commands = [_command_receipt(index) for index in range(1, 3 if full else 2)]
    if full:
        steps = [
            {
                "sequence": 1,
                "dot_ref": {"dot_id": "dot-one", "version": "1.0.0"},
                "implementation_ref": {
                    "implementation_id": "implementation-one",
                    "version": "1.0.0",
                },
                "input": {"value": "request"},
                "output": {"value": "request"},
                "command_receipt_hash": commands[0]["receipt_sha256"],
                "evidence_ids": ["dot-one-output"],
            },
            {
                "sequence": 2,
                "dot_ref": {"dot_id": "dot-two", "version": "1.0.0"},
                "implementation_ref": {
                    "implementation_id": "implementation-two",
                    "version": "1.0.0",
                },
                "input": {"value": "request"},
                "output": {"value": "report"},
                "command_receipt_hash": commands[1]["receipt_sha256"],
                "evidence_ids": ["dot-two-output"],
            },
        ]
    else:
        steps = [
            {
                "sequence": 1,
                "dot_ref": {"dot_id": "dot-one", "version": "1.0.0"},
                "implementation_ref": {
                    "implementation_id": "implementation-one",
                    "version": "1.0.0",
                },
                "input": {"value": "request"},
                "output": {"value": "request"},
                "command_receipt_hash": commands[0]["receipt_sha256"],
                "evidence_ids": ["dot-one-output"],
            }
        ]
    return build_trial_receipt(
        plan,
        steps,
        receipt_id="trial-one" if full else "trial-dot-one",
        outcome={"status": "passed", "summary": "bounded candidate trial"},
        duration_ms=12,
        cost=2,
        work_signature="work-signature-one",
        evidence_ids=["trial-output"],
        command_receipts=commands,
    )


def _rebind(value: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(value)
    if isinstance(value.get("plan"), dict):
        plan = value["plan"]
        plan.pop("plan_sha256", None)
        plan["plan_sha256"] = _digest(plan)
    value.pop("receipt_sha256", None)
    value["receipt_sha256"] = _digest(value)
    return value


def _swap_steps(value: dict[str, object]) -> dict[str, object]:
    value["steps"][0], value["steps"][1] = value["steps"][1], value["steps"][0]
    return value


def _wrong_output(value: dict[str, object]) -> dict[str, object]:
    value["steps"][0]["output"] = {"wrong": True}
    return value


def _excess_effect(value: dict[str, object]) -> dict[str, object]:
    value["steps"][0]["observed_side_effects"] = ["publish"]
    return value


def test_full_path_marks_only_the_selected_implementation_and_higher_layers() -> None:
    graph = _graph()
    original = copy.deepcopy(graph)
    receipt = _receipt(graph)

    candidate = evaluate_candidate_trials(graph, [receipt])

    assert graph == original
    assert candidate["dots"][0]["implementations"][0]["verification"]["status"] == "verified-staged"
    assert candidate["dots"][0]["implementations"][1]["verification"]["status"] == "unverified"
    assert candidate["dots"][1]["implementations"][0]["verification"]["status"] == "verified-staged"
    assert candidate["dots"][0]["trial"]["status"] == "passed"
    assert candidate["workflows"][0]["trial"]["status"] == "passed"
    assert candidate["workflows"][0]["trial"]["workflow_version"] == "1.0.0"
    assert candidate["workflows"][0]["verification"]["status"] == "verified-staged"
    assert candidate["actions"][0]["verification"]["status"] == "verified-staged"
    assert candidate["actions"][0]["activation"] == original["actions"][0]["activation"]
    assert candidate["actions"][0]["system_review"] == original["actions"][0]["system_review"]
    assert candidate["actions"][0]["human_decision"] == original["actions"][0]["human_decision"]
    assert candidate["trial_evaluation"]["persistence_state_change"] is False
    assert candidate["trial_evaluation"]["activation"]["active_surface"] is False


def test_partial_coverage_leaves_alternatives_and_workflow_unverified() -> None:
    graph = _graph()
    receipt = _receipt(graph, full=False)

    candidate = evaluate_candidate_trials(graph, receipt)
    metrics = candidate["trial_metrics"]

    assert metrics["verified_implementations"] == 1
    assert metrics["unverified_implementations"] == 2
    assert metrics["covered_dots"] == 1
    assert metrics["covered_workflows"] == 0
    assert metrics["covered_actions"] == 0
    assert candidate["workflows"][0]["verification"]["status"] == "unverified"
    assert candidate["workflows"][0]["trial"]["status"] == "pending"
    assert candidate["actions"][0]["verification"]["status"] == "unverified"


def test_multiple_trials_are_idempotent_and_content_digest_is_bound() -> None:
    graph = _graph()
    receipt = _receipt(graph)

    first = evaluate_candidate_trials(graph, [receipt, receipt])
    second = evaluate_candidate_trials(first, [receipt])

    assert first["trial_metrics"] == second["trial_metrics"]
    assert (
        first["trial_evaluation"]["base_graph_content_digest"]
        == candidate_graph_content_digest(graph)
    )
    assert (
        second["trial_evaluation"]["base_graph_content_digest"]
        == first["trial_evaluation"]["base_graph_content_digest"]
    )


def test_pending_base_trial_status_reaches_the_same_evaluation_fixed_point() -> None:
    graph = _graph()
    graph["metrics"] = {"source_count": 1, "unverified_implementations": 3}
    for dot in graph["dots"]:
        dot["trial"]["status"] = "pending"
    receipt = _receipt(graph)

    first = evaluate_candidate_trials(graph, [receipt])
    second = evaluate_candidate_trials(first, [receipt])

    assert first == second
    assert candidate_graph_content_digest(first) == candidate_graph_content_digest(graph)


@pytest.mark.parametrize("mutation,expected", [
    (_swap_steps, TrialOrderError),
    (_wrong_output, TrialOutputError),
    (_excess_effect, TrialScopeError),
])
def test_order_output_and_side_effect_bounds_fail_closed(mutation, expected) -> None:
    graph = _graph()
    mutated = _rebind(mutation(_receipt(graph)))

    with pytest.raises(expected):
        validate_trial_receipt(mutated, graph=graph)


def test_unknown_reference_is_rejected() -> None:
    graph = _graph()
    receipt = _receipt(graph)
    receipt["plan"]["dot_refs"][0]["dot_id"] = "unknown-dot"
    receipt = _rebind(receipt)

    with pytest.raises(Exception, match="unknown candidate dot"):
        validate_trial_receipt(receipt, graph=graph)


def test_tampered_or_failed_command_receipt_is_rejected() -> None:
    graph = _graph()
    receipt = _receipt(graph)
    command = receipt["command_receipts"][0]
    command["status"] = "failed"
    command["receipt_sha256"] = _digest(
        {key: value for key, value in command.items() if key != "receipt_sha256"}
    )
    receipt = _rebind(receipt)

    with pytest.raises(TrialIntegrityError):
        validate_trial_receipt(receipt, graph=graph)


def test_failed_trial_receipt_is_recorded_without_candidate_promotion() -> None:
    graph = _graph()
    failed = _receipt(graph)
    failed["status"] = "failed"
    failed["outcome"] = {"status": "failed", "summary": "bounded trial failed"}
    failed = _rebind(failed)

    candidate = evaluate_candidate_trials(graph, failed)

    assert candidate["trial_metrics"]["failures"] == 1
    assert candidate["trial_metrics"]["verified_implementations"] == 0
    assert candidate["dots"][0]["verification"]["status"] == "unverified"
    assert candidate["workflows"][0]["verification"]["status"] == "unverified"


def test_scope_expiry_and_authority_are_rejected() -> None:
    graph = _graph()
    expiry = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    with pytest.raises(TrialScopeError):
        _plan(graph, expires_at=expiry)

    receipt = _receipt(graph)
    with pytest.raises(TrialScopeError):
        validate_trial_receipt(receipt, graph=graph, project_id="different-project")
    receipt["provider_authority"] = {"provider_id": "outside-scope"}
    receipt = _rebind(receipt)
    with pytest.raises(TrialScopeError):
        validate_trial_receipt(receipt, graph=graph)


def test_metrics_are_compact_and_candidate_remains_inactive() -> None:
    graph = _graph()
    candidate = evaluate_candidate_trials(graph, _receipt(graph))
    metrics = candidate["trial_metrics"]

    assert set(metrics) >= {
        "verified_implementations",
        "unverified_implementations",
        "covered_dots",
        "covered_workflows",
        "covered_actions",
        "failures",
    }
    assert candidate["candidate_only"] is True
    assert all(item["activation"]["status"] == "inactive" for item in candidate["dots"])
    assert candidate.get("persistence_state_change", True)
