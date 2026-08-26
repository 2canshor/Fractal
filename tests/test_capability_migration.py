from __future__ import annotations

import copy
import json
from importlib.resources import files
from pathlib import Path

import pytest

from fractal.capability_action_induction import induce_candidate_actions
from fractal.capability_compiler import compile_capability_graph
from fractal.capability_migration import (
    MigrationInputError,
    MigrationPlacementError,
    MigrationValidationError,
    audit_capability_migration,
    simulate_authorised_version_admission,
)
from fractal.capability_source import (
    build_source,
    empty_source_catalogue,
    merge_source_catalogue,
)
from fractal.capability_workflow import build_workflow


def workflow(workflow_id: str = "review") -> dict:
    return build_workflow(
        workflow_id=workflow_id,
        version="1.0.0",
        human_name=workflow_id.title(),
        match_contract={"intent": workflow_id},
        inputs=["request"],
        outputs=["result"],
        dot_refs=[
            {
                "sequence": 1,
                "dot_id": f"{workflow_id}-dot",
                "version": "1.0.0",
                "lifecycle": "candidate",
            }
        ],
        success_contract={"outcome": "one bounded result"},
        side_effect_contract={"effects": ["none"]},
        recovery={"strategy": "restore the bounded attempt", "evidence_ids": ["recover"]},
        provenance={"evidence_refs": [f"workflow-evidence-{workflow_id}"]},
    )


def candidate_graph() -> dict:
    review = workflow()
    induced = induce_candidate_actions(
        [review],
        [
            {
                "workflow_id": "review",
                "version": "1.0.0",
                "stable_id": "review",
                "statement": "Review the result for the person.",
                "familiar": "review",
                "evidence_ids": ["intent-review"],
            }
        ],
        {
            "proposals": {"review": ["review"]},
            "rationale": {"review": "The familiar human outcome is explicit."},
            "alternatives": {"review": []},
            "language": "en",
            "part_of_speech": "verb",
            "provenance": {
                "component_id": "naming-system",
                "version": "0.1.0",
                "evidence_ids": ["naming-system-review"],
            },
        },
    )
    return {"candidate_workflows": [review], "candidate_actions": induced["candidate_actions"]}


def mapping() -> dict:
    return {"workflow_id": "review", "element_id": "capability-check"}


def compiled_graph() -> dict:
    source = build_source(
        name="Compiler Source",
        source_type="skill",
        donor_id="compiler-donor",
        locator="https://example.invalid/compiler",
        commit="c" * 40,
        content_sha256="d" * 64,
        retrieved_at="2026-08-25T00:00:00Z",
        licence={"status": "verified", "spdx": "MIT", "evidence": "LICENSE"},
        constraints={"content_reuse": "candidate-copy"},
    )
    catalogue = merge_source_catalogue(empty_source_catalogue(), source)
    responsibilities = [
        {
            "responsibility": "Parse bounded requests.",
            "inputs": ["request"],
            "outputs": ["parsed request"],
            "preconditions": ["the request is present"],
            "side_effects": ["emit one bounded output"],
        },
        {
            "responsibility": "Compose auditable reports.",
            "inputs": ["parsed request"],
            "outputs": ["report"],
            "preconditions": ["the parsed request is present"],
            "side_effects": ["emit one bounded output"],
        },
    ]
    documents = {
        source["source_id"]: {"frontmatter": {"responsibilities": responsibilities}}
    }
    observed = {
        "observations": [
            {"signature": "run-a", "outcome": "triaged report", "succeeded": True},
            {"signature": "run-b", "outcome": "triaged report", "succeeded": True},
        ],
        "outcomes": [
            {
                "outcome": "triaged report",
                "coherent_outcome": True,
                "reusable": True,
                "occurrences": 2,
                "evidence_ids": ["outcome-proof"],
            }
        ],
    }
    return compile_capability_graph(
        catalogue,
        documents,
        observed,
        [{
            "stable_id": "triage",
            "statement": "Triage a report for the reader.",
            "familiar": "triage",
            "evidence_ids": ["intent-triage"],
        }],
        {
            "proposals": {"triage": ["triage"]},
            "rationale": {"triage": "Use the human outcome."},
            "alternatives": {"triage": []},
            "language": "en",
            "part_of_speech": "verb",
            "provenance": {
                "component_id": "naming-system",
                "version": "0.1.0",
                "evidence_ids": ["naming-system-triage"],
            },
        },
        {"comparisons": [{"left": 0, "right": 1, "relation": "distinct"}]},
    )


