"""Deterministic synthesis of inactive Candidate capability Workflows.

This module is the boundary between the Dot graph and the reusable Workflow
contract.  It deliberately works bottom-up: a Workflow can only refer to
versioned Candidate or Active Dots, and it is emitted only when a coherent
outcome has explicit reuse evidence.  The function is pure and returns
ordinary JSON-compatible values; it never reads or writes Workplace state,
executes a provider, promotes a candidate, or creates a user-facing Action.

The implementation has two gates.  First, structural compatibility is checked
using port names/types, preconditions, bounded side effects, permissions,
lifecycle and provider-independent responsibility.  Only paths that pass that
gate are then compared with reusable-outcome and observed-composition
evidence.  This ordering is important: semantic similarity cannot make two
technically incompatible Dots into a Workflow.
"""

# The synthesis boundary keeps compact deterministic expressions close to the
# contract vocabulary; the repository's neighbouring integration engine uses
# the same line-length exception.
# ruff: noqa: E501

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fractal.capability_dot import CapabilityDotError, validate_capability_dot
from fractal.capability_workflow import build_workflow

SYNTHESIS_RECORD_TYPE = "capability-workflow-synthesis"
SYNTHESIS_RECORD_VERSION = 1


class WorkflowSynthesisError(ValueError):
    """Raised when synthesis input would cross a capability boundary."""


class WorkflowInputError(WorkflowSynthesisError):
    """Raised for a non-Dot, forbidden, or malformed synthesis input."""


class WorkflowCompatibilityError(WorkflowSynthesisError):
    """Raised for an irreconcilable structural compatibility input."""


# Compatibility spellings keep the boundary easy to discover without making
# callers depend on an implementation-specific exception hierarchy.
CapabilityWorkflowSynthesisError = WorkflowSynthesisError
WorkflowSynthesisValidationError = WorkflowSynthesisError


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_PERSISTENCE_WORDS = frozenset(
    {
        "activate",
        "activation",
        "canonical-write",
        "commit",
        "persist",
        "publication",
        "publish",
        "system-version",
        "write-canonical",
    }
)
_FORBIDDEN_RECORD_TYPES = frozenset(
    {
        "action",
        "capability-action",
        "capability_action",
        "legacy-action",
        "legacy_action",
        "source",
        "capability-source",
        "capability_source",
        "workflow",
        "capability-workflow",
        "capability_workflow",
        "legacy-workflow",
        "legacy_workflow",
        "dot-group",
        "dot_group",
        "legacy-dot-group",
        "legacy_dot_group",
    }
)
_FORBIDDEN_ROOT_KEYS = frozenset(
    {
        "action",
        "action_id",
        "action_name",
        "action_authority",
        "source",
        "source_id",
        "source_ref",
        "source_refs",
        "source_authority",
        "workflow",
        "workflow_id",
        "workflow_ref",
        "workflow_refs",
        "workflow_authority",
        "dot_group",
        "dot_group_id",
        "dot_group_ref",
        "category",
        "categories",
    }
)
_FORBIDDEN_ANY_KEYS = frozenset(
    {
        "action",
        "action_id",
        "action_name",
        "action_authority",
        "dot_group",
        "dot_group_id",
        "dot_group_ref",
        "legacy_action",
        "legacy_workflow",
    }
)
_SAFE_DOT_EVIDENCE_KEYS = frozenset(
    {
        "evidence",
        "provenance",
        "lineage",
        "records",
        "observations",
        "source_reference",
        "source_references",
        "source_ids",
    }
)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as error:
        raise WorkflowInputError("Workflow synthesis evidence must be portable JSON") from error


def _digest(value: Any, *, prefix: str, length: int = 32) -> str:
    return f"{prefix}{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()[:length]}"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip())
    return ""


def _key(value: Any) -> str:
    text = str(value).strip().casefold()
    text = re.sub(r"[.!?]+$", "", text)
    return text.replace("-", "_").replace(" ", "_")


def _require_nonblank(value: Any, label: str) -> str:
    text = _text(value)
    if not text:
        raise WorkflowInputError(f"{label} must be a non-blank string")
    return text


def _slug(value: Any, *, prefix: str = "value", max_length: int = 80) -> str:
    """Turn human port/evidence text into a canonical contract id."""

    text = _text(value).casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        text = _digest(str(value), prefix=f"{prefix}-", length=20)
    if not text[0].isdigit() and _ID_RE.fullmatch(text[:128]) is not None:
        return text[:max_length]
    return f"{prefix}-{text}"[:max_length]


def _safe_evidence_id(value: Any) -> str:
    """Keep evidence ids portable without carrying Source/provider identity."""

    text = _text(value)
    if not text:
        return ""
    normal = _slug(text, prefix="evidence")
    lowered = text.casefold()
    if any(token in lowered for token in ("source", "provider", "action", "workflow")):
        return _digest(text, prefix="dot-evidence-", length=20)
    return normal


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({item for item in values if item}, key=lambda item: (item.casefold(), item))


def _id(value: Any, label: str) -> str:
    text = _require_nonblank(value, label)
    if _ID_RE.fullmatch(text) is None:
        raise WorkflowInputError(f"{label} is not a stable id: {text}")
    return text


def _version(value: Any, label: str = "version") -> str:
    text = _require_nonblank(value, label)
    if _VERSION_RE.fullmatch(text) is None:
        raise WorkflowInputError(f"{label} is not a supported version: {text}")
    return text


