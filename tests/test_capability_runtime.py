from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_capability_action import action as action_fixture
from test_capability_dot import active_dot as active_dot_fixture
from test_capability_dot import authority as authority_fixture
from test_capability_source import _source as source_fixture
from test_capability_workflow import workflow as workflow_fixture

from fractal.capability_runtime import (
    MISSING_CAPABILITY,
    MISSING_WORKFLOW,
    UNAVAILABLE,
    CandidateExecutionScopeError,
    CandidatePersistenceAuthorityError,
    RuntimeValidationError,
    build_compact_evidence,
    execute_resolution,
    load_active_capabilities_for_workplace,
    recover_missing_capability,
    resolve_implementation,
    resolve_request,
    runtime_request_digest,
    simulate_execution,
    validate_compact_evidence,
)


def graph() -> tuple[dict, dict, dict]:
    dot = active_dot_fixture()
    dot["dot_id"] = "verify-dot"
    workflow = workflow_fixture(status="active")
    action = action_fixture(
        status="active",
        action_id="review",
        workflow_ids=[("verify-evidence", "1.0.0")],
        human_name="review",
    )
    return action, workflow, dot


def request(**extra: object) -> dict:
    value = {
        "intent": "verify",
        "signals": ["evidence"],
        "inputs": ["bounded input"],
        "outputs": ["auditable output"],
    }
    value.update(extra)
    return value


def untyped_dot(*implementations: dict) -> dict:
    return {
        "dot_id": "canvas-step",
        "version": "1.0.0",
        "lifecycle": {"state": "active"},
        "inputs": ["request"],
        "outputs": ["canvas"],
        "implementations": list(implementations),
    }


def implementation(
    implementation_id: str,
    provider: str,
    *,
    availability: str = "available",
    quality: str = "high",
) -> dict:
    return {
        "implementation_id": implementation_id,
        "version": "1.0.0",
        "provider": provider,
        "availability": availability,
        "artifact": "local-artifact",
        "quality": quality,
        "cost": 1,
        "speed": 1,
        "recovery_score": 1,
        "permissions": ["read"],
        "dependencies": [],
        "compatibility": {"compatible": True},
        "verification": {"status": "verified"},
    }


def persistence_authority(request_value: dict, source_ids: list[str]) -> dict:
    return {
        "record_type": "workplace-candidate-persistence-authority",
        "record_version": 1,
        "authority_id": "candidate-persistence-1",
        "operation": "write-candidate-dot",
        "project_id": "project-a",
        "project_revision": 7,
        "task_id": "task-a",
        "request_digest": runtime_request_digest(request_value),
        "source_refs": source_ids,
        "allowed_relative_root": "genesis/candidates/dots",
        "candidate_write_count": 1,
        "candidate_only": True,
        "activation": False,
        "version": False,
        "publication": False,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }


def test_exact_fast_path_is_local_and_has_no_discovery_or_mutation() -> None:
    action, workflow, dot = graph()
    before = deepcopy((action, workflow, dot))
    result = resolve_request(
        request(),
        [action],
        [workflow],
        [dot],
        # Exact runtime must not parse or validate retained Sources at all.
        sources=[{"not": "a Source and must remain untouched on the fast path"}],
    )

    assert result["state"] == "exact"
    assert result["composition"] is None
    assert result["external_research_request"] is None
    assert result["candidate_signal"] is None
    assert result["mutations"] == []
    assert (action, workflow, dot) == before


