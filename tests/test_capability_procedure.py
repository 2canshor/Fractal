from __future__ import annotations

import copy

import pytest

from fractal.capability_procedure import (
    CapabilityProcedureError,
    execute_procedure,
    procedure_ref,
    validate_procedure_registry,
)


def procedure(procedure_id: str, operation: str, output: str) -> dict[str, object]:
    return {
        "record_type": "capability-implementation-procedure",
        "record_version": 1,
        "procedure_id": procedure_id,
        "version": "1.0.0",
        "operation": operation,
        "input_contract": ["bounded input"],
        "output_contract": [output],
        "steps": ["Validate input.", "Produce bounded output.", "Verify output."],
        "verification": {"status": "unverified"},
        "recovery": {"strategy": "Discard the candidate execution result."},
        "evidence_ids": [f"procedure-{procedure_id}"],
        "candidate_only": True,
        "side_effects": [],
    }


def registry(*records: dict[str, object]) -> dict[str, object]:
    return {
        "record_type": "capability-implementation-procedure-registry",
        "record_version": 1,
        "candidate_only": True,
        "procedures": list(records),
    }


def test_software_procedures_execute_one_bounded_verified_chain() -> None:
    records = registry(
        procedure("plan", "sequence-plan", "change_context"),
        procedure("test", "verify-test-first", "tested_change"),
        procedure("verify", "verify-gates", "verified_change"),
    )
    plan = execute_procedure(
        procedure_ref("plan", "1.0.0"),
        records,
        {
            "request": "Implement one bounded change.",
            "tasks": [{"task_id": "change", "status": "completed", "reviewed": True}],
            "red_test": {"exit_code": 1},
            "green_test": {"exit_code": 0},
            "gates": {"tests": "passed", "quality": "passed", "security": "passed"},
        },
    )
    tested = execute_procedure(
        procedure_ref("test", "1.0.0"),
        records,
        plan["output"],
    )
    verified = execute_procedure(
        procedure_ref("verify", "1.0.0"),
        records,
        tested["output"],
    )

    assert verified["output"]["verified_change"]["verified"] is True
    assert all(item["status"] == "succeeded" for item in (plan, tested, verified))
    assert all(item["side_effects"] == [] for item in (plan, tested, verified))


def test_debug_procedure_attributes_a_real_mismatch() -> None:
    records = registry(procedure("debug", "diagnose-failure", "change_context"))
    receipt = execute_procedure(
        procedure_ref("debug", "1.0.0"),
        records,
        {
            "failure_context": {
                "expected": 3,
                "actual": 2,
                "observations": ["The reducer dropped one completed item."],
            },
            "red_test": {"exit_code": 1},
            "green_test": {"exit_code": 0},
            "gates": {"tests": "passed", "quality": "passed", "security": "passed"},
        },
    )

    assert receipt["output"]["change_context"]["root_cause"] == (
        "The reducer dropped one completed item."
    )


def test_grounded_search_returns_the_supporting_document() -> None:
    records = registry(
        procedure("index", "build-semantic-index", "semantic_index"),
        procedure("search", "search-grounded-index", "grounded_answer"),
    )
    indexed = execute_procedure(
        procedure_ref("index", "1.0.0"),
        records,
        {
            "knowledge_request": {
                "query": "Which rule requires evidence before claims?",
                "documents": [
                    {"document_id": "unrelated", "text": "Blue is a colour."},
                    {
                        "document_id": "rule",
                        "text": "Verification requires evidence before success claims.",
                    },
                ],
            }
        },
    )
    searched = execute_procedure(
        procedure_ref("search", "1.0.0"),
        records,
        indexed["output"],
    )

    assert searched["output"]["grounded_answer"]["citations"] == ["rule"]
    assert searched["output"]["grounded_answer"]["grounded"] is True


def test_reference_registry_and_procedure_contracts_fail_closed() -> None:
    records = registry(procedure("plan", "sequence-plan", "change_context"))
    with pytest.raises(CapabilityProcedureError, match="does not resolve"):
        execute_procedure(procedure_ref("missing", "1.0.0"), records, {})

    tampered = copy.deepcopy(records)
    tampered["procedures"][0]["side_effects"] = ["write canonical state"]
    with pytest.raises(CapabilityProcedureError, match="zero side effects"):
        validate_procedure_registry(tampered)
