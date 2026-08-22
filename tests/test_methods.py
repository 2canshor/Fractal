from __future__ import annotations

from fractal.design_queue import activate_next, resolve_active
from fractal.methods import (
    choose_execution_element,
    load_agentic_element_map,
    load_method_registry,
)


def test_correct_improvement_hierarchy_is_canonical() -> None:
    registry = load_method_registry()
    assert [item["id"] for item in registry["core_philosophies"]] == [
        "continuous-improvement"
    ]
    assert [item["id"] for item in registry["protagonist_mechanisms"]] == [
        "system-review"
    ]
    assert [item["id"] for item in registry["secondary_mechanisms"]] == [
        "project-review"
    ]


def test_methodologies_are_exactly_five_steps_and_three_values() -> None:
    registry = load_method_registry()
    methodologies = registry["methodologies"]
    steps = [item for item in methodologies if item["methodology_kind"] == "five-step"]
    values = [item for item in methodologies if item["methodology_kind"] == "three-value"]
    assert [item["id"] for item in steps] == [
        "find-problems",
        "find-local-patterns",
        "compare-history",
        "choose-system-response",
        "reality-check",
    ]
    assert [item["sequence"] for item in steps] == [1, 2, 3, 4, 5]
    assert [item["id"] for item in values] == ["fatigue", "curiosity", "greed"]
    for value in values:
        assert value["decision_status"] == "intent-established-methodology-partially-defined"
        assert value["open_questions"]
        assert value["evidence_requirement"]


def test_named_operational_concepts_are_mechanisms() -> None:
    registry = load_method_registry()
    mechanism_ids = {item["id"] for item in registry["mechanisms"]}
    assert {
        "deterministic-over-probabilistic",
        "quantity-over-quality",
        "subtraction-first",
        "global-outcome-over-local-optimisation",
        "work-signature",
        "naming-system",
        "capability-check",
        "hooks",
    } <= mechanism_ids


def test_every_hierarchy_node_has_one_valid_agentic_element_mapping() -> None:
    mapping = load_agentic_element_map()
    by_id = {item["node_id"]: item for item in mapping["mappings"]}
    assert by_id["system-review"]["primary_element"] == "main-agent"
    assert by_id["project-review"]["primary_element"] == "main-agent"
    assert by_id["fatigue"]["primary_element"] == "deterministic-program"
    assert by_id["curiosity"]["primary_element"] == "skill"
    assert by_id["greed"]["primary_element"] == "skill"
    assert by_id["deterministic-over-probabilistic"]["primary_element"] == (
        "deterministic-program"
    )


def test_deterministic_over_probabilistic_selects_the_smallest_exact_executor() -> None:
    exact = choose_execution_element(
        task_id="validate-project-record",
        repeatable_rule_available=True,
        exact_output_contract=True,
    )
    assert exact["route"] == "deterministic-program"

    mixed = choose_execution_element(
        task_id="interpret-validated-evidence",
        repeatable_rule_available=True,
        exact_output_contract=True,
        requires_causal_reasoning=True,
        requires_tradeoff=True,
    )
    assert mixed["route"] == "deterministic-first-then-main-agent"
    assert mixed["judgement_reasons"] == ["causal-reasoning", "tradeoff"]

    contextual = choose_execution_element(
        task_id="write-final-assessment",
        repeatable_rule_available=False,
        exact_output_contract=False,
        requires_synthesis=True,
    )
    assert contextual["route"] == "main-agent"


def test_live_claims_are_separate_from_source_and_projection() -> None:
    mapping = load_agentic_element_map()
    by_id = {item["node_id"]: item for item in mapping["mappings"]}
    assert by_id["work-signature"]["status"] == {
        "source": "implemented",
        "projection": "active",
        "execution": "verified-live",
    }
    assert by_id["system-review"]["status"] == {
        "source": "implemented",
        "projection": "staged",
        "execution": "verified-synthetic",
    }


def test_open_design_queue_handles_one_question_at_a_time() -> None:
    queue = {
        "questions": [
            {"id": "question-a", "status": "waiting"},
            {"id": "question-b", "status": "waiting"},
        ]
    }
    active_queue, active = activate_next(queue)
    assert active["id"] == "question-a"
    still_active, same = activate_next(active_queue)
    assert same["id"] == "question-a"
    resolved = resolve_active(
        still_active,
        question_id="question-a",
        outcome_status="deferred",
        outcome_summary="More evidence is required",
    )
    next_queue, next_item = activate_next(resolved)
    assert next_item["id"] == "question-b"
    assert sum(item["status"] == "active" for item in next_queue["questions"]) == 1