def test_new_user_onboarding_loads_only_active_graph_and_never_runs_genesis() -> None:
    action, workflow, dot = graph()
    active_graph = {"actions": [action], "workflows": [workflow], "dots": [dot]}
    before = deepcopy(active_graph)

    result = load_active_capabilities_for_workplace(active_graph)

    assert result["status"] == "active-capabilities-loaded"
    assert result["actions"] == [{"action_id": "review", "version": "1.0.0"}]
    assert result["workflows"] == [{"workflow_id": "verify-evidence", "version": "1.0.0"}]
    assert result["dots"] == [{"dot_id": "verify-dot", "version": "1.0.0"}]
    assert result["source_intake_performed"] is False
    assert result["source_scraping_performed"] is False
    assert result["responsibility_extraction_performed"] is False
    assert result["dot_synthesis_performed"] is False
    assert result["workflow_synthesis_performed"] is False
    assert result["action_induction_performed"] is False
    assert result["genesis_performed"] is False
    assert result["persistent_mutations"] == []
    assert active_graph == before

    with pytest.raises(RuntimeValidationError, match="Genesis/candidate input is forbidden"):
        load_active_capabilities_for_workplace(
            {**active_graph, "sources": [source_fixture(capabilities=("verify",))]}
        )


def test_explicit_unknown_action_id_cannot_fall_through_to_matching_workflow() -> None:
    action, workflow, dot = graph()
    before = deepcopy((action, workflow, dot))
    result = resolve_request(
        request(action_id="unknown-action"),
        [action],
        [workflow],
        [dot],
    )

    assert result["state"] == MISSING_CAPABILITY
    assert result["route_state"] == MISSING_CAPABILITY
    assert result["unknown_action_id"] == "unknown-action"
    assert result["action_ref"] is None
    assert result["workflow_ref"] is None
    assert result["workflow_refs"] == []
    assert result["dot_refs"] == []
    assert result["implementation_refs"] == []
    assert result["dot_plans"] == []
    assert result["implementation_resolution"] == []
    assert result["composition"] is None
    assert result["persistent"] is False
    assert result["mutations"] == []
    assert (action, workflow, dot) == before

    evidence = build_compact_evidence(resolution=result)
    assert evidence["status"] == "blocked"
    assert evidence["route_state"] == MISSING_CAPABILITY
    assert evidence["action_ref"] is None
    assert evidence["workflow_refs"] == []
    assert evidence["dot_refs"] == []
    assert evidence["implementation_refs"] == []
    assert evidence["persistent"] is False
    assert evidence["promoted"] is False
    assert validate_compact_evidence(evidence)["evidence_digest"] == evidence["evidence_digest"]


def test_explicit_unknown_action_ref_returns_source_recovery_only() -> None:
    action, workflow, dot = graph()
    retained_source = source_fixture(capabilities=("unrelated-capability",))
    result = resolve_request(
        request(
            action_ref={"action_id": "unknown-action", "version": "1.0.0"},
            source_refs=[retained_source["source_id"]],
        ),
        [action],
        [workflow],
        [dot],
        sources=[retained_source],
    )

    assert result["state"] == MISSING_CAPABILITY
    assert result["unknown_action_id"] == "unknown-action"
    assert result["external_research_request"]["network_call"] is False
    assert result["external_research_request"]["requires_explicit_research_authority"] is True
    assert result["source_refs_checked"] == [retained_source["source_id"]]
    assert result["workflow_refs"] == []
    assert result["dot_plans"] == []
    assert result["persistent"] is False


@pytest.mark.parametrize(
    "selector",
    [
        {"action_id": "review"},
        {"action_ref": {"action_id": "review", "version": "1.0.0"}},
    ],
)
def test_known_explicit_action_selector_remains_exact(selector: dict[str, object]) -> None:
    action, workflow, dot = graph()
    result = resolve_request(request(**selector), [action], [workflow], [dot])

    assert result["state"] == "exact"
    assert result["action_ref"] == {"action_id": "review", "version": "1.0.0"}
    assert result["workflow_refs"]
    assert result["dot_plans"]


def test_partial_route_keeps_adaptation_project_local() -> None:
    action, workflow, dot = graph()
    result = resolve_request(
        request(project_local_adaptation={"input_alias": "bounded input"}),
        [action],
        [workflow],
        [dot],
    )

    assert result["state"] == "partial"
    assert result["project_local_adaptation"]["persistent"] is False
    assert result["workflow_refs"]


