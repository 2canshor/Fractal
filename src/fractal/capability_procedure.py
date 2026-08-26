"""Portable execution for bounded Candidate Dot implementation procedures.

Procedure records are Workplace-owned implementation evidence below a Dot;
they are not a fifth canonical capability object and never carry activation,
version, publication, Source, provider, network, or persistence authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

PROCEDURE_RECORD_TYPE = "capability-implementation-procedure"
PROCEDURE_RECORD_VERSION = 1
PROCEDURE_REF_KIND = "workplace-candidate-procedure"
OPERATIONS = frozenset(
    {
        "sequence-plan",
        "diagnose-failure",
        "verify-test-first",
        "verify-gates",
        "build-semantic-index",
        "search-grounded-index",
    }
)
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_REF = re.compile(
    r"^workplace://genesis/procedures/([a-z0-9][a-z0-9._-]{0,127})@([0-9]+\.[0-9]+\.[0-9]+)$"
)
_TOKEN = re.compile(r"[a-z0-9]+")


class CapabilityProcedureError(ValueError):
    """A procedure contract, reference, or bounded execution is invalid."""


def _copy_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityProcedureError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityProcedureError(f"{label} must contain text")
    return value.strip()


def _strings(value: Any, label: str, *, required: bool = True) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CapabilityProcedureError(f"{label} must be an ordered list")
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if required and not result:
        raise CapabilityProcedureError(f"{label} must not be empty")
    if len(set(result)) != len(result):
        raise CapabilityProcedureError(f"{label} must not contain duplicates")
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise CapabilityProcedureError("procedure values must be portable JSON") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def procedure_ref(procedure_id: str, version: str) -> dict[str, str]:
    if _ID.fullmatch(procedure_id) is None or _VERSION.fullmatch(version) is None:
        raise CapabilityProcedureError("procedure identity is invalid")
    return {
        "kind": PROCEDURE_REF_KIND,
        "ref": f"workplace://genesis/procedures/{procedure_id}@{version}",
    }


def validate_procedure(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _copy_mapping(value, "procedure")
    allowed = {
        "record_type",
        "record_version",
        "procedure_id",
        "version",
        "operation",
        "input_contract",
        "output_contract",
        "steps",
        "verification",
        "recovery",
        "evidence_ids",
        "candidate_only",
        "side_effects",
    }
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise CapabilityProcedureError(f"procedure contains unsupported fields: {unknown}")
    if record.get("record_type") != PROCEDURE_RECORD_TYPE or record.get("record_version") != 1:
        raise CapabilityProcedureError("procedure record type or version is invalid")
    procedure_id = _text(record.get("procedure_id"), "procedure_id")
    version = _text(record.get("version"), "version")
    if _ID.fullmatch(procedure_id) is None or _VERSION.fullmatch(version) is None:
        raise CapabilityProcedureError("procedure identity is invalid")
    if record.get("operation") not in OPERATIONS:
        raise CapabilityProcedureError("procedure operation is unsupported")
    if record.get("candidate_only") is not True:
        raise CapabilityProcedureError("procedure must remain candidate-only")
    if record.get("side_effects") != []:
        raise CapabilityProcedureError("portable procedures must declare zero side effects")
    _strings(record.get("input_contract"), "input_contract")
    _strings(record.get("output_contract"), "output_contract")
    _strings(record.get("steps"), "steps")
    _strings(record.get("evidence_ids"), "evidence_ids")
    verification = _copy_mapping(record.get("verification"), "verification")
    if verification.get("status") != "unverified":
        raise CapabilityProcedureError("canonical procedure starts unverified")
    recovery = _copy_mapping(record.get("recovery"), "recovery")
    _text(recovery.get("strategy"), "recovery.strategy")
    return record


def validate_procedure_registry(value: Mapping[str, Any]) -> dict[str, Any]:
    registry = _copy_mapping(value, "procedure registry")
    if registry.get("record_type") != "capability-implementation-procedure-registry":
        raise CapabilityProcedureError("procedure registry type is invalid")
    if registry.get("record_version") != 1 or registry.get("candidate_only") is not True:
        raise CapabilityProcedureError("procedure registry version or lifecycle is invalid")
    records = registry.get("procedures")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise CapabilityProcedureError("procedure registry requires an ordered list")
    validated = [validate_procedure(item) for item in records]
    identities = [(item["procedure_id"], item["version"]) for item in validated]
    if len(set(identities)) != len(identities):
        raise CapabilityProcedureError("procedure registry contains duplicate identities")
    registry["procedures"] = sorted(
        validated, key=lambda item: (item["procedure_id"], item["version"])
    )
    return registry


def _tasks(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("tasks")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or not values
    ):
        raise CapabilityProcedureError("sequence-plan requires tasks")
    tasks = [_copy_mapping(item, f"tasks[{index}]") for index, item in enumerate(values)]
    for index, task in enumerate(tasks):
        _text(task.get("task_id"), f"tasks[{index}].task_id")
        if task.get("status") != "completed" or task.get("reviewed") is not True:
            raise CapabilityProcedureError("every planned task must be completed and reviewed")
    return tasks


def _sequence_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    tasks = _tasks(payload)
    return {
        "change_context": {
            "request": _text(payload.get("request"), "request"),
            "completed_task_ids": [item["task_id"] for item in tasks],
            "reviewed": True,
            "red_test": copy.deepcopy(payload.get("red_test")),
            "green_test": copy.deepcopy(payload.get("green_test")),
            "gates": copy.deepcopy(payload.get("gates")),
        }
    }


def _diagnose_failure(payload: Mapping[str, Any]) -> dict[str, Any]:
    failure = _copy_mapping(payload.get("failure_context"), "failure_context")
    expected = failure.get("expected")
    actual = failure.get("actual")
    observations = _strings(failure.get("observations"), "failure_context.observations")
    if expected == actual:
        raise CapabilityProcedureError("diagnosis requires an observed mismatch")
    return {
        "change_context": {
            "root_cause": observations[0],
            "expected": expected,
            "actual": actual,
            "red_test": copy.deepcopy(payload.get("red_test")),
            "green_test": copy.deepcopy(payload.get("green_test")),
            "gates": copy.deepcopy(payload.get("gates")),
        }
    }


def _verify_test_first(payload: Mapping[str, Any]) -> dict[str, Any]:
    context = _copy_mapping(payload.get("change_context"), "change_context")
    red = _copy_mapping(context.get("red_test"), "change_context.red_test")
    green = _copy_mapping(context.get("green_test"), "change_context.green_test")
    if red.get("exit_code") == 0 or green.get("exit_code") != 0:
        raise CapabilityProcedureError("test-first evidence must show RED then GREEN")
    return {
        "tested_change": {
            "context_digest": _digest(context),
            "red_exit_code": red["exit_code"],
            "green_exit_code": green["exit_code"],
            "gates": copy.deepcopy(context.get("gates")),
        }
    }


def _verify_gates(payload: Mapping[str, Any]) -> dict[str, Any]:
    tested = _copy_mapping(payload.get("tested_change"), "tested_change")
    gates = _copy_mapping(tested.get("gates"), "tested_change.gates")
    required = ("tests", "quality", "security")
    if any(gates.get(name) != "passed" for name in required):
        raise CapabilityProcedureError("tests, quality, and security gates must all pass")
    return {
        "verified_change": {
            "tested_change_digest": _digest(tested),
            "gates": {name: gates[name] for name in required},
            "verified": True,
        }
    }


def _tokens(value: str) -> list[str]:
    return [token for token in _TOKEN.findall(value.casefold()) if len(token) > 1]


def _build_semantic_index(payload: Mapping[str, Any]) -> dict[str, Any]:
    request = _copy_mapping(payload.get("knowledge_request"), "knowledge_request")
    documents = request.get("documents")
    if (
        not isinstance(documents, Sequence)
        or isinstance(documents, (str, bytes, bytearray))
        or not documents
    ):
        raise CapabilityProcedureError("knowledge_request.documents must not be empty")
    rows = []
    document_frequency: Counter[str] = Counter()
    for index, raw in enumerate(documents):
        document = _copy_mapping(raw, f"documents[{index}]")
        document_id = _text(document.get("document_id"), f"documents[{index}].document_id")
        text = _text(document.get("text"), f"documents[{index}].text")
        frequencies = Counter(_tokens(text))
        document_frequency.update(frequencies)
        rows.append({"document_id": document_id, "text": text, "term_frequency": dict(frequencies)})
    total = len(rows)
    idf = {
        term: math.log((1 + total) / (1 + count)) + 1 for term, count in document_frequency.items()
    }
    return {
        "semantic_index": {
            "query": _text(request.get("query"), "knowledge_request.query"),
            "documents": rows,
            "inverse_document_frequency": idf,
        }
    }


def _search_grounded_index(payload: Mapping[str, Any]) -> dict[str, Any]:
    index = _copy_mapping(payload.get("semantic_index"), "semantic_index")
    query = _text(index.get("query"), "semantic_index.query")
    query_terms = Counter(_tokens(query))
    idf = _copy_mapping(index.get("inverse_document_frequency"), "inverse_document_frequency")
    ranked = []
    for raw in index.get("documents", []):
        document = _copy_mapping(raw, "semantic_index.documents[]")
        term_frequency = _copy_mapping(document.get("term_frequency"), "term_frequency")
        score = sum(
            float(idf.get(term, 0)) * count * term_frequency.get(term, 0)
            for term, count in query_terms.items()
        )
        ranked.append((score, document["document_id"], document["text"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked or ranked[0][0] <= 0:
        raise CapabilityProcedureError("grounded search found no supporting document")
    best = ranked[0]
    return {
        "grounded_answer": {
            "answer": best[2],
            "citations": [best[1]],
            "score": round(best[0], 6),
            "grounded": True,
        }
    }


_EXECUTORS = {
    "sequence-plan": _sequence_plan,
    "diagnose-failure": _diagnose_failure,
    "verify-test-first": _verify_test_first,
    "verify-gates": _verify_gates,
    "build-semantic-index": _build_semantic_index,
    "search-grounded-index": _search_grounded_index,
}


def execute_procedure(
    reference: Mapping[str, Any],
    registry: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    ref = _copy_mapping(reference, "procedure_ref")
    if ref.get("kind") != PROCEDURE_REF_KIND or not isinstance(ref.get("ref"), str):
        raise CapabilityProcedureError("procedure_ref kind is not executable")
    matched = _REF.fullmatch(ref["ref"])
    if matched is None:
        raise CapabilityProcedureError("procedure_ref is not an exact Workplace reference")
    identity = matched.groups()
    validated = validate_procedure_registry(registry)
    records = {(item["procedure_id"], item["version"]): item for item in validated["procedures"]}
    procedure = records.get(identity)
    if procedure is None:
        raise CapabilityProcedureError("procedure_ref does not resolve")
    input_value = _copy_mapping(payload, "payload")
    output = _EXECUTORS[procedure["operation"]](input_value)
    receipt = {
        "record_type": "capability-procedure-execution-receipt",
        "record_version": 1,
        "procedure_ref": ref,
        "procedure_digest": _digest(procedure),
        "input_digest": _digest(input_value),
        "output": output,
        "output_digest": _digest(output),
        "status": "succeeded",
        "verification": {"status": "verified", "contract": procedure["output_contract"]},
        "side_effects": [],
        "persistence_state_change": False,
        "activation": False,
        "version": False,
        "publication": False,
    }
    receipt["receipt_digest"] = _digest(receipt)
    return receipt


__all__ = [
    "CapabilityProcedureError",
    "OPERATIONS",
    "PROCEDURE_RECORD_TYPE",
    "PROCEDURE_RECORD_VERSION",
    "PROCEDURE_REF_KIND",
    "execute_procedure",
    "procedure_ref",
    "validate_procedure",
    "validate_procedure_registry",
]
