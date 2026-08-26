"""Runtime resolution and bounded recovery for the canonical capability graph.

The runtime is intentionally a small state machine, not another registry.  It
matches a familiar Action, chooses an active Workflow, resolves each Dot to a
verified Implementation, executes bounded caller-supplied implementations,
verifies every Dot output, and emits compact evidence.  The normal resolver is
pure and never writes Workplace state, calls a provider, retrieves a Source,
induces an Action, synthesises a Workflow, or activates a record.

The public records returned by this module are detached dictionaries.  A
caller may hand a returned Execution Composition or candidate trial request to
the appropriate Workplace boundary.  ``recover_missing_capability`` is the one
explicit exception: it can persist one inactive Candidate Dot beneath a
caller-supplied Workplace root after separately validating exact persistence
and execution authorities.  Candidate execution authority remains an expiring,
project/task-local permission to try a Dot; it is never persistence authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fractal.capability_action import validate_action
from fractal.capability_dot import (
    validate_candidate_execution,
    validate_candidate_execution_authority,
    validate_capability_dot,
)
from fractal.capability_extraction import extract_responsibilities
from fractal.capability_integration import synthesize_candidate_dot
from fractal.capability_source import source_only, validate_source, validate_source_catalogue
from fractal.capability_workflow import (
    compose_execution,
    validate_execution_composition,
    validate_workflow,
)

RUNTIME_RECORD_TYPE = "capability-runtime-resolution"
RUNTIME_RECORD_VERSION = 1
EXECUTION_RECORD_TYPE = "capability-runtime-execution"
EXECUTION_RECORD_VERSION = 1
EVIDENCE_RECORD_TYPE = "capability-runtime-evidence"
EVIDENCE_RECORD_VERSION = 1
ONBOARDING_RECORD_TYPE = "capability-runtime-onboarding"
ONBOARDING_RECORD_VERSION = 1
RECOVERY_RECORD_TYPE = "capability-runtime-missing-capability-recovery"
RECOVERY_RECORD_VERSION = 1
PERSISTENCE_AUTHORITY_RECORD_TYPE = "workplace-candidate-persistence-authority"
PERSISTENCE_AUTHORITY_RECORD_VERSION = 1

EXACT = "exact"
PARTIAL = "partial"
MISSING_WORKFLOW = "missing-workflow"
UNAVAILABLE = "unavailable"
MISSING_CAPABILITY = "missing-capability"
ROUTE_STATES = frozenset({EXACT, PARTIAL, MISSING_WORKFLOW, UNAVAILABLE, MISSING_CAPABILITY})

# ``missing`` is the user-surface route name used by the Action contract.  A
# more specific state is retained in the runtime record so System Review can
# distinguish a missing reusable Workflow from a missing Dot capability.
ROUTE_STATE_ALIASES = {
    "missing": MISSING_WORKFLOW,
    "missing_workflow": MISSING_WORKFLOW,
    "missing-capability": MISSING_CAPABILITY,
    "missing_capability": MISSING_CAPABILITY,
}

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$"
)
_SECRET = re.compile(
    r"(?:token|password|secret|api[_-]?key|credential|authorization|private[_-]?key)",
    re.IGNORECASE,
)

_FILTERS = (
    "availability",
    "permission",
    "dependencies",
    "input_output_compatibility",
    "platform_compatibility",
    "verified",
)
_RANK_FIELDS = (
    "artifact",
    "provider",
    "workplace_preference",
    "quality",
    "cost",
    "speed",
    "recovery",
)


class CapabilityRuntimeError(ValueError):
    """Base error for malformed runtime inputs or receipts."""


class RuntimeValidationError(CapabilityRuntimeError):
    """Raised when a canonical runtime boundary is invalid."""


class RuntimeResolutionError(CapabilityRuntimeError):
    """Raised when a requested route cannot be resolved safely."""


class ImplementationResolutionError(RuntimeResolutionError):
    """Raised when a caller asks for a direct implementation but none fits."""


class CandidateExecutionScopeError(RuntimeResolutionError):
    """Raised when candidate execution authority does not name this exact task."""


class RuntimeExecutionError(CapabilityRuntimeError):
    """Raised when a blocked plan is asked to execute or a receipt is malformed."""


class RuntimeVerificationError(RuntimeExecutionError):
    """Raised for invalid output verification input."""


class CandidatePersistenceAuthorityError(CapabilityRuntimeError):
    """Raised when a Candidate Dot Workplace write lacks exact authority."""


class WorkplaceCandidateStorageError(CapabilityRuntimeError):
    """Raised when a Candidate Dot cannot be atomically stored and read back."""


# Compatibility spellings keep the boundary discoverable to callers that use
# the names from the neighbouring capability modules.
CapabilityResolutionError = RuntimeResolutionError
CapabilityRuntimeValidationError = RuntimeValidationError
ExecutionAuthorityError = CandidateExecutionScopeError
RuntimeErrorBase = CapabilityRuntimeError


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _id(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    if _ID.fullmatch(text) is None:
        raise RuntimeValidationError(f"{label} is not a stable identifier: {text!r}")
    return text


def _version(value: Any, label: str = "version") -> str:
    text = _nonblank(value, label)
    if _VERSION.fullmatch(text) is None:
        raise RuntimeValidationError(f"{label} is not a supported version: {text!r}")
    return text


def _records(values: Any, identity: str, label: str) -> list[dict[str, Any]]:
    """Normalise a list or id-indexed mapping without changing the input."""

    if values is None:
        return []
    if isinstance(values, Mapping):
        if values.get(identity) is not None:
            return [_copy(dict(values))]
        result: list[dict[str, Any]] = []
        for key, raw in values.items():
            if not isinstance(raw, Mapping):
                continue
            item = _copy(dict(raw))
            if item.get(identity) is None and isinstance(key, str):
                item[identity] = key
            result.append(item)
        return result
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        result = []
        for item in values:
            if not isinstance(item, Mapping):
                raise RuntimeValidationError(f"{label} entries must be objects")
            result.append(_copy(dict(item)))
        return result
    raise RuntimeValidationError(f"{label} must be an object, list, or id-indexed mapping")


def _record_state(record: Mapping[str, Any]) -> str | None:
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        state = lifecycle.get("status", lifecycle.get("state"))
        if isinstance(state, str):
            return state
    state = record.get("status", record.get("state"))
    return state if isinstance(state, str) else None


def _lookup(
    records: Sequence[Mapping[str, Any]],
    identity: str,
    identifier: str,
    version: str | None = None,
) -> dict[str, Any] | None:
    matches = [
        item
        for item in records
        if item.get(identity) == identifier and (version is None or item.get("version") == version)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeValidationError(
            f"Duplicate {identity} record: {identifier}" + (f"@{version}" if version else "")
        )
    return _copy(dict(matches[0]))


def _ref(record: Mapping[str, Any], kind: str, *, include_provider: bool = False) -> dict[str, Any]:
    identity = {
        "action": "action_id",
        "workflow": "workflow_id",
        "dot": "dot_id",
        "implementation": "implementation_id",
    }.get(kind)
    if identity is None:
        raise RuntimeValidationError(f"Unsupported reference kind: {kind}")
    identifier = record.get(identity, record.get("id"))
    result = {
        identity: _id(identifier, f"{kind} id"),
        "version": _version(record.get("version"), f"{kind} version"),
    }
    if include_provider and record.get("provider") is not None:
        provider = record["provider"]
        if isinstance(provider, Mapping):
            provider = provider.get("provider_id", provider.get("id"))
        result["provider"] = _nonblank(provider, "implementation provider")
    return result


def _ref_from_mapping(value: Any, kind: str) -> dict[str, Any]:
    if isinstance(value, str):
        identifier, separator, version = value.rpartition("@")
        if not separator:
            raise RuntimeValidationError(f"{kind} reference requires id@version")
        key = {
            "action": "action_id",
            "workflow": "workflow_id",
            "dot": "dot_id",
            "implementation": "implementation_id",
        }[kind]
        return {key: _id(identifier, f"{kind} id"), "version": _version(version)}
    if not isinstance(value, Mapping):
        raise RuntimeValidationError(f"{kind} reference must be an object")
    return _ref(value, kind, include_provider=kind == "implementation")


def _ref_key(value: Mapping[str, Any], kind: str) -> tuple[str, str]:
    ref = _ref_from_mapping(value, kind)
    key = {
        "action": "action_id",
        "workflow": "workflow_id",
        "dot": "dot_id",
        "implementation": "implementation_id",
    }[kind]
    return ref[key], ref["version"]


def _ref_text(value: Mapping[str, Any], kind: str) -> str:
    ref = _ref_from_mapping(value, kind)
    key = {
        "action": "action_id",
        "workflow": "workflow_id",
        "dot": "dot_id",
        "implementation": "implementation_id",
    }[kind]
    return f"{ref[key]}@{ref['version']}"


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise RuntimeValidationError("runtime value must be JSON serialisable") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    parsed = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        observed = datetime.fromisoformat(parsed)
    except ValueError as error:
        raise RuntimeValidationError(f"{label} must be ISO-8601") from error
    if observed.tzinfo is None:
        raise RuntimeValidationError(f"{label} must include a timezone")
    return text


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise RuntimeValidationError("runtime validation time must include a timezone")
    return current


def _future_expiry(value: Any, *, now: datetime) -> str:
    if value is None:
        return (now + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = _timestamp(value, "expiry")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed <= now:
        raise CandidateExecutionScopeError("candidate execution authority has expired")
    return text


def _normalise_scope(
    request: Mapping[str, Any],
    project: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    supplied: dict[str, Any] = {}
    for value in (project, request.get("scope"), request.get("project"), request):
        if isinstance(value, Mapping):
            for key in ("project_id", "project_revision", "revision", "task_id"):
                if key in value and key not in supplied:
                    supplied[key] = value[key]
    project_id = supplied.get("project_id")
    revision = supplied.get("project_revision", supplied.get("revision", 0))
    task_id = supplied.get("task_id", "runtime-task")
    if project_id is None:
        return {"project_id": None, "project_revision": revision, "task_id": task_id}
    return {
        "project_id": _id(project_id, "project_id"),
        "project_revision": _nonnegative_int(revision, "project_revision"),
        "task_id": _id(task_id, "task_id"),
    }


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeValidationError(f"{label} must be a non-negative integer")
    return value


def _list_text(value: Any, label: str, *, allow_none: bool = True) -> list[str]:
    if value is None and allow_none:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise RuntimeValidationError(f"{label} must be a list")
    result = [_nonblank(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise RuntimeValidationError(f"{label} contains duplicate values")
    return result


def _port_names(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, Mapping):
            name = item.get("id", item.get("name", item.get("type")))
            if isinstance(name, str) and name.strip():
                result.append(name)
    return result


def _tokens(values: Any) -> set[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence) or isinstance(values, (bytes, bytearray)):
        return set()
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _request_text(request: Mapping[str, Any]) -> str:
    for key in ("intent", "goal", "query", "request", "human_intent", "description"):
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, Mapping):
            for nested in ("statement", "familiar", "intent", "description"):
                if isinstance(value.get(nested), str) and value[nested].strip():
                    return value[nested].strip()
    return ""


def _contract_matches(contract: Any, request: Mapping[str, Any]) -> bool:
    """Apply deterministic contract predicates; absent predicates are open."""

    if not isinstance(contract, Mapping) or not contract:
        return True
    text = _request_text(request).casefold()
    intent = request.get("intent", request.get("goal"))
    object_type = request.get("object_type", request.get("object_kind"))
    signals = _tokens(request.get("signals", request.get("required_signals", [])))
    for key in ("intent", "goal", "human_intent"):
        expected = contract.get(key)
        if expected is not None:
            expected_text = (
                expected.get("statement", expected.get("familiar", ""))
                if isinstance(expected, Mapping)
                else str(expected)
            )
            if isinstance(intent, str) and intent.casefold() != expected_text.casefold():
                if (
                    expected_text.casefold() not in text
                    and expected_text.casefold() not in text.split()
                ):
                    return False
            elif not isinstance(intent, str) and expected_text.casefold() not in text:
                return False
    for key in ("object_type", "object_kind"):
        expected = contract.get(key)
        if (
            expected is not None
            and object_type is not None
            and str(expected).casefold() != str(object_type).casefold()
        ):
            return False
    required = _tokens(contract.get("required_signals", contract.get("signals", [])))
    if required and not required.issubset(signals | _tokens(request.get("text_signals", []))):
        return False
    keywords = _tokens(contract.get("keywords", contract.get("terms", [])))
    if keywords and not any(keyword in text for keyword in keywords):
        return False
    platform = contract.get("platform")
    request_platform = request.get("platform")
    return not (
        platform is not None
        and request_platform is not None
        and str(platform).casefold() != str(request_platform).casefold()
    )


def _validate_action_runtime(
    action: Mapping[str, Any], workflows: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    value = _copy(dict(action))
    if value.get("record_type") == "capability-action":
        try:
            return validate_action(value, workflow_records=workflows)
        except Exception as error:
            raise RuntimeValidationError(f"Invalid canonical Action: {error}") from error
    _id(value.get("action_id", value.get("id")), "action_id")
    _version(value.get("version"), "action version")
    if _record_state(value) != "active":
        raise RuntimeValidationError("runtime Action must be active")
    return value


def _validate_workflow_runtime(
    workflow: Mapping[str, Any], dots: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    value = _copy(dict(workflow))
    if value.get("record_type") == "capability-workflow":
        try:
            return validate_workflow(value, dot_records=dots)
        except Exception as error:
            raise RuntimeValidationError(f"Invalid canonical Workflow: {error}") from error
    _id(value.get("workflow_id", value.get("id")), "workflow_id")
    _version(value.get("version"), "workflow version")
    if _record_state(value) != "active":
        raise RuntimeValidationError("runtime Workflow must be active")
    return value


def _validate_dot_runtime(dot: Mapping[str, Any]) -> dict[str, Any]:
    value = _copy(dict(dot))
    if value.get("record_type") == "capability-dot":
        try:
            return validate_capability_dot(value, require_active=True)
        except Exception as error:
            # A Dot that has already been admitted to the active graph may
            # still have every executable alternative unavailable at runtime.
            # Preserve that state so resolution can report ``unavailable``;
            # do not turn a provider failure into a graph mutation.
            if _record_state(value) != "active":
                raise RuntimeValidationError(f"Invalid executable Dot: {error}") from error
            _id(value.get("dot_id"), "dot_id")
            _version(value.get("version"), "Dot version")
            return value
    _id(value.get("dot_id", value.get("id")), "dot_id")
    _version(value.get("version"), "Dot version")
    if _record_state(value) != "active":
        raise RuntimeValidationError("runtime Dot must be active")
    return value


def _explicit_action_selector(
    request: Mapping[str, Any],
) -> tuple[str, str | None] | None:
    """Return the explicit Action selector, if the request supplied one.

    ``action`` remains a human-facing synonym and deliberately is not treated
    as an Action authority boundary.  ``action_id`` and ``action_ref`` are
    different: once either is explicitly named, only that active Action may
    satisfy the request.  A reference may be supplied in the canonical
    ``action_id@version`` form or as an object with an ``action_id`` and an
    optional version.  The optional version keeps this boundary tolerant of
    callers that only know an Action id while still allowing an exact
    versioned lookup when one is available.
    """

    raw_action_id = request.get("action_id")
    if raw_action_id is not None:
        if not isinstance(raw_action_id, str):
            raise RuntimeValidationError("action_id must be a non-empty string")
        if raw_action_id.strip():
            return _id(raw_action_id, "action_id"), None

    raw_action_ref = request.get("action_ref")
    if raw_action_ref is None:
        return None
    if isinstance(raw_action_ref, str):
        reference = raw_action_ref.strip()
        if not reference:
            return None
        identifier, separator, version = reference.rpartition("@")
        if separator and identifier.strip() and version.strip():
            return _id(identifier, "action_ref action_id"), _version(version, "action_ref version")
        return _id(reference, "action_ref action_id"), None
    if not isinstance(raw_action_ref, Mapping):
        raise RuntimeValidationError("action_ref must be a non-empty reference")

    identifier = raw_action_ref.get("action_id", raw_action_ref.get("id"))
    action_id = _id(identifier, "action_ref action_id")
    raw_version = raw_action_ref.get("version")
    version = None if raw_version is None else _version(raw_version, "action_ref version")
    return action_id, version


def _active_actions(
    request: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    workflows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for raw in actions:
        if _record_state(raw) != "active":
            continue
        action = _validate_action_runtime(raw, workflows)
        if _record_state(action) == "active":
            active.append(action)
    if not active:
        return []
    explicit = request.get("action_id")
    label = request.get("action")

    def score(action: Mapping[str, Any]) -> tuple[int, int, int, str, str]:
        action_id = str(action.get("action_id", ""))
        human_name = str(action.get("human_name", ""))
        familiar = action.get("human_intent", {})
        familiar_name = familiar.get("familiar", "") if isinstance(familiar, Mapping) else ""
        exact_id = int(explicit is not None and str(explicit) == action_id)
        familiar_match = int(
            isinstance(label, str)
            and label.casefold()
            in {action_id.casefold(), human_name.casefold(), str(familiar_name).casefold()}
        )
        contract_match = int(_contract_matches(action.get("match_contract"), request))
        return (-exact_id, -familiar_match, -contract_match, action_id, str(action.get("version")))

    ordered = sorted(active, key=score)
    if explicit is not None:
        exact = [item for item in ordered if item.get("action_id") == explicit]
        if exact:
            return exact
    # An unfamiliar label is not a jail around the reusable graph.  If the
    # request's intent matches an Action contract, retain that Action even when
    # the user used a local synonym for its familiar surface name.
    matched = [item for item in ordered if _contract_matches(item.get("match_contract"), request)]
    if matched:
        return matched
    return ordered


def _workflow_refs(action: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    refs = action.get("workflow_refs", []) if isinstance(action, Mapping) else []
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
        raise RuntimeValidationError("Action workflow_refs must be a list")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in refs:
        if not isinstance(raw, Mapping):
            raise RuntimeValidationError("Action workflow_refs must be objects")
        key = _ref_key(raw, "workflow")
        if key in seen:
            raise RuntimeValidationError(f"Duplicate Workflow reference: {key[0]}@{key[1]}")
        seen.add(key)
        result.append(
            {
                "workflow_id": key[0],
                "version": key[1],
                "lifecycle": raw.get("lifecycle", "active"),
            }
        )
    return result


def _active_workflows(
    request: Mapping[str, Any],
    action: Mapping[str, Any] | None,
    workflows: Sequence[Mapping[str, Any]],
    dots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    refs = _workflow_refs(action)
    candidates: list[dict[str, Any]] = []
    if refs:
        for ref in refs:
            workflow = _lookup(workflows, "workflow_id", ref["workflow_id"], ref["version"])
            if workflow is None or _record_state(workflow) != "active":
                continue
            candidates.append(_validate_workflow_runtime(workflow, dots))
    else:
        for workflow in workflows:
            if _record_state(workflow) == "active":
                candidates.append(_validate_workflow_runtime(workflow, dots))
    explicit = request.get("workflow_id")

    def score(workflow: Mapping[str, Any]) -> tuple[int, int, str, str]:
        identifier = str(workflow.get("workflow_id", ""))
        explicit_match = int(explicit is not None and explicit == identifier)
        contract_match = int(_contract_matches(workflow.get("match_contract"), request))
        return (-explicit_match, -contract_match, identifier, str(workflow.get("version")))

    return [
        workflow
        for workflow in sorted(candidates, key=score)
        if explicit is None
        or workflow.get("workflow_id") == explicit
        or _contract_matches(workflow.get("match_contract"), request)
    ]


def _implementation_provider(implementation: Mapping[str, Any]) -> str:
    provider = implementation.get("provider", "")
    if isinstance(provider, Mapping):
        provider = provider.get("provider_id", provider.get("id", ""))
    return str(provider).strip()


def _implementation_artifact(implementation: Mapping[str, Any]) -> str:
    for key in ("artifact", "artifact_id", "artifact_type", "kind", "executable_target"):
        value = implementation.get(key)
        if isinstance(value, Mapping):
            value = value.get(
                "artifact", value.get("artifact_id", value.get("kind", value.get("ref", "")))
            )
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _implementation_status(implementation: Mapping[str, Any]) -> str:
    availability = implementation.get("availability")
    if isinstance(availability, Mapping):
        availability = availability.get("status", availability.get("state"))
    if availability is None:
        availability = implementation.get("status")
    return str(availability).casefold() if availability is not None else "available"


def _available_dependencies(
    request: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[set[str], bool]:
    raw = context.get("available_dependencies", request.get("available_dependencies"))
    if raw is None:
        raw = context.get("dependencies", request.get("dependencies"))
    if raw is None:
        return set(), False
    if isinstance(raw, Mapping):
        return {str(key) for key, value in raw.items() if value is True}, True
    return {str(item) for item in raw if isinstance(item, (str, int))}, True


def _permissions(
    implementation: Mapping[str, Any], request: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[set[str], set[str]]:
    required_raw = context.get("required_permissions", request.get("required_permissions"))
    if required_raw is None:
        required_raw = request.get("permissions_required")
    required = _tokens(required_raw)
    granted_raw = context.get("granted_permissions", request.get("granted_permissions"))
    granted = _tokens(granted_raw)
    declared = implementation.get("permissions", [])
    if isinstance(declared, Mapping):
        for key in ("operations", "allowed", "permissions", "grants"):
            granted |= _tokens(declared.get(key, []))
        granted |= {str(key).casefold() for key, value in declared.items() if value is True}
    else:
        granted |= _tokens(declared)
    return required, granted


def _compatibility_reason(
    implementation: Mapping[str, Any],
    dot: Mapping[str, Any],
    request: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    compatibility = implementation.get("compatibility", {})
    if not isinstance(compatibility, Mapping):
        return "input_output_compatibility", "implementation compatibility is not an object"
    if compatibility.get("compatible") is False:
        return "input_output_compatibility", "implementation declares incompatible"
    dot_version = dot.get("version")
    versions = []
    for key in ("dot_version", "version"):
        if compatibility.get(key) is not None:
            versions.append(str(compatibility[key]))
    if compatibility.get("dot_versions") is not None:
        versions.extend(str(item) for item in compatibility.get("dot_versions", []))
    if versions and str(dot_version) not in versions:
        return "input_output_compatibility", "implementation does not support this Dot version"

    request_inputs = _tokens(request.get("inputs", request.get("input_types", [])))
    request_outputs = _tokens(request.get("outputs", request.get("output_types", [])))
    for wanted, keys, label in (
        (
            request_inputs,
            ("inputs", "input_types", "supported_inputs"),
            "input_output_compatibility",
        ),
        (
            request_outputs,
            ("outputs", "output_types", "supported_outputs"),
            "input_output_compatibility",
        ),
    ):
        supported: set[str] = set()
        for key in keys:
            supported |= _tokens(compatibility.get(key, implementation.get(key)))
        if wanted and supported and not wanted.issubset(supported | {"*", "any"}):
            return label, "implementation input/output compatibility does not match request"

    requested_platform = context.get("platform", request.get("platform"))
    supported_platforms = _tokens(
        compatibility.get(
            "platforms",
            compatibility.get("supported_platforms", implementation.get("platforms")),
        )
    )
    if compatibility.get("platform") is not None:
        supported_platforms |= _tokens(compatibility.get("platform"))
    if (
        requested_platform is not None
        and supported_platforms
        and str(requested_platform).casefold() not in supported_platforms | {"*", "any"}
    ):
        return "platform_compatibility", "implementation platform is unavailable"
    return None, None


def _numeric(value: Any, *, default: float, high_is_good: bool = False) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return -float(value) if high_is_good else float(value)
    if isinstance(value, str):
        table = {"excellent": 3.0, "high": 3.0, "good": 2.0, "medium": 2.0, "low": 1.0, "poor": 0.0}
        if value.casefold() in table:
            return -table[value.casefold()] if high_is_good else table[value.casefold()]
    return default


def _preference_rank(value: Any, target: str) -> float:
    if value is None:
        return 1.0
    if isinstance(value, Mapping):
        raw = value.get(target)
        if raw is None:
            raw = value.get("default", 1)
        return _numeric(raw, default=1.0)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        lowered = [str(item).casefold() for item in value]
        try:
            return float(lowered.index(target.casefold()))
        except ValueError:
            return float(len(lowered) + 1)
    if isinstance(value, str):
        return 0.0 if value.casefold() == target.casefold() else 1.0
    return _numeric(value, default=1.0)


def _material_tie(
    implementations: Sequence[Mapping[str, Any]],
    request: Mapping[str, Any],
    context: Mapping[str, Any],
) -> bool:
    if (
        request.get("materially_consequential") is True
        or context.get("materially_consequential") is True
    ):
        return True
    providers = {_implementation_provider(item) for item in implementations}
    artifacts = {_implementation_artifact(item) for item in implementations}
    if len(providers) > 1 and (
        request.get("provider_choice_is_consequential") is True
        or context.get("provider_choice_is_consequential") is True
    ):
        return True
    return len(artifacts) > 1 and request.get("artifact_choice_is_consequential") is True


def resolve_implementation(
    dot: Mapping[str, Any],
    request: Mapping[str, Any] | None = None,
    *,
    context: Mapping[str, Any] | None = None,
    preferred_provider: str | None = None,
    preferred_artifact: str | None = None,
    workplace_preference: Any = None,
    required_permissions: Sequence[str] | None = None,
    available_dependencies: Any = None,
    platform: str | None = None,
    materially_consequential: bool | None = None,
) -> dict[str, Any]:
    """Resolve one Dot using deterministic hard gates, then ordered ranking.

    The return value includes rejected alternatives and the filter trace.  A
    provider is only ever present inside an Implementation/ref; no caller can
    accidentally promote it to a Workflow or Action identity.
    """

    raw_request = _copy(dict(request or {}))
    raw_context = _copy(dict(context or {}))
    if preferred_provider is not None:
        raw_context["preferred_provider"] = preferred_provider
    if preferred_artifact is not None:
        raw_context["preferred_artifact"] = preferred_artifact
    if workplace_preference is not None:
        raw_context["workplace_preference"] = workplace_preference
    if required_permissions is not None:
        raw_context["required_permissions"] = list(required_permissions)
    if available_dependencies is not None:
        raw_context["available_dependencies"] = available_dependencies
    if platform is not None:
        raw_context["platform"] = platform
    if materially_consequential is not None:
        raw_context["materially_consequential"] = materially_consequential
    validated_dot = _validate_dot_runtime(dot)
    implementations = validated_dot.get("implementations", [])
    if not isinstance(implementations, Sequence) or isinstance(
        implementations, (str, bytes, bytearray)
    ):
        raise RuntimeValidationError("Dot implementations must be a list")
    available_deps, dependency_context_present = _available_dependencies(raw_request, raw_context)
    rejected: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for raw in implementations:
        if not isinstance(raw, Mapping):
            raise RuntimeValidationError("Implementation entries must be objects")
        implementation = _copy(dict(raw))
        _id(implementation.get("implementation_id"), "implementation_id")
        trace: list[dict[str, Any]] = []

        availability = _implementation_status(implementation)
        unavailable_values = {"unavailable", "disabled", "missing", "failed", "unverified"}
        if (
            availability in unavailable_values
            or implementation.get("available") is False
            or implementation.get("callable") is False
        ):
            rejected.append(
                {
                    "implementation_ref": _ref(
                        implementation, "implementation", include_provider=True
                    ),
                    "filter": "availability",
                    "reason": "implementation is unavailable",
                }
            )
            continue
        trace.append({"filter": "availability", "status": "passed"})

        required, granted = _permissions(implementation, raw_request, raw_context)
        if required and not required.issubset(granted):
            rejected.append(
                {
                    "implementation_ref": _ref(
                        implementation, "implementation", include_provider=True
                    ),
                    "filter": "permission",
                    "reason": "required permission is not granted",
                }
            )
            continue
        trace.append({"filter": "permission", "status": "passed"})

        dependencies = implementation.get("dependencies", [])
        required_deps = {
            str(item.get("dependency_id", item.get("name", item.get("id", ""))))
            if isinstance(item, Mapping)
            else str(item)
            for item in dependencies
        }
        required_deps.discard("")
        if dependency_context_present and not required_deps.issubset(available_deps):
            rejected.append(
                {
                    "implementation_ref": _ref(
                        implementation, "implementation", include_provider=True
                    ),
                    "filter": "dependencies",
                    "reason": "required dependency is unavailable",
                }
            )
            continue
        trace.append({"filter": "dependencies", "status": "passed"})

        filter_name, reason = _compatibility_reason(
            implementation, validated_dot, raw_request, raw_context
        )
        if filter_name is not None:
            rejected.append(
                {
                    "implementation_ref": _ref(
                        implementation, "implementation", include_provider=True
                    ),
                    "filter": filter_name,
                    "reason": reason,
                }
            )
            continue
        trace.append({"filter": "input_output_compatibility", "status": "passed"})
        trace.append({"filter": "platform_compatibility", "status": "passed"})

        verification = implementation.get("verification", {})
        verified = isinstance(verification, Mapping) and verification.get("status") == "verified"
        if not verified:
            rejected.append(
                {
                    "implementation_ref": _ref(
                        implementation, "implementation", include_provider=True
                    ),
                    "filter": "verified",
                    "reason": "Implementation is not verified",
                }
            )
            continue
        trace.append({"filter": "verified", "status": "passed"})
        implementation["_runtime_filter_trace"] = trace
        eligible.append(implementation)

    if not eligible:
        return {
            "status": "unavailable",
            "dot_ref": _ref(validated_dot, "dot"),
            "implementation": None,
            "implementation_ref": None,
            "candidates": [],
            "rejected": rejected,
            "ask_user": False,
            "selection_trace": {"filters": list(_FILTERS), "rejected": rejected},
        }

    preferred_provider_value = str(raw_context.get("preferred_provider", "")).casefold()
    preferred_artifact_value = str(raw_context.get("preferred_artifact", "")).casefold()
    workplace = raw_context.get("workplace_preference")

    def rank(item: Mapping[str, Any]) -> tuple[float, ...]:
        provider = _implementation_provider(item)
        artifact = _implementation_artifact(item)
        quality = item.get("quality", item.get("quality_score"))
        cost = item.get("cost", item.get("cost_score"))
        speed = item.get("speed", item.get("speed_score"))
        recovery = item.get("recovery_score", item.get("recoverability"))
        return (
            0.0
            if preferred_artifact_value and artifact.casefold() == preferred_artifact_value
            else 1.0,
            0.0
            if preferred_provider_value and provider.casefold() == preferred_provider_value
            else 1.0,
            _preference_rank(workplace, item.get("implementation_id", "")),
            _numeric(quality, default=1.0, high_is_good=True),
            _numeric(cost, default=0.0),
            _numeric(speed, default=0.0),
            _numeric(recovery, default=0.0, high_is_good=True),
        )

    ranked = sorted(
        eligible,
        key=lambda item: (
            rank(item),
            item.get("implementation_id", ""),
            item.get("version", ""),
            _implementation_provider(item),
        ),
    )
    best_rank = rank(ranked[0])
    ties = [item for item in ranked if rank(item) == best_rank]
    if len(ties) > 1 and _material_tie(ties, raw_request, raw_context):
        return {
            "status": "ask-user",
            "dot_ref": _ref(validated_dot, "dot"),
            "implementation": None,
            "implementation_ref": None,
            "candidates": [_ref(item, "implementation", include_provider=True) for item in ties],
            "rejected": rejected,
            "ask_user": True,
            "tie": "materially-consequential-implementation-choice",
            "selection_trace": {"filters": list(_FILTERS), "rank_fields": list(_RANK_FIELDS)},
        }
    selected = _copy(ranked[0])
    trace = selected.pop("_runtime_filter_trace", [])
    return {
        "status": "selected",
        "dot_ref": _ref(validated_dot, "dot"),
        "implementation": selected,
        "implementation_ref": _ref(selected, "implementation", include_provider=True),
        "candidates": [_ref(item, "implementation", include_provider=True) for item in ranked],
        "rejected": rejected,
        "ask_user": False,
        "selection_trace": {
            "filters": list(_FILTERS),
            "rank_fields": list(_RANK_FIELDS),
            "passed": trace,
        },
    }


def _dot_refs_for_workflow(
    workflow: Mapping[str, Any], dots: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_refs = workflow.get("dot_refs", [])
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes, bytearray)):
        raise RuntimeValidationError("Workflow dot_refs must be a list")
    selected: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping):
            raise RuntimeValidationError("Workflow Dot refs must be objects")
        dot_id, dot_version = _ref_key(raw_ref, "dot")
        dot = _lookup(dots, "dot_id", dot_id, dot_version)
        if dot is None or _record_state(dot) != "active":
            return [], []
        selected.append(_validate_dot_runtime(dot))
        refs.append({"dot_id": dot_id, "version": dot_version, "lifecycle": "active"})
    return selected, refs


def load_active_capabilities_for_workplace(
    active_graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Load an already-versioned active graph for new-user onboarding.

    This is deliberately a load-only fast path.  It accepts no Source bodies,
    candidate objects, extraction evidence, compiler input, or discovery
    callback, so onboarding cannot accidentally rerun Capability System
    Genesis.  Workplace creation itself belongs to the Workplace boundary;
    this function only proves which active capability references were loaded.
    """

    if not isinstance(active_graph, Mapping):
        raise RuntimeValidationError("active capability graph must be an object")
    forbidden = {
        "sources",
        "source_catalogue",
        "source_documents",
        "extractions",
        "responsibilities",
        "candidate_dots",
        "candidate_workflows",
        "candidate_actions",
        "observed_compositions",
        "genesis",
        "compiler",
    }
    present = sorted(forbidden & set(active_graph))
    if present:
        raise RuntimeValidationError(
            "new-user onboarding accepts only the active capability graph; "
            "Genesis/candidate input is forbidden: " + ", ".join(present)
        )

    dots = _records(active_graph.get("dots"), "dot_id", "active Dots")
    workflows = _records(active_graph.get("workflows"), "workflow_id", "active Workflows")
    actions = _records(active_graph.get("actions"), "action_id", "active Actions")
    checked_dots = [_validate_dot_runtime(item) for item in dots]
    checked_workflows = [_validate_workflow_runtime(item, checked_dots) for item in workflows]
    checked_actions = [_validate_action_runtime(item, checked_workflows) for item in actions]

    for workflow in checked_workflows:
        selected, refs = _dot_refs_for_workflow(workflow, checked_dots)
        raw_refs = workflow.get("dot_refs", [])
        if len(selected) != len(raw_refs) or len(refs) != len(raw_refs):
            raise RuntimeValidationError(
                f"active Workflow {workflow.get('workflow_id')!r} does not resolve only active Dots"
            )
    for action in checked_actions:
        for ref in _workflow_refs(action):
            if (
                _lookup(
                    checked_workflows,
                    "workflow_id",
                    ref["workflow_id"],
                    ref["version"],
                )
                is None
            ):
                raise RuntimeValidationError(
                    f"active Action {action.get('action_id')!r} references a missing Workflow"
                )

    return {
        "record_type": ONBOARDING_RECORD_TYPE,
        "record_version": ONBOARDING_RECORD_VERSION,
        "status": "active-capabilities-loaded",
        "actions": [_ref(item, "action") for item in checked_actions],
        "workflows": [_ref(item, "workflow") for item in checked_workflows],
        "dots": [_ref(item, "dot") for item in checked_dots],
        "source_intake_performed": False,
        "source_scraping_performed": False,
        "responsibility_extraction_performed": False,
        "dot_synthesis_performed": False,
        "workflow_synthesis_performed": False,
        "action_induction_performed": False,
        "genesis_performed": False,
        "persistent_mutations": [],
    }