def test_missing_workflow_uses_one_nonpersistent_composition() -> None:
    dot = active_dot_fixture()
    dot["dot_id"] = "parse"
    dot["inputs"] = ["request"]
    dot["outputs"] = ["parsed"]
    result = resolve_request(
        {"intent": "parse", "inputs": ["request"], "outputs": ["parsed"], "project_id": "p"},
        actions=[],
        workflows=[],
        dots=[dot],
        project={"project_id": "p", "project_revision": 2, "task_id": "t"},
    )

    assert result["state"] == MISSING_WORKFLOW
    assert result["workflow_refs"] == []
    assert result["composition"]["persistent"] is False
    assert result["composition"]["record_type"] == "execution-composition"
    execution = execute_resolution(
        result,
        executor=lambda _plan, current_input: {"parsed": str(current_input["request"]).upper()},
        initial_input={"request": "bounded input"},
        duration_ms=4,
    )
    evidence = build_compact_evidence(execution)
    assert execution["status"] == "succeeded"
    assert execution["outcome"]["result"] == {"parsed": "BOUNDED INPUT"}
    assert execution["verification"]["status"] == "verified"
    assert evidence["route_state"] == MISSING_WORKFLOW
    assert evidence["verification"]["status"] == "verified"
    assert result["workflow_refs"] == []
    assert result["composition"]["persistent"] is False


def test_bounded_current_task_execution_stops_at_exact_failing_dot() -> None:
    dot = active_dot_fixture()
    dot["dot_id"] = "parse"
    dot["inputs"] = ["request"]
    dot["outputs"] = ["parsed"]
    resolution = resolve_request(
        {"intent": "parse", "inputs": ["request"], "outputs": ["parsed"]},
        actions=[],
        workflows=[],
        dots=[dot],
        project={"project_id": "p", "project_revision": 2, "task_id": "t"},
    )

    execution = execute_resolution(
        resolution,
        executor=lambda _plan, _input: (_ for _ in ()).throw(ValueError("bounded failure")),
    )
    evidence = build_compact_evidence(execution)

    assert execution["status"] == "failed"
    assert execution["failure"]["stage"] == "implementation-execution"
    assert execution["failure"]["dot_ref"] == {"dot_id": "parse", "version": "1.0.0"}
    assert len(execution["steps"]) == 1
    assert evidence["status"] == "failed"
    assert evidence["verification"]["status"] == "failed"


def test_work_signature_and_fatigue_are_derived_from_canonical_receipts() -> None:
    dot = active_dot_fixture()
    dot["dot_id"] = "parse"
    dot["inputs"] = ["request"]
    dot["outputs"] = ["parsed"]
    request_value = {
        "intent": "parse",
        "inputs": ["request"],
        "outputs": ["parsed"],
        "project_id": "p",
    }
    initial = resolve_request(
        request_value,
        actions=[],
        workflows=[],
        dots=[dot],
        project={"project_id": "p", "project_revision": 2, "task_id": "t"},
    )
    receipts = []
    for duration in (1, 2, 3):
        execution = execute_resolution(
            initial,
            executor=lambda _plan, _input: {"parsed": "done"},
            duration_ms=duration,
        )
        receipts.append(build_compact_evidence(execution))

    result = resolve_request(
        request_value,
        actions=[],
        workflows=[],
        dots=[dot],
        project={"project_id": "p", "project_revision": 2, "task_id": "t"},
        execution_evidence=receipts,
    )

    assert result["candidate_signal"]["status"] == "candidate"
    assert result["candidate_signal"]["workflow_promotion"] is False
    assert result["candidate_signal"]["patterns"][0]["occurrences"] == 3
    assert result["candidate_signal"]["fatigue"] == {
        "status": "triggered",
        "threshold": 3,
        "distinct_receipt_count": 3,
        "caller_occurrence_counts_trusted": False,
    }
    assert result["workflow_refs"] == []

    with pytest.raises(RuntimeValidationError, match="canonical compact execution evidence"):
        resolve_request(
            request_value,
            actions=[],
            workflows=[],
            dots=[dot],
            project={"project_id": "p", "project_revision": 2, "task_id": "t"},
            repeated_patterns=[{"signature": "caller-claim", "occurrences": 999}],
        )


