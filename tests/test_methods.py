from __future__ import annotations

from fractal.design_queue import activate_next, resolve_active
from fractal.methods import load_method_registry


def test_continuous_improvement_is_the_only_core_philosophy() -> None:
    registry = load_method_registry()
    assert [item["id"] for item in registry["core_philosophies"]] == [
        "continuous-improvement"
    ]


def test_partial_supporting_philosophies_have_real_landings_and_open_designs() -> None:
    registry = load_method_registry()
    supporting = {item["id"]: item for item in registry["supporting_philosophies"]}
    for item_id in ["curiosity", "fatigue", "greed"]:
        item = supporting[item_id]
        assert item["decision_status"] == "intent-established-mechanism-partially-defined"
        assert item["operational_mapping"]
        assert item["open_questions"]
        assert item["false_claim_guard"]
    all_mappings = " ".join(
        mapping
        for item in supporting.values()
        for mapping in item["operational_mapping"]
    )
    assert "repetition-monitor" not in all_mappings
    assert "fatigue-monitor" not in all_mappings
    assert "curiosity-explorer" not in all_mappings


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
