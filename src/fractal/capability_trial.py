# ruff: noqa: E501,SIM102,E731

"""Pure validation and evaluation of capability trial receipts.

The genesis compiler produces an inactive candidate graph.  This module is a
small, System-owned boundary around *evidence* for that graph: a caller may
bring back receipts from an already authorised Reality Check execution and
ask for a detached candidate overlay.  The module never executes a command,
contacts a provider, writes Workplace state, changes the compiler graph, or
activates a candidate.

There are two deliberately separate concepts here:

``validate_trial_receipt``
    Checks that a receipt is an intact, exact, bounded account of one trial.

``evaluate_candidate_trials``
    Applies only the verification facts proven by valid receipts to a deep
    copy of a candidate graph.  The returned copy is still inactive and is a
    derived candidate state, not a new canonical graph.

The public builders are convenience functions for callers and tests.  A
caller that already has Reality Check receipts can supply them directly;
this module does not provide an execution function.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

TRIAL_PLAN_RECORD_TYPE = "capability-trial-plan"
TRIAL_RECEIPT_RECORD_TYPE = "capability-trial-receipt"
TRIAL_EVALUATION_RECORD_TYPE = "capability-trial-evaluation"
TRIAL_RECORD_VERSION = 1
VERIFIED_STAGED = "verified-staged"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:@/+~=,-]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_ISO_Z_RE = re.compile(r"Z$")

# These fields are authority-bearing in a receipt.  A provider or Source may
# be present in an implementation record in the candidate graph, but a trial
# receipt must never smuggle one in as execution authority.
_FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "source",
        "source_id",
        "source_ref",
        "source_refs",
        "source_authority",
        "source_execution",
        "execute_source",
        "provider",
        "provider_id",
        "provider_ref",
        "provider_refs",
        "provider_authority",
        "provider_execution",
        "provider_selection",
        "provider_implementation",
        "platform_skill",
        "platform_skill_id",
        "connector",
        "vendor",
        "activation_authority",
        "publication_authority",
        "persistence_authority",
    }
)
_PERSISTENCE_WORDS = frozenset(
    {
        "activate",
        "activation",
        "canonical-write",
        "commit",
        "mutate-project",
        "persist",
        "publication",
        "publish",
        "registry-write",
        "system-version",
        "write-canonical",
    }
)
_PERSISTENCE_FIELDS = frozenset(
    {
        "activate",
        "activated",
        "activation",
        "active_surface",
        "authorized_activation",
        "authorised_activation",
        "canonical_write",
        "mutate_project",
        "persist",
        "persistence",
        "persistence_state_change",
        "promote",
        "promoted",
        "publish",
        "publication",
        "registry_write",
    }
)


class CapabilityTrialError(ValueError):
    """Base error for a malformed or unsafe trial contract."""


class TrialValidationError(CapabilityTrialError):
    """Raised when a trial plan or receipt fails a deterministic gate."""


class TrialIntegrityError(TrialValidationError):
    """Raised when a plan, receipt, or command receipt has been tampered with."""


class TrialScopeError(TrialValidationError):
    """Raised when a trial exceeds its Project/task or side-effect scope."""


class TrialOrderError(TrialValidationError):
    """Raised when a receipt skips, duplicates, or reorders a Dot."""


class TrialOutputError(TrialValidationError):
    """Raised when a trial output does not satisfy its declared contract."""


# Compatibility names mirror neighbouring capability modules.
CapabilityTrialValidationError = TrialValidationError
CapabilityTrialIntegrityError = TrialIntegrityError
CapabilityTrialScopeError = TrialScopeError
CapabilityTrialOrderError = TrialOrderError
CapabilityTrialOutputError = TrialOutputError


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise TrialValidationError("trial values must be portable JSON") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise TrialIntegrityError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TrialValidationError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrialValidationError(f"{label} must be a non-empty string")
    return value


def _id(value: Any, label: str) -> str:
    result = _text(value, label)
    if _ID_RE.fullmatch(result) is None:
        raise TrialValidationError(f"{label} is not a stable identifier: {result!r}")
    return result


def _version(value: Any, label: str = "version") -> str:
    result = _text(value, label)
    if _VERSION_RE.fullmatch(result) is None:
        raise TrialValidationError(f"{label} is not a supported version: {result!r}")
    return result


def _list(value: Any, label: str, *, allow_empty: bool = True) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TrialValidationError(f"{label} must be an ordered list")
    result = copy.deepcopy(list(value))
    if not allow_empty and not result:
        raise TrialValidationError(f"{label} must not be empty")
    return result


def _unique_text(value: Any, label: str, *, required: bool = False) -> list[str]:
    if value is None and not required:
        return []
    values = _list(value, label, allow_empty=not required)
    result: list[str] = []
    for index, item in enumerate(values):
        text = _text(item, f"{label}[{index}]")
        if text in result:
            raise TrialValidationError(f"{label} contains duplicate value: {text}")
        result.append(text)
    return result


def _normal_key(value: Any) -> str:
    return str(value).strip().casefold().replace("-", "_").replace(" ", "_")


def _reject_authority(value: Any, *, path: str = "$") -> None:
    """Reject Source/provider and mutation authority in receipt material."""

    if isinstance(value, Mapping):
        record_type = _normal_key(value.get("record_type", ""))
        if record_type in {
            "source",
            "capability_source",
            "provider",
            "platform_skill",
            "capability_provider",
        }:
            raise TrialScopeError(f"Source/provider authority is not valid trial evidence at {path}")
        for raw_key, child in value.items():
            key = _normal_key(raw_key)
            if key in _FORBIDDEN_AUTHORITY_KEYS:
                raise TrialScopeError(
                    f"Source/provider authority is not valid trial evidence: {path}.{raw_key}"
                )
            if key in _PERSISTENCE_FIELDS:
                # The one safe exception is the exact boolean boundary.  It is
                # checked separately by the scope validator below.
                if key != "persistence_state_change":
                    raise TrialScopeError(
                        f"persistence/activation/publication authority is forbidden: {path}.{raw_key}"
                    )
            _reject_authority(child, path=f"{path}.{raw_key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_authority(child, path=f"{path}[{index}]")


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(_ISO_Z_RE.sub("+00:00", text))
    except ValueError as error:
        raise TrialValidationError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise TrialValidationError(f"{label} must include a timezone")
    return parsed


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise TrialValidationError("trial validation time must include a timezone")
    return current


def _bounded_effects(value: Any, label: str, *, required: bool = False) -> list[str]:
    effects = _unique_text(value, label, required=required)
    for effect in effects:
        normalised = effect.strip().casefold().replace("_", "-").replace(" ", "-")
        if (
            "*" in effect
            or "?" in effect
            or ".." in effect
            or normalised in {"*", "any", "unbounded"}
            or any(word in normalised for word in _PERSISTENCE_WORDS)
        ):
            raise TrialScopeError(f"{label} contains an unbounded or persistent side effect: {effect}")
    return effects


def _cost(value: Any, label: str) -> Any:
    if isinstance(value, bool):
        raise TrialValidationError(f"{label} must be non-negative")
    if isinstance(value, (int, float)):
        if value < 0:
            raise TrialValidationError(f"{label} must be non-negative")
        return value
    if isinstance(value, Mapping):
        result = copy.deepcopy(dict(value))
        for key, item in result.items():
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)) and item < 0:
                raise TrialValidationError(f"{label}.{key} must be non-negative")
        return result
    raise TrialValidationError(f"{label} must be numeric or an object")


def _duration(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise TrialValidationError(f"{label} must be non-negative")
    return value


def _signature(value: Any, label: str) -> Any:
    if isinstance(value, str):
        _text(value, label)
        return value
    if isinstance(value, Mapping) and value:
        return copy.deepcopy(dict(value))
    raise TrialValidationError(f"{label} must be a non-empty string or object")


def _identity(value: Any, kind: str, label: str) -> dict[str, Any]:
    """Normalise id@version and object references without provider fields."""

    identity_key = f"{kind}_id"
    if isinstance(value, str):
        identifier, separator, version = value.rpartition("@")
        if not separator:
            raise TrialValidationError(f"{label} requires id@version")
        return {identity_key: _id(identifier, f"{label}.{identity_key}"), "version": _version(version, f"{label}.version")}
    item = _mapping(value, label)
    nested = item.get("ref", item.get("target"))
    if isinstance(nested, (Mapping, str)):
        item = _mapping(nested, label)
    identifier = item.get(identity_key, item.get("id"))
    if identifier is None:
        identifier = item.get("ref_id")
    result = {
        identity_key: _id(identifier, f"{label}.{identity_key}"),
        "version": _version(item.get("version"), f"{label}.version"),
    }
    if "sequence" in item:
        result["sequence"] = item["sequence"]
    return result


def _ref_key(value: Mapping[str, Any], kind: str) -> tuple[str, str]:
    identity_key = f"{kind}_id"
    return str(value[identity_key]), str(value["version"])


def _records(value: Any, kind: str, label: str) -> list[dict[str, Any]]:
    identity_key = f"{kind}_id"
    if value is None:
        return []
    if isinstance(value, Mapping):
        if value.get(identity_key) is not None or value.get("id") is not None:
            return [copy.deepcopy(dict(value))]
        result: list[dict[str, Any]] = []
        for key, raw in value.items():
            if not isinstance(raw, Mapping):
                raise TrialValidationError(f"{label} registry entries must be objects")
            item = copy.deepcopy(dict(raw))
            item.setdefault(identity_key, key)
            result.append(item)
        return result
    values = _list(value, label)
    result = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise TrialValidationError(f"{label}[{index}] must be an object")
        result.append(copy.deepcopy(dict(raw)))
    return result


def _record_identity(record: Mapping[str, Any], kind: str, label: str) -> tuple[str, str]:
    return _ref_key(_identity(record, kind, label), kind)


def _graph_index(graph: Mapping[str, Any]) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    if not isinstance(graph, Mapping):
        raise TrialValidationError("candidate graph must be an object")
    indexes: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for kind in ("action", "workflow", "dot"):
        raw_collection = graph.get(f"{kind}s", [])
        identity_key = f"{kind}_id"
        if isinstance(raw_collection, Mapping):
            if raw_collection.get(identity_key) is not None or raw_collection.get("id") is not None:
                collection = [raw_collection]
            else:
                collection = []
                for key, raw in raw_collection.items():
                    if not isinstance(raw, Mapping):
                        raise TrialValidationError(
                            f"graph.{kind}s registry entries must be objects"
                        )
                    if raw.get(identity_key) is None and raw.get("id") is None:
                        item = dict(raw)
                        item[identity_key] = key
                        collection.append(item)
                    else:
                        collection.append(raw)
        elif isinstance(raw_collection, Sequence) and not isinstance(
            raw_collection, (str, bytes, bytearray)
        ):
            collection = list(raw_collection)
        else:
            raise TrialValidationError(f"graph.{kind}s must be an object or ordered list")
        index: dict[tuple[str, str], dict[str, Any]] = {}
        for item in collection:
            if not isinstance(item, Mapping):
                raise TrialValidationError(f"graph.{kind}s entries must be objects")
            identity = _record_identity(item, kind, f"graph.{kind}")
            if identity in index:
                raise TrialValidationError(f"duplicate graph {kind} reference: {identity[0]}@{identity[1]}")
            index[identity] = item  # type: ignore[assignment]
        indexes[kind] = index
    # Implementations are owned by Dots, never by a free-standing registry.
    implementation_index: dict[tuple[str, str], dict[str, Any]] = {}
    for dot in indexes["dot"].values():
        implementations = dot.get("implementations", dot.get("implementation_records", []))
        if isinstance(implementations, Mapping):
            if implementations.get("implementation_id") is not None or implementations.get("id") is not None:
                implementation_values = [implementations]
            else:
                implementation_values = []
                for key, raw in implementations.items():
                    if not isinstance(raw, Mapping):
                        raise TrialValidationError(
                            "graph.dot.implementations registry entries must be objects"
                        )
                    if raw.get("implementation_id") is None and raw.get("id") is None:
                        item = dict(raw)
                        item["implementation_id"] = key
                        implementation_values.append(item)
                    else:
                        implementation_values.append(raw)
        elif isinstance(implementations, Sequence) and not isinstance(
            implementations, (str, bytes, bytearray)
        ):
            implementation_values = list(implementations)
        else:
            raise TrialValidationError("graph.dot.implementations must be an object or ordered list")
        for item in implementation_values:
            if not isinstance(item, Mapping):
                raise TrialValidationError("graph.dot.implementations entries must be objects")
            identity = _record_identity(item, "implementation", "graph.implementation")
            if identity in implementation_index:
                raise TrialValidationError(
                    f"duplicate graph implementation reference: {identity[0]}@{identity[1]}"
                )
            implementation_index[identity] = item  # type: ignore[assignment]
    indexes["implementation"] = implementation_index
    return indexes


def _record_ref_list(value: Any, kind: str, label: str) -> list[dict[str, Any]]:
    values = _list(value, label, allow_empty=True)
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(values, start=1):
        if isinstance(raw, Mapping) and raw.get("ref") is not None:
            raw = raw["ref"]
        item = _identity(raw, kind, f"{label}[{index}]")
        item["sequence"] = raw.get("sequence", index) if isinstance(raw, Mapping) else index
        if isinstance(item["sequence"], bool) or not isinstance(item["sequence"], int) or item["sequence"] < 1:
            raise TrialOrderError(f"{label}[{index}].sequence must be a positive integer")
        result.append(item)
    return result


def _workflow_dots(workflow: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = workflow.get("dot_refs")
    if raw is None:
        raw = workflow.get("steps", [])
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            raw = [
                item.get("dot_ref", item.get("ref"))
                for item in raw
                if isinstance(item, Mapping)
                and (item.get("dot_ref") is not None or item.get("ref") is not None)
            ]
    return _record_ref_list(raw or [], "dot", "workflow.dot_refs")


def _action_workflows(action: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = action.get("workflow_refs")
    if raw is None:
        raw = action.get("workflows", [])
    return _record_ref_list(raw or [], "workflow", "action.workflow_refs")


def _plan_mapping(receipt: Mapping[str, Any]) -> dict[str, Any]:
    plan = receipt.get("plan", receipt.get("trial_plan"))
    if plan is None:
        plan = receipt.get("trial")
    if plan is None:
        # A compact receipt may put all plan fields at its root.
        plan = receipt
    if not isinstance(plan, Mapping):
        raise TrialValidationError("trial receipt plan must be an object")
    return copy.deepcopy(dict(plan))


def _candidate_container(plan: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "candidate_refs", "targets", "references"):
        value = plan.get(key)
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    return {}


def _plan_ref(plan: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    key = f"{kind}_ref"
    candidates = [plan.get(key), _candidate_container(plan).get(key)]
    if kind == "action":
        candidates.extend([plan.get("action"), _candidate_container(plan).get("action")])
    elif kind == "workflow":
        candidates.extend([plan.get("workflow"), _candidate_container(plan).get("workflow")])
    for candidate in candidates:
        if candidate is not None:
            return _identity(candidate, kind, f"plan.{key}")
    return None


def _plan_refs(plan: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    keys = (f"{kind}_refs", f"{kind}s")
    for key in keys:
        value = plan.get(key)
        if value is not None:
            return _record_ref_list(value, kind, f"plan.{key}")
    candidate = _candidate_container(plan)
    for key in keys:
        value = candidate.get(key)
        if value is not None:
            return _record_ref_list(value, kind, f"plan.candidate.{key}")
    return []


def _step_list(receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = receipt.get("steps", receipt.get("dot_steps", receipt.get("executions")))
    if raw is None:
        raise TrialOrderError("trial receipt must contain ordered Dot steps")
    values = _list(raw, "receipt.steps", allow_empty=False)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(values, start=1):
        result.append(_mapping(item, f"receipt.steps[{index}]"))
    return result


def _step_ref(step: Mapping[str, Any], kind: str, sequence: int) -> dict[str, Any]:
    key = f"{kind}_ref"
    value = step.get(key)
    if value is None:
        value = step.get(kind)
    if value is None:
        raise TrialValidationError(f"receipt.steps[{sequence}].{key} is required")
    result = _identity(value, kind, f"receipt.steps[{sequence}].{key}")
    return result


def _scope(plan: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    candidate = plan.get("execution_authority", plan.get("scope"))
    if candidate is None:
        candidate = receipt.get("execution_authority", receipt.get("scope"))
    if candidate is None:
        candidate = plan
    scope = _mapping(candidate, "trial execution scope")
    project_revision = scope.get("project_revision", scope.get("revision"))
    if isinstance(project_revision, bool) or not isinstance(project_revision, int) or project_revision < 0:
        raise TrialScopeError("trial scope project_revision must be a non-negative integer")
    result = {
        "project_id": _text(scope.get("project_id"), "trial scope project_id"),
        "project_revision": project_revision,
        "task_id": _text(scope.get("task_id"), "trial scope task_id"),
        "allowed_side_effects": _bounded_effects(
            scope.get("allowed_side_effects", []), "trial scope allowed_side_effects"
        ),
        "expires_at": _text(scope.get("expires_at"), "trial scope expires_at"),
        "persistence_state_change": scope.get("persistence_state_change"),
    }
    _timestamp(result["expires_at"], "trial scope expires_at")
    if result["persistence_state_change"] is not False:
        raise TrialScopeError("candidate execution authority must set persistence_state_change=false")
    # Carry optional authority id for evidence but keep authority semantics
    # exact; no provider/source fields are accepted by _reject_authority.
    if scope.get("authority_id") is not None:
        result["authority_id"] = _id(scope["authority_id"], "trial scope authority_id")
    return result


def _base_graph_projection(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Remove only derived trial state before computing the base content hash."""

    value = copy.deepcopy(dict(graph))
    for key in (
        "content_digest",
        "graph_content_digest",
        "trial_evaluation",
        "candidate_evaluation",
        "evaluation_overlay",
        "trial_metrics",
        "evaluation_metrics",
    ):
        value.pop(key, None)
    metrics = value.get("metrics")
    if isinstance(metrics, dict):
        metrics.pop("trial", None)
    # Sequential evaluation is intentionally idempotent.  The evaluator owns
    # the staged status and evidence fields below; lifecycle, review, decision,
    # and activation dimensions are never stripped or changed.
    for collection, kind in (("dots", "dot"), ("workflows", "workflow"), ("actions", "action")):
        records = value.get(collection)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            verification = record.get("verification")
            if isinstance(verification, dict) and verification.get("status") == VERIFIED_STAGED:
                verification["status"] = "unverified"
                for field in ("evidence_ids", "trial_receipt_ids", "receipt_ids"):
                    verification.pop(field, None)
            if kind in {"dot", "workflow"}:
                trial = record.get("trial")
                if isinstance(trial, dict) and trial.get("status") in {
                    "passed",
                    "pending",
                    "not-run",
                }:
                    # Candidate builders historically used both ``pending``
                    # and ``not-run`` for the same pre-trial state.  Canonical
                    # content binding must map both base forms and the derived
                    # passed form to one neutral value so evaluation is a true
                    # fixed point.
                    trial["status"] = "pending"
                    for field in (
                        "evidence_ids",
                        "trial_receipt_ids",
                        "receipt_ids",
                        "workflow_version",
                    ):
                        trial.pop(field, None)
            for child in record.get("implementations", []) if isinstance(record.get("implementations"), list) else []:
                if isinstance(child, dict):
                    child_verification = child.get("verification")
                    if isinstance(child_verification, dict):
                        if child_verification.get("status") == VERIFIED_STAGED:
                            child_verification["status"] = "unverified"
                        if child_verification.get("status") == "unverified":
                            child_verification["evidence_ids"] = []
                            for field in ("trial_receipt_ids", "receipt_ids"):
                                child_verification.pop(field, None)
    return value