def test_missing_capability_checks_complete_sources_then_writes_reads_and_executes(
    tmp_path,
) -> None:
    active_dots = []
    before = deepcopy(active_dots)
    unrelated = source_fixture(capabilities=("Translate documents for one bounded request.",))
    retained = source_fixture(
        "skill",
        capabilities=("Paint images for one bounded request.",),
    )
    request_value = {
        "intent": "paint images",
        "inputs": ["request"],
        "outputs": ["image"],
        "allowed_side_effects": ["emit one bounded output"],
    }
    authority = authority_fixture(
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        allowed_side_effects=["emit one bounded output"],
    )
    result = recover_missing_capability(
        request_value,
        actions=[],
        workflows=[],
        dots=active_dots,
        workplace_sources={
            "record_type": "capability-source-catalogue",
            "record_version": 1,
            "sources": sorted([retained, unrelated], key=lambda item: item["source_id"]),
        },
        workplace_root=tmp_path,
        project={"project_id": "project-a", "project_revision": 7, "task_id": "task-a"},
        candidate_execution_authority=authority,
        candidate_persistence_authority=persistence_authority(
            request_value, [retained["source_id"]]
        ),
        candidate_executor=lambda _plan, _request: {"image": "bounded-image-result"},
        candidate_verifier=lambda _plan, output: output.get("image") == "bounded-image-result",
    )

    resolution = result["resolution"]
    assert resolution["state"] == MISSING_CAPABILITY
    assert resolution["source_refs_checked"] == sorted(
        [retained["source_id"], unrelated["source_id"]]
    )
    assert resolution["suitable_source_refs"] == [retained["source_id"]]
    assert resolution["external_research_request"] is None
    assert result["trial_request"]["persistent"] is False
    assert result["trial_request"]["execution_authority"]["project_id"] == ("project-a")
    receipt = result["candidate_write_receipt"]
    assert receipt["read_back_verified"] is True
    candidate_path = tmp_path / receipt["path"]
    assert json.loads(candidate_path.read_text()) == result["candidate_dot"]
    assert result["current_task_result"]["status"] == "succeeded"
    assert result["current_task_result"]["outcome"]["result"] == {"image": "bounded-image-result"}
    assert result["compact_evidence"]["route_state"] == MISSING_CAPABILITY
    assert result["compact_evidence"]["verification"]["status"] == "verified"
    assert result["candidate_persistence_state_change"] is True
    assert result["active_persistence_state_change"] is False
    assert result["active_graph_unchanged"] is True
    assert result["activation"] is False
    assert result["version"] is False
    assert result["publication"] is False
    assert active_dots == before


def test_missing_capability_requests_external_research_only_after_full_catalogue(
    tmp_path,
) -> None:
    unrelated = source_fixture(capabilities=("Translate documents for one bounded request.",))
    request_value = {"intent": "paint images", "outputs": ["image"]}
    result = recover_missing_capability(
        request_value,
        actions=[],
        workflows=[],
        dots=[],
        workplace_sources=[unrelated],
        workplace_root=tmp_path,
        project={"project_id": "project-a", "project_revision": 7, "task_id": "task-a"},
        candidate_execution_authority=authority_fixture(),
        candidate_persistence_authority=persistence_authority(request_value, []),
        candidate_executor=lambda _plan, _request: {"image": "should-not-run"},
    )

    assert result["status"] == "external-research-required"
    assert result["resolution"]["source_refs_checked"] == [unrelated["source_id"]]
    assert result["resolution"]["external_research_request"]["network_call"] is False
    assert result["candidate_write_receipt"] is None
    assert result["current_task_result"] is None
    assert not (tmp_path / "genesis").exists()


def test_candidate_persistence_authority_is_separate_and_fail_closed(tmp_path) -> None:
    retained = source_fixture(capabilities=("Paint images for one bounded request.",))
    request_value = {"intent": "paint images", "outputs": ["image"]}
    bad = persistence_authority(request_value, [retained["source_id"]])
    bad["activation"] = True

    with pytest.raises(CandidatePersistenceAuthorityError, match="cannot grant activation"):
        recover_missing_capability(
            request_value,
            actions=[],
            workflows=[],
            dots=[],
            workplace_sources=[retained],
            workplace_root=tmp_path,
            project={"project_id": "project-a", "project_revision": 7, "task_id": "task-a"},
            candidate_execution_authority=authority_fixture(),
            candidate_persistence_authority=bad,
            candidate_executor=lambda _plan, _request: {"image": "no-write"},
        )
    assert not (tmp_path / "genesis").exists()


