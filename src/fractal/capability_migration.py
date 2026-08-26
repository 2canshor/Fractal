"""Pure audit and rehearsal contracts for removing the old capability architecture.

This boundary is intentionally narrower than a migration executor.  It accepts a
candidate graph and an explicitly separate legacy inventory, validates the new
Source -> extraction -> Dot -> Workflow -> Action graph, and returns a
copy-only cutover rehearsal.  No Workplace or runtime writer is imported here;
the returned plan cannot delete, switch, activate, or publish anything.

Legacy Actions, taxonomy, Dot Groups, and Workflow material are retained as a
recoverable historical snapshot and audit input only.  They are never included
in the candidate graph or its digest.  A same-named Action is accepted only
when its own canonical induction evidence proves a fresh Workflow-cluster ->
human-intent -> Naming-System route.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from fractal.blueprint import load_blueprint
from fractal.capability_action import (
    ActionValidationError,
    validate_action,
    validate_platform_projection,
)
from fractal.capability_compiler import validate_candidate_graph
from fractal.capability_dot import CapabilityDotError, validate_capability_dot
from fractal.capability_extraction import ExtractionValidationError, validate_extraction
from fractal.capability_source import SourceValidationError, validate_source
from fractal.capability_workflow import (
    WorkflowValidationError,
    validate_execution_composition,
    validate_workflow,
)
from fractal.storage import canonical_json_bytes

MIGRATION_RECORD_TYPE = "capability-architecture-migration-audit"
MIGRATION_RECORD_VERSION = 1
MIGRATION_METHOD = "deterministic-old-architecture-removal-rehearsal"
MIGRATION_METHOD_VERSION = "1.0.0"

CUTOVER_STAGES = ("extract", "rebuild", "test", "switch", "remove")
WORKPLACE_KINDS = (
    "sources",
    "extractions",
    "candidate_dots",
    "candidate_workflows",
    "candidate_actions",
    "execution_compositions",
    "trials",
    "evidence",
)
SYSTEM_ACTIVE_KINDS = {"candidate_dots", "candidate_workflows", "candidate_actions"}


class CapabilityMigrationError(ValueError):
    """Base error for an invalid candidate migration audit."""


class MigrationInputError(CapabilityMigrationError):
    """Raised when a graph or inventory crosses a contract boundary."""


class MigrationValidationError(CapabilityMigrationError):
    """Raised when a candidate cannot be staged safely."""


class MigrationPlacementError(MigrationValidationError):
    """Raised when a record is owned by the wrong durable layer."""


# Compatibility spellings make the boundary easy for callers to discover
# without creating a second lifecycle or storage implementation.
CapabilityArchitectureMigrationError = CapabilityMigrationError
CapabilityMigrationValidationError = MigrationValidationError
MigrationAuditError = CapabilityMigrationError
PlacementValidationError = MigrationPlacementError


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_SPACE_RE = re.compile(r"\s+")

_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "sources": (
        "sources",
        "source_refs",
        "source",
        "source_records",
        "candidate_sources",
    ),
    "extractions": (
        "extractions",
        "extraction_evidence",
        "extraction",
        "responsibilities",
        "responsibility_extractions",
        "extraction_records",
        "candidate_extractions",
    ),
    "candidate_dots": ("candidate_dots", "dots", "dot_records", "candidate_dot_records"),
    "candidate_workflows": (
        "candidate_workflows",
        "workflows",
        "workflow_records",
        "candidate_workflow_records",
    ),
    "candidate_actions": (
        "candidate_actions",
        "actions",
        "action_records",
        "candidate_action_records",
    ),
    "execution_compositions": (
        "execution_compositions",
        "compositions",
        "execution_composition_records",
    ),
    "trials": ("trials", "trial", "trial_records", "candidate_trials"),
    "evidence": ("evidence", "evidence_records", "trial_evidence"),
}

_PROJECTION_ALIASES = (
    "staged_projections",
    "compatibility_projections",
    "platform_projections",
    "projections",
)

_IDENTITY_KEYS = {
    "sources": "source_id",
    "extractions": "evidence_digest",
    "candidate_dots": "dot_id",
    "candidate_workflows": "workflow_id",
    "candidate_actions": "action_id",
    "execution_compositions": "composition_id",
    "trials": "trial_id",
    "evidence": "evidence_id",
}

# These fields are only allowed in the separately supplied historical input.
# The anti-seed attestation uses ``no_legacy_*`` flags, which deliberately do
# not appear here.
_LEGACY_KEYS = {
    "legacy",
    "legacy_fixture",
    "legacy_inventory",
    "legacy_actions",
    "legacy_action",
    "legacy_action_id",
    "legacy_workflows",
    "legacy_workflow",
    "old_actions",
    "old_action",
    "old_workflows",
    "old_workflow",
    "workflow",
    "category",
    "categories",
    "old_taxonomy",
    "taxonomy",
    "taxonomies",
    "dot_group",
    "dot_groups",
    "dot_group_id",
    "dot_group_refs",
    "canonical_dot_groups",
    "inherited_action",
    "inherited_action_id",
    "carry_forward",
    "carryforward",
    "preserve_old",
    "preserve_old_logic",
    "fallback",
}

_CANDIDATE_SECTIONS = {
    "dots",
    "workflows",
    "actions",
    "candidate_dots",
    "candidate_workflows",
    "candidate_actions",
}
_CANDIDATE_RECORD_CHILD_RESET = {
    "history",
    "fallback",
    "metadata",
    "provenance",
    "recovery",
    "evidence",
    "induction_evidence",
    "lineage",
}
_LEGACY_RECORD_TYPES = {
    "action",
    "legacy_action",
    "old_action",
    "capability_action",
    "capability_action_record",
    "workflow",
    "legacy_workflow",
    "old_workflow",
    "capability_workflow",
    "capability_workflow_record",
    "dot_group",
    "legacy_dot_group",
    "old_dot_group",
}

_DIRECT_SKILL_KEYS = {
    "skill",
    "skills",
    "skill_id",
    "skill_ref",
    "skill_refs",
    "external_skill",
    "external_skills",
    "platform_skill",
    "platform_skills",
    "platform_skill_id",
    "platform_skill_ref",
}

_SOURCE_CALLABILITY_KEYS = {
    "callable_source",
    "source_callable",
    "source_callability",
    "source_execution",
    "source_invocation",
}

_PLACEMENT_KEYS = ("placement", "owner", "ownership", "layer")
_VERSION_EVIDENCE_KEYS = (
    "version_evidence",
    "system_version_evidence",
    "future_version_evidence",
    "version_authorisation",
    "version_authorization",
)


def _key(value: Any) -> str:
    return _SPACE_RE.sub("_", str(value).strip().casefold().replace("-", "_"))


def _copy(value: Any, label: str = "value") -> Any:
    try:
        return copy.deepcopy(value)
    except (TypeError, ValueError) as error:
        raise MigrationInputError(f"{label} must be copyable JSON data") from error


def _canonical(value: Any, label: str = "value") -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        raise MigrationInputError(f"{label} must be portable JSON data") from error


def _digest(value: Any, *, prefix: str) -> str:
    return f"{prefix}{hashlib.sha256(_canonical(value)).hexdigest()}"


def _text(value: Any) -> str:
    return _SPACE_RE.sub(" ", value.strip()) if isinstance(value, str) else ""


def _slug(value: Any) -> str:
    text = _text(value).casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _state(record: Mapping[str, Any]) -> str | None:
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        state = lifecycle.get("status", lifecycle.get("state"))
        if isinstance(state, str):
            return state
    status = record.get("status", record.get("state"))
    if isinstance(status, str):
        return status
    activation = record.get("activation")
    if isinstance(activation, Mapping) and activation.get("status") == "active":
        return "active"
    return None


def _record_identity(record: Mapping[str, Any], kind: str, *, fallback: str = "") -> str:
    kind = {"actions": "candidate_actions", "workflows": "candidate_workflows"}.get(
        kind, kind
    )
    identity = _IDENTITY_KEYS[kind]
    value = record.get(identity)
    if kind == "extractions" and not value:
        value = record.get("evidence_id", record.get("record_type"))
    if isinstance(value, str) and value.strip():
        version = record.get("version")
        return f"{value}@{version}" if isinstance(version, str) else value
    return fallback or _digest(record, prefix=f"{kind[:-1]}-")


def _is_compact_source_ref(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    source_id = value.get("source_id")
    provenance_refs = value.get("provenance_refs")
    return (
        isinstance(source_id, str)
        and bool(source_id.strip())
        and isinstance(provenance_refs, Sequence)
        and not isinstance(provenance_refs, (str, bytes, bytearray))
        and all(isinstance(item, str) and item.strip() for item in provenance_refs)
        and set(value).issubset({"source_id", "provenance_refs"})
    )


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _flatten_records(value: Any, kind: str, *, label: str) -> list[dict[str, Any]]:
    """Expand an ordered list or an id-indexed collection without conversion."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        identity = _IDENTITY_KEYS[kind]
        if identity in value or (kind == "extractions" and "record_type" in value):
            return [dict(value)]
        # A conventional wrapper is accepted only when its key belongs to the
        # same section.  This prevents an old Workflow object from becoming a
        # candidate through a permissive generic flattening path.
        for alias in _SECTION_ALIASES[kind]:
            if alias in value:
                return _flatten_records(value[alias], kind, label=f"{label}.{alias}")
        records: list[dict[str, Any]] = []
        for index, child in value.items():
            if not isinstance(child, Mapping):
                raise MigrationInputError(f"{label}[{index!r}] must be an object")
            item = dict(child)
            if (
                _IDENTITY_KEYS[kind] not in item
                and isinstance(index, str)
                and kind != "extractions"
            ):
                # id-indexed registries carry the id as the map key.  Do not
                # invent evidence ids for extraction records.
                item[_IDENTITY_KEYS[kind]] = index
            records.append(item)
        return records
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        records = []
        for index, child in enumerate(value):
            if not isinstance(child, Mapping):
                raise MigrationInputError(f"{label}[{index}] must be an object")
            records.append(dict(child))
        return records
    raise MigrationInputError(f"{label} must be an object, id-indexed object, or list")