def current_blueprint_mapping() -> dict:
    return json.loads(
        files("fractal.data")
        .joinpath("blueprint-implementation-map.json")
        .read_text(encoding="utf-8")
    )


def user_surface_policy() -> dict:
    return json.loads(
        (
            Path("/")
            / "Users"
            / "carsonchan"
            / "Fractal Workspace"
            / "system"
            / "components"
            / "user-surface-policy.json"
        )
        .read_text(encoding="utf-8")
    )


def test_full_legacy_action_list_is_removed_or_independently_reinduced() -> None:
    result = audit_capability_migration(
        candidate_graph(),
        {
            "label": "legacy-inventory",
            "actions": [
                {"action_id": "old-review", "human_name": "Review", "status": "active"},
                {"action_id": "old-retired", "human_name": "Retired Legacy", "status": "retired"},
            ],
            "taxonomy": {"assurance": ["old-review"]},
            "dot_groups": [{"group_id": "assurance", "action_ids": ["old-review"]}],
        },
        blueprint_mapping=mapping(),
    )

    assert [item["status"] for item in result["legacy_removal_audit"]] == [
        "independently-reinduced",
        "removed-from-candidate",
    ]
    assert result["legacy"]["fallback_recovery_only"] is True
    assert result["candidate"]["legacy_excluded"] is True


def test_same_name_requires_new_workflow_intent_naming_and_digest() -> None:
    graph = candidate_graph()
    action = copy.deepcopy(graph["candidate_actions"][0])
    action["induction_evidence"].pop("naming_system")
    graph["candidate_actions"] = [action]

    with pytest.raises(MigrationValidationError, match="candidate Action"):
        audit_capability_migration(
            graph,
            {"actions": [{"action_id": "old-review", "human_name": "Review"}]},
            blueprint_mapping=mapping(),
        )


def test_legacy_rename_or_delete_cannot_change_candidate_graph_or_digest() -> None:
    graph = candidate_graph()
    first = audit_capability_migration(
        graph,
        {"actions": [{"action_id": "old-review", "human_name": "Review"}]},
        blueprint_mapping=mapping(),
    )
    second = audit_capability_migration(
        graph,
        {"actions": [{"action_id": "renamed-old", "human_name": "Renamed Legacy"}]},
        blueprint_mapping=mapping(),
    )

    assert first["candidate_digest"] == second["candidate_digest"]
    assert first["candidate_graph"] == second["candidate_graph"]
    assert first["legacy_removal_audit"][0]["status"] == "independently-reinduced"
    assert second["legacy_removal_audit"][0]["status"] == "removed-from-candidate"


@pytest.mark.parametrize(
    "bad",
    [
        {"dot_groups": []},
        {"workflow": {"id": "old"}},
        {"skills": [{"name": "external"}]},
        {"candidate_actions": [{"legacy_action_id": "old"}]},
    ],
)
def test_old_taxonomy_dot_group_workflow_and_direct_skill_inputs_are_rejected(bad: dict) -> None:
    with pytest.raises(MigrationInputError):
        audit_capability_migration(bad, {})


