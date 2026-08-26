# ruff: noqa: E501

"""Focused tests for the pure bottom-up genesis compiler."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import fractal.capability_compiler as compiler
from fractal.capability_compiler import (
    CompilerBoundaryError,
    CompilerInputError,
    compile_capability_graph,
)
from fractal.capability_source import (
    build_source,
    empty_source_catalogue,
    merge_source_catalogue,
)


def _source(name: str, commit: str) -> dict[str, object]:
    return build_source(
        name=name,
        source_type="skill",
        donor_id=f"donor-{name.casefold()}",
        locator=f"https://example.invalid/{name.casefold()}",
        commit=commit * 40,
        content_sha256=(commit * 64),
        retrieved_at="2026-08-25T00:00:00Z",
        licence={"status": "verified", "spdx": "MIT", "evidence": "LICENSE"},
        constraints={"content_reuse": "candidate-copy"},
        claimed_capabilities=[],
    )


def _catalogue() -> tuple[dict[str, object], list[dict[str, object]]]:
    first = _source("Alpha", "a")
    second = _source("Beta", "b")
    catalogue = empty_source_catalogue()
    catalogue = merge_source_catalogue(catalogue, first)
    catalogue = merge_source_catalogue(catalogue, second)
    return catalogue, [first, second]


def _research_only_source() -> dict[str, object]:
    return build_source(
        name="Research-only",
        source_type="skill",
        donor_id="donor-research-only",
        locator="https://example.invalid/research-only",
        commit="d" * 40,
        content_sha256="e" * 64,
        retrieved_at="2026-08-25T00:00:00Z",
        licence={"status": "missing"},
        constraints={"content_reuse": "metadata-only"},
        claimed_capabilities=[],
    )


def _responsibilities() -> list[dict[str, object]]:
    return [
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


def _inputs() -> tuple[dict[str, object], object, list[dict[str, object]], dict[str, object], dict[str, object], dict[str, object]]:
    catalogue, sources = _catalogue()
    responsibilities = _responsibilities()
    documents = {
        source["source_id"]: {"frontmatter": {"responsibilities": responsibilities}}
        for source in sources
    }
    observed = {
        "observations": [
            {"signature": "run-a", "outcome": "triaged report", "succeeded": True, "evidence_ids": ["run-a"]},
            {"signature": "run-b", "outcome": "triaged report", "succeeded": True, "evidence_ids": ["run-b"]},
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
    # Natural human-facing vocabulary is supplied independently of the old
    # surface.  The compiler must induce the one-word verb, not inherit it.
    intents = [{"stable_id": "triage", "statement": "Triage a report for the reader.", "familiar": "triage", "evidence_ids": ["intent-triage"]}]
    naming = {
        "proposals": {"triage": ["triage"]},
        "rationale": {"triage": "Use the familiar human outcome."},
        "alternatives": {"triage": []},
        "language": "en",
        "part_of_speech": "verb",
        "provenance": {
            "component_id": "naming-system",
            "version": "0.1.0",
            "evidence_ids": ["naming-system-evaluation"],
        },
    }
    comparisons = {"comparisons": [{"left": 0, "right": 1, "relation": "distinct", "evidence_ids": ["bounded-comparison"]}]}
    return catalogue, documents, observed, intents, naming, comparisons


def test_end_to_end_bottom_up_graph_has_natural_action_vocabulary() -> None:
    args = _inputs()
    graph = compile_capability_graph(*args)

    assert graph["record_type"] == "capability-candidate-graph"
    assert graph["schema_version"] == 1
    assert graph["input_digest"]
    assert graph["dots"]
    assert graph["workflows"]
    assert {item["action_id"] for item in graph["actions"]} == {"triage"}
    assert all(item["activation"]["status"] == "inactive" for item in graph["actions"])
    assert graph["actions"][0]["induction_evidence"]["anti_seed_attestation"]["no_legacy_action"] is True
    assert graph["actions"][0]["induction_evidence"]["new_workflow_clusters"]
    assert "provider" not in json.dumps(graph["actions"], ensure_ascii=False).casefold()


def test_pipeline_order_is_explicit_and_bottom_up(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _inputs()
    calls: list[str] = []
    names = (
        ("extract_responsibilities", compiler.extract_responsibilities),
        ("integrate_capabilities", compiler.integrate_capabilities),
        ("synthesize_candidate_workflows", compiler.synthesize_candidate_workflows),
        ("induce_candidate_actions", compiler.induce_candidate_actions),
        ("compress_candidate_actions", compiler.compress_candidate_actions),
    )
    for name, original in names:
        def wrapper(*inner_args: object, _name: str = name, _original: object = original, **inner_kwargs: object) -> object:
            calls.append(_name)
            return _original(*inner_args, **inner_kwargs)  # type: ignore[operator]

        monkeypatch.setattr(compiler, name, wrapper)

    compile_capability_graph(*args)
    first = {name: calls.index(name) for name, _ in names}
    assert list(first.values()) == sorted(first.values())
    assert calls.count("extract_responsibilities") == 2
    assert calls.count("integrate_capabilities") == 1


def test_workflow_contracts_resolve_responsibilities_only_after_dot_synthesis() -> None:
    args = _inputs()
    graph = compile_capability_graph(
        *args,
        workflow_contracts={
            "inputs": ["request"],
            "dot_contracts": [
                {
                    "responsibility": "Parse bounded requests.",
                    "inputs": ["request"],
                    "outputs": ["parsed request"],
                },
                {
                    "responsibility": "Compose auditable reports.",
                    "inputs": ["parsed request"],
                    "outputs": ["report"],
                },
            ],
            "bindings": [
                {
                    "from_responsibility": "Parse bounded requests.",
                    "to_responsibility": "Compose auditable reports.",
                    "output": "parsed request",
                    "input": "parsed request",
                }
            ],
        },
    )

    assert len(graph["workflows"]) == 1
    assert len(graph["workflows"][0]["dot_refs"]) == 2


def test_order_independence_and_no_source_copies_or_mutation() -> None:
    args = _inputs()
    original = copy.deepcopy(args)
    first = compile_capability_graph(*args)
    reversed_args = (
        args[0],
        {key: {"frontmatter": {"responsibilities": list(reversed(value["frontmatter"]["responsibilities"]))}} for key, value in reversed(list(args[1].items()))},
        {"observations": list(reversed(args[2]["observations"])), "outcomes": list(reversed(args[2]["outcomes"]))},
        list(reversed(args[3])),
        copy.deepcopy(args[4]),
        {"comparisons": list(reversed(args[5]["comparisons"]))},
    )
    second = compile_capability_graph(*reversed_args)

    assert first == second
    assert first["input_digest"] == second["input_digest"]
    assert args == original
    assert all(set(ref) <= {"source_id", "provenance_refs"} for ref in first["source_refs"])
    assert "sources" not in first
    assert '"record_type": "capability-source"' not in json.dumps(first, sort_keys=True)


def test_metrics_are_evidence_and_conflicts_are_retained() -> None:
    args = list(_inputs())
    args[5] = {"comparisons": [{"left": 0, "right": 2, "relation": "conflicting", "evidence_ids": ["conflict-proof"]}]}
    graph = compile_capability_graph(*args)
    metrics = graph["metrics"]
    assert metrics["source_count"] == 2
    assert metrics["responsibility_count"] == 4
    assert metrics["dot_count"] == 2
    assert metrics["duplicate_collapses"] >= 1
    assert metrics["candidate_contributing_responsibilities"] == metrics["responsibility_count"]
    assert metrics["blocked_findings"] == 0
    assert metrics["relation_edge_counts"]["duplicate"] >= 1
    assert metrics["workflow_count"] >= 1
    assert metrics["action_count"] >= 1
    assert metrics["unresolved_conflicts"] >= 1
    assert metrics["unverified_implementations"] >= 1
    assert metrics["counts_are_evidence"] is True
    assert metrics["optimisation_target"] is None
    detail = metrics["lookup_estimate_detail"]
    assert detail["responsibility_lookups"] == metrics["responsibility_count"]
    assert metrics["lookup_estimate"] == sum(
        value
        for key, value in detail.items()
        if key not in {"unit", "possible_responsibility_pairs"}
    )
    assert any(item.get("relation") == "conflicting" for item in graph["unresolved_conflicts"])


def test_research_only_extraction_is_evidence_but_never_a_candidate_dot() -> None:
    blocked = _research_only_source()
    catalogue = merge_source_catalogue(empty_source_catalogue(), blocked)
    args = list(_inputs())
    args[0] = catalogue
    args[1] = {
        blocked["source_id"]: {
            "frontmatter": {"responsibilities": ["Extract public findings."]}
        }
    }

    graph = compile_capability_graph(*args)

    assert len(graph["extraction_evidence"]) == 1
    assert graph["extraction_evidence"][0]["candidate_contribution_allowed"] is False
    assert graph["dots"] == []
    assert graph["workflows"] == []
    assert graph["actions"] == []
    assert graph["metrics"]["responsibility_count"] == 1
    assert graph["metrics"]["candidate_contributing_responsibilities"] == 0
    assert graph["metrics"]["blocked_findings"] == 1
    assert graph["gates"]["candidate_contribution"]["blocked_findings"] == 1
    assert graph["gates"]["candidate_contribution"]["allowed_findings"] == 0


def test_mixed_allowed_and_blocked_findings_only_allowed_records_synthesize() -> None:
    catalogue, sources = _catalogue()
    blocked = _research_only_source()
    catalogue = merge_source_catalogue(catalogue, blocked)
    args = list(_inputs())
    args[0] = catalogue
    args[1] = {
        source["source_id"]: {
            "frontmatter": {"responsibilities": _responsibilities()}
        }
        for source in sources
    }
    args[1][blocked["source_id"]] = {
        "frontmatter": {"responsibilities": ["Extract private findings."]}
    }

    graph = compile_capability_graph(*args)

    assert graph["metrics"]["responsibility_count"] == 5
    assert graph["metrics"]["candidate_contributing_responsibilities"] == 4
    assert graph["metrics"]["blocked_findings"] == 1
    assert len(graph["dots"]) == 2
    assert all(
        "Extract private findings." not in item.get("responsibility", "")
        for item in graph["dots"]
    )
    assert graph["gates"]["candidate_contribution"]["passed"] is True


def test_missing_candidate_contribution_gate_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _inputs()

    def malformed_extraction(*_: object) -> list[dict[str, object]]:
        return [{"record_type": "responsibility-extraction", "responsibility": "Read evidence."}]

    monkeypatch.setattr(compiler, "_extract_records", malformed_extraction)
    with pytest.raises(CompilerInputError, match="candidate contribution gate"):
        compile_capability_graph(*args)


def test_transitive_duplicate_component_counts_record_reduction_not_edges() -> None:
    sources = [_source("Alpha", "a"), _source("Beta", "b"), _source("Gamma", "c")]
    catalogue = empty_source_catalogue()
    for source in sources:
        catalogue = merge_source_catalogue(catalogue, source)
    args = list(_inputs())
    args[0] = catalogue
    args[1] = {
        source["source_id"]: {
            "frontmatter": {"responsibilities": ["Parse bounded requests."]}
        }
        for source in sources
    }
    args[5] = {"comparisons": []}

    graph = compile_capability_graph(*args)

    assert graph["metrics"]["candidate_contributing_responsibilities"] == 3
    assert graph["metrics"]["dot_count"] == 1
    assert graph["metrics"]["duplicate_collapses"] == 2
    assert graph["metrics"]["relation_edge_counts"]["duplicate"] == 3


def test_missing_reusable_evidence_fails_closed_without_workflow_or_action() -> None:
    args = list(_inputs())
    args[2] = []
    graph = compile_capability_graph(*args)
    assert graph["dots"]
    assert graph["workflows"] == []
    assert graph["actions"] == []
    assert graph["gates"]["required_evidence"]["passed"] is False
    assert graph["gates"]["automatic_runtime_invocation"]["enabled"] is False


def test_boundaries_reject_callable_source_old_fields_provider_and_active_records() -> None:
    args = list(_inputs())
    bad_catalogue = copy.deepcopy(args[0])
    bad_catalogue["sources"][0]["source_only"]["callable"] = True
    with pytest.raises(CompilerBoundaryError):
        compile_capability_graph(bad_catalogue, *args[1:])

    bad_documents = copy.deepcopy(args[1])
    source_id = next(iter(bad_documents))
    bad_documents[source_id]["frontmatter"]["dot_group"] = "legacy"
    with pytest.raises(CompilerBoundaryError):
        compile_capability_graph(args[0], bad_documents, *args[2:])

    bad_intent = [{**args[3][0], "provider": "leak"}]
    with pytest.raises(CompilerBoundaryError, match="provider"):
        compile_capability_graph(args[0], args[1], args[2], bad_intent, args[4], args[5])

    bad_observed = [{"record_type": "capability-workflow", "workflow_id": "old", "status": "active"}]
    with pytest.raises(CompilerBoundaryError):
        compile_capability_graph(args[0], args[1], bad_observed, args[3], args[4], args[5])


def test_required_evidence_arguments_are_explicit() -> None:
    args = _inputs()
    with pytest.raises(CompilerInputError):
        compile_capability_graph(args[0], args[1], None, args[3], args[4], args[5])
    with pytest.raises(CompilerInputError):
        compile_capability_graph(args[0], args[1], args[2], None, args[4], args[5])
    with pytest.raises(CompilerInputError):
        compile_capability_graph(args[0], args[1], args[2], args[3], args[4], None)


def test_candidate_graph_schema_is_self_validating_and_enforces_boundaries() -> None:
    schema_path = Path(__file__).parents[1] / "src" / "fractal" / "schemas" / "capability-candidate-graph.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    graph = compile_capability_graph(*_inputs())
    validator.validate(graph)

    invalid_graphs = []
    missing_metrics = copy.deepcopy(graph)
    del missing_metrics["metrics"]
    invalid_graphs.append(missing_metrics)
    active_action = copy.deepcopy(graph)
    active_action["actions"][0]["lifecycle"]["status"] = "active"
    invalid_graphs.append(active_action)
    raw_source = copy.deepcopy(graph)
    raw_source["source_refs"][0]["name"] = "raw Source definition"
    invalid_graphs.append(raw_source)
    source_alias = copy.deepcopy(graph)
    source_alias["sources"] = copy.deepcopy(graph["source_refs"])
    invalid_graphs.append(source_alias)
    provider_leak = copy.deepcopy(graph)
    provider_leak["actions"][0]["provider"] = "leak"
    invalid_graphs.append(provider_leak)
    wrong_metric_type = copy.deepcopy(graph)
    wrong_metric_type["metrics"]["source_count"] = "2"
    invalid_graphs.append(wrong_metric_type)

    for invalid in invalid_graphs:
        with pytest.raises(CompilerInputError, match="schema validation failed"):
            compiler.validate_candidate_graph(invalid)