initialise_workplace_capabilities = load_active_capabilities_for_workplace
initialize_workplace_capabilities = load_active_capabilities_for_workplace


def _request_ports(request: Mapping[str, Any], key: str) -> list[str]:
    values = request.get(key)
    if values is None:
        values = request.get("input_types" if key == "inputs" else "output_types")
    return _port_names(values) or ([str(values)] if isinstance(values, str) else [])


def _dot_can_start(dot: Mapping[str, Any], available: set[str], requested_inputs: set[str]) -> bool:
    inputs = _tokens(_port_names(dot.get("inputs")))
    if not inputs:
        return True
    return bool(inputs & (available | requested_inputs))


def _find_dot_chain(
    request: Mapping[str, Any], dots: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    active = [_validate_dot_runtime(dot) for dot in dots if _record_state(dot) == "active"]
    active.sort(key=lambda item: (str(item.get("dot_id")), str(item.get("version"))))
    if not active:
        return []
    requested_inputs = _tokens(_request_ports(request, "inputs"))
    requested_outputs = _tokens(_request_ports(request, "outputs"))
    explicit_dot = request.get("dot_id")
    if explicit_dot is not None:
        active = [item for item in active if item.get("dot_id") == explicit_dot]
    if not active:
        return []

    def dfs(path: list[dict[str, Any]], available: set[str]) -> list[dict[str, Any]] | None:
        if path:
            produced = _tokens(_port_names(path[-1].get("outputs")))
            if requested_outputs and requested_outputs.issubset(produced | available):
                return path
            if not requested_outputs:
                return path
        if len(path) >= min(8, len(active)):
            return None
        used = {item.get("dot_id") for item in path}
        for dot in active:
            if dot.get("dot_id") in used:
                continue
            if not _dot_can_start(dot, available, requested_inputs):
                continue
            outputs = _tokens(_port_names(dot.get("outputs")))
            found = dfs(path + [dot], available | outputs)
            if found:
                return found
        return None

    result = dfs([], set())
    return result or []


def _contract_items(values: Any) -> list[str]:
    names = _port_names(values)
    return names or ["result"]


def _composition_id(scope: Mapping[str, Any], dots: Sequence[Mapping[str, Any]]) -> str:
    payload = {
        "project_id": scope.get("project_id"),
        "project_revision": scope.get("project_revision"),
        "task_id": scope.get("task_id"),
        "dots": [_ref(dot, "dot") for dot in dots],
    }
    return f"composition-{_digest(payload)[:24]}"


def _build_execution_composition(
    request: Mapping[str, Any],
    scope: Mapping[str, Any],
    dots: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    evidence_refs: Sequence[str],
) -> dict[str, Any] | None:
    if scope.get("project_id") is None:
        return None
    steps = []
    for sequence, dot in enumerate(dots, start=1):
        steps.append(
            {
                "step_id": f"dot-step-{sequence}",
                "sequence": sequence,
                "kind": "dot",
                "ref": {"dot_id": dot["dot_id"], "version": dot["version"], "lifecycle": "active"},
                "input_bindings": _contract_items(dot.get("inputs")),
                "output_bindings": _contract_items(dot.get("outputs")),
            }
        )
    expiry = _future_expiry(request.get("expires_at"), now=now)
    side_effects = _list_text(request.get("allowed_side_effects", []), "allowed_side_effects")
    if not side_effects:
        side_effects = []
    composition = compose_execution(
        composition_id=_composition_id(scope, dots),
        project_id=scope["project_id"],
        project_revision=scope["project_revision"],
        task_id=scope["task_id"],
        inputs=_contract_items(request.get("inputs")),
        outputs=_contract_items(request.get("outputs")),
        steps=steps,
        verification={"checks": ["each Dot output is verified before the next step"]},
        expiry={"expires_at": expiry, "reason": "project-local missing-Workflow composition"},
        allowed_side_effects=side_effects,
        recovery={"strategy": "discard this temporary composition and retain active records"},
        evidence_refs=list(evidence_refs),
        dots=dots,
        now=now,
    )
    if composition.get("persistent") is not False:
        raise RuntimeValidationError("Execution Composition must remain persistent=false")
    return validate_execution_composition(composition, dots=dots, now=now)


def _source_records(values: Any) -> list[dict[str, Any]]:
    if isinstance(values, Mapping) and isinstance(values.get("sources"), Sequence):
        if values.get("record_type") == "capability-source-catalogue":
            try:
                return _copy(validate_source_catalogue(values)["sources"])
            except Exception as error:
                raise RuntimeValidationError(
                    f"Invalid retained Workplace Source catalogue: {error}"
                ) from error
        values = values["sources"]
    records = _records(values, "source_id", "Source records")
    validated = []
    for raw in records:
        source_id = raw.get("source_id", "unknown")
        try:
            validated.append(validate_source(raw))
        except Exception as error:
            raise RuntimeValidationError(f"Invalid retained Source {source_id}: {error}") from error
    return validated


def _source_is_suitable(source: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    if source.get("status") not in {None, "source-only"}:
        return False
    claim_text = " ".join(
        [
            str(source.get("name", "")),
            " ".join(str(item) for item in source.get("claimed_capabilities", [])),
        ]
    ).casefold()
    intent = _request_text(request).casefold()
    if not intent:
        return bool(claim_text)
    words = {word for word in re.findall(r"[a-z0-9][a-z0-9._-]+", intent) if len(word) > 2}
    return bool(words & set(re.findall(r"[a-z0-9][a-z0-9._-]+", claim_text)))


def _source_research_request(
    request: Mapping[str, Any], checked_source_refs: Sequence[str]
) -> dict[str, Any]:
    return {
        "record_type": "external-research-request",
        "record_version": 1,
        "persistent": False,
        "query": _request_text(request) or "missing capability",
        "source_refs_checked": list(checked_source_refs),
        "retained_source_count": len(checked_source_refs),
        "reason": "retained Workplace Source evidence is insufficient",
        "network_call": False,
        "requires_explicit_research_authority": True,
    }


def _candidate_trial_request(
    request: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    authority: Mapping[str, Any] | None,
    candidate_dot: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any] | None:
    if scope.get("project_id") is None or authority is None:
        return None
    candidate_id = (
        candidate_dot.get("dot_id")
        if isinstance(candidate_dot, Mapping)
        else request.get("candidate_dot_id", request.get("dot_id"))
    )
    if candidate_id is None:
        candidate_id = f"candidate-{_slug(_request_text(request) or 'capability')}"
    candidate_id = _id(candidate_id, "candidate Dot id")
    candidate_version = (
        candidate_dot.get("version")
        if isinstance(candidate_dot, Mapping)
        else request.get("candidate_dot_version", "0.1.0")
    )
    candidate_version = _version(candidate_version, "candidate Dot version")
    raw_authority = _copy(dict(authority))
    supplied_scope = raw_authority.get("scope")
    expected_scope = {
        "kind": "project-task-local",
        "project_id": scope["project_id"],
        "task_id": scope["task_id"],
    }
    if isinstance(supplied_scope, Mapping):
        scope_project = supplied_scope.get("project_id")
        scope_task = supplied_scope.get("task_id")
        if scope_project != expected_scope["project_id"] or scope_task != expected_scope["task_id"]:
            raise CandidateExecutionScopeError(
                "candidate execution authority scope names a different Project or task"
            )
        if (
            supplied_scope.get("project_revision", scope["project_revision"])
            != scope["project_revision"]
        ):
            raise CandidateExecutionScopeError(
                "candidate execution authority scope names a different Project revision"
            )
    # Composition authority has a wider shape than Dot-level authority.  The
    # scope was checked above; the remaining composition-only keys do not
    # become authority here.
    for key in (
        "scope",
        "allowed_step_ids",
        "candidate_dot_execution",
        "candidate_dot_ids",
        "human_approved",
        "persistent",
    ):
        raw_authority.pop(key, None)
    raw_authority.setdefault(
        "authority_id", f"candidate-execution-{_digest({'dot': candidate_id, 'scope': scope})[:16]}"
    )
    raw_authority.setdefault("project_id", scope["project_id"])
    raw_authority.setdefault("project_revision", scope["project_revision"])
    raw_authority.setdefault("task_id", scope["task_id"])
    raw_authority.setdefault(
        "allowed_side_effects",
        _list_text(request.get("allowed_side_effects", []), "allowed_side_effects"),
    )
    raw_authority.setdefault("persistence_state_change", False)
    raw_authority.setdefault("expires_at", _future_expiry(request.get("expires_at"), now=now))
    # Dot-level authority has no ``persistent`` field; persistence is
    # expressed by the trial request itself and is always false there.
    if raw_authority.get("dot_id") not in {None, candidate_id}:
        raise CandidateExecutionScopeError("candidate execution authority names a different Dot")
    if raw_authority.get("dot_version") not in {None, candidate_version}:
        raise CandidateExecutionScopeError(
            "candidate execution authority names a different Dot version"
        )
    raw_authority["dot_id"] = candidate_id
    raw_authority["dot_version"] = candidate_version
    try:
        checked = validate_candidate_execution_authority(
            raw_authority,
            project_id=scope["project_id"],
            project_revision=scope["project_revision"],
            task_id=scope["task_id"],
            now=now,
        )
    except Exception as error:
        raise CandidateExecutionScopeError(str(error)) from error
    return {
        "record_type": "candidate-dot-trial-request",
        "record_version": 1,
        "dot_ref": {"dot_id": candidate_id, "version": candidate_version, "lifecycle": "candidate"},
        "scope": {
            "kind": "project-task-local",
            "project_id": scope["project_id"],
            "project_revision": scope["project_revision"],
            "task_id": scope["task_id"],
        },
        "execution_authority": checked,
        "allowed_side_effects": list(checked["allowed_side_effects"]),
        "persistent": False,
        "trial_only": True,
        "graph_mutation": False,
    }


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return text[:80] or "capability"


def derive_work_signature(
    *,
    action_ref: Any = None,
    workflow_refs: Any = None,
    dot_refs: Any = None,
    implementation_refs: Any = None,
    route_state: str,
) -> str:
    """Derive a stable structural Work Signature, excluding result payloads."""

    if route_state not in ROUTE_STATES:
        raise RuntimeValidationError("Work Signature route_state is invalid")
    payload = {
        "action_ref": _copy(action_ref),
        "workflow_refs": _copy(workflow_refs or []),
        "dot_refs": _copy(dot_refs or []),
        "implementation_refs": _copy(implementation_refs or []),
        "route_state": route_state,
    }
    return "work-" + _digest(payload)[:24]


def _repeated_signal(
    execution_evidence: Any,
    *,
    refs: Sequence[Mapping[str, Any]],
    fatigue_threshold: int = 3,
) -> dict[str, Any] | None:
    """Derive recurrence only from canonical compact execution receipts.

    Caller-supplied occurrence counters are intentionally rejected.  Fatigue
    observes distinct, digest-valid, successful missing-Workflow receipts and
    computes recurrence itself.
    """

    if execution_evidence is None:
        return None
    if not refs:
        return None
    if isinstance(fatigue_threshold, bool) or not isinstance(fatigue_threshold, int):
        raise RuntimeValidationError("fatigue_threshold must be an integer")
    if fatigue_threshold < 2:
        raise RuntimeValidationError("fatigue_threshold must be at least 2")
    values = (
        [execution_evidence]
        if isinstance(execution_evidence, Mapping)
        else list(execution_evidence)
        if isinstance(execution_evidence, Sequence)
        and not isinstance(execution_evidence, (str, bytes, bytearray))
        else None
    )
    if values is None:
        raise RuntimeValidationError("execution evidence must be a receipt or list")

    expected_refs = [_ref_text(item, "dot") for item in refs]
    distinct: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, Mapping):
            raise RuntimeValidationError("execution evidence entries must be objects")
        try:
            receipt = validate_compact_evidence(item)
        except CapabilityRuntimeError as error:
            raise RuntimeValidationError(
                "Fatigue recurrence requires canonical compact execution evidence"
            ) from error
        if receipt["status"] != "succeeded" or receipt["verification"]["status"] != "verified":
            continue
        if receipt["route_state"] != MISSING_WORKFLOW:
            continue
        receipt_refs = [_ref_text(ref, "dot") for ref in receipt["dot_refs"]]
        if expected_refs and receipt_refs != expected_refs:
            continue
        distinct.setdefault(receipt["evidence_digest"], receipt)

    grouped: dict[str, dict[str, Any]] = {}
    for digest, receipt in sorted(distinct.items()):
        # Re-derive the structural signature from canonical references.  The
        # receipt's own work_signature remains evidence, but cannot choose the
        # recurrence bucket.
        signature = derive_work_signature(
            action_ref=receipt.get("action_ref"),
            workflow_refs=receipt["workflow_refs"],
            dot_refs=receipt["dot_refs"],
            implementation_refs=receipt["implementation_refs"],
            route_state=receipt["route_state"],
        )
        signature_key = signature
        group = grouped.setdefault(
            signature_key,
            {
                "work_signature": _copy(signature),
                "evidence_digests": [],
            },
        )
        group["evidence_digests"].append(digest)

    normalised = []
    for key in sorted(grouped):
        group = grouped[key]
        occurrences = len(group["evidence_digests"])
        if occurrences < fatigue_threshold:
            continue
        normalised.append(
            {
                "work_signature": group["work_signature"],
                "occurrences": occurrences,
                "evidence_digests": group["evidence_digests"],
            }
        )
    if not normalised:
        return None
    return {
        "record_type": "runtime-candidate-signal",
        "record_version": 1,
        "kind": "repeated-pattern",
        "status": "candidate",
        "persistent": False,
        "workflow_promotion": False,
        "patterns": normalised,
        "fatigue": {
            "status": "triggered",
            "threshold": fatigue_threshold,
            "distinct_receipt_count": sum(len(item["evidence_digests"]) for item in normalised),
            "caller_occurrence_counts_trusted": False,
        },
        "dot_refs": [_copy(dict(item)) for item in refs],
        "next_step": (
            "System Review may evaluate a candidate Workflow; no runtime promotion occurred"
        ),
    }


def _make_dot_plan(
    dots: Sequence[Mapping[str, Any]], request: Mapping[str, Any], context: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plans: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    for sequence, dot in enumerate(dots, start=1):
        selection = resolve_implementation(dot, request, context=context)
        selections.append(selection)
        if selection["status"] != "selected":
            continue
        implementation = selection["implementation"]
        plans.append(
            {
                "sequence": sequence,
                "dot_ref": _ref(dot, "dot"),
                "implementation_ref": _ref(implementation, "implementation", include_provider=True),
                "implementation": implementation,
                "inputs": _port_names(dot.get("inputs")),
                "outputs": _port_names(dot.get("outputs")),
            }
        )
    return plans, selections


def resolve_request(
    request: Mapping[str, Any] | str,
    actions: Any = None,
    workflows: Any = None,
    dots: Any = None,
    sources: Any = None,
    *,
    workplace_sources: Any = None,
    project: Mapping[str, Any] | None = None,
    candidate_execution_authority: Mapping[str, Any] | None = None,
    candidate_dot: Mapping[str, Any] | None = None,
    repeated_patterns: Any = None,
    execution_evidence: Any = None,
    fatigue_threshold: int = 3,
    now: datetime | None = None,
    context: Mapping[str, Any] | None = None,
    capability_graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one request through Action → Workflow → Dot → Implementation.

    All branches return a detached, non-persistent plan.  ``exact`` and
    ``partial`` plans identify a reusable Workflow; ``missing-workflow`` plans
    contain one temporary Execution Composition; ``unavailable`` identifies a
    real executable dependency failure; and ``missing-capability`` returns
    only bounded research/trial requests.
    """

    if isinstance(request, str):
        request_value: dict[str, Any] = {"intent": request}
    elif isinstance(request, Mapping):
        request_value = _copy(dict(request))
    else:
        raise RuntimeValidationError("runtime request must be an object or string")
    if capability_graph is not None:
        actions = capability_graph.get("actions", actions)
        workflows = capability_graph.get("workflows", workflows)
        dots = capability_graph.get("dots", dots)
        if sources is None:
            sources = capability_graph.get("sources")
    workflow_records = _records(workflows, "workflow_id", "Workflow records")
    dot_records = _records(dots, "dot_id", "Dot records")
    action_records = _records(actions, "action_id", "Action records")
    source_input = workplace_sources if workplace_sources is not None else sources
    current = _now(now)
    scope = _normalise_scope(request_value, project)
    runtime_context = _copy(dict(context or {}))
    runtime_context.setdefault("platform", request_value.get("platform"))
    action_selector = _explicit_action_selector(request_value)
    action_request = request_value
    if action_selector is not None:
        # Keep the existing Action scorer useful for explicit references while
        # retaining the selector's optional version constraint below.
        action_request = _copy(request_value)
        action_request["action_id"] = action_selector[0]
    action_candidates = _active_actions(action_request, action_records, workflow_records)
    action = None
    unknown_action_id: str | None = None
    if action_selector is None:
        action = action_candidates[0] if action_candidates else None
    else:
        selected_action_id, selected_action_version = action_selector
        action = next(
            (
                item
                for item in action_candidates
                if item.get("action_id") == selected_action_id
                and (
                    selected_action_version is None
                    or item.get("version") == selected_action_version
                )
            ),
            None,
        )
        if action is None:
            # An explicit Action is an authority boundary.  Do not reinterpret
            # its intent through an unrelated Workflow or Dot chain.
            unknown_action_id = selected_action_id
    workflows_candidates = (
        []
        if unknown_action_id is not None
        else _active_workflows(request_value, action, workflow_records, dot_records)
    )
    workflow = workflows_candidates[0] if workflows_candidates else None
    selected_dots: list[dict[str, Any]] = []
    dot_refs: list[dict[str, Any]] = []
    workflow_ref: dict[str, Any] | None = None
    workflow_refs: list[dict[str, Any]] = []
    if workflow is not None:
        workflow_ref = _ref(workflow, "workflow")
        workflow_refs = [workflow_ref]
        selected_dots, dot_refs = _dot_refs_for_workflow(workflow, dot_records)
        if not selected_dots:
            workflow = None
            workflow_ref = None
            workflow_refs = []

    recurrence_evidence = execution_evidence
    if recurrence_evidence is None:
        recurrence_evidence = repeated_patterns
    if recurrence_evidence is None:
        recurrence_evidence = request_value.get("execution_evidence")
    repeated_signal = _repeated_signal(
        recurrence_evidence, refs=dot_refs, fatigue_threshold=fatigue_threshold
    )
    if workflow is not None:
        plans, selections = _make_dot_plan(selected_dots, request_value, runtime_context)
        implementation_refs = [item["implementation_ref"] for item in plans]
        adaptation = request_value.get("project_local_adaptation", request_value.get("adaptation"))
        partial = adaptation is not None or request_value.get("adaptation_required") is True
        route_state = PARTIAL if partial else EXACT
        if any(item["status"] != "selected" for item in selections) or len(plans) != len(
            selected_dots
        ):
            route_state = UNAVAILABLE
        result: dict[str, Any] = {
            "record_type": RUNTIME_RECORD_TYPE,
            "record_version": RUNTIME_RECORD_VERSION,
            "state": route_state,
            "route_state": route_state,
            "action_ref": _ref(action, "action") if action is not None else None,
            "workflow_ref": workflow_ref,
            "workflow_refs": workflow_refs,
            "dot_refs": dot_refs,
            "implementation_refs": implementation_refs,
            "dot_plans": plans,
            "implementation_resolution": selections,
            "persistent": False,
            "composition": None,
            "candidate_signal": repeated_signal,
            "external_research_request": None,
            "candidate_dot_trial_request": None,
            "mutations": [],
            "selection_order": list(_FILTERS) + list(_RANK_FIELDS),
            "explanation": "Active Workflow owns the requested outcome."
            if route_state in {EXACT, PARTIAL}
            else (
                "The correct Workflow/Dots exist, but no executable verified "
                "Implementation is available."
            ),
        }
        if partial:
            result["project_local_adaptation"] = {
                "scope": "project-task-local",
                "persistent": False,
                "value": _copy(adaptation) if adaptation is not None else {"required": True},
            }
        return result

    # No active Workflow matched.  Compose a temporary path from active Dots,
    # but only when the Dots themselves own a coherent input/output chain.
    chain = [] if unknown_action_id is not None else _find_dot_chain(request_value, dot_records)
    if chain:
        plans, selections = _make_dot_plan(chain, request_value, runtime_context)
        dot_refs = [_ref(dot, "dot") | {"lifecycle": "active"} for dot in chain]
        workflow_refs = []
        if all(item["status"] == "selected" for item in selections):
            evidence_refs = [
                f"runtime-{_digest({'request': request_value, 'dots': dot_refs})[:16]}"
            ]
            composition = _build_execution_composition(
                request_value,
                scope,
                chain,
                now=current,
                evidence_refs=evidence_refs,
            )
            if composition is not None:
                candidate_signal = _repeated_signal(
                    recurrence_evidence,
                    refs=dot_refs,
                    fatigue_threshold=fatigue_threshold,
                )
                return {
                    "record_type": RUNTIME_RECORD_TYPE,
                    "record_version": RUNTIME_RECORD_VERSION,
                    "state": MISSING_WORKFLOW,
                    "route_state": MISSING_WORKFLOW,
                    "action_ref": _ref(action, "action") if action is not None else None,
                    "workflow_ref": None,
                    "workflow_refs": [],
                    "dot_refs": dot_refs,
                    "implementation_refs": [item["implementation_ref"] for item in plans],
                    "dot_plans": plans,
                    "implementation_resolution": selections,
                    "persistent": False,
                    "composition": composition,
                    "candidate_signal": candidate_signal,
                    "external_research_request": None,
                    "candidate_dot_trial_request": None,
                    "mutations": [],
                    "selection_order": list(_FILTERS) + list(_RANK_FIELDS),
                    "explanation": (
                        "No reusable Workflow matched; active Dots were composed for this "
                        "Project/task only."
                    ),
                }
        else:
            return {
                "record_type": RUNTIME_RECORD_TYPE,
                "record_version": RUNTIME_RECORD_VERSION,
                "state": UNAVAILABLE,
                "route_state": UNAVAILABLE,
                "action_ref": _ref(action, "action") if action is not None else None,
                "workflow_ref": None,
                "workflow_refs": [],
                "dot_refs": dot_refs,
                "implementation_refs": [],
                "dot_plans": plans,
                "implementation_resolution": selections,
                "persistent": False,
                "composition": None,
                "candidate_signal": _repeated_signal(
                    recurrence_evidence,
                    refs=dot_refs,
                    fatigue_threshold=fatigue_threshold,
                ),
                "external_research_request": None,
                "candidate_dot_trial_request": None,
                "mutations": [],
                "selection_order": list(_FILTERS) + list(_RANK_FIELDS),
                "explanation": (
                    "Active Dots form the intended path, but no executable Implementation "
                    "is available."
                ),
            }

    # A caller may supply Source refs as hints, but a missing-capability route
    # must search the complete retained Workplace catalogue before requesting
    # external research.  Source order cannot change the result.
    source_records = _source_records(source_input)
    source_refs_raw = request_value.get(
        "source_refs", request_value.get("workplace_source_refs", [])
    )
    hinted_source_refs = _list_text(source_refs_raw, "source_refs")
    checked_sources: list[dict[str, Any]] = []
    suitable_sources: list[dict[str, Any]] = []
    for raw_source in sorted(source_records, key=lambda item: str(item.get("source_id", ""))):
        source_id = _id(raw_source.get("source_id"), "source_id")
        source = _copy(raw_source)
        if not source_only(source):
            continue
        checked_sources.append({"source_id": source_id})
        if _source_is_suitable(source, request_value):
            suitable_sources.append(source)
    research_request = None
    if not suitable_sources:
        research_request = _source_research_request(
            request_value, [item["source_id"] for item in checked_sources]
        )
    trial_request = None
    if suitable_sources and candidate_dot is not None:
        trial_request = _candidate_trial_request(
            request_value,
            scope,
            authority=candidate_execution_authority
            or request_value.get("candidate_execution_authority"),
            candidate_dot=candidate_dot,
            now=current,
        )
    result = {
        "record_type": RUNTIME_RECORD_TYPE,
        "record_version": RUNTIME_RECORD_VERSION,
        "status": "blocked",
        "state": MISSING_CAPABILITY,
        "route_state": MISSING_CAPABILITY,
        "action_ref": _ref(action, "action") if action is not None else None,
        "workflow_ref": None,
        "workflow_refs": [],
        "dot_refs": [],
        "implementation_refs": [],
        "dot_plans": [],
        "implementation_resolution": [],
        "persistent": False,
        "composition": None,
        "candidate_signal": _repeated_signal(
            recurrence_evidence, refs=[], fatigue_threshold=fatigue_threshold
        ),
        "external_research_request": research_request,
        "candidate_dot_trial_request": trial_request,
        "source_refs_checked": [item["source_id"] for item in checked_sources],
        "hinted_source_refs": hinted_source_refs,
        "suitable_source_refs": [source["source_id"] for source in suitable_sources],
        "mutations": [],
        "selection_order": list(_FILTERS) + list(_RANK_FIELDS),
        "explanation": "No suitable active Dot exists; the active graph remains unchanged.",
    }
    if unknown_action_id is not None:
        result.update(
            {
                "unknown_action_id": unknown_action_id,
                "status": "blocked",
                "explanation": (
                    f"No active Action matches explicit Action {unknown_action_id!r}; "
                    "no Workflow or Dot route was executed."
                ),
            }
        )
    return result


def runtime_request_digest(request: Mapping[str, Any] | str) -> str:
    """Return the exact digest used by Candidate persistence authority."""

    value = {"intent": request} if isinstance(request, str) else request
    if not isinstance(value, Mapping):
        raise RuntimeValidationError("runtime request must be an object or string")
    return _digest(value)


def _validate_candidate_persistence_authority(
    authority: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    scope: Mapping[str, Any],
    source_refs: Sequence[str],
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(authority, Mapping):
        raise CandidatePersistenceAuthorityError(
            "Candidate Dot persistence requires separate machine-readable authority"
        )
    value = _copy(dict(authority))
    allowed_keys = {
        "record_type",
        "record_version",
        "authority_id",
        "operation",
        "project_id",
        "project_revision",
        "task_id",
        "request_digest",
        "source_refs",
        "allowed_relative_root",
        "candidate_write_count",
        "candidate_only",
        "activation",
        "version",
        "publication",
        "expires_at",
    }
    unexpected = sorted(set(value) - allowed_keys)
    if unexpected:
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority contains unsupported fields: " + ", ".join(unexpected)
        )
    if (
        value.get("record_type") != PERSISTENCE_AUTHORITY_RECORD_TYPE
        or value.get("record_version") != PERSISTENCE_AUTHORITY_RECORD_VERSION
    ):
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority record type or version is invalid"
        )
    _id(value.get("authority_id"), "candidate persistence authority_id")
    if value.get("operation") != "write-candidate-dot":
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority permits only write-candidate-dot"
        )
    for key in ("project_id", "project_revision", "task_id"):
        if value.get(key) != scope.get(key):
            raise CandidatePersistenceAuthorityError(
                f"Candidate persistence authority names a different {key}"
            )
    if value.get("request_digest") != _digest(request):
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority is bound to a different request"
        )
    authorised_sources = _list_text(value.get("source_refs"), "authority.source_refs")
    if sorted(authorised_sources) != sorted(source_refs):
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority is bound to different Source refs"
        )
    if value.get("allowed_relative_root") != "genesis/candidates/dots":
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority path is not the governed Candidate Dot root"
        )
    if value.get("candidate_write_count") != 1 or value.get("candidate_only") is not True:
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority must permit exactly one inactive Candidate Dot"
        )
    if any(value.get(key) is not False for key in ("activation", "version", "publication")):
        raise CandidatePersistenceAuthorityError(
            "Candidate persistence authority cannot grant activation, versioning, or publication"
        )
    expiry = _timestamp(value.get("expires_at"), "candidate persistence expiry")
    if datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= now:
        raise CandidatePersistenceAuthorityError("Candidate persistence authority has expired")
    return value


def _candidate_storage_path(workplace_root: Path, candidate: Mapping[str, Any]) -> Path:
    if not workplace_root.exists() or not workplace_root.is_dir():
        raise WorkplaceCandidateStorageError("Workplace root must be an existing directory")
    if workplace_root.is_symlink():
        raise WorkplaceCandidateStorageError("Workplace root cannot be a symlink")
    root = workplace_root.resolve()
    relative = (
        Path("genesis")
        / "candidates"
        / "dots"
        / (f"{candidate['dot_id']}@{candidate['version']}.json")
    )
    destination = root / relative
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise WorkplaceCandidateStorageError(
                f"Candidate storage path cannot traverse a symlink: {current}"
            )
    destination = destination.resolve(strict=False)
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise WorkplaceCandidateStorageError(
            "Candidate Dot destination escapes the Workplace root"
        ) from error
    return destination


def _persist_candidate_dot(
    workplace_root: str | os.PathLike[str],
    candidate: Mapping[str, Any],
    *,
    authority_id: str,
) -> dict[str, Any]:
    checked = validate_capability_dot(candidate, require_active=False)
    if _record_state(checked) != "candidate":
        raise WorkplaceCandidateStorageError("Only an inactive Candidate Dot may be stored")
    destination = _candidate_storage_path(Path(workplace_root).expanduser(), checked)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical(checked) + "\n").encode("utf-8")
    created = not destination.exists()
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkplaceCandidateStorageError(
                "Existing Candidate Dot record cannot be read safely"
            ) from error
        existing = validate_capability_dot(existing, require_active=False)
        if _canonical(existing) != _canonical(checked):
            raise WorkplaceCandidateStorageError(
                "Candidate Dot path already contains different content"
            )
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Hard-link publication is atomic and cannot clobber a
                # concurrently-created Candidate record.  ``os.replace``
                # would silently overwrite that conflict.
                os.link(temporary_name, destination)
            except FileExistsError:
                created = False
                try:
                    concurrent = json.loads(destination.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise WorkplaceCandidateStorageError(
                        "Concurrent Candidate Dot record cannot be read safely"
                    ) from error
                concurrent = validate_capability_dot(concurrent, require_active=False)
                if _canonical(concurrent) != _canonical(checked):
                    raise WorkplaceCandidateStorageError(
                        "Concurrent Candidate Dot write contains different content"
                    ) from None
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
        except Exception:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
            raise
    try:
        read_back = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkplaceCandidateStorageError("Candidate Dot read-back failed") from error
    read_back = validate_capability_dot(read_back, require_active=False)
    if _canonical(read_back) != _canonical(checked):
        raise WorkplaceCandidateStorageError("Candidate Dot read-back does not match the write")
    root = Path(workplace_root).expanduser().resolve()
    return {
        "record_type": "workplace-candidate-dot-write-receipt",
        "record_version": 1,
        "status": "created" if created else "idempotent-read-back",
        "authority_id": authority_id,
        "path": destination.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "candidate_ref": _ref(checked, "dot"),
        "read_back_verified": True,
        "candidate_only": True,
        "activation": False,
        "version": False,
        "publication": False,
    }


def _recovery_responsibility_score(
    record: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[int, str]:
    wanted_text = " ".join(
        [
            _request_text(request),
            " ".join(_request_ports(request, "inputs")),
            " ".join(_request_ports(request, "outputs")),
        ]
    ).casefold()
    wanted = set(re.findall(r"[a-z0-9][a-z0-9._-]+", wanted_text))
    record_text = " ".join(
        [
            str(record.get("responsibility", "")),
            " ".join(str(item) for item in record.get("inputs", [])),
            " ".join(str(item) for item in record.get("outputs", [])),
        ]
    ).casefold()
    observed = set(re.findall(r"[a-z0-9][a-z0-9._-]+", record_text))
    return len(wanted & observed), str(record.get("normalized_signature", ""))


def recover_missing_capability(
    request: Mapping[str, Any] | str,
    *,
    actions: Any = None,
    workflows: Any = None,
    dots: Any = None,
    workplace_sources: Any,
    workplace_root: str | os.PathLike[str],
    project: Mapping[str, Any],
    candidate_persistence_authority: Mapping[str, Any],
    candidate_execution_authority: Mapping[str, Any],
    candidate_executor: Callable[[Mapping[str, Any], Any], Any],
    candidate_verifier: Callable[[Mapping[str, Any], Any], bool] | None = None,
    source_documents: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover one missing capability without changing the active graph.

    The complete retained Source catalogue is searched first.  If it is
    insufficient, the function returns an external-research request and makes
    no write.  Otherwise it performs bounded responsibility extraction,
    synthesises one inactive Candidate Dot, validates separate persistence and
    execution authorities, atomically writes/reads the Dot in the Workplace,
    executes one current-task trial, and emits canonical compact evidence.
    """

    request_value = {"intent": request} if isinstance(request, str) else _copy(request)
    if not isinstance(request_value, Mapping):
        raise RuntimeValidationError("runtime request must be an object or string")
    request_value = dict(request_value)
    active_before = _copy(
        {"actions": actions or [], "workflows": workflows or [], "dots": dots or []}
    )
    active_digest = _digest(active_before)
    source_input_digest = _digest(workplace_sources)
    current = _now(now)
    scope = _normalise_scope(request_value, project)
    resolution = resolve_request(
        request_value,
        actions=actions,
        workflows=workflows,
        dots=dots,
        workplace_sources=workplace_sources,
        project=project,
        now=current,
        context=context,
    )
    if resolution["state"] != MISSING_CAPABILITY:
        raise RuntimeResolutionError("missing-capability recovery requires a missing capability")
    if resolution["external_research_request"] is not None:
        return {
            "record_type": RECOVERY_RECORD_TYPE,
            "record_version": RECOVERY_RECORD_VERSION,
            "status": "external-research-required",
            "resolution": resolution,
            "candidate_dot": None,
            "candidate_write_receipt": None,
            "trial_request": None,
            "current_task_result": None,
            "compact_evidence": None,
            "active_graph_unchanged": _digest(active_before) == active_digest,
            "active_graph_mutations": [],
            "activation": False,
            "version": False,
            "publication": False,
        }

    # ``resolve_request`` validated this exact, unchanged input while checking
    # the complete catalogue.  Reuse the same in-memory records instead of
    # repeating hundreds of schema validations in one recovery transaction.
    if _digest(workplace_sources) != source_input_digest:
        raise RuntimeValidationError("Retained Workplace Source input changed during recovery")
    raw_source_records = (
        workplace_sources.get("sources")
        if isinstance(workplace_sources, Mapping)
        else workplace_sources
    )
    source_records = _records(raw_source_records, "source_id", "Source records")
    by_source_id = {item["source_id"]: item for item in source_records}
    if len(by_source_id) != len(source_records):
        raise RuntimeValidationError("Retained Workplace Source ids must be unique")
    suitable_ids = resolution["suitable_source_refs"]
    suitable = [by_source_id[source_id] for source_id in suitable_ids]
    documents: Any = source_documents
    if source_documents is not None and "documents" not in source_documents:
        documents = {"documents": source_documents}
    extracted: list[dict[str, Any]] = []
    for source in suitable:
        extracted.extend(extract_responsibilities(source, documents))
    extracted = [item for item in extracted if item.get("candidate_contribution_allowed") is True]
    if not extracted:
        raise RuntimeResolutionError(
            "Retained Sources matched but yielded no candidate-contributing responsibility"
        )
    scored = sorted(
        [(_recovery_responsibility_score(item, request_value), item) for item in extracted],
        key=lambda item: (-item[0][0], item[0][1]),
    )
    best_score = scored[0][0][0]
    best = [item for score, item in scored if score[0] == best_score]
    signatures = {str(item.get("normalized_signature")) for item in best}
    if len(signatures) != 1:
        raise RuntimeResolutionError(
            "Multiple distinct retained responsibilities match equally; "
            "bounded recovery is ambiguous"
        )
    candidate = synthesize_candidate_dot(best)
    checked_persistence = _validate_candidate_persistence_authority(
        candidate_persistence_authority,
        request=request_value,
        scope=scope,
        source_refs=suitable_ids,
        now=current,
    )
    trial_request = _candidate_trial_request(
        request_value,
        scope,
        authority=candidate_execution_authority,
        candidate_dot=candidate,
        now=current,
    )
    if trial_request is None:
        raise CandidateExecutionScopeError("Candidate execution authority is required")
    validate_candidate_execution(
        candidate,
        trial_request["execution_authority"],
        project_id=scope["project_id"],
        project_revision=scope["project_revision"],
        task_id=scope["task_id"],
        now=current,
    )
    write_receipt = _persist_candidate_dot(
        workplace_root,
        candidate,
        authority_id=checked_persistence["authority_id"],
    )

    implementations = sorted(
        candidate["implementations"], key=lambda item: (item["implementation_id"], item["version"])
    )
    implementation = implementations[0]
    plan = {
        "sequence": 1,
        "dot_ref": _ref(candidate, "dot"),
        "implementation_ref": _ref(implementation, "implementation", include_provider=True),
        "implementation": _copy(implementation),
        "inputs": _port_names(candidate.get("inputs")),
        "outputs": _port_names(candidate.get("outputs")),
    }
    try:
        output = candidate_executor(_copy(plan), _copy(request_value))
        verification = verify_dot_output(plan, output, verifier=candidate_verifier)
        failure = None
    except Exception as error:
        output = None
        verification = {
            "status": "failed",
            "checks": list(plan["outputs"]),
            "reason": f"executor error: {error}",
        }
        failure = {
            "stage": "candidate-implementation-execution",
            "dot_ref": plan["dot_ref"],
            "implementation_ref": plan["implementation_ref"],
            "step_sequence": 1,
            "reason": verification["reason"],
        }
    if verification["status"] != "verified" and failure is None:
        failure = {
            "stage": "candidate-output-verification",
            "dot_ref": plan["dot_ref"],
            "implementation_ref": plan["implementation_ref"],
            "step_sequence": 1,
            "reason": verification.get("reason", "Candidate Dot output verification failed"),
        }
    execution_status = "succeeded" if failure is None else "failed"
    execution = verify_execution(
        {
            "record_type": EXECUTION_RECORD_TYPE,
            "record_version": EXECUTION_RECORD_VERSION,
            "status": execution_status,
            "route_state": MISSING_CAPABILITY,
            "action_ref": None,
            "workflow_refs": [],
            "dot_refs": [plan["dot_ref"]],
            "implementation_refs": [plan["implementation_ref"]],
            "steps": [
                {
                    "sequence": 1,
                    "dot_ref": plan["dot_ref"],
                    "implementation_ref": plan["implementation_ref"],
                    "input": _copy(request_value),
                    "output": _copy(output),
                    "verification": verification,
                    "status": verification["status"],
                }
            ],
            "outcome": {
                "status": execution_status,
                "summary": "bounded missing-capability current task trial",
                "result": _copy(output),
            },
            "duration_ms": 0,
            "cost": 0,
            "work_signature": derive_work_signature(
                action_ref=None,
                workflow_refs=[],
                dot_refs=[plan["dot_ref"]],
                implementation_refs=[plan["implementation_ref"]],
                route_state=MISSING_CAPABILITY,
            ),
            "verification": {
                "status": verification["status"],
                "checks": ["Candidate Dot output verified within exact trial scope"],
            },
            "failure": failure,
            "persistent": False,
            "mutations": [],
            "candidate_trial": True,
        }
    )
    evidence = build_compact_evidence(execution)
    active_unchanged = (
        _digest({"actions": actions or [], "workflows": workflows or [], "dots": dots or []})
        == active_digest
    )
    if not active_unchanged:
        raise RuntimeExecutionError("Active capability graph changed during Candidate recovery")
    return {
        "record_type": RECOVERY_RECORD_TYPE,
        "record_version": RECOVERY_RECORD_VERSION,
        "status": execution["status"],
        "resolution": resolution,
        "responsibility_evidence": _copy(best),
        "candidate_dot": candidate,
        "candidate_write_receipt": write_receipt,
        "trial_request": trial_request,
        "current_task_result": execution,
        "compact_evidence": evidence,
        "candidate_persistence_state_change": True,
        "active_persistence_state_change": False,
        "active_graph_unchanged": True,
        "active_graph_mutations": [],
        "external_research_request": None,
        "activation": False,
        "version": False,
        "publication": False,
    }


def _simulation_value(simulated_outputs: Any, dot_id: str, sequence: int) -> Any:
    if simulated_outputs is None:
        return None
    if isinstance(simulated_outputs, Mapping):
        if dot_id in simulated_outputs:
            return simulated_outputs[dot_id]
        if str(sequence) in simulated_outputs:
            return simulated_outputs[str(sequence)]
        return None
    if isinstance(simulated_outputs, Sequence) and not isinstance(
        simulated_outputs, (str, bytes, bytearray)
    ):
        return simulated_outputs[sequence - 1] if len(simulated_outputs) >= sequence else None
    return simulated_outputs


def _output_contains(value: Any, expected: Sequence[str]) -> bool:
    if not expected:
        return True
    if isinstance(value, Mapping):
        if value.get("verified") is False:
            return False
        status = value.get("status")
        if status in {"failed", "unverified", "rejected"}:
            return False
        if value.get("outputs") is not None:
            return _output_contains(value["outputs"], expected)
        if value.get("output") is not None:
            return _output_contains(value["output"], expected)
        keys = {str(key).casefold() for key in value}
        return set(expected).issubset(keys)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        names = set()
        for item in value:
            if isinstance(item, Mapping):
                name = item.get("id", item.get("name", item.get("type")))
                if name is not None:
                    names.add(str(name).casefold())
            else:
                names.add(str(item).casefold())
        return set(expected).issubset(names)
    if isinstance(value, str):
        return len(expected) == 1 and value.casefold() == expected[0].casefold()
    return False


def verify_dot_output(
    dot_plan: Mapping[str, Any],
    output: Any,
    *,
    verifier: Callable[[Mapping[str, Any], Any], bool] | None = None,
) -> dict[str, Any]:
    """Verify one Dot output without invoking its Implementation/provider."""

    expected = [str(item) for item in dot_plan.get("outputs", [])]
    if verifier is not None:
        try:
            passed = bool(verifier(_copy(dict(dot_plan)), _copy(output)))
        except Exception as error:
            return {"status": "failed", "checks": [], "reason": f"verifier error: {error}"}
        if not passed:
            return {
                "status": "failed",
                "checks": ["custom verifier"],
                "reason": "custom verifier rejected output",
            }
        return {"status": "verified", "checks": ["custom verifier"]}
    if output is None:
        return {"status": "failed", "checks": expected, "reason": "Dot output was not supplied"}
    if not _output_contains(output, expected):
        return {
            "status": "failed",
            "checks": expected,
            "reason": "Dot output does not satisfy declared outputs",
        }
    if isinstance(output, Mapping) and output.get("verified") is False:
        return {
            "status": "failed",
            "checks": expected,
            "reason": "simulation marked output unverified",
        }
    return {"status": "verified", "checks": expected}


def verify_execution(execution: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the ordered receipt and preserve the first Dot failure attribution."""

    if not isinstance(execution, Mapping) or execution.get("record_type") != EXECUTION_RECORD_TYPE:
        raise RuntimeVerificationError("runtime execution record is not canonical")
    value = _copy(dict(execution))
    steps = value.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes, bytearray)):
        raise RuntimeVerificationError("runtime execution steps must be a list")
    failure = value.get("failure")
    passed = True
    for step in steps:
        if not isinstance(step, Mapping):
            raise RuntimeVerificationError("runtime execution step must be an object")
        verification = step.get("verification")
        if not isinstance(verification, Mapping) or verification.get("status") != "verified":
            passed = False
            if failure is None:
                failure = {
                    "stage": "output-verification",
                    "dot_ref": _copy(step.get("dot_ref")),
                    "implementation_ref": _copy(step.get("implementation_ref")),
                    "step_sequence": step.get("sequence"),
                    "reason": "Dot output verification failed",
                }
            break
    if value.get("status") == "failed":
        passed = False
    value["status"] = "succeeded" if passed else "failed"
    value["failure"] = failure
    value["verification"] = {
        "status": "verified" if passed else "failed",
        "checks": ["each Dot output verified before the next step"],
        "verified_steps": [
            step.get("sequence")
            for step in steps
            if isinstance(step, Mapping)
            and isinstance(step.get("verification"), Mapping)
            and step["verification"].get("status") == "verified"
        ],
    }
    value["persistent"] = False
    return value


def simulate_execution(
    resolution: Mapping[str, Any],
    *,
    simulated_outputs: Any = None,
    outputs: Any = None,
    duration_ms: int | float = 0,
    duration: int | float | None = None,
    cost: Any = 0,
    work_signature: str | Mapping[str, Any] | None = None,
    verifier: Callable[[Mapping[str, Any], Any], bool] | None = None,
) -> dict[str, Any]:
    """Execute only a local simulation and verify each Dot before continuing."""

    if not isinstance(resolution, Mapping) or resolution.get("record_type") != RUNTIME_RECORD_TYPE:
        raise RuntimeExecutionError("runtime resolution record is not canonical")
    state = resolution.get("state", resolution.get("route_state"))
    if state in {UNAVAILABLE, MISSING_CAPABILITY}:
        raise RuntimeExecutionError(f"Cannot execute blocked runtime state: {state}")
    plans = resolution.get("dot_plans", [])
    if not isinstance(plans, Sequence) or isinstance(plans, (str, bytes, bytearray)) or not plans:
        raise RuntimeExecutionError("runtime resolution has no executable Dot plan")
    elapsed = duration if duration is not None else duration_ms
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise RuntimeExecutionError("duration_ms must be non-negative")
    if isinstance(cost, bool) or not isinstance(cost, (int, float, Mapping)):
        raise RuntimeExecutionError("cost must be numeric or an object")
    if isinstance(cost, (int, float)) and cost < 0:
        raise RuntimeExecutionError("cost must be non-negative")
    supplied_outputs = simulated_outputs if simulated_outputs is not None else outputs
    step_receipts: list[dict[str, Any]] = []
    failure = None
    for sequence, raw_plan in enumerate(plans, start=1):
        if not isinstance(raw_plan, Mapping):
            raise RuntimeExecutionError("runtime Dot plan must be an object")
        dot_ref = _ref_from_mapping(raw_plan.get("dot_ref"), "dot")
        implementation_ref = _ref_from_mapping(raw_plan.get("implementation_ref"), "implementation")
        output = _simulation_value(supplied_outputs, dot_ref["dot_id"], sequence)
        if output is None:
            # A simulation has no provider result.  The declared output shape
            # is the only safe synthetic value and remains explicitly marked
            # as simulated evidence.
            output = {"outputs": list(raw_plan.get("outputs", [])), "simulated": True}
        verification = verify_dot_output(raw_plan, output, verifier=verifier)
        receipt = {
            "sequence": sequence,
            "dot_ref": dot_ref,
            "implementation_ref": implementation_ref,
            "output": _copy(output),
            "verification": verification,
            "status": verification["status"],
        }
        step_receipts.append(receipt)
        if verification["status"] != "verified":
            failure = {
                "stage": "output-verification",
                "dot_ref": dot_ref,
                "implementation_ref": implementation_ref,
                "step_sequence": sequence,
                "reason": verification.get("reason", "Dot output verification failed"),
            }
            break
    status = "succeeded" if failure is None and len(step_receipts) == len(plans) else "failed"
    if work_signature is None:
        work_signature = derive_work_signature(
            action_ref=resolution.get("action_ref"),
            workflow_refs=resolution.get("workflow_refs", []),
            dot_refs=resolution.get("dot_refs", []),
            implementation_refs=resolution.get("implementation_refs", []),
            route_state=state,
        )
    execution: dict[str, Any] = {
        "record_type": EXECUTION_RECORD_TYPE,
        "record_version": EXECUTION_RECORD_VERSION,
        "status": status,
        "route_state": state,
        "action_ref": _copy(resolution.get("action_ref")),
        "workflow_refs": _copy(resolution.get("workflow_refs", [])),
        "dot_refs": _copy(resolution.get("dot_refs", [])),
        "implementation_refs": _copy(resolution.get("implementation_refs", [])),
        "steps": step_receipts,
        "outcome": {"status": status, "summary": "simulated execution"},
        "duration_ms": elapsed,
        "cost": _copy(cost),
        "work_signature": _copy(work_signature),
        "verification": {
            "status": "verified" if status == "succeeded" else "failed",
            "checks": ["each Dot output verified before the next step"],
        },
        "failure": failure,
        "persistent": False,
        "mutations": [],
    }
    return verify_execution(execution)


def execute_resolution(
    resolution: Mapping[str, Any],
    *,
    executor: Callable[[Mapping[str, Any], Any], Any],
    initial_input: Any = None,
    duration_ms: int | float = 0,
    cost: Any = 0,
    verifier: Callable[[Mapping[str, Any], Any], bool] | None = None,
) -> dict[str, Any]:
    """Execute and verify one resolved current task through bounded callbacks.

    The runtime supplies only the selected Dot plan and the preceding output.
    It does not discover or import a provider.  A failed callback or failed Dot
    contract stops the chain immediately and is attributed to the exact Dot and
    Implementation reference.  Successful missing-Workflow work remains an
    Execution Composition and never creates a persistent Workflow.
    """

    if not callable(executor):
        raise RuntimeExecutionError("runtime executor must be callable")
    if not isinstance(resolution, Mapping) or resolution.get("record_type") != RUNTIME_RECORD_TYPE:
        raise RuntimeExecutionError("runtime resolution record is not canonical")
    state = resolution.get("state", resolution.get("route_state"))
    if state not in {EXACT, PARTIAL, MISSING_WORKFLOW}:
        raise RuntimeExecutionError(f"Cannot execute blocked runtime state: {state}")
    plans = resolution.get("dot_plans", [])
    if not isinstance(plans, Sequence) or isinstance(plans, (str, bytes, bytearray)) or not plans:
        raise RuntimeExecutionError("runtime resolution has no executable Dot plan")
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, (int, float))
        or duration_ms < 0
    ):
        raise RuntimeExecutionError("duration_ms must be non-negative")
    if isinstance(cost, bool) or not isinstance(cost, (int, float, Mapping)):
        raise RuntimeExecutionError("cost must be numeric or an object")
    if isinstance(cost, (int, float)) and cost < 0:
        raise RuntimeExecutionError("cost must be non-negative")

    previous = _copy(initial_input)
    step_receipts: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    for sequence, raw_plan in enumerate(plans, start=1):
        if not isinstance(raw_plan, Mapping):
            raise RuntimeExecutionError("runtime Dot plan must be an object")
        plan = _copy(dict(raw_plan))
        dot_ref = _ref_from_mapping(plan.get("dot_ref"), "dot")
        implementation_ref = _ref_from_mapping(plan.get("implementation_ref"), "implementation")
        try:
            output = executor(plan, _copy(previous))
        except Exception as error:
            verification = {
                "status": "failed",
                "checks": list(plan.get("outputs", [])),
                "reason": f"executor error: {error}",
            }
            failure = {
                "stage": "implementation-execution",
                "dot_ref": dot_ref,
                "implementation_ref": implementation_ref,
                "step_sequence": sequence,
                "reason": str(verification["reason"]),
            }
            step_receipts.append(
                {
                    "sequence": sequence,
                    "dot_ref": dot_ref,
                    "implementation_ref": implementation_ref,
                    "input": _copy(previous),
                    "output": None,
                    "verification": verification,
                    "status": "failed",
                }
            )
            break
        verification = verify_dot_output(plan, output, verifier=verifier)
        step_receipts.append(
            {
                "sequence": sequence,
                "dot_ref": dot_ref,
                "implementation_ref": implementation_ref,
                "input": _copy(previous),
                "output": _copy(output),
                "verification": verification,
                "status": verification["status"],
            }
        )
        if verification["status"] != "verified":
            failure = {
                "stage": "output-verification",
                "dot_ref": dot_ref,
                "implementation_ref": implementation_ref,
                "step_sequence": sequence,
                "reason": verification.get("reason", "Dot output verification failed"),
            }
            break
        previous = _copy(output)

    status = "succeeded" if failure is None and len(step_receipts) == len(plans) else "failed"
    signature = derive_work_signature(
        action_ref=resolution.get("action_ref"),
        workflow_refs=resolution.get("workflow_refs", []),
        dot_refs=resolution.get("dot_refs", []),
        implementation_refs=resolution.get("implementation_refs", []),
        route_state=state,
    )
    execution = {
        "record_type": EXECUTION_RECORD_TYPE,
        "record_version": EXECUTION_RECORD_VERSION,
        "status": status,
        "route_state": state,
        "action_ref": _copy(resolution.get("action_ref")),
        "workflow_refs": _copy(resolution.get("workflow_refs", [])),
        "dot_refs": _copy(resolution.get("dot_refs", [])),
        "implementation_refs": _copy(resolution.get("implementation_refs", [])),
        "steps": step_receipts,
        "outcome": {
            "status": status,
            "summary": "bounded current task execution",
            "result": _copy(previous) if status == "succeeded" else None,
        },
        "duration_ms": duration_ms,
        "cost": _copy(cost),
        "work_signature": signature,
        "verification": {
            "status": "verified" if status == "succeeded" else "failed",
            "checks": ["each Dot output verified before the next step"],
        },
        "failure": failure,
        "persistent": False,
        "mutations": [],
        "execution_mode": "bounded-caller-implementation",
    }
    return verify_execution(execution)


execute_runtime_resolution = execute_resolution


def _evidence_ref(value: Any, kind: str) -> dict[str, Any]:
    return _ref_from_mapping(value, kind)


def _validate_evidence_ref_list(value: Any, kind: str, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RuntimeValidationError(f"{label} must be a list")
    result = [_evidence_ref(item, kind) for item in value]
    keys = [_ref_text(item, kind) for item in result]
    if len(keys) != len(set(keys)):
        raise RuntimeValidationError(f"{label} contains duplicate references")
    return result


def build_compact_evidence(
    execution: Mapping[str, Any] | None = None,
    *,
    resolution: Mapping[str, Any] | None = None,
    outcome: Any = None,
    duration_ms: int | float | None = None,
    duration: int | float | None = None,
    cost: Any = None,
    work_signature: str | Mapping[str, Any] | None = None,
    verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact evidence record containing refs, not definitions."""

    source: Mapping[str, Any] | None = execution if execution is not None else resolution
    if source is None or not isinstance(source, Mapping):
        raise RuntimeValidationError("compact evidence needs a resolution or execution record")
    source_type = source.get("record_type")
    if source_type not in {RUNTIME_RECORD_TYPE, EXECUTION_RECORD_TYPE}:
        raise RuntimeValidationError("compact evidence source is not a runtime record")
    route_state = source.get("route_state", source.get("state", EXACT))
    if route_state not in ROUTE_STATES:
        route_state = ROUTE_STATE_ALIASES.get(str(route_state), route_state)
    if route_state not in ROUTE_STATES:
        raise RuntimeValidationError("compact evidence route_state is invalid")
    action_ref = source.get("action_ref")
    workflow_refs = source.get("workflow_refs", [])
    dot_refs = source.get("dot_refs", [])
    implementation_refs = source.get("implementation_refs", [])
    if action_ref is not None:
        action_ref = _evidence_ref(action_ref, "action")
    workflow_refs = _validate_evidence_ref_list(workflow_refs, "workflow", "workflow_refs")
    dot_refs = _validate_evidence_ref_list(dot_refs, "dot", "dot_refs")
    implementation_refs = _validate_evidence_ref_list(
        implementation_refs, "implementation", "implementation_refs"
    )
    status = source.get("status", "succeeded")
    if status not in {"succeeded", "failed", "blocked"}:
        raise RuntimeValidationError("compact evidence status is invalid")
    final_outcome = _copy(
        outcome if outcome is not None else source.get("outcome", {"status": status})
    )
    if isinstance(final_outcome, str):
        final_outcome = {"status": status, "summary": final_outcome}
    if not isinstance(final_outcome, Mapping):
        raise RuntimeValidationError("compact evidence outcome must be an object or string")
    elapsed = duration if duration is not None else duration_ms
    if elapsed is None:
        elapsed = source.get("duration_ms", 0)
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise RuntimeValidationError("compact evidence duration_ms must be non-negative")
    final_cost = _copy(cost if cost is not None else source.get("cost", 0))
    if isinstance(final_cost, bool) or not isinstance(final_cost, (int, float, Mapping)):
        raise RuntimeValidationError("compact evidence cost must be numeric or an object")
    if isinstance(final_cost, (int, float)) and final_cost < 0:
        raise RuntimeValidationError("compact evidence cost must be non-negative")
    final_signature = _copy(
        work_signature if work_signature is not None else source.get("work_signature")
    )
    if final_signature is None:
        final_signature = derive_work_signature(
            action_ref=action_ref,
            workflow_refs=workflow_refs,
            dot_refs=dot_refs,
            implementation_refs=implementation_refs,
            route_state=route_state,
        )
    if isinstance(final_signature, str):
        _nonblank(final_signature, "work_signature")
    elif not isinstance(final_signature, Mapping):
        raise RuntimeValidationError("work_signature must be a string or object")
    final_verification = _copy(
        verification if verification is not None else source.get("verification", {})
    )
    if not isinstance(final_verification, Mapping):
        raise RuntimeValidationError("compact evidence verification must be an object")
    final_verification.setdefault("status", "verified" if status == "succeeded" else "failed")
    record: dict[str, Any] = {
        "record_type": EVIDENCE_RECORD_TYPE,
        "record_version": EVIDENCE_RECORD_VERSION,
        "status": status,
        "route_state": route_state,
        "action_ref": action_ref,
        "workflow_refs": workflow_refs,
        "dot_refs": dot_refs,
        "implementation_refs": implementation_refs,
        "outcome": dict(final_outcome),
        "duration_ms": elapsed,
        "cost": final_cost,
        "work_signature": final_signature,
        "verification": dict(final_verification),
        "failure": _copy(source.get("failure")),
        "persistent": False,
        "promoted": False,
    }
    record["evidence_digest"] = _digest(record)
    return validate_compact_evidence(record)


def validate_compact_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a compact receipt and reject embedded capability definitions."""

    if not isinstance(value, Mapping):
        raise RuntimeValidationError("compact evidence must be an object")
    record = _copy(dict(value))
    if (
        record.get("record_type") != EVIDENCE_RECORD_TYPE
        or record.get("record_version") != EVIDENCE_RECORD_VERSION
    ):
        raise RuntimeValidationError("compact evidence record type or version is not canonical")
    for forbidden in ("action", "workflow", "dot", "implementation", "definitions", "source"):
        if forbidden in record:
            raise RuntimeValidationError(
                "compact evidence must contain references, not definitions"
            )
    if record.get("route_state") not in ROUTE_STATES:
        raise RuntimeValidationError("compact evidence route_state is invalid")
    if record.get("persistent") is not False or record.get("promoted") is not False:
        raise RuntimeValidationError("compact evidence cannot grant persistence or promotion")
    if record.get("action_ref") is not None:
        record["action_ref"] = _evidence_ref(record["action_ref"], "action")
    record["workflow_refs"] = _validate_evidence_ref_list(
        record.get("workflow_refs", []), "workflow", "workflow_refs"
    )
    record["dot_refs"] = _validate_evidence_ref_list(record.get("dot_refs", []), "dot", "dot_refs")
    record["implementation_refs"] = _validate_evidence_ref_list(
        record.get("implementation_refs", []), "implementation", "implementation_refs"
    )
    status = record.get("status")
    if status not in {"succeeded", "failed", "blocked"}:
        raise RuntimeValidationError("compact evidence status is invalid")
    verification = record.get("verification")
    if not isinstance(verification, Mapping) or verification.get("status") not in {
        "verified",
        "failed",
        "unverified",
    }:
        raise RuntimeValidationError("compact evidence verification is invalid")
    if status == "succeeded" and verification.get("status") != "verified":
        raise RuntimeValidationError("successful compact evidence requires verified output")
    if (
        isinstance(record.get("duration_ms"), bool)
        or not isinstance(record.get("duration_ms"), (int, float))
        or record["duration_ms"] < 0
    ):
        raise RuntimeValidationError("compact evidence duration_ms must be non-negative")
    cost = record.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float, Mapping)):
        raise RuntimeValidationError("compact evidence cost is invalid")
    if isinstance(cost, (int, float)) and cost < 0:
        raise RuntimeValidationError("compact evidence cost must be non-negative")
    if not isinstance(record.get("outcome"), Mapping):
        raise RuntimeValidationError("compact evidence outcome is invalid")
    signature = record.get("work_signature")
    if not isinstance(signature, (str, Mapping)) or (
        isinstance(signature, str) and not signature.strip()
    ):
        raise RuntimeValidationError("compact evidence work_signature is invalid")
    digest = record.pop("evidence_digest", None)
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise RuntimeValidationError("compact evidence digest must be SHA-256")
    if _digest(record) != digest:
        raise RuntimeValidationError("compact evidence digest does not match record")
    record["evidence_digest"] = digest
    return record


def execute_simulation(resolution: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Readable alias for :func:`simulate_execution`."""

    return simulate_execution(resolution, **kwargs)


def compact_evidence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Readable alias for :func:`build_compact_evidence`."""

    return build_compact_evidence(*args, **kwargs)


def record_compact_evidence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return build_compact_evidence(*args, **kwargs)


class CapabilityRuntime:
    """Stateless convenience facade over the pure runtime functions."""

    def __init__(
        self,
        *,
        actions: Any = None,
        workflows: Any = None,
        dots: Any = None,
        sources: Any = None,
    ) -> None:
        self.actions = _copy(actions)
        self.workflows = _copy(workflows)
        self.dots = _copy(dots)
        self.sources = _copy(sources)

    def resolve(self, request: Mapping[str, Any] | str, **kwargs: Any) -> dict[str, Any]:
        return resolve_request(
            request,
            actions=self.actions,
            workflows=self.workflows,
            dots=self.dots,
            sources=self.sources,
            **kwargs,
        )

    def simulate(self, resolution: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return simulate_execution(resolution, **kwargs)

    def execute(self, resolution: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        if "executor" in kwargs:
            return execute_resolution(resolution, **kwargs)
        return simulate_execution(resolution, **kwargs)

    def compact(self, execution: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return build_compact_evidence(execution, **kwargs)

    def onboard(self, active_graph: Mapping[str, Any]) -> dict[str, Any]:
        return load_active_capabilities_for_workplace(active_graph)

    def recover(self, request: Mapping[str, Any] | str, **kwargs: Any) -> dict[str, Any]:
        return recover_missing_capability(
            request,
            actions=self.actions,
            workflows=self.workflows,
            dots=self.dots,
            workplace_sources=self.sources,
            **kwargs,
        )


RuntimeStateMachine = CapabilityRuntime
CapabilityResolver = CapabilityRuntime
resolve_runtime = resolve_request
resolve_capability = resolve_request
resolve_action = resolve_request
select_runtime_implementation = resolve_implementation
validate_runtime_evidence = validate_compact_evidence
validate_evidence = validate_compact_evidence


__all__ = [
    "CapabilityResolver",
    "CapabilityRuntime",
    "CapabilityRuntimeError",
    "CapabilityResolutionError",
    "CandidatePersistenceAuthorityError",
    "CandidateExecutionScopeError",
    "EVIDENCE_RECORD_TYPE",
    "EVIDENCE_RECORD_VERSION",
    "EXECUTION_RECORD_TYPE",
    "EXECUTION_RECORD_VERSION",
    "EXACT",
    "ImplementationResolutionError",
    "MISSING_CAPABILITY",
    "MISSING_WORKFLOW",
    "ONBOARDING_RECORD_TYPE",
    "ONBOARDING_RECORD_VERSION",
    "PARTIAL",
    "PERSISTENCE_AUTHORITY_RECORD_TYPE",
    "PERSISTENCE_AUTHORITY_RECORD_VERSION",
    "RECOVERY_RECORD_TYPE",
    "RECOVERY_RECORD_VERSION",
    "ROUTE_STATES",
    "RuntimeExecutionError",
    "RuntimeResolutionError",
    "RuntimeStateMachine",
    "RuntimeValidationError",
    "RuntimeVerificationError",
    "UNAVAILABLE",
    "WorkplaceCandidateStorageError",
    "build_compact_evidence",
    "compact_evidence",
    "derive_work_signature",
    "execute_resolution",
    "execute_runtime_resolution",
    "execute_simulation",
    "initialise_workplace_capabilities",
    "initialize_workplace_capabilities",
    "load_active_capabilities_for_workplace",
    "record_compact_evidence",
    "recover_missing_capability",
    "resolve_action",
    "resolve_capability",
    "resolve_implementation",
    "resolve_request",
    "resolve_runtime",
    "runtime_request_digest",
    "select_runtime_implementation",
    "simulate_execution",
    "validate_compact_evidence",
    "validate_evidence",
    "validate_runtime_evidence",
    "verify_dot_output",
    "verify_execution",
]