def test_source_callability_is_rejected_and_source_provenance_licence_are_preserved() -> None:
    source = build_source(
        name="Portable Source",
        source_type="skill",
        donor_id="portable-donor",
        locator="https://example.invalid/source",
        commit="a" * 40,
        content_sha256="b" * 64,
        licence={"status": "verified", "spdx": "MIT", "evidence": "licence-file"},
        constraints={"content_reuse": "candidate-copy"},
    )
    graph = {"sources": [source]}
    result = audit_capability_migration(graph, {}, blueprint_mapping=mapping())
    assert result["preservation"]["provenance_preserved"] is True
    assert result["preservation"]["licences_preserved"] is True
    assert result["preservation"]["source_ids"] == [source["source_id"]]

    bad = copy.deepcopy(source)
    bad["source_only"]["callable"] = True
    with pytest.raises(MigrationValidationError, match="Source"):
        audit_capability_migration({"sources": [bad]}, {})


def test_placement_blueprint_and_workflow_flow_boundaries_are_explicit() -> None:
    graph = candidate_graph()
    graph["placement"] = {"candidate_workflows": "System-owned"}
    with pytest.raises(MigrationPlacementError, match="candidate candidate_workflows"):
        audit_capability_migration(graph, {}, blueprint_mapping=mapping())

    with pytest.raises(MigrationValidationError, match="Blueprint Flow"):
        audit_capability_migration(
            candidate_graph(),
            {},
            blueprint_mapping={
                "workflow_id": "review",
                "flow_id": "find-problems",
                "element_id": "curiosity",
            },
        )
    with pytest.raises(MigrationValidationError, match="cannot add"):
        audit_capability_migration(
            candidate_graph(),
            {},
            blueprint_mapping={"workflow_id": "review", "new_element_id": "new"},
        )


def test_cutover_is_extract_rebuild_test_switch_remove_and_keeps_fallback() -> None:
    result = audit_capability_migration(
        candidate_graph(),
        {
            "actions": [{"action_id": "old-review", "human_name": "Review", "status": "active"}],
            "workflows": [{"workflow_id": "old-review-workflow", "status": "active"}],
            "dot_groups": [{"group_id": "old-assurance"}],
        },
        blueprint_mapping=mapping(),
    )
    plan = result["cutover_plan"]
    assert plan["order"] == ["extract", "rebuild", "test", "switch", "remove"]
    assert plan["stages"][3]["status"] == "blocked-until-future-version"
    assert plan["stages"][4]["status"] == "blocked-until-future-version"
    assert plan["fallback"]["fallback_retained"] is True
    assert plan["fallback"]["remove_authorised"] is False
    assert result["rehearsal"]["writes"] is False
    assert result["rehearsal"]["deletes"] is False
    assert result["rehearsal"]["live_mutation"] is False


def test_input_objects_are_not_mutated() -> None:
    graph = candidate_graph()
    legacy = {"actions": [{"action_id": "old-review", "human_name": "Review"}]}
    before_graph, before_legacy = copy.deepcopy(graph), copy.deepcopy(legacy)
    audit_capability_migration(graph, legacy, blueprint_mapping=mapping())
    assert graph == before_graph
    assert legacy == before_legacy


def test_compiler_candidate_graph_is_a_direct_audit_input_without_normalisation() -> None:
    graph = compiled_graph()
    before = copy.deepcopy(graph)
    result = audit_capability_migration(
        graph,
        {"actions": [{"action_id": "old-triage", "human_name": "Triage"}]},
        blueprint_mapping=current_blueprint_mapping(),
    )

    assert result["candidate_graph"] == graph
    assert result["candidate"]["graph"] == graph
    assert any(
        item["target"]["existing_element_id"] == "continuous-improvement"
        for item in result["blueprint_mapping"]["mappings"]
    )
    assert result["preservation"]["provenance_preserved"] is True
    assert result["preservation"]["licences_preserved"] is True
    assert graph == before

    changed_legacy = {"actions": [{"action_id": "renamed-old", "human_name": "Renamed"}]}
    changed = audit_capability_migration(
        graph,
        changed_legacy,
        blueprint_mapping=current_blueprint_mapping(),
    )
    assert changed["candidate_digest"] == result["candidate_digest"]