def candidate_graph_content_digest(graph: Mapping[str, Any]) -> str:
    """Return the deterministic content digest used by trial plans."""

    return _digest(_base_graph_projection(graph))


graph_content_digest = candidate_graph_content_digest


def _graph_digests(graph: Mapping[str, Any]) -> tuple[str | None, set[str]]:
    input_digest = graph.get("input_digest", graph.get("graph_input_digest"))
    if input_digest is not None:
        _sha(input_digest, "candidate graph input_digest")
    content = candidate_graph_content_digest(graph)
    accepted = {content}
    for key in ("content_digest", "graph_content_digest"):
        value = graph.get(key)
        if value is not None:
            accepted.add(_sha(value, f"candidate graph {key}"))
    # A caller may have hashed the exact graph rather than the projection; it
    # remains bound to the supplied graph and is accepted for compatibility.
    accepted.add(_digest(dict(graph)))
    return input_digest, accepted


def _validate_graph_refs(
    graph: Mapping[str, Any],
    plan: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    indexes = _graph_index(graph)
    action_ref = _plan_ref(plan, "action")
    workflow_ref = _plan_ref(plan, "workflow")
    dot_refs = _plan_refs(plan, "dot")
    implementation_refs = _plan_refs(plan, "implementation")

    step_dots = [_step_ref(step, "dot", index) for index, step in enumerate(steps, start=1)]
    step_implementations = [
        _step_ref(step, "implementation", index)
        for index, step in enumerate(steps, start=1)
    ]
    if not dot_refs:
        dot_refs = [dict(item, sequence=index) for index, item in enumerate(step_dots, start=1)]
    if not implementation_refs:
        implementation_refs = [
            dict(item, sequence=index)
            for index, item in enumerate(step_implementations, start=1)
        ]
    if len(dot_refs) != len(steps) or len(implementation_refs) != len(steps):
        raise TrialOrderError("trial plan Dot and Implementation refs must cover every receipt step")

    expected_sequences = list(range(1, len(dot_refs) + 1))
    if [item.get("sequence") for item in dot_refs] != expected_sequences:
        raise TrialOrderError("trial plan Dot refs must be contiguous and ordered")
    if [item.get("sequence") for item in implementation_refs] != expected_sequences:
        raise TrialOrderError("trial plan Implementation refs must be contiguous and ordered")

    for kind, refs in (("dot", dot_refs), ("implementation", implementation_refs)):
        for ref in refs:
            key = _ref_key(ref, kind)
            if key not in indexes[kind]:
                raise TrialValidationError(f"unknown candidate {kind} reference: {key[0]}@{key[1]}")
    for index, (declared, actual) in enumerate(zip(dot_refs, step_dots, strict=True), start=1):
        if _ref_key(declared, "dot") != _ref_key(actual, "dot"):
            raise TrialOrderError(f"receipt step {index} does not match the ordered plan Dot")
    for index, (declared, actual) in enumerate(zip(implementation_refs, step_implementations, strict=True), start=1):
        if _ref_key(declared, "implementation") != _ref_key(actual, "implementation"):
            raise TrialOrderError(f"receipt step {index} does not match the plan Implementation")

    for dot_ref, implementation_ref in zip(dot_refs, implementation_refs, strict=True):
        dot = indexes["dot"][_ref_key(dot_ref, "dot")]
        implementation = indexes["implementation"][_ref_key(implementation_ref, "implementation")]
        dot_implementations = _records(
            dot.get("implementations", dot.get("implementation_records", [])),
            "implementation",
            "candidate Dot implementations",
        )
        if not any(
            _record_identity(item, "implementation", "candidate implementation")
            == _ref_key(implementation_ref, "implementation")
            for item in dot_implementations
        ):
            raise TrialValidationError(
                "Implementation reference is not owned by the selected candidate Dot"
            )
        if implementation.get("provider_authority") or implementation.get("source_authority"):
            raise TrialScopeError("candidate implementation authority cannot be supplied by a trial")

    if workflow_ref is not None:
        workflow_key = _ref_key(workflow_ref, "workflow")
        if workflow_key not in indexes["workflow"]:
            raise TrialValidationError(
                f"unknown candidate workflow reference: {workflow_key[0]}@{workflow_key[1]}"
            )
        workflow_dots = _workflow_dots(indexes["workflow"][workflow_key])
        workflow_keys = [_ref_key(item, "dot") for item in workflow_dots]
        selected_keys = [_ref_key(item, "dot") for item in dot_refs]
        if selected_keys != workflow_keys[: len(selected_keys)]:
            raise TrialOrderError(
                "trial Dots must be an ordered Workflow prefix without skipped Dots"
            )
    if action_ref is not None:
        action_key = _ref_key(action_ref, "action")
        if action_key not in indexes["action"]:
            raise TrialValidationError(
                f"unknown candidate action reference: {action_key[0]}@{action_key[1]}"
            )
        action_workflows = _action_workflows(indexes["action"][action_key])
        if workflow_ref is not None and _ref_key(workflow_ref, "workflow") not in {
            _ref_key(item, "workflow") for item in action_workflows
        }:
            raise TrialValidationError("trial Workflow is not referenced by the selected Action")

    # Exact refs may also be repeated at the receipt root.  Repetition is
    # useful for compact evidence but a disagreement is unsafe.
    for kind, expected in (("action", action_ref), ("workflow", workflow_ref)):
        repeated = plan.get(f"{kind}_ref")
        if repeated is not None and expected is not None and _ref_key(_identity(repeated, kind, f"plan.{kind}_ref"), kind) != _ref_key(expected, kind):
            raise TrialValidationError(f"conflicting plan {kind} refs")
    return {
        "indexes": indexes,
        "action_ref": action_ref,
        "workflow_ref": workflow_ref,
        "dot_refs": dot_refs,
        "implementation_refs": implementation_refs,
    }


def _contract_for(plan: Mapping[str, Any], kind: str, dot_id: str, sequence: int) -> Any:
    singular = f"{kind}_contract"
    plural = f"{kind}_contracts"
    value = plan.get(plural, plan.get(singular))
    if isinstance(value, Mapping):
        # A mapping with an explicit expected/schema/fields key is one global
        # contract; otherwise it is conventionally keyed by Dot id/sequence.
        if any(key in value for key in ("expected", "equals", "exact", "schema", "fields", "type", "required")):
            return copy.deepcopy(dict(value))
        if dot_id in value:
            return copy.deepcopy(value[dot_id])
        if str(sequence) in value:
            return copy.deepcopy(value[str(sequence)])
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) == 1:
            return copy.deepcopy(value[0])
        if 0 <= sequence - 1 < len(value):
            return copy.deepcopy(value[sequence - 1])
    return copy.deepcopy(value) if value is not None else None


