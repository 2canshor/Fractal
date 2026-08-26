"""Contracts for reusable capability Workflows and temporary execution plans.

The genesis compiler is deliberately layered.  A Workflow is a reusable
System record made from ordered Dot references; it is not a Blueprint Flow,
an Action, a Source, or a provider implementation.  An Execution Composition
is the small, project/task-local plan used to try a bounded combination of
already-available records.  It is always temporary and never turns into a
Workflow merely because it succeeds.

This module contains validation and pure constructors only.  It does not read
or write Workplace state and does not import the Dot contract, which lets the
Source, Dot, and Workflow contracts be developed independently in genesis
wave 1a.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

WORKFLOW_RECORD_TYPE = "capability-workflow"
WORKFLOW_RECORD_VERSION = 1
COMPOSITION_RECORD_TYPE = "execution-composition"
COMPOSITION_RECORD_VERSION = 1
COMPOSITION_EVIDENCE_RECORD_TYPE = "execution-composition-evidence"

_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?\Z"
)
_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
    r"[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?Z\Z"
)
_PERSISTENCE_EFFECT_TOKENS = {
    "activate",
    "activation",
    "canonical-write",
    "commit",
    "persist",
    "publish",
    "system-version",
    "write-canonical",
}
_VERIFICATION_STATES = frozenset(
    {"unverified", "in-progress", "verified-staged", "verified", "failed"}
)
_TRIAL_STATES = frozenset({"not-run", "pending", "running", "passed", "failed"})
_SYSTEM_REVIEW_STATES = frozenset({"pending", "in-progress", "completed", "rejected"})
_HUMAN_DECISION_STATES = frozenset({"pending", "approved", "rejected", "deferred"})
_ACTIVATION_STATES = frozenset({"inactive", "ready", "active", "revoked"})

_WORKFLOW_MATERIAL_FIELDS = (
    "human_name",
    "match_contract",
    "inputs",
    "outputs",
    "dot_refs",
    "success_contract",
    "side_effect_contract",
    "recovery",
    "provider_semantics",
)

# These names are intentionally structural.  A Dot can describe a provider
# in its own evidence, but a Workflow/Composition may only refer to the Dot;
# carrying a provider implementation or a raw Source across this boundary is
# a compile-time error.
_FORBIDDEN_REFERENCE_KEYS = {
    "source",
    "source_id",
    "source_ref",
    "source_refs",
    "provider",
    "provider_id",
    "provider_ref",
    "provider_refs",
    "provider_selection",
    "provider_implementation",
    "implementation",
    "implementation_id",
    "tool",
    "tool_id",
    "action",
    "action_id",
    "flow",
    "flow_id",
    "blueprint",
    "blueprint_flow",
    "blueprint_flow_id",
    "instruction",
    "instructions",
    "prompt",
    "prose",
}


class WorkflowError(ValueError):
    """Base error for invalid Workflow or Execution Composition contracts."""


class WorkflowValidationError(WorkflowError):
    """Raised when a Workflow is structurally or semantically invalid."""


class CompositionValidationError(WorkflowError):
    """Raised when a temporary Execution Composition is invalid."""


# A compatibility name makes the boundary easy to catch without creating a
# second hierarchy of errors for callers that only know the capability layer.
CapabilityWorkflowError = WorkflowError


@lru_cache(maxsize=2)
def _validator(filename: str) -> Draft202012Validator:
    schema = json.loads(files("fractal.schemas").joinpath(filename).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_schema(
    value: Mapping[str, Any], filename: str, error_type: type[WorkflowError]
) -> None:
    try:
        _validator(filename).validate(value)
    except ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path)
        location = f" at {path}" if path else ""
        raise error_type(f"Invalid {filename}{location}: {error.message}") from error


def _require_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise WorkflowError(f"Invalid {label}: {value!r}")
    return value


def _require_version(value: Any, label: str = "version") -> str:
    if not isinstance(value, str) or _VERSION_PATTERN.fullmatch(value) is None:
        raise WorkflowError(f"Invalid {label}: {value!r}")
    return value


def _require_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise WorkflowError(f"Invalid {label}: {value!r}")
    return value


def _ensure_not_expired(value: str, label: str, *, now: datetime | None = None) -> None:
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        raise WorkflowError(f"{label} must include a timezone")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise WorkflowError("Composition validation time must include a timezone")
    if current >= observed:
        raise WorkflowError(f"{label} has expired")


def _is_persistent_effect(value: str) -> bool:
    normalised = value.strip().lower().replace("_", "-").replace(" ", "-")
    return normalised in _PERSISTENCE_EFFECT_TOKENS or any(
        token in normalised for token in ("persist", "canonical-write", "activate", "publish")
    )


def _require_nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} must be a non-empty string")
    return value


def _normal_key(key: Any) -> str:
    return str(key).lower().replace("-", "_")


def _reject_forbidden_keys(
    value: Any,
    *,
    path: str = "",
    allow_provider_semantics: bool = False,
    error_type: type[WorkflowError] = WorkflowError,
) -> None:
    """Reject references that would skip the Source -> Dot boundary."""
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _normal_key(raw_key)
            if (
                key in {"provider_semantics", "provider_specific"}
                and allow_provider_semantics
                and path == ""
            ):
                if key == "provider_semantics":
                    _reject_forbidden_keys(
                        child,
                        path=f"{path}{raw_key}.",
                        error_type=error_type,
                    )
                continue
            if (
                key in _FORBIDDEN_REFERENCE_KEYS
                or key.startswith("source_")
                or key.startswith("provider_")
                or key.startswith("implementation_")
            ):
                raise error_type(
                    "Raw Source/provider/Action/Flow reference is not allowed: "
                    f"{raw_key}"
                )
            _reject_forbidden_keys(
                child,
                path=f"{path}{raw_key}.",
                allow_provider_semantics=allow_provider_semantics,
                error_type=error_type,
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(
                child,
                path=f"{path}[{index}].",
                allow_provider_semantics=allow_provider_semantics,
                error_type=error_type,
            )


def _state(record: Mapping[str, Any]) -> str | None:
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        candidate = lifecycle.get("status", lifecycle.get("state"))
        if isinstance(candidate, str):
            return candidate
    status = record.get("status", record.get("state"))
    return status if isinstance(status, str) else None


def _record_id(record: Mapping[str, Any], kind: str) -> str | None:
    key = "workflow_id" if kind == "workflow" else "dot_id"
    value = record.get(key)
    return value if isinstance(value, str) else None


def _record_version(record: Mapping[str, Any]) -> str | None:
    value = record.get("version")
    return value if isinstance(value, str) else None


def _records(values: Any, kind: str) -> list[Mapping[str, Any]]:
    """Normalise a list or id-indexed registry without importing Dot code."""
    if values is None:
        return []
    if isinstance(values, Mapping):
        # A single record is convenient in focused tests; otherwise treat the
        # mapping as an id -> record registry.
        if _record_id(values, kind) is not None:
            return [values]
        records: list[Mapping[str, Any]] = []
        id_key = "workflow_id" if kind == "workflow" else "dot_id"
        for key, item in values.items():
            if not isinstance(item, Mapping):
                continue
            if item.get(id_key) is None and isinstance(key, str):
                records.append({id_key: key, **dict(item)})
            else:
                records.append(item)
        return records
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        return [item for item in values if isinstance(item, Mapping)]
    raise WorkflowError(f"{kind} records must be a record, list, or id-indexed mapping")


def _lookup_record(
    values: Any, kind: str, record_id: str, version: str
) -> Mapping[str, Any] | None:
    for item in _records(values, kind):
        if _record_id(item, kind) == record_id and _record_version(item) == version:
            return item
    return None


def _contract_items(values: Any, label: str, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise WorkflowError(f"{label} must be an ordered list")
    if not values and not allow_empty:
        raise WorkflowError(f"{label} must not be empty")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, value in enumerate(values, start=1):
        if isinstance(value, str):
            item: dict[str, Any] = {
                "id": _require_id(value, f"{label} id"),
                "type": "value",
                "required": True,
            }
        elif isinstance(value, Mapping):
            item = copy.deepcopy(dict(value))
            if "id" not in item:
                for alias in ("input_id", "output_id", "name"):
                    if alias in item:
                        item["id"] = item.pop(alias)
                        break
            item.setdefault("type", "value")
            item.setdefault("required", True)
            _require_id(item.get("id"), f"{label} id")
            if not isinstance(item["required"], bool):
                raise WorkflowError(f"{label} required must be boolean")
        else:
            raise WorkflowError(f"{label}[{index}] must be a string or object")
        item_id = _require_id(item["id"], f"{label} id")
        if item_id in ids:
            raise WorkflowError(f"Duplicate {label} id: {item_id}")
        ids.add(item_id)
        result.append(item)
    return result


def _normalise_dot_refs(values: Any, workflow_status: str) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise WorkflowError("dot_refs must be an ordered list")
    if not values:
        raise WorkflowError("A Workflow requires at least one Dot reference")
    refs: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, value in enumerate(values, start=1):
        if isinstance(value, str):
            raise WorkflowError("Dot references require dot_id and version; raw ids are ambiguous")
        if not isinstance(value, Mapping):
            raise WorkflowError(f"dot_refs[{index}] must be an object")
        item = copy.deepcopy(dict(value))
        if "lifecycle" not in item:
            item["lifecycle"] = item.pop("required_state", item.pop("state", workflow_status))
        item.setdefault("sequence", index)
        if item.get("sequence") != index:
            raise WorkflowError("Dot references must have contiguous sequence values starting at 1")
        item["dot_id"] = _require_id(item.get("dot_id"), "dot_id")
        item["version"] = _require_version(item.get("version"), "Dot version")
        if item["lifecycle"] not in {"active", "candidate"}:
            raise WorkflowError("Dot reference lifecycle must be active or candidate")
        if item["dot_id"] in ids:
            raise WorkflowError(f"Duplicate Dot reference: {item['dot_id']}")
        ids.add(item["dot_id"])
        refs.append(item)
    return refs


def _validate_lifecycle(lifecycle: Mapping[str, Any], *, error_type: type[WorkflowError]) -> str:
    status = lifecycle.get("status", lifecycle.get("state"))
    if (
        lifecycle.get("status") is not None
        and lifecycle.get("state") is not None
        and lifecycle["status"] != lifecycle["state"]
    ):
        raise error_type("Workflow lifecycle status and state disagree")
    if status not in {"candidate", "active", "retired"}:
        raise error_type(f"Invalid Workflow lifecycle status: {status!r}")
    expected = {
        "candidate": (True, False, False),
        "active": (False, True, True),
        "retired": (False, False, False),
    }[status]
    observed = (
        lifecycle.get("candidate"),
        lifecycle.get("active"),
        lifecycle.get("active_surface"),
    )
    if observed != expected:
        raise error_type(
            "Workflow candidate/active/active_surface dimensions do not match lifecycle status"
        )
    return status


def _evidence_ids(value: Any, label: str, *, required: bool = False) -> list[str]:
    if value is None:
        if required:
            raise WorkflowValidationError(f"{label} is required")
        return []
    if not isinstance(value, list):
        raise WorkflowValidationError(f"{label} must be a list")
    result = [_require_nonempty_text(item, label) for item in value]
    if required and not result:
        raise WorkflowValidationError(f"{label} is required")
    if len(result) != len(set(result)):
        raise WorkflowValidationError(f"{label} must contain unique evidence ids")
    return result


def _validate_dimension(
    value: Any,
    label: str,
    states: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"Workflow {label} must be an object")
    result = copy.deepcopy(dict(value))
    if result.get("status") not in states:
        raise WorkflowValidationError(f"Workflow {label} status is invalid")
    _evidence_ids(result.get("evidence_ids"), f"{label}.evidence_ids")
    return result


def _activation_authorised(activation: Mapping[str, Any]) -> bool:
    values = [
        activation[key]
        for key in ("authorised", "authorized")
        if key in activation
    ]
    if not values:
        return False
    if any(not isinstance(value, bool) for value in values):
        raise WorkflowValidationError("activation authorisation flags must be boolean")
    if len(values) == 2 and values[0] != values[1]:
        raise WorkflowValidationError("activation authorisation spellings disagree")
    return values[0]


def _validate_activation(
    value: Any,
    *,
    workflow_version: str,
    lifecycle_status: str,
) -> dict[str, Any]:
    activation = _validate_dimension(value, "activation", _ACTIVATION_STATES)
    authorised = _activation_authorised(activation)
    evidence = activation.get("activation_evidence")
    if evidence is not None:
        if not isinstance(evidence, list):
            raise WorkflowValidationError("activation.activation_evidence must be a list")
        seen: set[str] = set()
        for index, raw in enumerate(evidence):
            if not isinstance(raw, Mapping):
                raise WorkflowValidationError(
                    f"activation.activation_evidence[{index}] must be an object"
                )
            record = dict(raw)
            evidence_id = _require_nonempty_text(
                record.get("evidence_id"),
                f"activation.activation_evidence[{index}].evidence_id",
            )
            if evidence_id in seen:
                raise WorkflowValidationError("activation evidence ids must be unique")
            seen.add(evidence_id)
            if _require_version(
                record.get("workflow_version"),
                f"activation.activation_evidence[{index}].workflow_version",
            ) != workflow_version:
                raise WorkflowValidationError(
                    "activation evidence must name the exact Workflow version"
                )
            if record.get("authorised", record.get("authorized")) is not True:
                raise WorkflowValidationError(
                    "activation evidence must be explicitly authorised"
                )
            if record.get("authority") != "system-version":
                raise WorkflowValidationError(
                    "Workflow activation authority must be an authorised System Version"
                )
            _require_version(
                record.get("system_version"),
                f"activation.activation_evidence[{index}].system_version",
            )
    if activation["status"] == "active":
        if lifecycle_status != "active":
            raise WorkflowValidationError("active activation requires an active Workflow lifecycle")
        if not authorised:
            raise WorkflowValidationError("active activation requires explicit authorisation")
        if not isinstance(evidence, list) or not evidence:
            raise WorkflowValidationError(
                "active activation requires exact-version System Version evidence"
            )
        if _require_version(
            activation.get("activated_version"), "activation.activated_version"
        ) != workflow_version:
            raise WorkflowValidationError(
                "activation.activated_version must equal Workflow version"
            )
    return {**activation, "_authorised": authorised}


def _validate_active_gate(
    workflow: Mapping[str, Any],
    *,
    lifecycle: Mapping[str, Any],
    trial: Mapping[str, Any],
    verification: Mapping[str, Any],
    system_review: Mapping[str, Any],
    human_decision: Mapping[str, Any],
    activation: Mapping[str, Any],
) -> None:
    version = workflow["version"]
    _evidence_ids(
        lifecycle.get("transition_evidence"),
        "lifecycle.transition_evidence",
        required=True,
    )
    if trial["status"] != "passed":
        raise WorkflowValidationError("An active Workflow requires a passed trial")
    _evidence_ids(trial.get("evidence_ids"), "trial.evidence_ids", required=True)
    if trial.get("workflow_version") not in {None, version}:
        raise WorkflowValidationError("trial evidence must name the exact Workflow version")
    if verification["status"] != "verified":
        raise WorkflowValidationError("An active Workflow requires verified evidence")
    _evidence_ids(
        verification.get("evidence_ids"), "verification.evidence_ids", required=True
    )
    if system_review["status"] != "completed":
        raise WorkflowValidationError("An active Workflow requires completed System Review")
    if _require_version(
        system_review.get("workflow_version"), "system_review.workflow_version"
    ) != version:
        raise WorkflowValidationError(
            "System Review must name the exact Workflow version"
        )
    _require_id(system_review.get("review_id"), "system_review.review_id")
    _evidence_ids(
        system_review.get("evidence_ids"),
        "system_review.evidence_ids",
        required=True,
    )
    if human_decision["status"] != "approved":
        raise WorkflowValidationError("An active Workflow requires an approved human decision")
    if human_decision.get("decided_by") != "primary-user":
        raise WorkflowValidationError(
            "Workflow persistence requires a primary-user decision"
        )
    if _require_version(
        human_decision.get("workflow_version"), "human_decision.workflow_version"
    ) != version:
        raise WorkflowValidationError(
            "human decision must name the exact Workflow version"
        )
    _require_id(human_decision.get("decision_id"), "human_decision.decision_id")
    _evidence_ids(
        human_decision.get("evidence_ids"),
        "human_decision.evidence_ids",
        required=True,
    )
    if activation["status"] != "active" or activation["_authorised"] is not True:
        raise WorkflowValidationError(
            "An active Workflow requires authorised System Version activation"
        )


def _validate_material_candidate_reset(workflow: Mapping[str, Any]) -> None:
    expected_statuses = {
        "trial": {"not-run", "pending"},
        "verification": {"unverified"},
        "system_review": {"pending"},
        "human_decision": {"pending"},
        "activation": {"inactive"},
    }
    forbidden_carried_fields = {
        "trial": {"trial_id", "workflow_version", "evidence_ids", "result"},
        "verification": {"workflow_version", "evidence_ids"},
        "system_review": {"review_id", "workflow_version", "evidence_ids", "outcome"},
        "human_decision": {
            "decision_id",
            "workflow_version",
            "decided_by",
            "decided_at",
            "evidence_ids",
            "reason",
        },
        "activation": {
            "authorised",
            "authorized",
            "activated_version",
            "activation_evidence",
            "evidence_ids",
        },
    }
    for label, allowed_statuses in expected_statuses.items():
        value = workflow[label]
        if value.get("status") not in allowed_statuses:
            raise WorkflowValidationError(
                f"material Workflow revision must reset {label}"
            )
        if forbidden_carried_fields[label].intersection(value):
            raise WorkflowValidationError(
                f"material Workflow revision cannot carry prior {label} evidence"
            )


def _validate_provider_semantics(value: Any, provider_specific: Any = None) -> None:
    if value is None and provider_specific is None:
        return
    if value is not None:
        if not isinstance(value, Mapping):
            raise WorkflowValidationError("provider_semantics must be an object")
        if value.get("is_outcome") is not True or not str(value.get("outcome", "")).strip():
            raise WorkflowValidationError(
                "A provider-specific Workflow is allowed only when provider semantics "
                "are the outcome"
            )
        for key in value:
            if _normal_key(key) in _FORBIDDEN_REFERENCE_KEYS or _normal_key(key).startswith(
                "provider_"
            ):
                raise WorkflowValidationError(
                    "Provider implementation or selection cannot be a Workflow reference"
                )
    if provider_specific is not None:
        if not isinstance(provider_specific, Mapping):
            raise WorkflowValidationError("provider_specific must be an object")
        _require_id(provider_specific.get("provider_id"), "provider_specific.provider_id")
        reason = provider_specific.get("intrinsic_provider_responsibility")
        if not isinstance(reason, Mapping):
            raise WorkflowValidationError(
                "provider-specific Workflow requires intrinsic outcome semantics"
            )
        outcome = reason.get("reason_code", reason.get("reason", reason.get("outcome")))
        if not isinstance(outcome, str) or not outcome.strip():
            raise WorkflowValidationError(
                "provider-specific Workflow requires intrinsic outcome semantics"
            )
        evidence = reason.get("evidence_ids")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            raise WorkflowValidationError(
                "provider-specific Workflow outcome semantics require evidence_ids"
            )
        if not evidence or len(evidence) != len(set(evidence)):
            raise WorkflowValidationError(
                "provider-specific Workflow outcome semantics require unique evidence_ids"
            )


def _validate_dot_registry(
    refs: Sequence[Mapping[str, Any]],
    dot_records: Any,
    *,
    workflow_status: str,
) -> None:
    for ref in refs:
        observed = _lookup_record(dot_records, "dot", ref["dot_id"], ref["version"])
        if observed is None:
            raise WorkflowValidationError(
                f"Workflow references missing Dot {ref['dot_id']}@{ref['version']}"
            )
        actual = _state(observed)
        if actual not in {"active", "candidate"}:
            raise WorkflowValidationError(f"Dot is not executable: {ref['dot_id']}")
        if actual != ref["lifecycle"]:
            if workflow_status == "active" and actual == "candidate":
                raise WorkflowValidationError("An active Workflow may reference only active Dots")
            raise WorkflowValidationError(
                f"Dot lifecycle does not match reference: {ref['dot_id']} ({actual})"
            )
        if workflow_status == "active" and actual != "active":
            raise WorkflowValidationError("An active Workflow may reference only active Dots")


def validate_workflow(
    workflow: Mapping[str, Any],
    *,
    dot_records: Any = None,
) -> dict[str, Any]:
    """Validate one canonical Workflow and return it unchanged.

    ``dot_records`` is optional so the contract can be validated while the
    concurrently-developed Dot module is absent.  When supplied, it is an
    authoritative registry and every reference must resolve to the exact
    version and lifecycle state.
    """
    if not isinstance(workflow, Mapping):
        raise WorkflowValidationError("Workflow must be an object")
    _validate_schema(workflow, "capability-workflow.schema.json", WorkflowValidationError)
    _reject_forbidden_keys(
        workflow,
        allow_provider_semantics=True,
        error_type=WorkflowValidationError,
    )
    _require_id(workflow["workflow_id"], "workflow_id")
    _require_version(workflow["version"])
    _require_nonempty_text(workflow["human_name"], "human_name")
    lifecycle = workflow["lifecycle"]
    status = _validate_lifecycle(lifecycle, error_type=WorkflowValidationError)
    trial = _validate_dimension(workflow["trial"], "trial", _TRIAL_STATES)
    verification = _validate_dimension(
        workflow["verification"], "verification", _VERIFICATION_STATES
    )
    system_review = _validate_dimension(
        workflow["system_review"], "system_review", _SYSTEM_REVIEW_STATES
    )
    human_decision = _validate_dimension(
        workflow["human_decision"], "human_decision", _HUMAN_DECISION_STATES
    )
    activation = _validate_activation(
        workflow["activation"],
        workflow_version=workflow["version"],
        lifecycle_status=status,
    )
    if verification["status"] in {"verified-staged", "verified"}:
        _evidence_ids(
            verification.get("evidence_ids"),
            "verification.evidence_ids",
            required=True,
        )
    if status != "active" and activation["status"] == "active":
        raise WorkflowValidationError(
            "active activation requires an active Workflow lifecycle"
        )
    if status == "active":
        _validate_active_gate(
            workflow,
            lifecycle=lifecycle,
            trial=trial,
            verification=verification,
            system_review=system_review,
            human_decision=human_decision,
            activation=activation,
        )
    _validate_provider_semantics(
        workflow.get("provider_semantics"), workflow.get("provider_specific")
    )

    dot_refs = workflow["dot_refs"]
    expected_ids: set[str] = set()
    for sequence, ref in enumerate(dot_refs, start=1):
        if ref["dot_id"] in expected_ids:
            raise WorkflowValidationError(f"Duplicate Dot reference: {ref['dot_id']}")
        expected_ids.add(ref["dot_id"])
        if ref["sequence"] != sequence:
            raise WorkflowValidationError("Dot references must remain in contiguous order")
        if status == "active" and ref["lifecycle"] != "active":
            raise WorkflowValidationError("An active Workflow may reference only active Dots")
    if dot_records is not None:
        _validate_dot_registry(dot_refs, dot_records, workflow_status=status)
    for label in ("inputs", "outputs"):
        _contract_items(workflow[label], label)
    return copy.deepcopy(dict(workflow))


def build_workflow(
    *,
    workflow_id: str,
    version: str,
    human_name: str,
    match_contract: Mapping[str, Any],
    inputs: Sequence[Any],
    outputs: Sequence[Any],
    dot_refs: Sequence[Mapping[str, Any]],
    success_contract: Mapping[str, Any],
    side_effect_contract: Mapping[str, Any],
    recovery: Mapping[str, Any],
    provenance: Mapping[str, Any],
    trial: Mapping[str, Any] | None = None,
    verification: Mapping[str, Any] | None = None,
    system_review: Mapping[str, Any] | None = None,
    human_decision: Mapping[str, Any] | None = None,
    activation: Mapping[str, Any] | None = None,
    status: str = "candidate",
    provider_semantics: Mapping[str, Any] | None = None,
    provider_specific: Mapping[str, Any] | None = None,
    supersedes: str | None = None,
    transition_evidence: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Construct and validate a Workflow candidate or active record."""
    if status not in {"candidate", "active", "retired"}:
        raise WorkflowError(f"Invalid Workflow status: {status}")
    if not isinstance(match_contract, Mapping) or not match_contract:
        raise WorkflowError("match_contract must be a non-empty object")
    for label, value in (
        ("success_contract", success_contract),
        ("side_effect_contract", side_effect_contract),
        ("recovery", recovery),
        ("provenance", provenance),
    ):
        if not isinstance(value, Mapping) or not value:
            raise WorkflowError(f"{label} must be a non-empty object")
    record: dict[str, Any] = {
        "record_type": WORKFLOW_RECORD_TYPE,
        "record_version": WORKFLOW_RECORD_VERSION,
        "workflow_id": _require_id(workflow_id, "workflow_id"),
        "version": _require_version(version),
        "human_name": _require_nonempty_text(human_name, "human_name"),
        "match_contract": copy.deepcopy(dict(match_contract)),
        "inputs": _contract_items(inputs, "inputs"),
        "outputs": _contract_items(outputs, "outputs"),
        "dot_refs": _normalise_dot_refs(dot_refs, status),
        "success_contract": copy.deepcopy(dict(success_contract)),
        "side_effect_contract": copy.deepcopy(dict(side_effect_contract)),
        "recovery": copy.deepcopy(dict(recovery)),
        "lifecycle": {
            "status": status,
            "state": status,
            "candidate": status == "candidate",
            "active": status == "active",
            "active_surface": status == "active",
            "supersedes": supersedes,
            "material_change": False,
            **(
                {"transition_evidence": list(transition_evidence)}
                if transition_evidence is not None
                else {}
            ),
        },
        "trial": copy.deepcopy(dict(trial or {"status": "pending"})),
        "verification": copy.deepcopy(dict(verification or {"status": "unverified"})),
        "system_review": copy.deepcopy(dict(system_review or {"status": "pending"})),
        "human_decision": copy.deepcopy(dict(human_decision or {"status": "pending"})),
        "activation": copy.deepcopy(dict(activation or {"status": "inactive"})),
        "provenance": copy.deepcopy(dict(provenance)),
    }
    if provider_semantics is not None:
        record["provider_semantics"] = copy.deepcopy(dict(provider_semantics))
    if provider_specific is not None:
        record["provider_specific"] = copy.deepcopy(dict(provider_specific))
    validate_workflow(record)
    return record