def _as_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowInputError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _as_sequence(value: Any, label: str) -> list[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise WorkflowInputError(f"{label} must be an ordered list")
    return list(value)


def _reject_legacy_fields(value: Any, *, path: str = "$", root: bool = False) -> None:
    """Reject authority-bearing legacy records without rejecting Dot evidence.

    Canonical Candidate Dots may retain Source provenance inside their evidence
    block.  That is evidence, not a Source input, so Source-shaped keys are
    rejected at the record boundary but retained below the canonical evidence
    boundary.  Action, old Workflow and dot-group hints have no such exception.
    """

    if isinstance(value, Mapping):
        for raw_name, child in value.items():
            name = str(raw_name)
            normal = _key(name)
            if root and normal in _FORBIDDEN_ROOT_KEYS:
                raise WorkflowInputError(f"forbidden synthesis input field: {path}.{name}")
            if normal in _FORBIDDEN_ANY_KEYS:
                raise WorkflowInputError(f"legacy Action/dot_group field is not a synthesis input: {path}.{name}")
            if root and (normal.startswith("source_") or normal.startswith("workflow_")):
                raise WorkflowInputError(f"forbidden synthesis input field: {path}.{name}")
            if not root and normal.startswith("workflow_"):
                raise WorkflowInputError(f"old Workflow field is not a synthesis input: {path}.{name}")
            if not root and normal.startswith("action_"):
                raise WorkflowInputError(f"Action field is not a synthesis input: {path}.{name}")
            if not root and normal.startswith("dot_group"):
                raise WorkflowInputError(f"dot_group field is not a synthesis input: {path}.{name}")
            # Source ids/references are legitimate only as retained Dot
            # evidence; a nested Source record is still rejected.
            if normal in {"record_type", "type"} and isinstance(child, str):
                record_type = _key(child)
                if record_type in _FORBIDDEN_RECORD_TYPES:
                    raise WorkflowInputError(f"forbidden record type at {path}.{name}: {child}")
            child_root = root and normal not in _SAFE_DOT_EVIDENCE_KEYS
            _reject_legacy_fields(child, path=f"{path}.{name}", root=child_root)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_legacy_fields(child, path=f"{path}[{index}]", root=False)


@dataclass(frozen=True)
class _Port:
    name: str
    type: str
    required: bool = True

    @property
    def identity(self) -> str:
        return _key(self.name)


@dataclass(frozen=True)
class _Dot:
    record: dict[str, Any]
    dot_id: str
    version: str
    lifecycle: str
    inputs: tuple[_Port, ...]
    outputs: tuple[_Port, ...]
    preconditions: tuple[str, ...]
    side_effects: tuple[str, ...]
    permissions: tuple[str, ...]
    provider_id: str | None
    provider_reason: str | None
    provider_evidence: tuple[str, ...]
    verification_status: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Boundary:
    values: dict[str, Any]

    def get(self, *names: str, default: Any = None) -> Any:
        for name in names:
            if name in self.values:
                return self.values[name]
        return default


@dataclass(frozen=True)
class _SemanticEvidence:
    evidence_ids: tuple[str, ...]
    outcome: str | None
    outcome_id: str | None
    path: tuple[str, ...]
    coherent: bool
    reusable: bool
    occurrences: int
    signature_ids: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class _Observation:
    path: tuple[str, ...]
    outcome: str | None
    outcome_id: str | None
    signature_id: str
    evidence_ids: tuple[str, ...]
    succeeded: bool


def _port(value: Any, label: str) -> _Port:
    if isinstance(value, str):
        name = _require_nonblank(value, label)
        return _Port(name=name, type="value", required=True)
    item = _as_mapping(value, label)
    name = _require_nonblank(
        item.get("name", item.get("id", item.get("input_id", item.get("output_id")))),
        f"{label}.name",
    )
    port_type = _text(item.get("type", item.get("value_type", "value"))) or "value"
    required = item.get("required", True)
    if not isinstance(required, bool):
        raise WorkflowInputError(f"{label}.required must be boolean")
    return _Port(name=name, type=port_type, required=required)


def _ports(value: Any, label: str, *, default: Sequence[Any] = ()) -> tuple[_Port, ...]:
    raw = default if value is None else value
    if isinstance(raw, (str, bytes, bytearray)):
        raw = [raw]
    if not isinstance(raw, Sequence):
        raise WorkflowInputError(f"{label} must be an ordered list")
    result: list[_Port] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        parsed = _port(item, f"{label}[{index}]")
        if parsed.identity in seen:
            raise WorkflowInputError(f"{label} contains duplicate port: {parsed.name}")
        seen.add(parsed.identity)
        result.append(parsed)
    return tuple(result)


def _text_list(value: Any, label: str, *, default: Sequence[str] = ()) -> tuple[str, ...]:
    raw = default if value is None else value
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Sequence):
        raise WorkflowInputError(f"{label} must be text or a list")
    values = [_require_nonblank(item, f"{label}[{index}]") for index, item in enumerate(raw)]
    return tuple(_unique_sorted(values))


def _flatten_permission(value: Any, prefix: str = "") -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        result: list[str] = []
        for key in sorted(value, key=str.casefold):
            child = value[key]
            label = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(child, bool):
                if child:
                    result.append(label)
            elif child is not None:
                result.extend(_flatten_permission(child, label))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for child in value for item in _flatten_permission(child, prefix)]
    text = _text(value)
    return [f"{prefix}:{text}" if prefix and text else text] if text else []


def _dot_evidence(record: Mapping[str, Any]) -> list[str]:
    result: set[str] = set()

    def collect(value: Any, *, evidence_boundary: bool = False) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normal = _key(key)
                if normal in {"evidence_ids", "evidence_refs", "verification_evidence", "transition_evidence"}:
                    if isinstance(child, str):
                        candidate = child
                    elif isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
                        for item in child:
                            safe = _safe_evidence_id(item)
                            if safe:
                                result.add(safe)
                        candidate = ""
                    else:
                        candidate = ""
                    safe = _safe_evidence_id(candidate)
                    if safe:
                        result.add(safe)
                elif evidence_boundary or normal in {"evidence", "verification", "trial", "system_review", "human_decision", "activation", "coherence", "lineage"}:
                    collect(child, evidence_boundary=True)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                collect(child, evidence_boundary=evidence_boundary)

    collect(record)
    return _unique_sorted(result)


def _normalise_dot(value: Any) -> _Dot:
    record = _as_mapping(value, "Dot")
    _reject_legacy_fields(record, root=True)
    record_type = _key(record.get("record_type"))
    if record_type and record_type != "capability_dot":
        raise WorkflowInputError("Workflow synthesis accepts only capability-dot records")
    if "record_type" in record and record.get("record_version") != 1:
        raise WorkflowInputError("Unsupported capability Dot record version")
    dot_id = _id(record.get("dot_id"), "dot_id")
    version = _version(record.get("version"), "Dot version")
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        state = lifecycle.get("status", lifecycle.get("state"))
    else:
        state = record.get("state", record.get("status"))
    if state not in {"candidate", "active"}:
        raise WorkflowInputError(f"Dot {dot_id} must be Candidate or Active")
    # A canonical record gets the full Dot contract gate.  Compact structural
    # records remain useful to graph callers and are still checked below.
    if record_type == "capability_dot":
        try:
            validated = validate_capability_dot(record)
        except (CapabilityDotError, TypeError, KeyError) as error:
            raise WorkflowInputError(f"Invalid capability Dot {dot_id}") from error
        record = validated
    inputs = _ports(record.get("inputs"), f"Dot {dot_id}.inputs", default=("input",))
    outputs = _ports(record.get("outputs"), f"Dot {dot_id}.outputs", default=("output",))
    preconditions = _text_list(record.get("preconditions"), f"Dot {dot_id}.preconditions")
    side_effects = _text_list(record.get("side_effects"), f"Dot {dot_id}.side_effects")
    for effect in side_effects:
        normal = re.sub(r"[_ ]+", "-", effect.casefold())
        if normal in {"*", "any", "unbounded"} or any(token in normal for token in _PERSISTENCE_WORDS):
            raise WorkflowInputError(f"Dot {dot_id} has unbounded or persistent side effects")
    permissions: list[str] = []
    permissions.extend(_flatten_permission(record.get("permissions")))
    implementations = record.get("implementations", [])
    if isinstance(implementations, Sequence) and not isinstance(implementations, (str, bytes, bytearray)):
        for implementation in implementations:
            if isinstance(implementation, Mapping):
                permissions.extend(_flatten_permission(implementation.get("permissions")))
    provider_id: str | None = None
    provider_reason: str | None = None
    provider_evidence: list[str] = []
    provider_scope = record.get("provider_specific")
    if provider_scope is not None:
        if not isinstance(provider_scope, Mapping):
            raise WorkflowInputError(f"Dot {dot_id}.provider_specific must be an object")
        provider_id = _id(provider_scope.get("provider_id"), f"Dot {dot_id} provider_id")
        reason = provider_scope.get("intrinsic_provider_responsibility")
        if isinstance(reason, Mapping):
            provider_reason = _text(reason.get("reason_code", reason.get("reason", reason.get("outcome")))) or None
            provider_evidence = [_safe_evidence_id(item) for item in (reason.get("evidence_ids") or [])]
        if not provider_reason or not provider_evidence:
            raise WorkflowInputError(f"Dot {dot_id} provider scope lacks intrinsic outcome evidence")
    verification = record.get("verification")
    verification_status = verification.get("status") if isinstance(verification, Mapping) else "unverified"
    evidence_ids = tuple(_dot_evidence(record))
    return _Dot(
        record=record,
        dot_id=dot_id,
        version=version,
        lifecycle=state,
        inputs=inputs,
        outputs=outputs,
        preconditions=preconditions,
        side_effects=side_effects,
        permissions=tuple(_unique_sorted(permissions)),
        provider_id=provider_id,
        provider_reason=provider_reason,
        provider_evidence=tuple(_unique_sorted(provider_evidence)),
        verification_status=verification_status if isinstance(verification_status, str) else "unverified",
        evidence_ids=evidence_ids,
    )