def _schema_type_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        return any(_schema_type_matches(actual, item) for item in expected)
    if expected in {"object", "mapping"}:
        return isinstance(actual, Mapping)
    if expected == "array":
        return isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray))
    if expected == "string":
        return isinstance(actual, str)
    if expected == "number":
        return isinstance(actual, (int, float)) and not isinstance(actual, bool)
    if expected == "integer":
        return isinstance(actual, int) and not isinstance(actual, bool)
    if expected == "boolean":
        return isinstance(actual, bool)
    if expected == "null":
        return actual is None
    return True


def _contract_matches(actual: Any, contract: Any) -> bool:
    if contract is None:
        return True
    if isinstance(contract, Mapping):
        if "expected" in contract:
            return actual == contract["expected"]
        if "equals" in contract:
            return actual == contract["equals"]
        if "exact" in contract and isinstance(contract["exact"], (Mapping, Sequence)):
            return actual == contract["exact"]
        if "schema" in contract:
            return _contract_matches(actual, contract["schema"])
        if "type" in contract and not _schema_type_matches(actual, contract["type"]):
            return False
        if "required" in contract:
            if not isinstance(actual, Mapping):
                return False
            required = contract["required"]
            if not isinstance(required, Sequence) or isinstance(required, (str, bytes, bytearray)):
                return False
            if any(item not in actual for item in required):
                return False
        fields = contract.get("fields")
        if fields is not None:
            return _contract_matches(actual, fields)
        if not isinstance(actual, Mapping):
            # A type-only contract has already passed above.
            return all(key in {"type", "required", "schema", "expected", "equals", "exact", "fields"} for key in contract)
        for key, expected in contract.items():
            if key in {"type", "required", "schema", "expected", "equals", "exact", "fields"}:
                continue
            if key not in actual or not _contract_matches(actual[key], expected):
                return False
        return True
    if isinstance(contract, Sequence) and not isinstance(contract, (str, bytes, bytearray)):
        # A list of names is a compact required-field contract; otherwise list
        # equality is the least surprising deterministic interpretation.
        if isinstance(actual, Mapping) and all(isinstance(item, str) for item in contract):
            return all(item in actual for item in contract)
        return actual == contract
    return actual == contract