def _find_section(graph: Mapping[str, Any], kind: str) -> tuple[str | None, Any]:
    observed: list[tuple[str, Any]] = []
    for key in _SECTION_ALIASES[kind]:
        if key in graph:
            observed.append((key, graph[key]))
    if len(observed) > 1:
        values = {_canonical(value) for _, value in observed}
        if len(values) > 1:
            names = ", ".join(name for name, _ in observed)
            raise MigrationInputError(f"Conflicting aliases for {kind}: {names}")
    return observed[0] if observed else (None, None)


def _walk_rejections(
    value: Any,
    *,
    path: str = "$",
    allow_history: bool = False,
    allow_projection_skills: bool = False,
    allow_compiler_receipts: bool = False,
    candidate_section: bool = False,
) -> None:
    """Reject old architecture and direct external authority by key."""

    if isinstance(value, Mapping):
        historical = allow_history or path.casefold().startswith("$.history")
        projection = allow_projection_skills or any(alias in path for alias in _PROJECTION_ALIASES)
        compiler_receipt = allow_compiler_receipts and any(
            path.casefold().startswith(f"$.{field}")
            for field in ("persistence", "execution", "activation")
        )
        for raw_key, child in value.items():
            key = _key(raw_key)
            child_candidate_section = candidate_section or (
                path == "$" and key in _CANDIDATE_SECTIONS
            )
            nested_old_workflow = key == "workflow" or (
                key == "workflows" and path != "$"
            )
            if not child_candidate_section and nested_old_workflow:
                raise MigrationInputError(
                    f"Old Workflow objects cannot enter a candidate: {path}.{raw_key}"
                )
            if key in {"legacy_action_id", "inherited_action", "inherited_action_id"}:
                raise MigrationInputError(
                    f"Inherited Action identity cannot enter a candidate: {path}.{raw_key}"
                )
            if not child_candidate_section and key in _LEGACY_KEYS:
                raise MigrationInputError(
                    f"Legacy taxonomy/Dot Group/Action/Workflow material cannot enter a candidate: "
                    f"{path}.{raw_key}"
                )
            if not projection and key in _DIRECT_SKILL_KEYS:
                raise MigrationInputError(
                    f"Direct external Skill reference is not a candidate object: {path}.{raw_key}"
                )
            if key in _SOURCE_CALLABILITY_KEYS and not compiler_receipt:
                raise MigrationInputError(
                    f"Source callability is not a candidate authority: {path}.{raw_key}"
                )
            _walk_rejections(
                child,
                path=f"{path}.{raw_key}",
                allow_history=historical,
                allow_projection_skills=projection,
                allow_compiler_receipts=allow_compiler_receipts,
                candidate_section=child_candidate_section
                and key not in _CANDIDATE_RECORD_CHILD_RESET,
            )
        record_type = _key(value.get("record_type"))
        if record_type in _LEGACY_RECORD_TYPES and not candidate_section:
            raise MigrationInputError(
                f"Legacy capability record cannot be nested in a candidate: {path}.record_type"
            )
        interface_type = _key(value.get("interface_type"))
        if interface_type in {"action", "command"} and not candidate_section:
            raise MigrationInputError(
                f"Legacy user-surface {interface_type} cannot enter a candidate: {path}"
            )
        if not candidate_section:
            keys = {_key(item) for item in value}
            looks_like_action = "action_id" in keys and bool(
                keys.intersection({"human_name", "name", "outcome", "human_intent"})
            )
            looks_like_workflow = "workflow_id" in keys and bool(
                keys.intersection({"dot_refs", "steps", "success_contract", "inputs", "outputs"})
            )
            looks_like_dot_group = "group_id" in keys and bool(
                keys.intersection({"component_ids", "action_ids", "workflow_ids"})
            )
            if looks_like_action or looks_like_workflow or looks_like_dot_group:
                raise MigrationInputError(
                    f"Legacy Action/Workflow/Dot Group object cannot enter a candidate: {path}"
                )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _walk_rejections(
                child,
                path=f"{path}[{index}]",
                allow_history=allow_history,
                allow_projection_skills=allow_projection_skills,
                allow_compiler_receipts=allow_compiler_receipts,
                candidate_section=candidate_section,
            )