def test_simulated_authorised_version_admits_selected_capabilities_to_system_only() -> None:
    graph = compiled_graph()
    result = simulate_authorised_version_admission(
        graph,
        {
            "record_type": "simulated-authorised-version-receipt",
            "system_version": "0.1.0-test",
            "candidate_input_digest": graph["input_digest"],
            "authorised": True,
            "primary_user": True,
        },
    )

    assert result["system_admission"]["owner"] == "system"
    assert len(result["system_admission"]["actions"]) == len(graph["actions"])
    assert len(result["system_admission"]["workflows"]) == len(graph["workflows"])
    assert len(result["system_admission"]["dots"]) == len(graph["dots"])
    assert result["workplace_retention"]["owner"] == "workplace"
    assert result["workplace_retention"]["source_ref_count"] == len(graph["source_refs"])
    assert result["performed"] is False
    assert result["writes"] == []


def test_compiler_graph_rejects_raw_sources_and_true_boundary_receipts() -> None:
    graph = compiled_graph()
    raw = copy.deepcopy(graph)
    raw_source = build_source(
        name="Raw Source",
        source_type="skill",
        donor_id="raw-donor",
        locator="https://example.invalid/raw",
        commit="e" * 40,
        content_sha256="f" * 64,
        licence={"status": "verified", "spdx": "MIT", "evidence": "LICENSE"},
        constraints={"content_reuse": "candidate-copy"},
    )
    raw["source_refs"] = [raw_source]
    raw["sources"] = [raw_source]
    with pytest.raises(MigrationValidationError, match="compact Source"):
        audit_capability_migration(raw, {})

    executed = copy.deepcopy(graph)
    executed["execution"]["provider_execution"] = True
    with pytest.raises(MigrationInputError, match="boundary receipt"):
        audit_capability_migration(executed, {})

    activated = copy.deepcopy(graph)
    activated["activation"]["active_surface"] = True
    with pytest.raises(MigrationInputError, match="boundary receipt"):
        audit_capability_migration(activated, {})


def test_actual_user_surface_fallback_audits_actions_commands_and_old_counts() -> None:
    graph = compiled_graph()
    policy = user_surface_policy()
    policy["workflows"] = [
        {"workflow_id": "legacy-one", "status": "active"},
        {"workflow_id": "legacy-two", "status": "active"},
    ]
    result = audit_capability_migration(
        graph,
        policy,
        blueprint_mapping=current_blueprint_mapping(),
    )

    assert len(result["legacy_removal_audit"]) == 3
    assert all(
        item["status"] == "removed-from-candidate"
        for item in result["legacy_removal_audit"]
    )
    assert len(result["command_controls"]) == 4
    assert all(
        item["status"] == "preserved-lifecycle-control"
        for item in result["command_controls"]
    )
    assert result["cutover_plan"]["fallback"]["active_workflow_count"] == 2
    assert result["cutover_plan"]["fallback"]["active_dot_group_count"] == len(
        policy["dot_groups"]
    )
    assert result["cutover_plan"]["fallback"]["commands_retained"] == 4
    assert result["cutover_plan"]["fallback"]["fallback_retained"] is True


@pytest.mark.parametrize(
    "nested",
    [
        {"history": {"actions": [{"action_id": "old", "human_name": "Old"}]}},
        {"fallback": {"actions": [{"action_id": "old", "human_name": "Old"}]}},
        {"metadata": {"dot_groups": [{"group_id": "old"}]}},
        {"history": {"workflows": [{"workflow_id": "old", "dot_refs": []}]}},
    ],
)
def test_nested_legacy_capability_objects_cannot_seed_candidate_digest(nested: dict) -> None:
    graph = compiled_graph()
    graph.update(nested)
    with pytest.raises(MigrationInputError):
        audit_capability_migration(graph, {})