def _step_value(step: Mapping[str, Any], kind: str) -> Any:
    aliases = {
        "input": ("input", "inputs", "input_value"),
        "output": ("output", "outputs", "result", "products", "output_value"),
    }
    for key in aliases[kind]:
        if key in step:
            return copy.deepcopy(step[key])
    return None


def _validate_step_status(step: Mapping[str, Any], sequence: int, receipt_status: str) -> bool:
    status = step.get("status", "passed" if receipt_status in {"passed", "succeeded"} else "failed")
    if status in {"failed", "error", "blocked"}:
        if receipt_status in {"failed", "error", "blocked"}:
            return False
        raise TrialValidationError(f"receipt step {sequence} did not pass")
    if status not in {"passed", "succeeded", "verified"}:
        raise TrialValidationError(f"receipt step {sequence} has an invalid status")
    verification = step.get("verification")
    if verification is None:
        if receipt_status in {"failed", "error", "blocked"}:
            return False
        raise TrialOutputError(f"receipt step {sequence} is missing per-Dot output verification")
    verification_map = _mapping(verification, f"receipt.steps[{sequence}].verification")
    if verification_map.get("status") not in {"verified", "passed", VERIFIED_STAGED}:
        if receipt_status in {"failed", "error", "blocked"}:
            return False
        raise TrialOutputError(f"receipt step {sequence} output verification did not pass")
    return True


def _receipt_hash_value(value: Mapping[str, Any], label: str) -> str:
    raw = value.get("receipt_sha256", value.get("receipt_hash", value.get("digest")))
    digest = _sha(raw, f"{label} hash")
    stripped = dict(value)
    for key in ("receipt_sha256", "receipt_hash", "digest"):
        stripped.pop(key, None)
    if _digest(stripped) != digest:
        raise TrialIntegrityError(f"{label} integrity failure")
    return digest


def _validate_command_receipt(value: Any, label: str, *, plan_digest: str | None = None) -> str:
    record = _mapping(value, label)
    _reject_authority(record, path=label)
    digest = _receipt_hash_value(record, label)
    record_type = _normal_key(record.get("record_type", ""))
    if record_type and not (
        "reality" in record_type or "command" in record_type or "execution" in record_type
    ):
        raise TrialIntegrityError(f"{label} is not a command or Reality Check receipt")
    status = record.get("status")
    exit_code = record.get("exit_code")
    if isinstance(record.get("byproducts"), Mapping):
        exit_code = record["byproducts"].get("exit_code", exit_code)
    if status not in {"passed", "succeeded", "verified"} or exit_code not in {None, 0}:
        raise TrialIntegrityError(f"{label} does not prove a passing command")
    if record.get("failure") not in {None, False, ""}:
        raise TrialIntegrityError(f"{label} contains a command failure")
    if record.get("persistent") is True or record.get("promoted") is True:
        raise TrialScopeError(f"{label} claims persistence or promotion")
    if plan_digest is not None and record.get("plan_sha256") not in {None, plan_digest}:
        raise TrialIntegrityError(f"{label} is bound to a different trial plan")
    return digest


def _hash_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        values: list[Any] = []
        for item in value.values():
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                values.extend(item)
            else:
                values.append(item)
    else:
        values = _list(value, label)
    result: list[str] = []
    for index, item in enumerate(values):
        if isinstance(item, Mapping):
            item = item.get("hash", item.get("receipt_sha256", item.get("receipt_hash")))
        digest = _sha(item, f"{label}[{index}]")
        if digest in result:
            raise TrialIntegrityError(f"{label} contains duplicate hashes")
        result.append(digest)
    return result