def _is_canonical_candidate_graph(value: Mapping[str, Any]) -> bool:
    return (
        value.get("record_type") == "capability-candidate-graph"
        and value.get("record_version") == 1
    )


def _validate_compiler_boundary_receipts(graph: Mapping[str, Any]) -> None:
    """Require compiler persistence/execution/activation receipts to be false."""

    def walk(value: Any, *, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                walk(child, path=f"{path}.{key}")
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                walk(child, path=f"{path}[{index}]")
            return
        if value is True:
            raise MigrationInputError(
                "Compiler boundary receipt must keep every mutation/authority/active "
                f"flag false: {path}"
            )
        if isinstance(value, str) and _key(value) in {
            "active",
            "activated",
            "published",
            "persistent",
            "executed",
        }:
            raise MigrationInputError(
                f"Compiler boundary receipt cannot claim active or executed state: {path}"
            )

    for field in ("persistence", "execution", "activation"):
        if field in graph:
            if not isinstance(graph[field], Mapping):
                raise MigrationInputError(f"candidate graph {field} receipt must be an object")
            walk(graph[field], path=f"$.{field}")


def _strip_placement_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    value = _copy(record, "candidate record")
    for key in (*_PLACEMENT_KEYS, *_VERSION_EVIDENCE_KEYS):
        value.pop(key, None)
    return value


def _normalise_label(value: Any, *, label: str) -> str:
    if isinstance(value, Mapping):
        for key in ("owner", "ownership", "layer", "domain", "placement"):
            if key in value:
                return _normalise_label(value[key], label=label)
        raise MigrationPlacementError(f"{label} must identify Workplace or System ownership")
    text = _key(value)
    if "workplace" in text or text in {"project", "project_local", "candidate"}:
        return "workplace"
    if text == "system" or text.startswith("system_") or text.endswith("_system"):
        return "system"
    raise MigrationPlacementError(f"{label} must identify Workplace or System ownership")


def _mapping_for_item(mapping: Any, kind: str, record: Mapping[str, Any]) -> Any:
    if mapping is None:
        return None
    if isinstance(mapping, str):
        return mapping
    if not isinstance(mapping, Mapping):
        raise MigrationPlacementError(f"placement.{kind} must be a label or mapping")
    identity = _record_identity(record, kind)
    candidate_keys = [identity, identity.split("@", 1)[0], record.get(_IDENTITY_KEYS[kind])]
    candidate_keys.extend(_SECTION_ALIASES[kind])
    for key in candidate_keys:
        if isinstance(key, str) and key in mapping:
            return mapping[key]
    for key in ("owner", "ownership", "layer", "domain", "placement", "default"):
        if key in mapping:
            return mapping[key]
    return None


def _version_evidence(value: Any, *, path: str = "$") -> bool:
    """Return whether a nested value explicitly authorises a future ``/version``."""

    if isinstance(value, Mapping):
        operation = value.get("command", value.get("route", value.get("operation")))
        marker = " ".join(
            str(value.get(key, ""))
            for key in ("command", "route", "operation", "authority", "reason")
        ).casefold()
        authorised = value.get("authorised", value.get("authorized")) is True
        if (operation == "/version" or "/version" in marker) and authorised:
            return True
        return any(_version_evidence(child, path=f"{path}.{key}") for key, child in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            _version_evidence(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        )
    return False


def _has_authorised_version_evidence(
    record: Mapping[str, Any], graph_version_evidence: Any, metadata: Any
) -> bool:
    candidates = [graph_version_evidence, metadata]
    for key in _VERSION_EVIDENCE_KEYS:
        candidates.append(record.get(key))
    # The explicit field is preferred, but a canonical active record may carry
    # the future command under its lifecycle/activation evidence block.
    candidates.extend((record.get("lifecycle"), record.get("activation")))
    return any(_version_evidence(value) for value in candidates if value is not None)


def _placement_for(
    graph: Mapping[str, Any],
    kind: str,
    record: Mapping[str, Any],
    *,
    version_evidence: Any,
) -> dict[str, Any]:
    state = _state(record)
    metadata: Any = None
    for key in _PLACEMENT_KEYS:
        if key in record:
            metadata = record[key]
            break
    if metadata is None:
        for key in ("placement", "ownership"):
            if key in graph:
                metadata = _mapping_for_item(graph[key], kind, record)
                if metadata is not None:
                    break
    if metadata is None:
        metadata = "workplace"
    owner = _normalise_label(metadata, label=f"{kind} placement")
    if kind in {"sources", "extractions", "execution_compositions", "trials", "evidence"}:
        if owner != "workplace":
            raise MigrationPlacementError(f"{kind} must be Workplace-owned")
    elif state == "active":
        if kind not in SYSTEM_ACTIVE_KINDS:
            raise MigrationPlacementError(f"active {kind} cannot be a candidate object")
        if owner != "system":
            raise MigrationPlacementError(f"active {kind} must be System-owned")
        if not _has_authorised_version_evidence(record, version_evidence, metadata):
            raise MigrationPlacementError(
                f"active {kind} requires explicit authorised /version evidence"
            )
    elif owner != "workplace":
        raise MigrationPlacementError(f"candidate {kind} must be Workplace-owned")
    return {
        "kind": kind,
        "identity": _record_identity(record, kind),
        "state": state or "candidate",
        "owner": owner,
        "version_authorised": _has_authorised_version_evidence(
            record, version_evidence, metadata
        ),
    }


def _validate_source_records(
    records: Sequence[Mapping[str, Any]], *, compact_only: bool = False
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if _is_compact_source_ref(record):
            result.append(_copy(dict(record), "compact Source reference"))
            continue
        if compact_only:
            raise MigrationValidationError(
                "canonical candidate graph must retain compact Source references, "
                "not raw Source records"
            )
        try:
            result.append(validate_source(record))
        except (SourceValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid candidate Source at index {index}: {error}"
            ) from error
    return result


def _validate_extraction_records(
    records: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    source_by_id = {item["source_id"]: item for item in sources}
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        source = None
        refs = record.get("source_refs")
        if (
            isinstance(refs, Sequence)
            and not isinstance(refs, (str, bytes, bytearray))
            and len(refs) == 1
            and refs[0] in source_by_id
        ):
            candidate_source = source_by_id[refs[0]]
            if not _is_compact_source_ref(candidate_source):
                source = candidate_source
        try:
            validated = validate_extraction(record, source=source)
        except (ExtractionValidationError, SourceValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid candidate responsibility extraction at index {index}: {error}"
            ) from error
        if isinstance(validated, Mapping) and "responsibilities" in validated:
            result.extend(_copy(validated["responsibilities"], "extractions"))
        elif isinstance(validated, list):
            result.extend(_copy(validated, "extractions"))
        else:
            result.append(_copy(validated, "extraction"))
    return result


def _validate_dot_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        try:
            result.append(validate_capability_dot(_strip_placement_metadata(record)))
        except (CapabilityDotError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid candidate Dot at index {index}: {error}"
            ) from error
    return result


def _validate_workflow_records(
    records: Sequence[Mapping[str, Any]], dots: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        try:
            result.append(
                validate_workflow(
                    _strip_placement_metadata(record), dot_records=dots or None
                )
            )
        except (WorkflowValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid candidate Workflow at index {index}: {error}"
            ) from error
    return result


def _validate_action_records(
    records: Sequence[Mapping[str, Any]], workflows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        try:
            result.append(
                validate_action(_strip_placement_metadata(record), workflow_records=workflows)
            )
        except (ActionValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid candidate Action at index {index}: {error}"
            ) from error
    # validate_action_graph is intentionally imported lazily: the individual
    # validator above gives a more useful error for a malformed graph, while
    # the graph check is still applied when multiple Actions share a Workflow.
    if result:
        from fractal.capability_action import validate_action_graph

        try:
            return validate_action_graph(result, workflow_records=workflows)
        except (ActionValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(f"Invalid candidate Action graph: {error}") from error
    return result


def _validate_composition_records(
    records: Sequence[Mapping[str, Any]],
    workflows: Sequence[Mapping[str, Any]],
    dots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        try:
            result.append(
                validate_execution_composition(
                    _strip_placement_metadata(record),
                    workflows=workflows or None,
                    dots=dots or None,
                )
            )
        except (WorkflowValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid candidate Execution Composition at index {index}: {error}"
            ) from error
    return result


def _validate_generic_records(
    records: Sequence[Mapping[str, Any]], kind: str
) -> list[dict[str, Any]]:
    result = []
    for index, record in enumerate(records):
        item = _strip_placement_metadata(record)
        if kind == "trials" and not any(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("trial_id", "id", "composition_id", "dot_id", "workflow_id")
        ):
            raise MigrationValidationError(f"trial at index {index} requires a stable identity")
        if not item:
            raise MigrationValidationError(f"{kind} at index {index} must not be empty")
        result.append(item)
    return result


def _validate_staged_projections(value: Any) -> list[dict[str, Any]]:
    """Validate derived compatibility projections without making them Actions."""

    if value is None:
        return []
    if isinstance(value, Mapping) and any(
        key in value for key in ("record_type", "platform", "platform_id", "derived_from")
    ):
        records = [dict(value)]
    else:
        records = _flatten_records(value, "evidence", label="staged_projections")
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if record.get("canonical") is True or record.get("status") in {
            "active",
            "canonical",
            "published",
            "activated",
        }:
            raise MigrationValidationError(
                "Compatibility projections may be staged but cannot be canonical or active"
            )
        try:
            projection = validate_platform_projection(record)
        except (ActionValidationError, TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid staged compatibility projection at index {index}: {error}"
            ) from error
        projection["status"] = "staged"
        projection["canonical"] = False
        result.append(projection)
    result.sort(key=lambda item: (item.get("platform", ""), _canonical(item)))
    return result


def _legacy_records(value: Any, kind: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in (
            kind,
            "actions" if kind == "actions" else "",
            f"legacy_{kind}",
            "records",
            "items",
        ):
            if key and key in value:
                return _legacy_records(value[key], kind)
        identity = "action_id" if kind == "actions" else "workflow_id"
        if identity in value:
            return [dict(value)]
        if kind == "inventory":
            return [dict(child) for child in value.values() if isinstance(child, Mapping)]
        return [dict(child) for child in value.values() if isinstance(child, Mapping)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    raise MigrationInputError(f"legacy {kind} must be an object or list")


def _legacy_section(value: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return None


def _normalised_name(record: Mapping[str, Any]) -> str:
    human_intent = record.get("human_intent")
    values = [
        record.get("human_name"),
        record.get("name"),
        record.get("entry_id"),
        record.get("component_id"),
    ]
    if isinstance(human_intent, Mapping):
        values.extend((human_intent.get("familiar"), human_intent.get("familiar_name")))
    for value in values:
        text = _slug(value)
        if text:
            return text
    return ""


def _legacy_surface_entries(
    value: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract actual user-surface entries without treating Commands as Actions."""

    entries = value.get("entries")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return [], []
    actions = [
        dict(item)
        for item in entries
        if isinstance(item, Mapping) and _key(item.get("interface_type")) == "action"
    ]
    commands = [
        dict(item)
        for item in entries
        if isinstance(item, Mapping) and _key(item.get("interface_type")) == "command"
    ]
    return actions, commands


def _legacy_action_records(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit = _legacy_records(
        _legacy_section(value, ("actions", "legacy_actions", "old_actions")),
        "actions",
    )
    surface, _ = _legacy_surface_entries(value)
    return explicit + surface


def _legacy_command_records(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    _, commands = _legacy_surface_entries(value)
    return commands


def _action_induction_valid(
    action: Mapping[str, Any], *, legacy_ids: set[str] | None = None
) -> tuple[bool, str]:
    evidence = action.get("induction_evidence")
    if not isinstance(evidence, Mapping):
        return False, "missing induction_evidence"
    clusters = evidence.get("new_workflow_clusters")
    if not isinstance(clusters, Sequence) or isinstance(clusters, (str, bytes, bytearray)):
        return False, "missing new Workflow cluster evidence"
    if not clusters:
        return False, "new Workflow cluster evidence is empty"
    refs = action.get("workflow_refs")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
        return False, "missing Workflow references"
    expected = {
        (item.get("workflow_id"), item.get("version"))
        for item in refs
        if isinstance(item, Mapping)
    }
    observed = {
        (item.get("workflow_id"), item.get("version"))
        for item in clusters
        if isinstance(item, Mapping)
    }
    if expected != observed:
        return False, "Workflow cluster evidence does not match the candidate Action"
    intent = evidence.get("human_intent_analysis")
    if not intent:
        return False, "missing human-intent evidence"
    naming = evidence.get("naming_system")
    if not isinstance(naming, Mapping) or not _text(naming.get("rationale")):
        return False, "missing Naming System evidence"
    alternatives = naming.get("alternatives")
    if not isinstance(alternatives, Sequence) or isinstance(alternatives, (str, bytes, bytearray)):
        return False, "Naming System alternatives are required"
    if not _text(evidence.get("input_digest")) and not (
        isinstance(evidence.get("input_digest"), Mapping)
        and _text(evidence["input_digest"].get("value", evidence["input_digest"].get("digest")))
    ):
        return False, "missing independent induction input digest"
    anti_seed = evidence.get("anti_seed_attestation", evidence.get("anti_seed"))
    if not isinstance(anti_seed, Mapping):
        return False, "missing anti-seed attestation"
    required_flags = (
        "attested",
        "independent",
        "no_inherited_action",
        "no_legacy_action",
        "no_preserve_old_logic",
    )
    if any(anti_seed.get(flag) is not True for flag in required_flags):
        return False, "anti-seed attestation does not prove independent induction"
    if legacy_ids and action.get("action_id") in legacy_ids:
        return False, "candidate Action inherited a legacy Action id"
    return True, "new Workflow cluster -> human intent -> Naming System evidence is complete"


def _validate_blueprint_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {
            "status": "not-supplied",
            "valid": False,
            "reason": "Blueprint Mapping is required before debate and future cutover",
        }
    records = value
    if isinstance(value, Mapping):
        for key in ("mappings", "candidates", "records"):
            if key in value and isinstance(value[key], Sequence) and not isinstance(
                value[key], (str, bytes, bytearray)
            ):
                records = value[key]
                break
    if isinstance(records, Mapping):
        records = [records]
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise MigrationValidationError("Blueprint Mapping must be an object or list")
    blueprint = load_blueprint()
    element_ids = {
        element["element_id"]
        for genre in blueprint["element_library"]["genres"]
        for element in genre["elements"]
    }
    core = blueprint["element_library"]["core"]
    element_ids.update(
        {
            core["philosophy"]["element_id"],
            core["protagonist"]["element_id"],
        }
    )
    flow_ids = {flow["flow_id"] for flow in blueprint["flows"]["entries"]}
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise MigrationValidationError(f"Blueprint Mapping[{index}] must be an object")
        item = _copy(raw, "Blueprint Mapping")
        _walk_rejections(item, path=f"$.blueprint_mapping[{index}]")
        target = item.get("target") if isinstance(item.get("target"), Mapping) else item
        if not isinstance(target, Mapping):
            raise MigrationValidationError("Blueprint Mapping requires a target")
        if any(
            target.get(key)
            for key in ("new_element_id", "new_element", "element_to_add")
        ):
            raise MigrationValidationError(
                "Old-architecture migration cannot add a Blueprint Element"
            )
        existing = target.get(
            "existing_element_id",
            target.get("element_id", target.get("target_element_id")),
        )
        if existing not in element_ids:
            raise MigrationValidationError(
                f"Blueprint Mapping must target an existing Element: {existing!r}"
            )
        workflow_id = item.get("workflow_id", target.get("workflow_id"))
        flow_id = item.get("flow_id", target.get("flow_id"))
        if flow_id is not None:
            raise MigrationValidationError("A candidate Workflow is not a Blueprint Flow")
        if workflow_id in flow_ids:
            raise MigrationValidationError("Workflow identity cannot be reused as a Blueprint Flow")
        status = item.get("status", "mapped-staged-not-active")
        if status in {"active", "canonical", "activated", "published"}:
            raise MigrationValidationError("Blueprint Mapping must remain staged and inactive")
        authority = item.get("authority")
        if (
            authority is not None
            and isinstance(authority, Mapping)
            and authority.get("scope", "proposal-only") != "proposal-only"
        ):
            raise MigrationValidationError(
                "Blueprint Mapping authority must remain proposal-only"
            )
        output.append(
            {
                **item,
                "status": "mapped-staged-not-active",
                "valid": True,
                "target": {
                    **dict(target),
                    "existing_element_id": existing,
                    "new_element_id": None,
                },
                "blueprint_version": blueprint["blueprint_version"],
                "workflow_is_flow": False,
                "new_element": False,
            }
        )
    return {
        "record_type": "blueprint-migration-mapping-audit",
        "status": "mapped-staged-not-active",
        "valid": bool(output),
        "blueprint_version": blueprint["blueprint_version"],
        "mappings": output,
        "uses_existing_elements_only": True,
        "workflow_is_flow": False,
        "new_element": False,
    }


def _candidate_graph_sections(
    graph: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    sections: dict[str, list[dict[str, Any]]] = {}
    aliases_used: set[str] = set()
    for kind in WORKPLACE_KINDS:
        alias, value = _find_section(graph, kind)
        if alias is not None:
            aliases_used.add(alias)
        sections[kind] = _flatten_records(value, kind, label=alias or kind)
    metadata = {
        key: _copy(value, f"candidate graph.{key}")
        for key, value in graph.items()
        if key not in aliases_used
    }
    return sections, metadata


def _canonical_candidate_graph(
    sections: Mapping[str, Sequence[Mapping[str, Any]]], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    result = _copy(dict(metadata), "candidate graph")
    for kind in WORKPLACE_KINDS:
        records = [_copy(item, kind) for item in sections[kind]]
        records.sort(key=lambda item: (_record_identity(item, kind), _canonical(item)))
        result[kind] = records
    # An explicit canonical graph marker makes the boundary inspectable while
    # retaining arbitrary evidence/history metadata supplied by the caller.
    result.setdefault("record_type", "capability-candidate-graph")
    result.setdefault("record_version", 1)
    return result


def _preservation_report(
    sources: Sequence[Mapping[str, Any]],
    extractions: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
    legacy_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    source_ids = [item.get("source_id") for item in sources]
    provenance_preserved = all(
        (
            _is_compact_source_ref(item)
            and bool(item.get("provenance_refs"))
        )
        or (
            isinstance(item.get("provenance"), Sequence)
            and bool(item.get("provenance"))
        )
        for item in sources
    )
    licences_preserved = all(
        _is_compact_source_ref(item)
        or (isinstance(item.get("licence"), Mapping) and bool(item.get("licence")))
        for item in sources
    )
    extraction_evidence = [
        ref
        for item in extractions
        for ref in item.get("evidence_refs", [])
        if isinstance(ref, str)
    ]
    history = metadata.get("history")
    return {
        "provenance_preserved": provenance_preserved,
        "licences_preserved": licences_preserved,
        "history_preserved": True,
        "source_ids": sorted(item for item in source_ids if isinstance(item, str)),
        "source_provenance": {
            item["source_id"]: _copy(
                item.get("provenance", item.get("provenance_refs", [])),
                "Source provenance",
            )
            for item in sources
            if isinstance(item.get("source_id"), str)
        },
        "source_licences": {
            item["source_id"]: _copy(
                item.get("licence", {"reference_only": _is_compact_source_ref(item)}),
                "Source licence",
            )
            for item in sources
            if isinstance(item.get("source_id"), str)
        },
        "extraction_evidence_refs": sorted(set(extraction_evidence)),
        "history_digest": _digest(history if history is not None else {}, prefix="history-") ,
        "legacy_snapshot_digest": _digest(legacy_inventory, prefix="legacy-history-") ,
        "legacy_snapshot_preserved": True,
    }


def _legacy_action_audit(
    legacy_inventory: Mapping[str, Any],
    candidate_actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    records = _legacy_action_records(legacy_inventory)
    legacy_ids = {
        _text(
            item.get(
                "action_id",
                item.get("id", item.get("entry_id", item.get("component_id"))),
            )
        )
        for item in records
        if _text(
            item.get(
                "action_id",
                item.get("id", item.get("entry_id", item.get("component_id"))),
            )
        )
    }
    candidate_by_name: dict[str, list[Mapping[str, Any]]] = {}
    for action in candidate_actions:
        candidate_by_name.setdefault(_normalised_name(action), []).append(action)
    audit: list[dict[str, Any]] = []
    for index, old in enumerate(records):
        old_id = _text(
            old.get(
                "action_id",
                old.get("id", old.get("entry_id", old.get("component_id"))),
            )
        ) or f"legacy-action-{index + 1}"
        old_name = _text(old.get("human_name", old.get("name")))
        name_key = _normalised_name(old)
        matches = sorted(
            candidate_by_name.get(name_key, []),
            key=lambda item: (item.get("action_id", ""), item.get("version", "")),
        )
        if not matches:
            audit.append(
                {
                    "legacy_action_id": old_id,
                    "legacy_reference": old_id,
                    "legacy_name": old_name,
                    "status": "removed-from-candidate",
                    "status_code": "removed",
                    "evidence": {
                        "excluded_from_candidate_graph": True,
                        "no_candidate_carry_forward": True,
                        "fallback_recovery_retained": True,
                    },
                }
            )
            continue
        valid, reason = _action_induction_valid(matches[0], legacy_ids=legacy_ids)
        if not valid:
            raise MigrationValidationError(
                f"Same-named legacy Action {old_id!r} is not independently re-induced: {reason}"
            )
        action = matches[0]
        audit.append(
            {
                "legacy_action_id": old_id,
                "legacy_reference": old_id,
                "legacy_name": old_name,
                "status": "independently-reinduced",
                "status_code": "independently_reinduced",
                "new_action_id": action["action_id"],
                "evidence": {
                    "excluded_from_candidate_graph": True,
                    "new_workflow_cluster_to_human_intent_to_naming_system": True,
                    "input_digest": _copy(
                        action["induction_evidence"]["input_digest"], "induction digest"
                    ),
                    "no_carry_forward_label": True,
                },
            }
        )
    return audit


def _legacy_command_audit(legacy_inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    controls = []
    for index, command in enumerate(_legacy_command_records(legacy_inventory)):
        command_id = _text(
            command.get("entry_id", command.get("component_id", command.get("id")))
        ) or f"legacy-command-{index + 1}"
        controls.append(
            {
                "command_id": command_id,
                "interface_type": "command",
                "status": "preserved-lifecycle-control",
                "role": "lifecycle-control",
                "outside_candidate_induction": True,
                "retained_until": "explicit-future-/version",
                "removed_from_candidate": True,
            }
        )
    return controls


def _cutover_plan(
    *,
    legacy_inventory: Mapping[str, Any],
    candidate_digest: str,
    blueprint: Mapping[str, Any],
) -> dict[str, Any]:
    active_legacy = []
    active_workflow_count = 0
    active_dot_group_count = 0
    for kind, names in (
        ("actions", ("actions", "legacy_actions", "old_actions")),
        ("workflows", ("workflows", "legacy_workflows", "old_workflows")),
        ("dot_groups", ("dot_groups", "canonical_dot_groups")),
        ("taxonomy", ("taxonomy", "old_taxonomy")),
    ):
        values = _legacy_section(legacy_inventory, names)
        records = (
            _legacy_records(values, kind if kind == "actions" else "inventory")
            if values is not None
            else []
        )
        for record in records:
            state = _state(record)
            is_active = state == "active" or (
                state is None and kind in {"workflows", "dot_groups"}
            )
            if is_active:
                if kind == "workflows":
                    active_workflow_count += 1
                if kind == "dot_groups":
                    active_dot_group_count += 1
                active_legacy.append(
                    {
                        "kind": kind,
                        "identity": _record_identity(
                            record,
                            "actions" if kind == "actions" else "candidate_workflows"
                            if kind == "workflows" else "evidence",
                        ),
                    }
                )
    stages = [
        {
            "stage": "extract",
            "status": "ready",
            "operation": "extract legacy material into evidence/donor references",
            "legacy_is_candidate_input": False,
            "mutates_live_state": False,
        },
        {
            "stage": "rebuild",
            "status": "ready",
            "operation": "rebuild Source -> extraction -> Dot -> Workflow -> Action candidates",
            "one_to_one_legacy_conversion": False,
            "mutates_live_state": False,
        },
        {
            "stage": "test",
            "status": "ready",
            "operation": (
                "validate contracts, placement, Blueprint Mapping and evidence preservation"
            ),
            "candidate_digest": candidate_digest,
            "mutates_live_state": False,
        },
        {
            "stage": "switch",
            "status": "blocked-until-future-version",
            "operation": "switch active routing only through a future authorised /version",
            "authorised": False,
            "mutates_live_state": False,
        },
        {
            "stage": "remove",
            "status": "blocked-until-future-version",
            "operation": (
                "remove old surface only after the active fallback has switched and recovered"
            ),
            "authorised": False,
            "mutates_live_state": False,
        },
    ]
    return {
        "order": list(CUTOVER_STAGES),
        "stages": stages,
        "steps": _copy(stages, "cutover stages"),
        "extract_rebuild_test_switch_remove": list(CUTOVER_STAGES),
        "fallback": {
            "active_legacy_records": active_legacy,
            "active_workflow_count": active_workflow_count,
            "active_dot_group_count": active_dot_group_count,
            "commands_retained": len(_legacy_command_records(legacy_inventory)),
            "retained_until": "explicit-future-/version",
            "fallback_retained": True,
            "recovery_available": True,
            "remove_authorised": False,
        },
        "blueprint_mapping": _copy(blueprint, "Blueprint Mapping"),
        "commands_retained": len(_legacy_command_records(legacy_inventory)),
        "safe_to_cutover": False,
        "requires_future_version": True,
        "no_live_writes": True,
        "no_live_deletes": True,
        "no_activation": True,
        "mutations": [],
        "rehearsal_only": True,
    }


def audit_capability_migration(
    candidate_graph: Mapping[str, Any] | None = None,
    legacy_inventory: Mapping[str, Any] | None = None,
    *,
    legacy_fixture: Mapping[str, Any] | None = None,
    blueprint_mapping: Any = None,
) -> dict[str, Any]:
    """Build a deterministic candidate old-architecture removal audit.

    The two inputs are deliberately separate.  ``legacy_inventory`` is copied
    for history and fallback audit only; it is never passed to a canonical
    validator and never enters the candidate digest.  The function performs no
    filesystem, Workplace, runtime, adapter, activation, or ``/version``
    operation.
    """

    if candidate_graph is None:
        candidate_graph = {}
    if not isinstance(candidate_graph, Mapping):
        raise MigrationInputError("candidate_graph must be an object")
    if legacy_inventory is not None and legacy_fixture is not None:
        raise MigrationInputError("legacy_inventory and legacy_fixture are aliases; supply one")
    legacy = legacy_inventory if legacy_inventory is not None else legacy_fixture
    if legacy is None:
        legacy = {}
    if not isinstance(legacy, Mapping):
        raise MigrationInputError("legacy_inventory must be a separately labelled object")
    graph = _copy(candidate_graph, "candidate_graph")
    legacy_snapshot = _copy(legacy, "legacy_inventory")
    canonical_graph_input = _is_canonical_candidate_graph(graph)
    # A nested historical fixture would make the source boundary ambiguous.
    for key in ("legacy", "legacy_fixture", "legacy_inventory", "legacy_actions", "old_actions"):
        if key in graph:
            raise MigrationInputError(
                "Legacy inventory must be supplied separately and cannot seed a candidate graph"
            )
    _walk_rejections(graph, allow_compiler_receipts=canonical_graph_input)
    if canonical_graph_input:
        _validate_compiler_boundary_receipts(graph)
    sections, metadata = _candidate_graph_sections(graph)
    version_evidence = metadata.get("version_evidence", metadata.get("system_version_evidence"))
    projection_values = [metadata.pop(alias, None) for alias in _PROJECTION_ALIASES]
    supplied_projections = [value for value in projection_values if value is not None]
    if len(supplied_projections) > 1:
        digests = {_digest(value, prefix="projection-input-") for value in supplied_projections}
        if len(digests) > 1:
            raise MigrationInputError("Conflicting aliases for staged compatibility projections")
    staged_projections = _validate_staged_projections(
        supplied_projections[0] if supplied_projections else None
    )
    if staged_projections:
        metadata["staged_projections"] = staged_projections

    sources = _validate_source_records(
        sections["sources"], compact_only=canonical_graph_input
    )
    if canonical_graph_input:
        try:
            # The compiler validator is a contract gate only.  Its detached
            # return value is deliberately discarded; the exact caller graph
            # remains the candidate retained and digested below.
            validate_candidate_graph(graph)
        except (TypeError, ValueError) as error:
            raise MigrationValidationError(
                f"Invalid canonical capability-candidate-graph contract: {error}"
            ) from error
    extractions = _validate_extraction_records(sections["extractions"], sources)
    dots = _validate_dot_records(sections["candidate_dots"])
    workflows = _validate_workflow_records(sections["candidate_workflows"], dots)
    actions = _validate_action_records(sections["candidate_actions"], workflows)
    compositions = _validate_composition_records(
        sections["execution_compositions"], workflows, dots
    )
    trials = _validate_generic_records(sections["trials"], "trials")
    evidence = _validate_generic_records(sections["evidence"], "evidence")

    canonical_sections: dict[str, list[dict[str, Any]]] = {
        "sources": sources,
        "extractions": extractions,
        "candidate_dots": dots,
        "candidate_workflows": workflows,
        "candidate_actions": actions,
        "execution_compositions": compositions,
        "trials": trials,
        "evidence": evidence,
    }
    # Placement is checked against canonical values but is not copied into
    # contract records because the underlying schemas intentionally have no
    # ownership authority field.
    placement: list[dict[str, Any]] = []
    for kind in WORKPLACE_KINDS:
        for record in sections[kind]:
            placement.append(
                _placement_for(graph, kind, record, version_evidence=version_evidence)
            )
    placement.sort(key=lambda item: (item["kind"], item["identity"]))

    old_action_records = _legacy_action_records(legacy_snapshot)
    legacy_ids = {
        _text(item.get("action_id", item.get("id")))
        for item in old_action_records
        if _text(item.get("action_id", item.get("id")))
    }
    inherited_ids = sorted(
        action["action_id"] for action in actions if action.get("action_id") in legacy_ids
    )
    if inherited_ids:
        raise MigrationValidationError(
            "Candidate Actions cannot inherit legacy Action ids: " + ", ".join(inherited_ids)
        )
    legacy_workflow_records = _legacy_records(
        _legacy_section(legacy_snapshot, ("workflows", "legacy_workflows", "old_workflows")),
        "inventory",
    )
    legacy_workflow_ids = {
        _text(item.get("workflow_id", item.get("id")))
        for item in legacy_workflow_records
        if _text(item.get("workflow_id", item.get("id")))
    }
    inherited_workflows = sorted(
        workflow["workflow_id"]
        for workflow in workflows
        if workflow.get("workflow_id") in legacy_workflow_ids
    )
    if inherited_workflows:
        raise MigrationValidationError(
            "Old Workflow objects cannot be converted one-to-one into candidates: "
            + ", ".join(inherited_workflows)
        )

    canonical_graph = (
        _copy(graph, "canonical candidate graph")
        if canonical_graph_input
        else _canonical_candidate_graph(canonical_sections, metadata)
    )
    # Candidate identity and induction evidence are derived solely from this
    # graph.  In particular, the legacy snapshot and audit are intentionally
    # absent from the hashed payload.
    candidate_digest = _digest(canonical_graph, prefix="candidate-migration-")
    blueprint = _validate_blueprint_mapping(
        blueprint_mapping if blueprint_mapping is not None else metadata.get("blueprint_mapping")
    )
    action_audit = _legacy_action_audit(legacy_snapshot, actions)
    command_audit = _legacy_command_audit(legacy_snapshot)
    preservation = _preservation_report(
        sources, extractions, metadata, legacy_snapshot
    )
    cutover = _cutover_plan(
        legacy_inventory=legacy_snapshot,
        candidate_digest=candidate_digest,
        blueprint=blueprint,
    )
    return {
        "record_type": MIGRATION_RECORD_TYPE,
        "record_version": MIGRATION_RECORD_VERSION,
        "method": MIGRATION_METHOD,
        "method_version": MIGRATION_METHOD_VERSION,
        "candidate": {
            "graph": canonical_graph,
            "candidate_graph": _copy(canonical_graph, "candidate graph"),
            "input_digest": candidate_digest,
            "induction_input_digest": candidate_digest,
            "legacy_excluded": True,
            "anti_seed_attestation": {
                "attested": True,
                "independent": True,
                "no_legacy_seed": True,
                "no_inherited_action_ids": True,
                "no_one_to_one_workflow_conversion": True,
                "basis": "canonical candidate graph only",
            },
        },
        "candidate_graph": _copy(canonical_graph, "candidate graph"),
        "candidate_digest": candidate_digest,
        "input_digest": candidate_digest,
        "anti_seed_attestation": {
            "attested": True,
            "independent": True,
            "no_legacy_seed": True,
            "no_inherited_action_ids": True,
            "no_one_to_one_workflow_conversion": True,
        },
        "legacy": {
            "inventory": legacy_snapshot,
            "excluded_from_candidate": True,
            "fallback_recovery_only": True,
            "inventory_digest": _digest(legacy_snapshot, prefix="legacy-inventory-"),
            "action_audit": action_audit,
            "legacy_actions": action_audit,
            "command_controls": command_audit,
            "commands": command_audit,
        },
        "legacy_removal_audit": action_audit,
        "removal_audit": _copy(action_audit, "legacy Action audit"),
        "command_controls": _copy(command_audit, "legacy Command audit"),
        "legacy_commands": _copy(command_audit, "legacy Command audit"),
        "placement": {
            "valid": True,
            "items": placement,
            "by_kind": {
                kind: (
                    owners[0] if len(owners) == 1 else "mixed"
                )
                for kind in WORKPLACE_KINDS
                if (owners := sorted({item["owner"] for item in placement if item["kind"] == kind}))
            },
            "owners_by_kind": {
                kind: sorted({item["owner"] for item in placement if item["kind"] == kind})
                for kind in WORKPLACE_KINDS
                if any(item["kind"] == kind for item in placement)
            },
            "default_owner_by_kind": {kind: "workplace" for kind in WORKPLACE_KINDS},
            "workplace_owned": list(WORKPLACE_KINDS),
            "system_owned_active_only": sorted(SYSTEM_ACTIVE_KINDS),
        },
        "blueprint_mapping": blueprint,
        "preservation": preservation,
        "provenance_licence_history": _copy(preservation, "preservation"),
        "cutover": cutover,
        "cutover_plan": _copy(cutover, "cutover plan"),
        "rehearsal": {
            "pure": True,
            "copy_only": True,
            "mappings_only": True,
            "writes": False,
            "deletes": False,
            "live_mutation": False,
            "workplace_writes": False,
            "activation": False,
            "version_command": False,
            "performed_operations": [],
        },
    }


def rehearse_capability_migration(
    candidate_graph: Mapping[str, Any] | None = None,
    legacy_inventory: Mapping[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Alias describing the copy-only nature of :func:`audit_capability_migration`."""

    return audit_capability_migration(candidate_graph, legacy_inventory, **options)


def plan_capability_migration(
    candidate_graph: Mapping[str, Any] | None = None,
    legacy_inventory: Mapping[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Return the deterministic staged cutover plan without applying it."""

    return audit_capability_migration(candidate_graph, legacy_inventory, **options)


def validate_capability_migration(
    candidate_graph: Mapping[str, Any] | None = None,
    legacy_inventory: Mapping[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Validate and return the complete migration audit receipt."""

    return audit_capability_migration(candidate_graph, legacy_inventory, **options)


def simulate_authorised_version_admission(
    candidate_graph: Mapping[str, Any],
    version_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Model the exact System/Workplace admission boundary without mutating it.

    This is acceptance-test machinery, not an activation route.  It proves
    which candidate records an exact future authorised ``/version`` *would*
    admit to System ownership while Source/extraction evidence and unselected
    candidates remain Workplace-owned.
    """

    graph = validate_candidate_graph(candidate_graph)
    receipt = _copy(version_receipt, "version_receipt")
    if receipt.get("record_type") != "simulated-authorised-version-receipt":
        raise MigrationValidationError("version admission requires an explicitly simulated receipt")
    if receipt.get("authorised") is not True or receipt.get("primary_user") is not True:
        raise MigrationValidationError(
            "version admission simulation requires primary-user authority"
        )
    if receipt.get("candidate_input_digest") != graph.get("input_digest"):
        raise MigrationValidationError(
            "version admission receipt targets a different candidate graph"
        )
    system_version = _text(receipt.get("system_version"))
    if not system_version:
        raise MigrationValidationError("version admission receipt requires a System Version")

    workflow_ids = {
        ref["workflow_id"]
        for action in graph["actions"]
        for ref in action.get("workflow_refs", [])
    }
    selected_workflows = [
        workflow for workflow in graph["workflows"] if workflow["workflow_id"] in workflow_ids
    ]
    dot_ids = {
        ref["dot_id"]
        for workflow in selected_workflows
        for ref in workflow.get("dot_refs", [])
    }
    selected_dots = [dot for dot in graph["dots"] if dot["dot_id"] in dot_ids]
    unselected_dots = [dot for dot in graph["dots"] if dot["dot_id"] not in dot_ids]
    return {
        "record_type": "capability-version-admission-simulation",
        "record_version": 1,
        "system_version": system_version,
        "candidate_input_digest": graph["input_digest"],
        "authority": {
            "simulated": True,
            "primary_user": True,
            "authorised": True,
            "consumed": False,
        },
        "system_admission": {
            "actions": [
                {"action_id": item["action_id"], "version": item["version"]}
                for item in graph["actions"]
            ],
            "workflows": [
                {"workflow_id": item["workflow_id"], "version": item["version"]}
                for item in selected_workflows
            ],
            "dots": [
                {"dot_id": item["dot_id"], "version": item["version"]}
                for item in selected_dots
            ],
            "owner": "system",
            "simulated_state": "would-enter-system-through-authorised-version",
        },
        "workplace_retention": {
            "source_ref_count": len(graph["source_refs"]),
            "extraction_evidence_count": len(graph["extraction_evidence"]),
            "unselected_candidate_dots": len(unselected_dots),
            "owner": "workplace",
        },
        "performed": False,
        "writes": [],
        "activation": False,
        "publication": False,
    }


# Alternate spellings used by callers that name the old architecture directly.
audit_old_architecture_migration = audit_capability_migration
rehearse_old_architecture_migration = rehearse_capability_migration
plan_old_architecture_cutover = plan_capability_migration
validate_migration_candidate = validate_capability_migration
build_migration_audit = audit_capability_migration
build_migration_plan = plan_capability_migration
audit_migration = audit_capability_migration
rehearse_migration = rehearse_capability_migration
build_cutover_plan = plan_capability_migration


__all__ = [
    "CUTOVER_STAGES",
    "MIGRATION_METHOD",
    "MIGRATION_METHOD_VERSION",
    "MIGRATION_RECORD_TYPE",
    "MIGRATION_RECORD_VERSION",
    "CapabilityArchitectureMigrationError",
    "CapabilityMigrationError",
    "CapabilityMigrationValidationError",
    "MigrationAuditError",
    "MigrationInputError",
    "MigrationPlacementError",
    "MigrationValidationError",
    "PlacementValidationError",
    "audit_capability_migration",
    "audit_migration",
    "audit_old_architecture_migration",
    "build_cutover_plan",
    "build_migration_audit",
    "build_migration_plan",
    "plan_capability_migration",
    "plan_old_architecture_cutover",
    "rehearse_capability_migration",
    "rehearse_migration",
    "rehearse_old_architecture_migration",
    "simulate_authorised_version_admission",
    "validate_capability_migration",
    "validate_migration_candidate",
]