def test_provider_resolution_filters_unavailable_rive_and_asks_on_material_tie() -> None:
    figma = implementation("implementation-figma", "figma")
    rive = implementation("implementation-rive", "rive", availability="unavailable")
    dot = untyped_dot(figma, rive)
    selected = resolve_implementation(dot, request={"platform": "desktop"})
    assert selected["status"] == "selected"
    assert selected["implementation_ref"]["provider"] == "figma"
    assert any(item["filter"] == "availability" for item in selected["rejected"])

    sketch = implementation("implementation-sketch", "sketch")
    tie = resolve_implementation(
        untyped_dot(figma, sketch),
        request={"materially_consequential": True},
    )
    assert tie["status"] == "ask-user"
    assert tie["ask_user"] is True


def test_candidate_scope_rejection_is_fail_closed(tmp_path) -> None:
    retained = source_fixture(capabilities=("Paint images for one bounded request.",))
    request_value = {"intent": "paint images", "outputs": ["image"]}
    authority = authority_fixture(
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        project_id="other-project",
    )
    with pytest.raises(CandidateExecutionScopeError, match="different Project"):
        recover_missing_capability(
            request_value,
            actions=[],
            workflows=[],
            dots=[],
            workplace_sources=[retained],
            workplace_root=tmp_path,
            project={"project_id": "project-a", "project_revision": 7, "task_id": "task-a"},
            candidate_execution_authority=authority,
            candidate_persistence_authority=persistence_authority(
                request_value, [retained["source_id"]]
            ),
            candidate_executor=lambda _plan, _request: {"image": "no-write"},
        )
    assert not (tmp_path / "genesis").exists()


def test_unavailable_reports_failure_after_alternatives() -> None:
    dot = untyped_dot(implementation("implementation-rive", "rive", availability="unavailable"))
    workflow = {
        "workflow_id": "canvas-workflow",
        "version": "1.0.0",
        "lifecycle": {"state": "active"},
        "match_contract": {"intent": "canvas"},
        "dot_refs": [{"dot_id": "canvas-step", "version": "1.0.0", "lifecycle": "active"}],
    }
    result = resolve_request(
        {"intent": "canvas"},
        actions=[],
        workflows=[workflow],
        dots=[dot],
    )
    assert result["state"] == UNAVAILABLE
    assert result["implementation_resolution"][0]["status"] == "unavailable"


def test_familiar_action_label_does_not_jail_workflow_matching() -> None:
    _, workflow, dot = graph()
    result = resolve_request(
        request(action="my familiar synonym"),
        actions=[],
        workflows=[workflow],
        dots=[dot],
    )
    assert result["state"] == "exact"


def test_compact_evidence_keeps_only_versioned_refs_and_verification() -> None:
    action, workflow, dot = graph()
    resolution = resolve_request(request(), [action], [workflow], [dot])
    execution = simulate_execution(resolution, duration_ms=12, cost=3, work_signature="sig-1")
    evidence = build_compact_evidence(execution)

    assert evidence["action_ref"] == {"action_id": "review", "version": "1.0.0"}
    assert evidence["workflow_refs"][0]["version"] == "1.0.0"
    assert evidence["dot_refs"][0]["version"] == "1.0.0"
    assert evidence["implementation_refs"][0]["version"] == "1.0.0"
    assert evidence["duration_ms"] == 12
    assert evidence["cost"] == 3
    assert evidence["work_signature"] == "sig-1"
    assert evidence["verification"]["status"] == "verified"
    assert "dot" not in evidence and "implementation" not in evidence
    assert validate_compact_evidence(evidence)["evidence_digest"] == evidence["evidence_digest"]