def workflow_material_fingerprint(workflow: Mapping[str, Any]) -> str:
    """Hash only the reusable Workflow contract, excluding lifecycle evidence."""
    material = {field: workflow.get(field) for field in _WORKFLOW_MATERIAL_FIELDS}
    payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def workflow_material_change(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Return whether an update changes executable Workflow semantics."""
    return workflow_material_fingerprint(before) != workflow_material_fingerprint(after)


def _version_parts(version: str) -> tuple[int, int, int]:
    base = version.split("-", 1)[0]
    major, minor, patch = (int(part) for part in base.split("."))
    return major, minor, patch


def next_workflow_version(workflow_or_version: Mapping[str, Any] | str) -> str:
    """Return the next patch version for a material Workflow change."""
    version = (
        workflow_or_version
        if isinstance(workflow_or_version, str)
        else workflow_or_version.get("version")
    )
    _require_version(version)
    major, minor, patch = _version_parts(version)
    return f"{major}.{minor}.{patch + 1}"


def revise_workflow(
    workflow: Mapping[str, Any],
    changes: Mapping[str, Any],
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """Return a new immutable Workflow value and require a new version on change."""
    validate_workflow(workflow)
    if not isinstance(changes, Mapping):
        raise WorkflowError("Workflow changes must be an object")
    forbidden = {"record_type", "record_version", "workflow_id"}
    if forbidden.intersection(changes):
        raise WorkflowError("Workflow identity and record type cannot be changed")
    updated = copy.deepcopy(dict(workflow))
    for key, value in changes.items():
        updated[key] = copy.deepcopy(value)
    material = workflow_material_change(workflow, updated)
    old_version = workflow["version"]
    requested_version = version if version is not None else updated.get("version")
    if material:
        if requested_version is None or requested_version == old_version:
            requested_version = next_workflow_version(old_version)
        _require_version(requested_version)
        if _version_order(requested_version) <= _version_order(old_version):
            raise WorkflowError("material Workflow revision must use a strictly newer version")
        updated["version"] = requested_version
        lifecycle = updated.setdefault("lifecycle", {})
        lifecycle.update(
            {
                "status": "candidate",
                "state": "candidate",
                "candidate": True,
                "active": False,
                "active_surface": False,
                "material_change": True,
                "supersedes": f"{workflow['workflow_id']}@{old_version}",
            }
        )
        lifecycle.pop("transition_evidence", None)
        updated["trial"] = {"status": "pending"}
        updated["verification"] = {"status": "unverified"}
        updated["system_review"] = {"status": "pending"}
        updated["human_decision"] = {"status": "pending"}
        updated["activation"] = {"status": "inactive"}
    elif requested_version is not None:
        _require_version(requested_version)
        updated["version"] = requested_version
    validate_workflow(updated)
    return updated


def new_workflow_version(
    workflow_or_version: Mapping[str, Any] | str,
    changes: Mapping[str, Any] | None = None,
) -> str | dict[str, Any]:
    """Compatibility helper: bump a version or revise a Workflow value."""
    if changes is None:
        return next_workflow_version(workflow_or_version)
    if isinstance(workflow_or_version, str):
        raise WorkflowError("Workflow changes require the current Workflow record")
    return revise_workflow(workflow_or_version, changes)


def _version_order(version: str) -> tuple[int, int, int, str]:
    _require_version(version)
    core, _, suffix = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, suffix


def validate_workflow_version_update(
    previous: Mapping[str, Any], updated: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate immutable Workflow lineage and material-version boundaries."""
    old = validate_workflow(previous)
    new = validate_workflow(updated)
    if old["workflow_id"] != new["workflow_id"]:
        raise WorkflowError("Workflow version updates must retain workflow_id")
    old_version = old["version"]
    new_version = new["version"]
    changed = workflow_material_change(old, new)
    if old_version == new_version:
        if changed:
            raise WorkflowError("material Workflow changes require a new version")
        return new
    if _version_order(new_version) <= _version_order(old_version):
        raise WorkflowError("Workflow version update must be strictly newer")
    if _state(new) != "candidate":
        raise WorkflowError("a material Workflow version update must create a candidate first")
    predecessor = new["lifecycle"].get("supersedes")
    expected = f"{old['workflow_id']}@{old_version}"
    if predecessor != expected:
        raise WorkflowError("Workflow version update requires predecessor lineage")
    _validate_material_candidate_reset(new)
    return new


def _normalise_steps(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise CompositionValidationError("steps must be an ordered list")
    if not values:
        raise CompositionValidationError("An Execution Composition requires at least one step")
    steps: list[dict[str, Any]] = []
    for sequence, raw in enumerate(values, start=1):
        if not isinstance(raw, Mapping):
            raise CompositionValidationError("Composition steps must be typed reference objects")
        item = copy.deepcopy(dict(raw))
        item.setdefault("sequence", sequence)
        item.setdefault("step_id", f"step-{sequence}")
        item.setdefault("input_bindings", [])
        item.setdefault("output_bindings", [])
        if "kind" not in item and "ref_type" in item:
            item["kind"] = item.pop("ref_type")
        if "ref" not in item:
            if "workflow_id" in item:
                item["kind"] = "workflow"
                item["ref"] = {
                    "workflow_id": item.pop("workflow_id"),
                    "version": item.pop("version"),
                    "lifecycle": item.pop("lifecycle", item.pop("state", "active")),
                }
            elif "dot_id" in item:
                item["kind"] = "dot"
                item["ref"] = {
                    "dot_id": item.pop("dot_id"),
                    "version": item.pop("version"),
                    "lifecycle": item.pop("lifecycle", item.pop("state", "candidate")),
                }
        if item.get("kind") == "workflow" and "workflow_id" in item.get("ref", {}):
            item["ref"].setdefault("lifecycle", "active")
        if item.get("kind") == "dot" and "dot_id" in item.get("ref", {}):
            item["ref"].setdefault("lifecycle", "candidate")
        steps.append(item)
    return steps


def _candidate_dot_refs(steps: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        step["ref"]["dot_id"]
        for step in steps
        if step.get("kind") == "dot" and step.get("ref", {}).get("lifecycle") == "candidate"
    ]


def _default_authority(
    *,
    composition_id: str,
    project_id: str,
    project_revision: int,
    task_id: str,
    steps: Sequence[Mapping[str, Any]],
    expires_at: str,
    allowed_side_effects: Sequence[str],
) -> dict[str, Any]:
    return {
        "authority_id": f"execution-authority-{composition_id}",
        "project_id": project_id,
        "project_revision": project_revision,
        "task_id": task_id,
        "allowed_side_effects": list(allowed_side_effects),
        "persistence_state_change": False,
        "scope": {
            "kind": "project-task-local",
            "project_id": project_id,
            "task_id": task_id,
        },
        "allowed_step_ids": [step["step_id"] for step in steps],
        "candidate_dot_execution": False,
        "candidate_dot_ids": [],
        "human_approved": False,
        "expires_at": expires_at,
        "persistent": False,
    }


def _validate_step_refs(
    composition: Mapping[str, Any],
    *,
    workflows: Any = None,
    dots: Any = None,
) -> None:
    authority = composition["execution_authority"]
    steps = composition["steps"]
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    candidate_ids: list[str] = []
    authority_candidate_ids = list(authority.get("candidate_dot_ids", []))
    if not authority_candidate_ids and authority.get("dot_id"):
        authority_candidate_ids = [authority["dot_id"]]
    candidate_authority = authority.get("candidate_dot_execution") is True or bool(
        authority_candidate_ids
    )
    for sequence, step in enumerate(steps, start=1):
        if step["sequence"] != sequence:
            raise CompositionValidationError(
                "Composition steps must have contiguous sequence values"
            )
        step_id = _require_id(step["step_id"], "step_id")
        if step_id in seen_ids:
            raise CompositionValidationError(f"Duplicate composition step: {step_id}")
        seen_ids.add(step_id)
        if step["sequence"] in seen_sequences:
            raise CompositionValidationError("Duplicate composition sequence")
        seen_sequences.add(step["sequence"])
        kind = step["kind"]
        ref = step["ref"]
        if kind == "workflow":
            reference_id = _require_id(ref.get("workflow_id"), "workflow ref workflow_id")
            ref_version = _require_version(ref.get("version"), "workflow ref version")
            if ref.get("lifecycle") != "active":
                raise CompositionValidationError(
                    "An Execution Composition may use only active Workflows"
                )
            if workflows is not None:
                record = _lookup_record(workflows, "workflow", reference_id, ref_version)
                if record is None:
                    raise CompositionValidationError(
                        f"Composition references missing Workflow {reference_id}@{ref_version}"
                    )
                if _state(record) != "active":
                    raise CompositionValidationError(
                        "Only active Workflows may enter a Composition; "
                        "candidate Workflows are isolated"
                    )
        elif kind == "dot":
            reference_id = _require_id(ref.get("dot_id"), "Dot ref dot_id")
            ref_version = _require_version(ref.get("version"), "Dot ref version")
            state = ref.get("lifecycle")
            if state not in {"active", "candidate"}:
                raise CompositionValidationError("Composition Dot refs must be active or candidate")
            if state == "candidate":
                candidate_ids.append(reference_id)
                if not candidate_authority:
                    raise CompositionValidationError(
                        "Candidate Dot execution requires explicit bounded authority"
                    )
            if dots is not None:
                record = _lookup_record(dots, "dot", reference_id, ref_version)
                if record is None:
                    raise CompositionValidationError(
                        f"Composition references missing Dot {reference_id}@{ref_version}"
                    )
                actual = _state(record)
                if actual != state:
                    raise CompositionValidationError(
                        f"Composition Dot lifecycle does not match reference: {reference_id}"
                    )
        else:
            raise CompositionValidationError(f"Unsupported composition step kind: {kind!r}")
    if sorted(candidate_ids) != sorted(authority_candidate_ids):
        raise CompositionValidationError(
            "Candidate Dot authority is broader or narrower than the steps"
        )
    if not candidate_ids and candidate_authority:
        raise CompositionValidationError(
            "Candidate Dot authority must be false when no candidate Dot is used"
        )


def validate_execution_composition(
    composition: Mapping[str, Any],
    *,
    workflows: Any = None,
    dots: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one temporary Project/task-local Execution Composition."""
    if not isinstance(composition, Mapping):
        raise CompositionValidationError("Execution Composition must be an object")
    _validate_schema(
        composition, "execution-composition.schema.json", CompositionValidationError
    )
    _reject_forbidden_keys(composition, error_type=CompositionValidationError)
    if composition["persistent"] is not False:
        raise CompositionValidationError("Execution Compositions are always persistent=false")
    scope = composition["scope"]
    _require_id(scope["project_id"], "composition project_id")
    _require_id(scope["task_id"], "composition task_id")
    authority = composition["execution_authority"]
    if authority["persistence_state_change"] is not False:
        raise CompositionValidationError("Execution authority cannot grant persistence")
    if (
        authority["project_id"] != scope["project_id"]
        or authority["task_id"] != scope["task_id"]
    ):
        raise CompositionValidationError(
            "Execution authority must exactly match Project/task scope"
        )
    if (
        "project_revision" in scope
        and authority["project_revision"] != scope["project_revision"]
    ):
        raise CompositionValidationError("Execution authority must match the Project revision")
    if authority.get("scope") is not None and authority["scope"] != scope:
        raise CompositionValidationError(
            "Execution authority must exactly match Project/task scope"
        )
    step_ids = [step["step_id"] for step in composition["steps"]]
    if authority.get("allowed_step_ids") != step_ids:
        raise CompositionValidationError(
            "Execution authority must enumerate the exact ordered steps"
        )
    if authority["allowed_side_effects"] != composition["allowed_side_effects"]:
        raise CompositionValidationError("Execution authority must match allowed side effects")
    if authority["expires_at"] != composition["expiry"]["expires_at"]:
        raise CompositionValidationError("Execution authority and Composition expiry must match")
    _require_timestamp(composition["expiry"]["expires_at"], "composition expiry")
    _require_timestamp(authority["expires_at"], "execution authority expiry")
    try:
        _ensure_not_expired(composition["expiry"]["expires_at"], "composition expiry", now=now)
    except WorkflowError as error:
        raise CompositionValidationError(str(error)) from error
    _validate_step_refs(composition, workflows=workflows, dots=dots)
    return copy.deepcopy(dict(composition))


def compose_execution(
    *,
    composition_id: str,
    project_id: str,
    task_id: str,
    project_revision: int = 0,
    inputs: Sequence[Any],
    outputs: Sequence[Any],
    steps: Sequence[Mapping[str, Any]],
    verification: Mapping[str, Any],
    expiry: Mapping[str, Any],
    allowed_side_effects: Sequence[str],
    recovery: Mapping[str, Any],
    evidence_refs: Sequence[str],
    execution_authority: Mapping[str, Any] | None = None,
    workflows: Any = None,
    dots: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a bounded temporary Composition without persisting it anywhere."""
    composition_id = _require_id(composition_id, "composition_id")
    project_id = _require_id(project_id, "project_id")
    task_id = _require_id(task_id, "task_id")
    normalised_steps = _normalise_steps(steps)
    if not isinstance(expiry, Mapping) or not expiry.get("expires_at"):
        raise CompositionValidationError("Composition expiry requires expires_at")
    expiry_value = copy.deepcopy(dict(expiry))
    _require_timestamp(expiry_value["expires_at"], "composition expiry")
    if not isinstance(verification, Mapping) or not verification:
        raise CompositionValidationError("Composition verification must be a non-empty object")
    if not isinstance(recovery, Mapping) or not recovery:
        raise CompositionValidationError("Composition recovery must be a non-empty object")
    verification_value = copy.deepcopy(dict(verification))
    verification_value.setdefault(
        "checks",
        [{"check_id": "contract-validation", "criterion": "all references validate"}],
    )
    verification_value.setdefault("success_effect", "evidence-only")
    verification_value.setdefault("persistent", False)
    if (
        verification_value["success_effect"] != "evidence-only"
        or verification_value["persistent"] is not False
    ):
        raise CompositionValidationError("Composition success may produce evidence only")
    if not isinstance(allowed_side_effects, Sequence) or isinstance(
        allowed_side_effects, (str, bytes, bytearray)
    ):
        raise CompositionValidationError("allowed_side_effects must be an ordered list")
    side_effects = [
        _require_nonempty_text(item, "allowed side effect") for item in allowed_side_effects
    ]
    if len(side_effects) != len(set(side_effects)):
        raise CompositionValidationError("Duplicate allowed side effect")
    if any(_is_persistent_effect(item) for item in side_effects):
        raise CompositionValidationError("Composition side effects cannot grant persistence")
    if not isinstance(evidence_refs, Sequence) or isinstance(
        evidence_refs, (str, bytes, bytearray)
    ):
        raise CompositionValidationError("evidence_refs must be a list")
    refs = [_require_id(item, "evidence ref") for item in evidence_refs]
    if len(refs) != len(set(refs)):
        raise CompositionValidationError("Duplicate composition evidence ref")
    authority = (
        copy.deepcopy(dict(execution_authority))
        if execution_authority is not None
        else _default_authority(
            composition_id=composition_id,
            project_id=project_id,
            project_revision=project_revision,
            task_id=task_id,
            steps=normalised_steps,
            expires_at=expiry_value["expires_at"],
            allowed_side_effects=side_effects,
        )
    )
    if isinstance(project_revision, bool) or not isinstance(project_revision, int):
        raise CompositionValidationError("project_revision must be a non-negative integer")
    if project_revision < 0:
        raise CompositionValidationError("project_revision must be a non-negative integer")
    scope = {
        "kind": "project-task-local",
        "project_id": project_id,
        "task_id": task_id,
    }
    authority.setdefault("project_id", project_id)
    authority.setdefault("project_revision", project_revision)
    authority.setdefault("task_id", task_id)
    authority.setdefault("allowed_side_effects", side_effects)
    authority.setdefault("persistence_state_change", False)
    authority.setdefault("scope", scope)
    authority.setdefault("allowed_step_ids", [step["step_id"] for step in normalised_steps])
    authority.setdefault("expires_at", expiry_value["expires_at"])
    authority.setdefault("persistent", False)
    candidates = _candidate_dot_refs(normalised_steps)
    if "candidate_dot_execution" not in authority:
        authority["candidate_dot_execution"] = bool(
            candidates and (authority.get("dot_id") or authority.get("candidate_dot_ids"))
        )
    if "candidate_dot_ids" not in authority:
        authority["candidate_dot_ids"] = (
            [authority["dot_id"]] if authority.get("dot_id") else []
        )
    authority.setdefault("human_approved", False)
    record: dict[str, Any] = {
        "record_type": COMPOSITION_RECORD_TYPE,
        "record_version": COMPOSITION_RECORD_VERSION,
        "composition_id": composition_id,
        "scope": scope,
        "persistent": False,
        "inputs": _contract_items(inputs, "composition inputs", allow_empty=True),
        "outputs": _contract_items(outputs, "composition outputs", allow_empty=True),
        "steps": normalised_steps,
        "verification": verification_value,
        "expiry": expiry_value,
        "allowed_side_effects": side_effects,
        "recovery": copy.deepcopy(dict(recovery)),
        "evidence_refs": refs,
        "execution_authority": authority,
    }
    validate_execution_composition(record, workflows=workflows, dots=dots, now=now)
    return record


def record_execution_evidence(
    composition: Mapping[str, Any],
    *,
    succeeded: bool,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Return evidence for a run; never return a persistent/promoted Workflow."""
    validate_execution_composition(composition)
    if not isinstance(succeeded, bool):
        raise CompositionValidationError("succeeded must be boolean")
    if not isinstance(evidence_refs, Sequence) or isinstance(
        evidence_refs, (str, bytes, bytearray)
    ):
        raise CompositionValidationError("evidence_refs must be a list")
    refs = list(composition["evidence_refs"])
    for item in evidence_refs:
        refs.append(_require_id(item, "evidence ref"))
    if len(refs) != len(set(refs)):
        raise CompositionValidationError("Duplicate composition evidence ref")
    return {
        "record_type": COMPOSITION_EVIDENCE_RECORD_TYPE,
        "record_version": COMPOSITION_RECORD_VERSION,
        "composition_id": composition["composition_id"],
        "project_id": composition["scope"]["project_id"],
        "task_id": composition["scope"]["task_id"],
        "status": "succeeded" if succeeded else "failed",
        "persistent": False,
        "promoted": False,
        "workflow_promotion": None,
        "evidence_refs": refs,
    }


# Readable alias for callers that describe a run as completion rather than
# recording evidence.  Both names have the same non-persistent semantics.
complete_execution_composition = record_execution_evidence

# Naming aliases mirror the neighbouring Source/Dot contracts and keep the
# public surface about the object being validated rather than its constructor.
validate_capability_workflow = validate_workflow
validate_composition = validate_execution_composition
build_execution_composition = compose_execution
validate_workflow_version = validate_workflow_version_update
validate_version_update = validate_workflow_version_update


__all__ = [
    "CapabilityWorkflowError",
    "COMPOSITION_EVIDENCE_RECORD_TYPE",
    "COMPOSITION_RECORD_TYPE",
    "COMPOSITION_RECORD_VERSION",
    "CompositionValidationError",
    "WORKFLOW_RECORD_TYPE",
    "WORKFLOW_RECORD_VERSION",
    "WorkflowError",
    "WorkflowValidationError",
    "build_workflow",
    "build_execution_composition",
    "complete_execution_composition",
    "compose_execution",
    "new_workflow_version",
    "next_workflow_version",
    "record_execution_evidence",
    "revise_workflow",
    "validate_capability_workflow",
    "validate_composition",
    "validate_execution_composition",
    "validate_workflow",
    "validate_workflow_version",
    "validate_workflow_version_update",
    "validate_version_update",
    "workflow_material_change",
    "workflow_material_fingerprint",
]