def _dot_values(values: Any) -> list[Any]:
    if values is None:
        return []
    if isinstance(values, Mapping):
        if values.get("record_type") is not None or values.get("dot_id") is not None:
            return [values]
        for name in ("dots", "candidate_dots", "dot_records", "records"):
            if name in values:
                return _dot_values(values[name])
        return [item for item in values.values() if isinstance(item, Mapping)]
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise WorkflowInputError("Dots must be a Dot record, registry, or ordered list")
    return list(values)


def _normalise_dots(values: Any) -> list[_Dot]:
    records = [_normalise_dot(item) for item in _dot_values(values)]
    by_id: dict[str, _Dot] = {}
    for item in records:
        previous = by_id.get(item.dot_id)
        if previous is None:
            by_id[item.dot_id] = item
        elif previous.version != item.version or _canonical(previous.record) != _canonical(item.record):
            raise WorkflowInputError(f"Dot {item.dot_id} has conflicting versions or records")
    return [by_id[key] for key in sorted(by_id)]


def _contract_entries(value: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return global contract fields and per-Dot contract entries."""

    if value is None:
        return {}, []
    if isinstance(value, Mapping):
        if any(key in value for key in ("dot_id", "dot", "dot_ref")):
            return {}, [dict(value)]
        for key in ("boundaries", "contracts", "dot_contracts", "by_dot", "dots"):
            if key in value:
                global_part = {name: child for name, child in value.items() if name != key}
                _, entries = _contract_entries(value[key])
                return global_part, entries
        # An id-indexed mapping is a per-Dot registry only when its values are
        # objects containing a contract field; ordinary global values remain
        # global.
        if value and all(isinstance(child, Mapping) for child in value.values()):
            return {}, [{"dot_id": key, **dict(child)} for key, child in value.items()]
        return dict(value), []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise WorkflowInputError("Contracts must be an object or ordered list")
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise WorkflowInputError(f"contract entry {index} must be an object")
        entries.append(dict(item))
    return {}, entries


def _entry_dot_id(entry: Mapping[str, Any]) -> str | None:
    for key in ("dot_id", "dot", "dot_ref", "id"):
        value = entry.get(key)
        if isinstance(value, Mapping):
            value = value.get("dot_id", value.get("id"))
        text = _text(value)
        if text:
            return text
    return None


def _merge_contracts(dots: Sequence[_Dot], value: Any) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str, str, str]], dict[str, Any]]:
    global_part, entries = _contract_entries(value)
    per_dot: dict[str, dict[str, Any]] = defaultdict(dict)
    bindings: set[tuple[str, str, str, str]] = set()
    for entry in entries:
        dot_id = _entry_dot_id(entry)
        if dot_id is None:
            continue
        per_dot[dot_id].update(copy.deepcopy(entry))
    raw_bindings: list[Any] = []
    raw_bindings.extend(global_part.get("bindings", []) if isinstance(global_part.get("bindings", []), Sequence) else [])
    raw_bindings.extend(global_part.get("compatibility", []) if isinstance(global_part.get("compatibility", []), Sequence) else [])
    for entry in entries:
        raw = entry.get("bindings", entry.get("compatible_with", []))
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            raw_bindings.extend(raw)
    for item in raw_bindings:
        if not isinstance(item, Mapping):
            continue
        left = _text(item.get("from_dot", item.get("left_dot", item.get("producer_dot"))))
        right = _text(item.get("to_dot", item.get("right_dot", item.get("consumer_dot"))))
        output = _text(item.get("output", item.get("from_output", item.get("producer_output"))))
        input_name = _text(item.get("input", item.get("to_input", item.get("consumer_input"))))
        if left and right and output and input_name:
            bindings.add((left, output.casefold(), right, input_name.casefold()))
    return per_dot, bindings, global_part


def _ports_for(dot: _Dot, per_dot: Mapping[str, Mapping[str, Any]], side: str) -> tuple[_Port, ...]:
    value = per_dot.get(dot.dot_id, {}).get(side)
    return _ports(value, f"Dot {dot.dot_id}.{side}", default=getattr(dot, side)) if value is not None else getattr(dot, side)


def _boundary_entries(value: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    global_part: dict[str, Any] = {}
    per_dot: dict[str, dict[str, Any]] = defaultdict(dict)
    if value is None:
        return global_part, per_dot
    _reject_legacy_fields(value, root=True)
    if isinstance(value, Mapping):
        if any(key in value for key in ("dot_id", "dot", "dot_ref")):
            dot_id = _entry_dot_id(value)
            if dot_id:
                per_dot[dot_id].update(copy.deepcopy(dict(value)))
            return global_part, per_dot
        child_key = next((key for key in ("boundaries", "verification_boundaries", "by_dot", "dots") if key in value), None)
        if child_key is not None:
            global_part = {key: copy.deepcopy(child) for key, child in value.items() if key != child_key}
            child = value[child_key]
            if isinstance(child, Mapping):
                for dot_id, item in child.items():
                    if isinstance(item, Mapping):
                        per_dot[str(dot_id)].update(copy.deepcopy(dict(item)))
            elif isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
                for item in child:
                    if isinstance(item, Mapping):
                        dot_id = _entry_dot_id(item)
                        if dot_id:
                            per_dot[dot_id].update(copy.deepcopy(dict(item)))
            return global_part, per_dot
        if value and all(isinstance(child, Mapping) for child in value.values()):
            for dot_id, item in value.items():
                per_dot[str(dot_id)].update(copy.deepcopy(dict(item)))
            return global_part, per_dot
        return copy.deepcopy(dict(value)), per_dot
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            if not isinstance(item, Mapping):
                raise WorkflowInputError("verification boundary entries must be objects")
            dot_id = _entry_dot_id(item)
            if dot_id:
                per_dot[dot_id].update(copy.deepcopy(dict(item)))
            else:
                global_part.update(copy.deepcopy(dict(item)))
        return global_part, per_dot
    raise WorkflowInputError("verification_boundaries must be an object or ordered list")


def _boundary_for(dot: _Dot, global_part: Mapping[str, Any], per_dot: Mapping[str, Mapping[str, Any]]) -> _Boundary:
    values = copy.deepcopy(dict(global_part))
    values.update(copy.deepcopy(dict(per_dot.get(dot.dot_id, {}))))
    return _Boundary(values)


def _normalised_names(value: Any) -> set[str]:
    if value is None:
        return set()
    raw = [value] if isinstance(value, str) else list(value) if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)) else [value]
    result: set[str] = set()
    for item in raw:
        if isinstance(item, Mapping):
            item = item.get("name", item.get("id", item.get("value", item.get("permission"))))
        text = _text(item)
        if text:
            result.add(_key(text))
    return result


def _port_compatible(output: _Port, input_port: _Port, *, binding: bool = False) -> bool:
    if output.type and input_port.type and output.type != "value" and input_port.type != "value" and _key(output.type) != _key(input_port.type):
        return False
    if binding:
        return True
    return output.identity == input_port.identity or _key(output.type) == _key(input_port.type) and output.type != "value"


def _token_set(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if token not in {"the", "a", "an", "one", "is", "are", "present", "within", "declared", "boundary"}}


def _precondition_satisfied(precondition: str, available: Sequence[_Port], external: Sequence[_Port]) -> bool:
    text = _text(precondition)
    tokens = _token_set(text)
    if not tokens or tokens <= {"input", "inputs", "request", "data", "output", "available"}:
        return bool(external) or bool(available)
    for port in (*available, *external):
        name_tokens = _token_set(port.name)
        if port.identity in {_key(text), _key(text.rstrip(".!?"))}:
            return True
        if name_tokens and (name_tokens <= tokens or tokens <= name_tokens):
            return True
        if _key(port.name) in _key(text):
            return True
    return False


def _effect_conflicts(effect: str, boundary: _Boundary) -> str | None:
    normal = _key(effect)
    forbidden = _normalised_names(boundary.get("forbidden_side_effects", "forbidden_effects", default=[]))
    if normal in forbidden or _key(effect) in {
        _key(item)
        for item in (boundary.get("forbidden", default=[]) or [])
        if isinstance(item, str)
    }:
        return f"side effect is forbidden: {effect}"
    allowed_raw = boundary.get("allowed_side_effects", "allowed_effects")
    allowed = _normalised_names(allowed_raw)
    if allowed and "*" not in allowed and normal not in allowed:
        # Exact matching is the deterministic gate.  A boundary can opt into
        # a textual prefix with an explicit ``allowed_prefixes`` list.
        prefixes = {
            _key(item)
            for item in (boundary.get("allowed_prefixes", default=[]) or [])
            if isinstance(item, str)
        }
        if not any(normal.startswith(prefix) for prefix in prefixes):
            return f"side effect is outside the verification boundary: {effect}"
    persistence = re.sub(r"[_ ]+", "-", effect.casefold())
    if any(token in persistence for token in _PERSISTENCE_WORDS):
        return f"persistent side effect cannot enter a Candidate Workflow: {effect}"
    return None


def _permission_conflicts(dot: _Dot, boundary: _Boundary) -> str | None:
    required = {
        variant
        for item in dot.permissions
        for variant in (_key(item), _key(item.rsplit(":", 1)[-1]))
    }
    forbidden = _normalised_names(boundary.get("forbidden_permissions", "denied_permissions", default=[]))
    if required.intersection(forbidden):
        return "Dot permissions include a forbidden permission"
    allowed_raw = boundary.get("allowed_permissions", "permissions")
    allowed = {
        variant
        for item in _normalised_names(allowed_raw)
        for variant in (item, item.rsplit(":", 1)[-1])
    }
    if allowed and "*" not in allowed and not required <= allowed:
        return "Dot permissions exceed the verification boundary"
    return None


def _lifecycle_conflicts(dot: _Dot, boundary: _Boundary) -> str | None:
    allowed = boundary.get("allowed_lifecycle", "lifecycle_states", "lifecycle")
    if isinstance(allowed, Mapping):
        allowed = allowed.get("allowed", allowed.get("states"))
    values = _normalised_names(allowed)
    if values and dot.lifecycle.casefold() not in values:
        return f"Dot lifecycle {dot.lifecycle} is outside the verification boundary"
    if boundary.get("active_only") is True and dot.lifecycle != "active":
        return "verification boundary requires Active Dots"
    if (
        boundary.get("verified_only", "require_verified") is True
        and dot.verification_status != "verified"
    ):
        return "verification boundary requires verified Dots"
    if dot.verification_status == "failed":
        return "Dot verification is failed"
    return None


def _structural_dot_gate(dot: _Dot, boundary: _Boundary) -> list[str]:
    issues: list[str] = []
    lifecycle = _lifecycle_conflicts(dot, boundary)
    if lifecycle:
        issues.append(lifecycle)
    permission = _permission_conflicts(dot, boundary)
    if permission:
        issues.append(permission)
    for effect in dot.side_effects:
        issue = _effect_conflicts(effect, boundary)
        if issue:
            issues.append(issue)
    if boundary.get("platform_independent") is False:
        issues.append("verification boundary does not permit platform-independent responsibility")
    return issues


def _edge_gate(
    left: _Dot,
    right: _Dot,
    left_outputs: Sequence[_Port],
    right_inputs: Sequence[_Port],
    bindings: set[tuple[str, str, str, str]],
    external_inputs: Sequence[_Port],
    available_outputs: Sequence[_Port],
    right_boundary: _Boundary,
) -> tuple[bool, list[str], list[dict[str, str]]]:
    issues: list[str] = []
    selected: list[dict[str, str]] = []
    for target in right_inputs:
        if not target.required:
            continue
        matches: list[_Port] = []
        for output in left_outputs:
            explicit = (left.dot_id, output.identity, right.dot_id, target.identity) in bindings
            if _port_compatible(output, target, binding=explicit):
                matches.append(output)
        if not matches:
            issues.append(f"output-to-input mismatch: {left.dot_id}.{target.name} -> {right.dot_id}.{target.name}")
        else:
            selected.append({"output": matches[0].name, "input": target.name})
    available = tuple(available_outputs) + tuple(left_outputs)
    for precondition in right.preconditions:
        if not _precondition_satisfied(precondition, available, external_inputs):
            issues.append(f"precondition is not satisfied: {right.dot_id}: {precondition}")
    for effect in right.side_effects:
        issue = _effect_conflicts(effect, right_boundary)
        if issue:
            issues.append(issue)
    return not issues, issues, selected


def _path_pairs(path: Sequence[_Dot], port_map: Mapping[str, tuple[tuple[_Port, ...], tuple[_Port, ...]]], bindings: set[tuple[str, str, str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for left, right in zip(path, path[1:], strict=False):
        left_outputs = port_map[left.dot_id][1]
        right_inputs = port_map[right.dot_id][0]
        for target in right_inputs:
            if not target.required:
                continue
            for output in left_outputs:
                if _port_compatible(output, target, binding=(left.dot_id, output.identity, right.dot_id, target.identity) in bindings):
                    result.append({"from_dot": left.dot_id, "output": output.name, "to_dot": right.dot_id, "input": target.name})
                    break
    return result


def _normalise_path(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("dot_id", item.get("id", item.get("ref")))
            if isinstance(item, Mapping):
                item = item.get("dot_id", item.get("id"))
        text = _text(item)
        if text:
            result.append(text)
    return tuple(result)


def _evidence_ids(value: Any) -> list[str]:
    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value) if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)) else [value]
    result: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            item = item.get("evidence_id", item.get("id", item.get("reference")))
        safe = _safe_evidence_id(item)
        if safe:
            result.append(safe)
    return _unique_sorted(result)


def _evidence_field(entry: Mapping[str, Any]) -> Any:
    for key in (
        "evidence_ids",
        "evidence_refs",
        "reuse_evidence_ids",
        "outcome_evidence_ids",
        "verification_evidence_ids",
        "evidence",
    ):
        if key in entry:
            return entry[key]
    return None


def _outcome_text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("outcome", "outcome_name", "success_outcome", "result", "responsibility"):
            if key in value:
                found = _text(value[key])
                if found:
                    return found
        return None
    return _text(value) or None


def _semantic_entries(value: Any) -> list[_SemanticEvidence]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("evidence", "reuse_evidence", "reusable_outcome_evidence", "outcomes", "observations"):
            if key in value and key not in {"evidence_ids", "evidence_refs"}:
                child = value[key]
                entries = _semantic_entries(child)
                if entries:
                    # Global flags/evidence travel into child entries.
                    parent_ids = _evidence_ids(_evidence_field(value))
                    parent_reusable = bool(
                        value.get("reusable", value.get("repeatable", value.get("repeated")))
                    )
                    parent_coherent = value.get("coherent", value.get("coherent_outcome")) is True
                    if parent_ids or parent_reusable or parent_coherent:
                        entries = [
                            _SemanticEvidence(
                                evidence_ids=tuple(_unique_sorted((*entry.evidence_ids, *parent_ids))),
                                outcome=entry.outcome,
                                outcome_id=entry.outcome_id,
                                path=entry.path,
                                coherent=entry.coherent or parent_coherent,
                                reusable=entry.reusable or parent_reusable,
                                occurrences=max(
                                    entry.occurrences,
                                    int(
                                        value.get(
                                            "occurrences",
                                            value.get(
                                                "repeat_count",
                                                value.get("count", value.get("repeated", 0)),
                                            ),
                                        )
                                        or 0
                                    ),
                                ),
                                signature_ids=entry.signature_ids,
                                raw=entry.raw,
                            )
                            for entry in entries
                        ]
                    return entries
        entry = dict(value)
        ids = _evidence_ids(_evidence_field(entry))
        path = _normalise_path(entry.get("dot_ids", entry.get("dot_sequence", entry.get("path", entry.get("steps")))))
        outcome = _outcome_text(entry)
        outcome_id = _text(entry.get("outcome_id", entry.get("result_id"))) or None
        occurrences_raw = entry.get(
            "occurrences",
            entry.get(
                "repeat_count",
                entry.get("count", entry.get("observations", entry.get("repeated", 0))),
            ),
        )
        if isinstance(occurrences_raw, Sequence) and not isinstance(occurrences_raw, (str, bytes, bytearray)):
            occurrences = len(occurrences_raw)
        else:
            try:
                occurrences = int(occurrences_raw or 0)
            except (TypeError, ValueError):
                occurrences = 0
        signatures = _evidence_ids(entry.get("signature_ids", entry.get("signatures")))
        reusable_flag = entry.get("reusable", entry.get("repeatable"))
        reusable = bool(reusable_flag) or occurrences >= 2 or len(signatures) >= 2
        coherent = entry.get("coherent", entry.get("coherent_outcome")) is True or bool(outcome)
        return [
            _SemanticEvidence(
                evidence_ids=tuple(ids),
                outcome=outcome,
                outcome_id=outcome_id,
                path=path,
                coherent=coherent,
                reusable=reusable,
                occurrences=occurrences,
                signature_ids=tuple(signatures),
                raw=entry,
            )
        ]
    if isinstance(value, str):
        return [_SemanticEvidence(( _safe_evidence_id(value),), None, None, (), False, False, 0, (), {"evidence_id": value})]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        result: list[_SemanticEvidence] = []
        for item in value:
            result.extend(_semantic_entries(item))
        return result
    raise WorkflowInputError("reusable outcome evidence must be an object or ordered list")


def _composition_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("signatures", "observed_compositions", "execution_compositions", "observations", "records"):
            if key in value:
                child = value[key]
                if isinstance(child, Mapping) and key == "signatures":
                    return [{"signature": signature, "occurrences": count} for signature, count in child.items()]
                return _composition_items(child)
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    raise WorkflowInputError("observed compositions must be an object or ordered list")


def _normalise_observations(value: Any) -> list[_Observation]:
    result: list[_Observation] = []
    for index, raw in enumerate(_composition_items(value)):
        if not isinstance(raw, Mapping):
            raise WorkflowInputError(f"observed composition {index} must be an object")
        _reject_legacy_fields(raw)
        record_type = _key(raw.get("record_type"))
        if record_type in {"capability_workflow", "workflow", "legacy_workflow"}:
            raise WorkflowInputError("old Workflow records cannot seed Workflow synthesis")
        if raw.get("persistent") is True:
            raise WorkflowInputError("persistent compositions cannot provide synthesis evidence")
        path = _normalise_path(raw.get("dot_ids", raw.get("dot_sequence", raw.get("path"))))
        if not path and isinstance(raw.get("steps"), Sequence):
            path_items: list[str] = []
            for step in raw["steps"]:
                if not isinstance(step, Mapping):
                    continue
                kind = _key(step.get("kind", step.get("ref_type")))
                ref = step.get("ref", step)
                if kind in {"workflow", "capability_workflow"} or (isinstance(ref, Mapping) and ref.get("workflow_id")):
                    raise WorkflowInputError("observed composition Workflow refs cannot seed a Candidate Workflow")
                if kind not in {"dot", "capability_dot", ""}:
                    raise WorkflowInputError(f"unsupported observed composition step kind: {kind}")
                if isinstance(ref, Mapping):
                    dot_id = _text(ref.get("dot_id", ref.get("id")))
                else:
                    dot_id = _text(ref)
                if dot_id:
                    path_items.append(dot_id)
            path = tuple(path_items)
        signature_raw = raw.get("signature", raw.get("composition_signature", raw.get("signature_id")))
        if isinstance(signature_raw, Mapping):
            signature_id = _digest(signature_raw, prefix="composition-signature-")
        elif _text(signature_raw):
            signature_id = _safe_evidence_id(signature_raw)
        else:
            signature_id = _digest({"path": path, "inputs": raw.get("inputs"), "outputs": raw.get("outputs")}, prefix="composition-signature-")
        outcome = _outcome_text(raw)
        outcome_id = _text(raw.get("outcome_id", raw.get("result_id"))) or None
        evidence_ids = _evidence_ids(raw.get("evidence_ids", raw.get("evidence_refs", raw.get("evidence"))))
        status = _key(raw.get("status"))
        succeeded = raw.get("succeeded") is not False and status not in {"failed", "failure"}
        occurrences = raw.get("occurrences", raw.get("count", 1))
        try:
            count = max(1, int(occurrences))
        except (TypeError, ValueError):
            count = 1
        for _occurrence in range(count):
            result.append(
                _Observation(
                    path=path,
                    outcome=outcome,
                    outcome_id=outcome_id,
                    # ``occurrences`` describes repeated observations of one
                    # signature; retaining the same id lets the reuse gate
                    # count those repetitions deterministically.
                    signature_id=signature_id,
                    evidence_ids=tuple(evidence_ids),
                    succeeded=succeeded,
                )
            )
    return result


def _semantic_for_path(entries: Sequence[_SemanticEvidence], path: tuple[str, ...]) -> list[_SemanticEvidence]:
    applicable = [entry for entry in entries if not entry.path or entry.path == path]
    return applicable


def _observation_for_path(observations: Sequence[_Observation], path: tuple[str, ...]) -> list[_Observation]:
    return [item for item in observations if item.path == path and item.succeeded]


def _explicit_provider_semantics(value: Any, outcome: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if "outcomes" in value and isinstance(value["outcomes"], Mapping):
            selected = value["outcomes"].get(outcome or "")
            return _explicit_provider_semantics(selected, outcome)
        if value.get("is_outcome") is True and _text(value.get("outcome")):
            return {"is_outcome": True, "outcome": _text(value["outcome"])}
    return None


def _provider_semantics_for_path(
    dots: Sequence[_Dot],
    semantic_entries: Sequence[_SemanticEvidence],
    path: tuple[str, ...],
    option: Any,
    outcome: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    providers = sorted({dot.provider_id for dot in dots if dot.provider_id})
    if not providers:
        return None, None, []
    semantics = _explicit_provider_semantics(option, outcome)
    path_entries = _semantic_for_path(semantic_entries, path)
    if semantics is None:
        for entry in path_entries:
            candidate = _explicit_provider_semantics(entry.raw.get("provider_semantics"), outcome)
            if candidate is not None:
                semantics = candidate
                break
    if semantics is None:
        return None, None, ["provider-specific Dot lacks explicit provider outcome semantics"]
    if outcome and _key(semantics["outcome"]) != _key(outcome):
        return None, None, ["provider semantics outcome conflicts with reusable outcome"]
    if len(providers) != 1:
        return None, None, ["provider-specific Dots name conflicting providers"]
    evidence = _unique_sorted(
        item
        for dot in dots
        for item in (*dot.provider_evidence, *dot.evidence_ids)
        if item
    )
    provider_specific = {
        "provider_id": providers[0],
        "intrinsic_provider_responsibility": {
            "reason_code": "provider-semantics-is-explicit-workflow-outcome",
            "evidence_ids": evidence or ["provider-outcome-evidence"],
        },
    }
    return semantics, provider_specific, []


def _outcome_identity(entry: _SemanticEvidence | _Observation | None, final_outputs: Sequence[_Port], path: Sequence[_Dot]) -> tuple[str, str]:
    if entry is not None:
        outcome = _outcome_text(entry.outcome) or ""
        outcome_id = _text(entry.outcome_id) or ""
        if outcome or outcome_id:
            return (_key(outcome_id or outcome), outcome or outcome_id)
    output_names = tuple(sorted(port.identity for port in final_outputs))
    fallback = " and ".join(port.name for port in final_outputs) or "bounded result"
    return (_digest({"outputs": output_names, "path": [dot.dot_id for dot in path]}, prefix="outcome-", length=24), fallback)


def _path_outputs(path: Sequence[_Dot], port_map: Mapping[str, tuple[tuple[_Port, ...], tuple[_Port, ...]]]) -> tuple[_Port, ...]:
    consumed: set[str] = set()
    for left, right in zip(path, path[1:], strict=False):
        for target in port_map[right.dot_id][0]:
            for output in port_map[left.dot_id][1]:
                if _port_compatible(output, target):
                    consumed.add(output.identity)
                    break
    last_outputs = port_map[path[-1].dot_id][1]
    result = [port for port in last_outputs if port.identity not in consumed]
    return tuple(result or last_outputs)


def _path_inputs(path: Sequence[_Dot], port_map: Mapping[str, tuple[tuple[_Port, ...], tuple[_Port, ...]]]) -> tuple[_Port, ...]:
    produced: set[str] = set()
    result: list[_Port] = []
    for index, dot in enumerate(path):
        inputs = port_map[dot.dot_id][0]
        if index:
            previous = port_map[path[index - 1].dot_id][1]
            for output in previous:
                produced.add(output.identity)
        for port in inputs:
            if port.required and port.identity not in produced:
                result.append(port)
    by_id = {port.identity: port for port in result}
    return tuple(by_id[key] for key in sorted(by_id))


def _contract_items_from_ports(ports: Sequence[_Port]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for port in ports:
        result.append({"id": _slug(port.name, prefix="port"), "type": port.type or "value", "required": port.required})
    return result


def _maximal_paths(
    dots: Sequence[_Dot],
    port_map: Mapping[str, tuple[tuple[_Port, ...], tuple[_Port, ...]]],
    bindings: set[tuple[str, str, str, str]],
    boundaries: Mapping[str, _Boundary],
    global_inputs: Sequence[_Port],
) -> tuple[list[tuple[_Dot, ...]], list[dict[str, Any]]]:
    by_id = {dot.dot_id: dot for dot in dots}
    adjacency: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, int] = {dot.dot_id: 0 for dot in dots}
    conflicts: list[dict[str, Any]] = []
    for left in dots:
        for right in dots:
            if left.dot_id == right.dot_id:
                continue
            ok, issues, _ = _edge_gate(
                left,
                right,
                port_map[left.dot_id][1],
                port_map[right.dot_id][0],
                bindings,
                global_inputs,
                port_map[left.dot_id][1],
                boundaries[right.dot_id],
            )
            if ok:
                adjacency[left.dot_id].append(right.dot_id)
                incoming[right.dot_id] += 1
            elif any(item.startswith("output-to-input") or item.startswith("precondition") for item in issues):
                conflicts.append(
                    {
                        "kind": "compatibility",
                        "left_dot": left.dot_id,
                        "right_dot": right.dot_id,
                        "reasons": sorted(set(issues)),
                    }
                )
    for key in adjacency:
        adjacency[key] = sorted(set(adjacency[key]))
    roots = sorted(dot.dot_id for dot in dots if incoming[dot.dot_id] == 0)
    if not roots:
        roots = sorted(by_id)

    paths: set[tuple[str, ...]] = set()

    def visit(current: str, path: tuple[str, ...]) -> None:
        successors = [item for item in adjacency.get(current, []) if item not in path]
        if not successors:
            paths.add(path)
            return
        extended = False
        for successor in successors:
            candidate_path = path + (successor,)
            candidate_available: list[_Port] = []
            for item in candidate_path[:-1]:
                candidate_available.extend(port_map[item][1])
            target = by_id[successor]
            if all(_precondition_satisfied(precondition, candidate_available, global_inputs) for precondition in target.preconditions):
                visit(successor, candidate_path)
                extended = True
            else:
                conflicts.append(
                    {
                        "kind": "compatibility",
                        "left_dot": current,
                        "right_dot": successor,
                        "reasons": ["precondition is not satisfied along the candidate path"],
                    }
                )
        if not extended:
            paths.add(path)

    for root in roots:
        visit(root, (root,))
    # If a disconnected Dot has no edge, retaining its one-Dot path lets
    # explicit semantic evidence decide whether it is a reusable Workflow.
    if not paths:
        paths = {(dot.dot_id,) for dot in dots}
    return [tuple(by_id[item] for item in path) for path in sorted(paths)], conflicts


def _observed_paths(
    observations: Sequence[_Observation], by_id: Mapping[str, _Dot]
) -> tuple[list[tuple[_Dot, ...]], list[dict[str, Any]]]:
    paths: set[tuple[str, ...]] = set()
    conflicts: list[dict[str, Any]] = []
    for observation in observations:
        if not observation.path:
            continue
        unknown = [item for item in observation.path if item not in by_id]
        if unknown:
            conflicts.append(
                {
                    "kind": "compatibility",
                    "path": list(observation.path),
                    "reasons": [f"observed composition names unknown Dot: {item}" for item in unknown],
                }
            )
            continue
        if len(set(observation.path)) != len(observation.path):
            conflicts.append({"kind": "compatibility", "path": list(observation.path), "reasons": ["composition path repeats a Dot"]})
            continue
        paths.add(tuple(observation.path))
    return [tuple(by_id[item] for item in path) for path in sorted(paths)], conflicts


def _reusable_proof(
    entries: Sequence[_SemanticEvidence],
    observations: Sequence[_Observation],
    path: tuple[str, ...],
) -> tuple[bool, list[_SemanticEvidence], list[_Observation], list[str]]:
    semantic = _semantic_for_path(entries, path)
    observed = _observation_for_path(observations, path)
    signature_counts = Counter(item.signature_id for item in observed)
    repeated_observations = [item for item in observed if signature_counts[item.signature_id] >= 2]
    evidence_ids = _unique_sorted(
        item
        for entry in semantic
        for item in entry.evidence_ids
    )
    evidence_ids.extend(
        item for observation in observed for item in observation.evidence_ids
    )
    evidence_ids = _unique_sorted(evidence_ids)
    explicit_reuse = any(entry.reusable and entry.evidence_ids for entry in semantic)
    # Distinct observation ids may represent separate runs of the same
    # composition shape; path repetition is therefore evidence as well as an
    # identical signature repeated twice.  One observation remains a one-off.
    repeated = len(observed) >= 2 or bool(repeated_observations) or any(
        entry.reusable and entry.occurrences >= 2 for entry in semantic
    )
    if not evidence_ids and repeated:
        evidence_ids = [_digest({"path": path, "repeated": True}, prefix="reuse-evidence-")]
    coherent = any(entry.coherent and (entry.outcome or entry.outcome_id) for entry in semantic)
    return coherent and (explicit_reuse or repeated), semantic, observed, evidence_ids


def _conflict_records(conflicts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = [copy.deepcopy(dict(item)) for item in conflicts]
    result.sort(key=lambda item: _canonical(item))
    return result


def _candidate_from_path(
    path: tuple[_Dot, ...],
    *,
    outcome: str,
    outcome_id: str,
    semantic: Sequence[_SemanticEvidence],
    observations: Sequence[_Observation],
    evidence_ids: Sequence[str],
    port_map: Mapping[str, tuple[tuple[_Port, ...], tuple[_Port, ...]]],
    bindings: set[tuple[str, str, str, str]],
    boundaries: Mapping[str, _Boundary],
    provider_semantics: Any,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    path_ids = tuple(dot.dot_id for dot in path)
    provider_outcome, provider_specific, provider_issues = _provider_semantics_for_path(
        path, semantic, path_ids, provider_semantics, outcome
    )
    if provider_issues:
        return None, [{"kind": "semantic", "path": list(path_ids), "reasons": provider_issues}]
    outputs = _path_outputs(path, port_map)
    inputs = _path_inputs(path, port_map)
    if not inputs:
        inputs = port_map[path[0].dot_id][0]
    binding_records = _path_pairs(path, port_map, bindings)
    all_effects = _unique_sorted(effect for dot in path for effect in dot.side_effects)
    all_permissions = _unique_sorted(permission for dot in path for permission in dot.permissions)
    dot_evidence = _unique_sorted(item for dot in path for item in dot.evidence_ids)
    all_evidence = _unique_sorted((*evidence_ids, *dot_evidence))
    if not all_evidence:
        all_evidence = [_digest({"path": path_ids, "outcome": outcome}, prefix="workflow-evidence-")]
    outcome_key = _slug(outcome_id or outcome, prefix="outcome")
    identity = {
        "outcome_id": outcome_id or outcome_key,
        "outcome": outcome,
        "dots": [{"dot_id": dot.dot_id, "version": dot.version, "lifecycle": dot.lifecycle} for dot in path],
        "inputs": _contract_items_from_ports(inputs),
        "outputs": _contract_items_from_ports(outputs),
        "bindings": binding_records,
        "provider_semantics": provider_outcome,
        "provider_specific": provider_specific,
    }
    workflow_id = _digest(identity, prefix="workflow-")
    workflow_inputs = _contract_items_from_ports(inputs)
    workflow_outputs = _contract_items_from_ports(outputs)
    verification_refs = _unique_sorted(
        item
        for entry in semantic
        for item in entry.evidence_ids
    )
    verification_refs.extend(item for observation in observations for item in observation.evidence_ids)
    verification_refs = _unique_sorted((*verification_refs, *all_evidence))
    boundary_evidence = _unique_sorted(
        item
        for dot in path
        for item in _evidence_ids(
            boundaries[dot.dot_id].get(
                "evidence_ids",
                boundaries[dot.dot_id].get(
                    "verification_evidence",
                    boundaries[dot.dot_id].get("required_evidence_ids"),
                ),
            )
        )
    )
    verification_refs = _unique_sorted((*verification_refs, *boundary_evidence))
    provenance = {
        "evidence_refs": verification_refs,
        "dot_evidence_refs": dot_evidence,
        "observed_composition_refs": _unique_sorted(item.signature_id for item in observations),
        "verification_boundary_refs": boundary_evidence,
        "reuse_observation_count": len(observations),
    }
    side_effect_contract: dict[str, Any] = {
        "allowed": all_effects or ["no additional side effect beyond Dot contracts"],
        "forbidden": ["persistence", "activation", "publication"],
    }
    if all_permissions:
        side_effect_contract["permissions"] = all_permissions
    success_contract = {
        "outcome": outcome,
        "checks": [
            "all ordered Dot contracts complete",
            "declared output contract is satisfied",
            "reusable outcome evidence remains attached",
        ],
        "evidence_refs": verification_refs,
    }
    match_contract = {
        "outcome": outcome,
        "outcome_id": outcome_key,
        "required_signals": [_slug(port.name, prefix="signal") for port in inputs],
        "dot_sequence": list(path_ids),
        "bindings": binding_records,
    }
    recovery = {
        "strategy": "Stop at the failed Dot, retain evidence, and restore the prior bounded composition.",
        "evidence_refs": verification_refs,
    }
    refs = [
        {
            "sequence": index,
            "dot_id": dot.dot_id,
            "version": dot.version,
            "lifecycle": dot.lifecycle,
        }
        for index, dot in enumerate(path, start=1)
    ]
    name = outcome.rstrip(".!?")
    try:
        candidate = build_workflow(
            workflow_id=workflow_id,
            version="1.0.0",
            human_name=name,
            match_contract=match_contract,
            inputs=workflow_inputs,
            outputs=workflow_outputs,
            dot_refs=refs,
            success_contract=success_contract,
            side_effect_contract=side_effect_contract,
            recovery=recovery,
            provenance=provenance,
            status="candidate",
            provider_semantics=provider_outcome,
            provider_specific=provider_specific,
        )
    except Exception as error:
        return None, [{"kind": "semantic", "path": list(path_ids), "reasons": [f"candidate Workflow failed canonical contract: {error}"]}]
    return candidate, []


def _option(options: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in options:
            return options[name]
    return None


def synthesize_candidate_workflows(
    dots: Any = None,
    contracts: Any = None,
    verification_boundaries: Any = None,
    reusable_outcome_evidence: Any = None,
    observed_compositions: Any = None,
    **options: Any,
) -> dict[str, Any]:
    """Synthesize deterministic inactive Candidate Workflows from Dot graphs.

    ``dots`` may be one canonical Dot, a list, or an id-indexed registry.
    The remaining arguments intentionally accept a few descriptive aliases so
    extraction and integration engines can pass their portable evidence
    without becoming coupled to this module.  The returned record contains
    ``candidates``, ``conflicts`` and ``rejected``; no state is persisted.
    """

    if dots is None:
        dots = _option(options, "candidate_dots", "dot_records", "dot_graph", "dot_graphs")
    if contracts is None:
        contracts = _option(options, "output_input_contracts", "input_output_contracts", "contracts")
    if verification_boundaries is None:
        verification_boundaries = _option(options, "verification_boundary", "boundaries")
    if reusable_outcome_evidence is None:
        reusable_outcome_evidence = _option(options, "reuse_evidence", "outcome_evidence", "reusable_evidence")
    if observed_compositions is None:
        observed_compositions = _option(options, "execution_compositions", "composition_signatures", "observed_execution_compositions")
    provider_semantics = _option(options, "provider_semantics", "workflow_provider_semantics")
    if dots is None:
        raise WorkflowInputError("Candidate/Active Dots are required")
    _reject_legacy_fields(contracts, root=True)
    _reject_legacy_fields(reusable_outcome_evidence, root=True)
    _reject_legacy_fields(observed_compositions, root=True)
    normalised_dots = _normalise_dots(dots)
    if not normalised_dots:
        raise WorkflowInputError("At least one Candidate or Active Dot is required")
    per_dot_contracts, bindings, global_contract = _merge_contracts(normalised_dots, contracts)
    boundary_global, boundary_per_dot = _boundary_entries(verification_boundaries)
    boundaries = {
        dot.dot_id: _boundary_for(dot, boundary_global, boundary_per_dot)
        for dot in normalised_dots
    }
    dot_by_id = {dot.dot_id: dot for dot in normalised_dots}
    port_map = {
        dot.dot_id: (
            _ports_for(dot, per_dot_contracts, "inputs"),
            _ports_for(dot, per_dot_contracts, "outputs"),
        )
        for dot in normalised_dots
    }
    global_inputs = _ports(global_contract.get("inputs"), "workflow inputs", default=()) if global_contract.get("inputs") is not None else ()
    semantic_entries = _semantic_entries(reusable_outcome_evidence)
    observations = _normalise_observations(observed_compositions)
    conflicts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for dot in normalised_dots:
        issues = _structural_dot_gate(dot, boundaries[dot.dot_id])
        if issues:
            rejected.append({"dot_id": dot.dot_id, "kind": "compatibility", "reasons": sorted(set(issues))})
    rejected_ids = {item["dot_id"] for item in rejected}
    usable_dots = [dot for dot in normalised_dots if dot.dot_id not in rejected_ids]
    usable_port_map = {key: value for key, value in port_map.items() if key not in rejected_ids}
    usable_boundaries = {key: value for key, value in boundaries.items() if key not in rejected_ids}
    # A global outcome claim cannot safely be reattached to a partial graph
    # after one of its Dots fails the structural gate.  Path-scoped evidence
    # may still identify an independent valid outcome, so that case remains
    # eligible below.
    global_evidence_blocked = bool(rejected_ids) and any(
        not entry.path for entry in semantic_entries
    ) and not observations
    if not usable_dots or global_evidence_blocked:
        return {
            "record_type": SYNTHESIS_RECORD_TYPE,
            "record_version": SYNTHESIS_RECORD_VERSION,
            "candidates": [],
            "candidate_workflows": [],
            "workflows": [],
            "conflicts": _conflict_records(conflicts),
            "rejected": sorted(rejected, key=_canonical),
        }
    observed_paths, observed_conflicts = _observed_paths(observations, {dot.dot_id: dot for dot in usable_dots})
    conflicts.extend(observed_conflicts)
    if observed_paths:
        paths = observed_paths
        # Even observed paths must pass the deterministic compatibility gate.
        valid_paths: list[tuple[_Dot, ...]] = []
        for path in paths:
            path_issues: list[str] = []
            available: list[_Port] = list(global_inputs)
            for index, dot in enumerate(path):
                if index:
                    left = path[index - 1]
                    ok, issues, _ = _edge_gate(
                        left,
                        dot,
                        usable_port_map[left.dot_id][1],
                        usable_port_map[dot.dot_id][0],
                        bindings,
                        global_inputs,
                        available,
                        usable_boundaries[dot.dot_id],
                    )
                    if not ok:
                        path_issues.extend(issues)
                available.extend(usable_port_map[dot.dot_id][1])
                path_issues.extend(_structural_dot_gate(dot, usable_boundaries[dot.dot_id]))
            if path_issues:
                conflicts.append({"kind": "compatibility", "path": list(dot.dot_id for dot in path), "reasons": sorted(set(path_issues))})
            else:
                valid_paths.append(path)
        paths = valid_paths
    else:
        paths, path_conflicts = _maximal_paths(usable_dots, usable_port_map, bindings, usable_boundaries, global_inputs)
        conflicts.extend(path_conflicts)
    if not paths:
        return {
            "record_type": SYNTHESIS_RECORD_TYPE,
            "record_version": SYNTHESIS_RECORD_VERSION,
            "candidates": [],
            "candidate_workflows": [],
            "workflows": [],
            "conflicts": _conflict_records(conflicts),
            "rejected": sorted(rejected, key=_canonical),
        }
    # A path that names a Dot outside the structural set is never semantic
    # evidence; keeping it in conflicts makes the unresolved boundary visible.
    candidates_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    candidate_evidence: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_paths: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    for path in paths:
        path_ids = tuple(dot.dot_id for dot in path)
        reusable, semantic, observed, reuse_ids = _reusable_proof(semantic_entries, observations, path_ids)
        if not reusable:
            rejected.append({
                "kind": "one-off",
                "path": list(path_ids),
                "reasons": ["coherent reusable outcome and reuse evidence are required"],
            })
            continue
        outcome_entries = [entry for entry in semantic if entry.outcome or entry.outcome_id]
        outcome_entries.extend(item for item in observed if item.outcome or item.outcome_id)
        if not outcome_entries:
            # Reuse evidence without a named outcome is not enough to persist a
            # Workflow: guessing from a graph would create every possible path.
            rejected.append({
                "kind": "semantic",
                "path": list(path_ids),
                "reasons": ["reusable evidence does not name a coherent outcome"],
            })
            continue
        outcome_keys = {_outcome_identity(entry, _path_outputs(path, usable_port_map), path) for entry in outcome_entries}
        if len(outcome_keys) > 1:
            conflicts.append({
                "kind": "semantic",
                "path": list(path_ids),
                "reasons": ["unresolved semantic conflict: multiple outcome identities"],
                "outcomes": sorted(key[0] for key in outcome_keys),
            })
            continue
        outcome_key, outcome = sorted(outcome_keys)[0]
        outcome_id = next((entry.outcome_id for entry in outcome_entries if entry.outcome_id), outcome_key)
        group_key = (outcome_key, outcome_id or outcome)
        candidate_paths[group_key].add(path_ids)
        candidate_evidence[group_key].update(reuse_ids)
        # Keep all meaningful evidence, but select one canonical path below.
        candidate_evidence[group_key].update(item for entry in semantic for item in entry.evidence_ids)
        candidate_evidence[group_key].update(item for item in observed for item in item.evidence_ids)
    for group_key in sorted(candidate_paths):
        chosen_path_ids = sorted(candidate_paths[group_key])[0]
        chosen_path = tuple(dot_by_id[item] for item in chosen_path_ids)
        path_semantic = _semantic_for_path(semantic_entries, chosen_path_ids)
        path_observed = _observation_for_path(observations, chosen_path_ids)
        candidate, candidate_conflicts = _candidate_from_path(
            chosen_path,
            outcome=next((entry.outcome for entry in path_semantic if entry.outcome), group_key[1]),
            outcome_id=next((entry.outcome_id for entry in path_semantic if entry.outcome_id), group_key[0]),
            semantic=path_semantic,
            observations=path_observed,
            evidence_ids=sorted(candidate_evidence[group_key]),
            port_map=usable_port_map,
            bindings=bindings,
            boundaries=usable_boundaries,
            provider_semantics=provider_semantics,
        )
        conflicts.extend(candidate_conflicts)
        if candidate is None:
            continue
        candidates_by_key[group_key] = candidate
    candidates = [candidates_by_key[key] for key in sorted(candidates_by_key)]
    return {
        "record_type": SYNTHESIS_RECORD_TYPE,
        "record_version": SYNTHESIS_RECORD_VERSION,
        "candidates": copy.deepcopy(candidates),
        "candidate_workflows": copy.deepcopy(candidates),
        "workflows": copy.deepcopy(candidates),
        "conflicts": _conflict_records(conflicts),
        "rejected": sorted(rejected, key=_canonical),
    }


def synthesize_candidate_workflow(dots: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return exactly one Candidate Workflow or fail closed on ambiguity."""

    result = synthesize_candidate_workflows(dots, *args, **kwargs)
    candidates = result["candidates"]
    if len(candidates) != 1:
        raise WorkflowSynthesisError(
            f"Expected exactly one Candidate Workflow; synthesized {len(candidates)}"
        )
    return copy.deepcopy(candidates[0])


# British spellings and descriptive aliases mirror the neighbouring genesis
# modules while keeping one implementation and one deterministic result shape.
synthesise_candidate_workflows = synthesize_candidate_workflows
synthesise_candidate_workflow = synthesize_candidate_workflow
compile_candidate_workflows = synthesize_candidate_workflows
build_candidate_workflows = synthesize_candidate_workflows
build_candidate_workflow = synthesize_candidate_workflow
synthesize_workflows = synthesize_candidate_workflows
synthesise_workflows = synthesize_candidate_workflows
synthesize_workflow = synthesize_candidate_workflow
synthesise_workflow = synthesize_candidate_workflow


__all__ = [
    "CapabilityWorkflowSynthesisError",
    "SYNTHESIS_RECORD_TYPE",
    "SYNTHESIS_RECORD_VERSION",
    "WorkflowCompatibilityError",
    "WorkflowInputError",
    "WorkflowSynthesisError",
    "WorkflowSynthesisValidationError",
    "build_candidate_workflow",
    "build_candidate_workflows",
    "compile_candidate_workflows",
    "synthesise_candidate_workflow",
    "synthesise_candidate_workflows",
    "synthesise_workflow",
    "synthesise_workflows",
    "synthesize_candidate_workflow",
    "synthesize_candidate_workflows",
    "synthesize_workflow",
    "synthesize_workflows",
]