def _validate_receipt_hashes(
    plan: Mapping[str, Any], receipt: Mapping[str, Any], steps: Sequence[Mapping[str, Any]], plan_digest: str | None
) -> tuple[list[str], list[str]]:
    command_objects: list[Any] = []
    reality_objects: list[Any] = []
    for key in ("command_receipts", "command_execution_receipts", "execution_receipts"):
        value = receipt.get(key, plan.get(key))
        if value is not None:
            command_objects.extend(_list(value, f"{key}"))
    for key in ("reality_check_receipts", "reality_receipts"):
        value = receipt.get(key, plan.get(key))
        if value is not None:
            reality_objects.extend(_list(value, f"{key}"))
    command_hashes = [
        _validate_command_receipt(item, f"command receipt[{index}]", plan_digest=plan_digest)
        for index, item in enumerate(command_objects)
    ]
    reality_hashes = [
        _validate_command_receipt(item, f"Reality Check receipt[{index}]", plan_digest=plan_digest)
        for index, item in enumerate(reality_objects)
    ]
    expected_command = _hash_list(
        plan.get("command_receipt_hashes", receipt.get("command_receipt_hashes")),
        "plan.command_receipt_hashes",
    )
    expected_reality = _hash_list(
        plan.get("reality_check_receipt_hashes", receipt.get("reality_check_receipt_hashes")),
        "plan.reality_check_receipt_hashes",
    )
    if command_hashes and expected_command and set(command_hashes) != set(expected_command):
        raise TrialIntegrityError("command receipts do not match the exact planned hashes")
    if reality_hashes and expected_reality and set(reality_hashes) != set(expected_reality):
        raise TrialIntegrityError("Reality Check receipts do not match the exact planned hashes")
    if expected_command and command_hashes and not set(expected_command) <= set(command_hashes):
        raise TrialIntegrityError("planned command receipt hash is missing")
    if expected_reality and reality_hashes and not set(expected_reality) <= set(reality_hashes):
        raise TrialIntegrityError("planned Reality Check receipt hash is missing")
    # The caller may retain the full command receipts in an external
    # evidence store and provide only their immutable hashes here.  A hash is
    # still a required binding; an inline receipt, when supplied, is checked
    # above.
    if not command_hashes:
        command_hashes = list(expected_command)
    if not reality_hashes:
        reality_hashes = list(expected_reality)

    for index, step in enumerate(steps, start=1):
        for kind, key_names, available in (
            (
                "command",
                ("command_receipt_hash", "command_receipt_sha256", "command_receipt"),
                command_hashes,
            ),
            (
                "reality",
                ("reality_check_receipt_hash", "reality_receipt_hash", "reality_check_receipt"),
                reality_hashes,
            ),
        ):
            expected: Any = None
            for key in key_names:
                if key in step:
                    expected = step[key]
                    break
            if isinstance(expected, Mapping):
                expected = expected.get("receipt_sha256", expected.get("receipt_hash", expected.get("digest")))
            if expected is not None:
                expected_hash = _sha(expected, f"receipt.steps[{index}].{kind}_receipt_hash")
                if available and expected_hash not in available:
                    raise TrialIntegrityError(
                        f"receipt step {index} {kind} receipt does not match its hash"
                    )
        nested_command = step.get("command_receipts")
        if nested_command is not None:
            for item in _list(nested_command, f"receipt.steps[{index}].command_receipts"):
                command_hashes.append(
                    _validate_command_receipt(
                        item, f"receipt step {index} command receipt", plan_digest=plan_digest
                    )
                )
        nested_reality = step.get("reality_check_receipts")
        if nested_reality is not None:
            for item in _list(nested_reality, f"receipt.steps[{index}].reality_check_receipts"):
                reality_hashes.append(
                    _validate_command_receipt(
                        item, f"receipt step {index} Reality Check receipt", plan_digest=plan_digest
                    )
                )
    # A trial step must carry at least one receipt hash or an inline command /
    # Reality Check receipt.  Hash-only evidence is valid when the external
    # caller owns the receipt store; supplied receipt objects are checked above.
    for index, step in enumerate(steps, start=1):
        has_hash = any(
            key in step
            for key in (
                "command_receipt_hash",
                "command_receipt_sha256",
                "command_receipt",
                "reality_check_receipt_hash",
                "reality_receipt_hash",
                "reality_check_receipt",
            )
        )
        if not has_hash and not command_hashes and not reality_hashes:
            raise TrialIntegrityError(f"receipt step {index} has no command/Reality Check receipt hash")
    return list(dict.fromkeys(command_hashes)), list(dict.fromkeys(reality_hashes))


def _validate_plan_digests(plan: Mapping[str, Any], graph: Mapping[str, Any] | None) -> tuple[str | None, set[str]]:
    base = plan.get("base_graph", plan.get("graph", {}))
    if not isinstance(base, Mapping):
        raise TrialIntegrityError("trial plan base_graph must be an object")
    input_digest = base.get("input_digest", plan.get("base_graph_input_digest", plan.get("graph_input_digest")))
    content_digest = base.get(
        "content_digest",
        plan.get("base_graph_content_digest", plan.get("graph_content_digest", plan.get("content_digest"))),
    )
    if input_digest is None or content_digest is None:
        raise TrialIntegrityError("trial plan must bind the base graph input and content digests")
    input_digest = _sha(input_digest, "trial plan base graph input_digest")
    content_digest = _sha(content_digest, "trial plan base graph content_digest")
    if graph is None:
        return input_digest, {content_digest}
    graph_input, graph_contents = _graph_digests(graph)
    if graph_input is not None and input_digest != graph_input:
        raise TrialIntegrityError("trial plan is bound to a different candidate graph input_digest")
    if content_digest not in graph_contents:
        raise TrialIntegrityError("trial plan is bound to a different candidate graph content_digest")
    return input_digest, graph_contents


def validate_trial_plan(
    plan: Mapping[str, Any],
    *,
    graph: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    project_id: str | None = None,
    project_revision: int | None = None,
    task_id: str | None = None,
    allowed_side_effects: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate an exact candidate trial plan without executing it."""

    value = _mapping(plan, "trial plan")
    _reject_authority(value)
    if value.get("record_type", TRIAL_PLAN_RECORD_TYPE) not in {
        TRIAL_PLAN_RECORD_TYPE,
        TRIAL_RECEIPT_RECORD_TYPE,
    }:
        raise TrialValidationError("trial plan record type is not canonical")
    if value.get("record_version", TRIAL_RECORD_VERSION) != TRIAL_RECORD_VERSION:
        raise TrialValidationError("unsupported trial plan record version")
    _validate_plan_digests(value, graph)
    scope = _scope(value, value)
    current = _now(now)
    if current >= _timestamp(scope["expires_at"], "trial scope expires_at"):
        raise TrialScopeError("candidate trial scope has expired")
    if project_id is not None and scope["project_id"] != project_id:
        raise TrialScopeError("candidate trial scope names a different Project")
    if project_revision is not None and scope["project_revision"] != project_revision:
        raise TrialScopeError("candidate trial scope names a different Project revision")
    if task_id is not None and scope["task_id"] != task_id:
        raise TrialScopeError("candidate trial scope names a different task")
    if allowed_side_effects is not None and scope["allowed_side_effects"] != list(allowed_side_effects):
        raise TrialScopeError("candidate trial side-effect scope does not match the requested scope")
    for kind in ("input", "output"):
        key = f"{kind}_contract"
        plural = f"{kind}_contracts"
        contract = value.get(key, value.get(plural))
        if contract is None:
            raise TrialValidationError(f"trial plan {key} is required")
        _canonical_bytes(contract)
    recovery = value.get("recovery")
    if not isinstance(recovery, Mapping) or not recovery:
        raise TrialValidationError("trial plan recovery is required")
    return {**value, "scope": scope}


def _validate_integrity(value: Mapping[str, Any]) -> str:
    record_hash = value.get("receipt_sha256", value.get("receipt_hash", value.get("digest")))
    digest = _sha(record_hash, "trial receipt")
    stripped = dict(value)
    for key in ("receipt_sha256", "receipt_hash", "digest"):
        stripped.pop(key, None)
    if _digest(stripped) != digest:
        raise TrialIntegrityError("trial receipt integrity failure")
    return digest


def _plan_digest_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    stripped = dict(plan)
    stripped.pop("plan_sha256", None)
    # ``validate_trial_plan`` adds this detached convenience index when the
    # caller used the canonical execution_authority spelling.
    if "execution_authority" in stripped and "scope" in stripped:
        stripped.pop("scope", None)
    return stripped


def validate_trial_receipt(
    receipt: Mapping[str, Any],
    *,
    graph: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    project_id: str | None = None,
    project_revision: int | None = None,
    task_id: str | None = None,
    allowed_side_effects: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate one complete candidate trial receipt.

    The returned value is detached and contains no execution authority beyond
    the exact, expiring scope that was supplied.  It is safe to pass to the
    pure evaluator; it is not a command invocation API.
    """

    value = _mapping(receipt, "trial receipt")
    _reject_authority(value)
    if value.get("record_type") not in {TRIAL_RECEIPT_RECORD_TYPE, "capability_trial_receipt", None}:
        raise TrialValidationError("trial receipt record type is not canonical")
    if value.get("record_version", TRIAL_RECORD_VERSION) != TRIAL_RECORD_VERSION:
        raise TrialValidationError("unsupported trial receipt record version")
    receipt_digest = _validate_integrity(value)
    plan = _plan_mapping(value)
    validated_plan = validate_trial_plan(
        plan,
        graph=graph,
        now=now,
        project_id=project_id,
        project_revision=project_revision,
        task_id=task_id,
        allowed_side_effects=allowed_side_effects,
    )
    for scope_key in ("scope", "execution_authority"):
        if isinstance(value.get(scope_key), Mapping):
            repeated_scope = _scope({"scope": value[scope_key]}, None)
            if any(
                repeated_scope[field] != validated_plan["scope"][field]
                for field in (
                    "project_id",
                    "project_revision",
                    "task_id",
                    "allowed_side_effects",
                    "expires_at",
                    "persistence_state_change",
                )
            ):
                raise TrialScopeError("receipt scope does not match the exact plan scope")
    plan_digest = value.get("plan_sha256", plan.get("plan_sha256"))
    if plan_digest is not None:
        plan_digest = _sha(plan_digest, "trial plan")
        if _digest(_plan_digest_payload(plan)) != plan_digest:
            raise TrialIntegrityError("trial plan integrity failure")
    steps = _step_list(value)
    refs = _validate_graph_refs(graph, plan, steps) if graph is not None else {
        "indexes": {},
        "action_ref": _plan_ref(plan, "action"),
        "workflow_ref": _plan_ref(plan, "workflow"),
        "dot_refs": _plan_refs(plan, "dot"),
        "implementation_refs": _plan_refs(plan, "implementation"),
    }
    if len(refs["dot_refs"]) != len(steps):
        raise TrialOrderError("trial receipt steps do not cover the exact planned Dots")
    receipt_status = value.get("status", "passed")
    if receipt_status not in {"passed", "succeeded", "failed", "blocked", "error"}:
        raise TrialValidationError("trial receipt status is invalid")
    passed_steps: list[dict[str, Any]] = []
    previous_output: Any = None
    for sequence, step in enumerate(steps, start=1):
        dot_ref = _step_ref(step, "dot", sequence)
        implementation_ref = _step_ref(step, "implementation", sequence)
        if _ref_key(dot_ref, "dot") != _ref_key(refs["dot_refs"][sequence - 1], "dot"):
            raise TrialOrderError(f"receipt step {sequence} is not the exact planned Dot")
        if _ref_key(implementation_ref, "implementation") != _ref_key(refs["implementation_refs"][sequence - 1], "implementation"):
            raise TrialOrderError(f"receipt step {sequence} is not the exact planned Implementation")
        passed = _validate_step_status(step, sequence, receipt_status)
        input_value = _step_value(step, "input")
        output_value = _step_value(step, "output")
        if sequence > 1 and input_value is not None and previous_output is not None and input_value != previous_output:
            raise TrialOutputError(f"receipt step {sequence} input does not match the preceding Dot output")
        input_contract = _contract_for(plan, "input", dot_ref["dot_id"], sequence)
        output_contract = step.get("output_contract", _contract_for(plan, "output", dot_ref["dot_id"], sequence))
        if input_value is not None and not _contract_matches(input_value, input_contract):
            raise TrialOutputError(f"receipt step {sequence} input does not match its contract")
        if passed and output_value is None:
            raise TrialOutputError(f"receipt step {sequence} has no output")
        if passed and not _contract_matches(output_value, output_contract):
            raise TrialOutputError(f"receipt step {sequence} output does not match its contract")
        observed_effects = step.get(
            "observed_side_effects",
            step.get("side_effects", step.get("effects")),
        )
        if observed_effects is not None:
            observed = _bounded_effects(observed_effects, f"receipt.steps[{sequence}].side_effects")
            if not set(observed) <= set(validated_plan["scope"]["allowed_side_effects"]):
                raise TrialScopeError(f"receipt step {sequence} exceeds its allowed side effects")
        verification = step.get("verification")
        if isinstance(verification, Mapping):
            _unique_text(verification.get("evidence_ids"), f"receipt.steps[{sequence}].verification.evidence_ids")
        if passed:
            passed_steps.append({
                "sequence": sequence,
                "dot_ref": dot_ref,
                "implementation_ref": implementation_ref,
                "output": output_value,
                "evidence_ids": _unique_text(step.get("evidence_ids"), f"receipt.steps[{sequence}].evidence_ids"),
            })
        previous_output = output_value
    command_hashes, reality_hashes = _validate_receipt_hashes(plan, value, steps, plan_digest)
    for key in ("observed_side_effects", "side_effects", "effects"):
        if key in value:
            observed = _bounded_effects(value[key], f"receipt.{key}")
            if not set(observed) <= set(validated_plan["scope"]["allowed_side_effects"]):
                raise TrialScopeError("trial receipt exceeds its allowed side effects")

    outcome = value.get("outcome")
    if not isinstance(outcome, Mapping):
        raise TrialValidationError("trial receipt outcome is required")
    duration = value.get("duration_ms", value.get("duration"))
    cost = value.get("cost")
    work_signature = value.get("work_signature")
    evidence_ids = _unique_text(value.get("evidence_ids"), "trial receipt.evidence_ids", required=True)
    duration = _duration(duration, "trial receipt duration_ms")
    cost = _cost(cost, "trial receipt cost")
    work_signature = _signature(work_signature, "trial receipt work_signature")
    recovery = value.get("recovery", validated_plan.get("recovery"))
    if not isinstance(recovery, Mapping) or not recovery:
        raise TrialValidationError("trial receipt recovery is required")
    if value.get("persistence_state_change", False) is not False:
        raise TrialScopeError("trial receipt cannot change persistence state")
    for forbidden in ("activation", "publication", "promote", "promoted"):
        if value.get(forbidden) not in {None, False, "inactive"}:
            raise TrialScopeError(f"trial receipt cannot claim {forbidden}")
    finished = value.get("finished_at")
    if finished is not None and _timestamp(finished, "trial receipt finished_at") > _timestamp(validated_plan["scope"]["expires_at"], "trial scope expires_at"):
        raise TrialScopeError("trial receipt finished after its trial scope expired")
    normalized = copy.deepcopy(value)
    normalized["record_type"] = TRIAL_RECEIPT_RECORD_TYPE
    normalized["record_version"] = TRIAL_RECORD_VERSION
    normalized["receipt_sha256"] = receipt_digest
    normalized["plan"] = validated_plan
    normalized["scope"] = validated_plan["scope"]
    normalized["refs"] = {
        key: copy.deepcopy(refs[key])
        for key in ("action_ref", "workflow_ref", "dot_refs", "implementation_refs")
    }
    normalized["passed_steps"] = passed_steps
    normalized["command_receipt_hashes"] = command_hashes
    normalized["reality_check_receipt_hashes"] = reality_hashes
    normalized["outcome"] = copy.deepcopy(dict(outcome))
    normalized["duration_ms"] = duration
    normalized["cost"] = cost
    normalized["work_signature"] = work_signature
    normalized["evidence_ids"] = evidence_ids
    # Normalisation adds detached indexes for the evaluator.  Rebind the
    # returned record so a caller may safely feed the validated value back
    # into ``evaluate_candidate_trials`` without losing integrity coverage.
    normalized["receipt_sha256"] = _digest(
        {key: item for key, item in normalized.items() if key != "receipt_sha256"}
    )
    return normalized


validate_capability_trial_receipt = validate_trial_receipt
validate_capability_trial_plan = validate_trial_plan
validate_receipt = validate_trial_receipt


def _mark_verification(record: dict[str, Any], evidence_ids: Sequence[str], receipt_ids: Sequence[str]) -> None:
    current = record.get("verification")
    verification = copy.deepcopy(dict(current)) if isinstance(current, Mapping) else {}
    verification["status"] = VERIFIED_STAGED
    merged = list(dict.fromkeys([
        *(_unique_text(verification.get("evidence_ids"), "verification.evidence_ids")),
        *evidence_ids,
    ]))
    verification["evidence_ids"] = merged
    verification["trial_receipt_ids"] = list(dict.fromkeys(receipt_ids))
    record["verification"] = verification


def _derived_metrics(graph: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], failures: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    indexes = _graph_index(graph)
    verified_impls = [
        key
        for key, implementation in indexes["implementation"].items()
        if isinstance(implementation.get("verification"), Mapping)
        and implementation["verification"].get("status") in {"verified", VERIFIED_STAGED}
    ]
    unverified_impls = [key for key in indexes["implementation"] if key not in verified_impls]
    covered_dots = [
        key
        for key, dot in indexes["dot"].items()
        if (
            isinstance(dot.get("verification"), Mapping)
            and dot["verification"].get("status") == VERIFIED_STAGED
        )
    ]
    covered_workflows = [
        key
        for key, workflow in indexes["workflow"].items()
        if isinstance(workflow.get("verification"), Mapping)
        and workflow["verification"].get("status") == VERIFIED_STAGED
    ]
    covered_actions = [
        key
        for key, action in indexes["action"].items()
        if isinstance(action.get("verification"), Mapping)
        and action["verification"].get("status") == VERIFIED_STAGED
    ]
    return {
        "verified_implementations": len(verified_impls),
        "unverified_implementations": len(unverified_impls),
        "covered_dots": len(covered_dots),
        "covered_workflows": len(covered_workflows),
        "covered_actions": len(covered_actions),
        "failures": len(failures),
        "failure_count": len(failures),
        "trial_count": len(receipts),
    }


def evaluate_candidate_trials(
    graph: Mapping[str, Any],
    receipts: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a pure, inactive candidate graph with trial evidence overlaid."""

    base = copy.deepcopy(dict(graph))
    existing_evaluation = base.get("trial_evaluation")
    if isinstance(existing_evaluation, Mapping) and isinstance(
        existing_evaluation.get("base_graph_content_digest"), str
    ):
        base_graph_content_digest = _sha(
            existing_evaluation["base_graph_content_digest"],
            "existing trial evaluation base_graph_content_digest",
        )
    else:
        # Bind lineage before any staged verification or metrics are overlaid.
        base_graph_content_digest = candidate_graph_content_digest(base)
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes, bytearray)):
        receipt_values = [receipts]
    else:
        receipt_values = list(receipts)
    if not receipt_values:
        raise TrialValidationError("at least one capability trial receipt is required")

    validated: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in receipt_values:
        checked = validate_trial_receipt(item, graph=base, now=now)
        validated.append(checked)
        status = checked.get("status", "passed")
        if status in {"failed", "blocked", "error"}:
            failures.append({
                "receipt_id": checked.get("receipt_id", checked["receipt_sha256"][:16]),
                "status": status,
                "reason": checked.get("failure", checked.get("outcome", {}).get("summary")),
            })

    # A receipt may be repeated safely.  The first copy is retained and all
    # evidence IDs are merged through deterministic sets below.
    by_receipt: dict[str, dict[str, Any]] = {}
    for receipt in validated:
        receipt_id = receipt.get("receipt_id", receipt["receipt_sha256"])
        existing = by_receipt.get(receipt_id)
        if existing is not None and existing["receipt_sha256"] != receipt["receipt_sha256"]:
            raise TrialIntegrityError(f"receipt id is reused with different content: {receipt_id}")
        by_receipt[receipt_id] = receipt
    validated = list(by_receipt.values())

    indexes = _graph_index(base)
    covered_impls: dict[tuple[str, str], set[str]] = {}
    covered_dots: dict[tuple[str, str], set[str]] = {}
    workflow_targets: set[tuple[str, str]] = set()
    action_targets: set[tuple[str, str]] = set()
    evidence_for_impl: dict[tuple[str, str], set[str]] = {}
    receipt_ids_for_impl: dict[tuple[str, str], set[str]] = {}
    evidence_for_dot: dict[tuple[str, str], set[str]] = {}
    receipt_ids_for_dot: dict[tuple[str, str], set[str]] = {}
    for receipt in validated:
        receipt_id = receipt.get("receipt_id", receipt["receipt_sha256"])
        refs = receipt["refs"]
        workflow_ref = refs.get("workflow_ref")
        action_ref = refs.get("action_ref")
        if workflow_ref is not None:
            workflow_targets.add(_ref_key(workflow_ref, "workflow"))
        if action_ref is not None:
            action_targets.add(_ref_key(action_ref, "action"))
        root_evidence = set(receipt.get("evidence_ids", []))
        for step in receipt.get("passed_steps", []):
            dot_key = _ref_key(step["dot_ref"], "dot")
            impl_key = _ref_key(step["implementation_ref"], "implementation")
            covered_impls.setdefault(dot_key, set()).add(impl_key)
            covered_dots.setdefault(dot_key, set()).add(impl_key)
            evidence = root_evidence | set(step.get("evidence_ids", []))
            evidence.add(f"trial:{receipt_id}")
            evidence_for_impl.setdefault(impl_key, set()).update(evidence)
            receipt_ids_for_impl.setdefault(impl_key, set()).add(str(receipt_id))
            evidence_for_dot.setdefault(dot_key, set()).update(evidence)
            receipt_ids_for_dot.setdefault(dot_key, set()).add(str(receipt_id))

    # Apply only implementation facts first; this makes all higher-level
    # statuses a deterministic reduction over exact, passed Dot steps.
    for dot_key, implementation_keys in covered_impls.items():
        dot = indexes["dot"].get(dot_key)
        if dot is None:
            continue
        for impl_key in implementation_keys:
            implementation = indexes["implementation"].get(impl_key)
            if implementation is None:
                continue
            _mark_verification(
                implementation,
                sorted(evidence_for_impl.get(impl_key, set())),
                sorted(receipt_ids_for_impl.get(impl_key, set())),
            )
        # ``indexes`` points into base's detached records, so the update above
        # is already applied.  The selected Dot is proven only by an exact
        # output-verified step for at least one implementation.
        _mark_verification(
            dot,
            sorted(evidence_for_dot.get(dot_key, set())),
            sorted(receipt_ids_for_dot.get(dot_key, set())),
        )
        trial = copy.deepcopy(dot.get("trial")) if isinstance(dot.get("trial"), Mapping) else {}
        trial["status"] = "passed"
        trial["evidence_ids"] = sorted(evidence_for_dot.get(dot_key, set()))
        trial["trial_receipt_ids"] = sorted(receipt_ids_for_dot.get(dot_key, set()))
        dot["trial"] = trial

    covered_workflows: set[tuple[str, str]] = set()
    for workflow_key in workflow_targets:
        workflow = indexes["workflow"].get(workflow_key)
        if workflow is None:
            continue
        expected_dots = _workflow_dots(workflow)
        expected_keys = [_ref_key(item, "dot") for item in expected_dots]
        if expected_keys and all(key in covered_dots for key in expected_keys):
            covered_workflows.add(workflow_key)
            evidence: set[str] = set()
            receipt_ids: set[str] = set()
            for dot_key in expected_keys:
                evidence.update(evidence_for_dot.get(dot_key, set()))
                receipt_ids.update(receipt_ids_for_dot.get(dot_key, set()))
            _mark_verification(workflow, sorted(evidence), sorted(receipt_ids))
            trial = (
                copy.deepcopy(workflow.get("trial"))
                if isinstance(workflow.get("trial"), Mapping)
                else {}
            )
            trial["status"] = "passed"
            trial["workflow_version"] = workflow["version"]
            trial["evidence_ids"] = sorted(evidence)
            trial["trial_receipt_ids"] = sorted(receipt_ids)
            workflow["trial"] = trial

    covered_actions: set[tuple[str, str]] = set()
    for action_key in action_targets:
        action = indexes["action"].get(action_key)
        if action is None:
            continue
        workflow_refs = _action_workflows(action)
        workflow_keys = [_ref_key(item, "workflow") for item in workflow_refs]
        if workflow_keys and all(key in covered_workflows for key in workflow_keys):
            covered_actions.add(action_key)
            evidence: set[str] = set()
            receipt_ids: set[str] = set()
            for workflow_key in workflow_keys:
                workflow = indexes["workflow"].get(workflow_key, {})
                verification = workflow.get("verification")
                if isinstance(verification, Mapping):
                    evidence.update(_unique_text(verification.get("evidence_ids"), "workflow.evidence_ids"))
                    receipt_ids.update(_unique_text(verification.get("trial_receipt_ids"), "workflow.trial_receipt_ids"))
            _mark_verification(action, sorted(evidence), sorted(receipt_ids))

    # Keep the graph's lifecycle/review/decision/activation dimensions exactly
    # as supplied.  Only the derived overlay and verification evidence are new.
    metrics = _derived_metrics(base, validated, failures)
    trial_evaluation = {
        "record_type": TRIAL_EVALUATION_RECORD_TYPE,
        "record_version": TRIAL_RECORD_VERSION,
        "status": VERIFIED_STAGED if metrics["verified_implementations"] else "unverified",
        "base_graph_input_digest": base.get("input_digest"),
        "base_graph_content_digest": base_graph_content_digest,
        "receipt_ids": sorted(str(item.get("receipt_id", item["receipt_sha256"])) for item in validated),
        "covered_implementation_refs": sorted(f"{key[0]}@{key[1]}" for key in evidence_for_impl),
        "covered_dot_refs": sorted(f"{key[0]}@{key[1]}" for key in covered_dots),
        "covered_workflow_refs": sorted(f"{key[0]}@{key[1]}" for key in covered_workflows),
        "covered_action_refs": sorted(f"{key[0]}@{key[1]}" for key in covered_actions),
        "failures": copy.deepcopy(failures),
        "metrics": metrics,
        "persistence_state_change": False,
        "activation": {"performed": False, "authorised": False, "active_surface": False},
        "publication": {"performed": False},
    }
    base["trial_evaluation"] = trial_evaluation
    base["trial_metrics"] = copy.deepcopy(metrics)
    base["evaluation_metrics"] = copy.deepcopy(metrics)
    existing_metrics = base.get("metrics")
    if isinstance(existing_metrics, Mapping):
        merged_metrics = copy.deepcopy(dict(existing_metrics))
        merged_metrics["trial"] = copy.deepcopy(metrics)
        base["metrics"] = merged_metrics
    return base


evaluate_trials = evaluate_candidate_trials
evaluate_capability_trials = evaluate_candidate_trials


def build_trial_plan(
    graph: Mapping[str, Any] | None = None,
    *,
    base_graph_input_digest: str | None = None,
    base_graph_content_digest: str | None = None,
    action_ref: Mapping[str, Any] | str | None = None,
    workflow_ref: Mapping[str, Any] | str | None = None,
    dot_refs: Sequence[Mapping[str, Any] | str] = (),
    implementation_refs: Sequence[Mapping[str, Any] | str] = (),
    project_id: str,
    project_revision: int,
    task_id: str,
    allowed_side_effects: Sequence[str] = (),
    expires_at: str,
    input_contract: Any,
    output_contract: Any,
    recovery: Mapping[str, Any],
    command_receipt_hashes: Sequence[str] = (),
    reality_check_receipt_hashes: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the compact plan shape consumed by :func:`validate_trial_plan`."""

    if graph is not None:
        graph_input, graph_contents = _graph_digests(graph)
        base_graph_input_digest = base_graph_input_digest or graph_input
        base_graph_content_digest = base_graph_content_digest or candidate_graph_content_digest(graph)
        if base_graph_content_digest not in graph_contents:
            raise TrialIntegrityError("supplied base graph content_digest does not match graph")
    if base_graph_input_digest is None or base_graph_content_digest is None:
        raise TrialIntegrityError("build_trial_plan needs base graph input/content digests")
    _sha(base_graph_input_digest, "base_graph_input_digest")
    _sha(base_graph_content_digest, "base_graph_content_digest")
    dot_values = [
        dict(_identity(item, "dot", "dot_ref"), sequence=index)
        for index, item in enumerate(dot_refs, start=1)
    ]
    implementation_values = [
        dict(_identity(item, "implementation", "implementation_ref"), sequence=index)
        for index, item in enumerate(implementation_refs, start=1)
    ]
    plan: dict[str, Any] = {
        "record_type": TRIAL_PLAN_RECORD_TYPE,
        "record_version": TRIAL_RECORD_VERSION,
        "base_graph": {
            "input_digest": base_graph_input_digest,
            "content_digest": base_graph_content_digest,
        },
        "candidate": {
            "action_ref": _identity(action_ref, "action", "action_ref") if action_ref is not None else None,
            "workflow_ref": _identity(workflow_ref, "workflow", "workflow_ref") if workflow_ref is not None else None,
            "dot_refs": dot_values,
            "implementation_refs": implementation_values,
        },
        "dot_refs": dot_values,
        "implementation_refs": implementation_values,
        "scope": {
            "project_id": project_id,
            "project_revision": project_revision,
            "task_id": task_id,
            "allowed_side_effects": list(allowed_side_effects),
            "expires_at": expires_at,
            "persistence_state_change": False,
        },
        "input_contract": copy.deepcopy(input_contract),
        "output_contract": copy.deepcopy(output_contract),
        "recovery": copy.deepcopy(dict(recovery)),
        "command_receipt_hashes": list(command_receipt_hashes),
        "reality_check_receipt_hashes": list(reality_check_receipt_hashes),
    }
    plan["plan_sha256"] = _digest(plan)
    return validate_trial_plan(plan, graph=graph)


build_capability_trial_plan = build_trial_plan


def build_trial_receipt(
    plan: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    *,
    receipt_id: str | None = None,
    outcome: Mapping[str, Any] | None = None,
    duration_ms: int | float = 0,
    cost: Any = 0,
    work_signature: str | Mapping[str, Any] = "trial-work",
    evidence_ids: Sequence[str] = (),
    recovery: Mapping[str, Any] | None = None,
    status: str = "passed",
    command_receipts: Sequence[Mapping[str, Any]] = (),
    reality_check_receipts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build an integrity-bound receipt from an existing trial plan."""

    plan_value = copy.deepcopy(dict(plan))
    if "plan_sha256" not in plan_value:
        plan_value["plan_sha256"] = _digest(plan_value)
    command_values = [copy.deepcopy(dict(item)) for item in command_receipts]
    reality_values = [copy.deepcopy(dict(item)) for item in reality_check_receipts]
    for item in command_values + reality_values:
        if "receipt_sha256" not in item:
            item["receipt_sha256"] = _digest(item)
    command_hashes = [item["receipt_sha256"] for item in command_values]
    reality_hashes = [item["receipt_sha256"] for item in reality_values]
    value: dict[str, Any] = {
        "record_type": TRIAL_RECEIPT_RECORD_TYPE,
        "record_version": TRIAL_RECORD_VERSION,
        "receipt_id": receipt_id or f"trial-{_digest({'plan': plan_value, 'steps': steps})[:20]}",
        "plan": plan_value,
        "steps": copy.deepcopy(list(steps)),
        "status": status,
        "outcome": copy.deepcopy(dict(outcome or {"status": status, "summary": "bounded trial"})),
        "duration_ms": duration_ms,
        "cost": copy.deepcopy(cost),
        "work_signature": copy.deepcopy(work_signature),
        "evidence_ids": list(evidence_ids),
        "recovery": copy.deepcopy(dict(recovery or plan_value.get("recovery", {}))),
        "persistence_state_change": False,
        "command_receipts": command_values,
        "reality_check_receipts": reality_values,
    }
    # Step-local hashes bind each step to the supplied receipt object without
    # changing caller-owned step dictionaries.
    for index, step in enumerate(value["steps"], start=1):
        if not isinstance(step, dict):
            continue
        step.setdefault("verification", {"status": "verified"})
        if index <= len(command_hashes):
            step.setdefault("command_receipt_hash", command_hashes[index - 1])
        if index <= len(reality_hashes):
            step.setdefault("reality_check_receipt_hash", reality_hashes[index - 1])
    value["receipt_sha256"] = _digest(value)
    return validate_trial_receipt(value)


build_capability_trial_receipt = build_trial_receipt
validate_trial_receipts = lambda receipts, **kwargs: [
    validate_trial_receipt(item, **kwargs) for item in receipts
]


__all__ = [
    "TRIAL_PLAN_RECORD_TYPE",
    "TRIAL_RECEIPT_RECORD_TYPE",
    "TRIAL_EVALUATION_RECORD_TYPE",
    "TRIAL_RECORD_VERSION",
    "VERIFIED_STAGED",
    "CapabilityTrialError",
    "TrialValidationError",
    "TrialIntegrityError",
    "TrialScopeError",
    "TrialOrderError",
    "TrialOutputError",
    "CapabilityTrialValidationError",
    "CapabilityTrialIntegrityError",
    "CapabilityTrialScopeError",
    "CapabilityTrialOrderError",
    "CapabilityTrialOutputError",
    "candidate_graph_content_digest",
    "graph_content_digest",
    "validate_trial_plan",
    "validate_capability_trial_plan",
    "validate_trial_receipt",
    "validate_capability_trial_receipt",
    "validate_receipt",
    "validate_trial_receipts",
    "evaluate_candidate_trials",
    "evaluate_trials",
    "evaluate_capability_trials",
    "build_trial_plan",
    "build_capability_trial_plan",
    "build_trial_receipt",
    "build_capability_trial_receipt",
]
