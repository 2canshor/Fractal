# ruff: noqa: E501

"""Deterministic bottom-up induction of canonical Candidate Actions.

Action induction is the last step of the genesis compiler.  This module only
accepts canonical Candidate Workflows and two kinds of human-facing evidence:
the mental model/language used to describe an intent and Naming System
proposals.  It deliberately does not inspect a Source, Dot, provider, Skill,
old Action, user surface, Workplace, or runtime state.

The implementation is pure.  Every identifier, digest, ordering decision and
compression record is derived from the supplied Candidate Workflows and human
evidence.  A legacy fixture can be supplied to :func:`induce_candidate_actions`
only to produce an excluded removal audit; it is never part of induction
inputs, names, identifiers, or digests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fractal.capability_action import (
    ActionValidationError,
    build_action,
    validate_action,
    validate_action_graph,
)
from fractal.capability_workflow import WorkflowValidationError, validate_workflow

INDUCTION_RECORD_TYPE = "capability-action-induction"
INDUCTION_RECORD_VERSION = 1
INDUCTION_METHOD = "deterministic-human-intent-action-induction"
INDUCTION_METHOD_VERSION = "1.0.0"
ACTION_FIT_RECORD_TYPE = "capability-action-fit-classification"
ACTION_FIT_RECORD_VERSION = 1
_ACTION_VERB_RE = re.compile(r"[a-z]+\Z")


class ActionInductionError(ValueError):
    """Base error for invalid induction evidence or an unsafe boundary."""


class ActionInductionInputError(ActionInductionError):
    """Raised when a non-Workflow or forbidden authority input is supplied."""


class ActionInductionValidationError(ActionInductionError):
    """Raised when an induced Action cannot satisfy its canonical contract."""


# Compatibility spellings keep the boundary discoverable like the neighbouring
# capability modules without creating a second implementation.
CapabilityActionInductionError = ActionInductionError
ActionInductionInputValidationError = ActionInductionInputError
CapabilityActionInductionValidationError = ActionInductionValidationError

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_SPACE_RE = re.compile(r"\s+")

# These are authority-bearing keys, not words.  Human evidence may truthfully
# say "provider independent" in a sentence, but it cannot carry a provider
# object or an old Action reference into the canonical Action.
_FORBIDDEN_KEYS = frozenset(
    {
        "source",
        "source_id",
        "source_ref",
        "source_refs",
        "source_reference",
        "source_references",
        "sources",
        "dot",
        "dot_id",
        "dot_ref",
        "dot_refs",
        "dot_ids",
        "dots",
        "dot_group",
        "dot_group_id",
        "dot_group_ref",
        "dot_group_refs",
        "provider",
        "provider_id",
        "provider_ref",
        "provider_refs",
        "provider_selection",
        "provider_implementation",
        "provider_implementations",
        "implementation",
        "implementation_id",
        "implementation_ref",
        "skill",
        "skill_id",
        "skill_ref",
        "skill_name",
        "platform_skill",
        "platform_skill_id",
        "platform_skill_ref",
        "action",
        "action_id",
        "action_ref",
        "action_refs",
        "actions",
        "legacy_action",
        "legacy_action_id",
        "legacy_actions",
        "legacy_workflow",
        "legacy_workflows",
        "old_action",
        "old_actions",
        "old_workflow",
        "old_workflows",
        "preserve_old",
        "preserve_old_action",
        "preserve_old_logic",
        "user_surface",
        "user_surface_action",
        "dot_group_taxonomy",
        "legacy_taxonomy",
    }
)

_WORKFLOW_TARGET_KEYS = frozenset(
    {
        "workflow_id",
        "workflow_ids",
        "workflow_ref",
        "workflow_refs",
        "workflow_version",
        "workflow_versions",
    }
)

_FORBIDDEN_RECORD_TYPES = frozenset(
    {
        "source",
        "capability-source",
        "capability_source",
        "dot",
        "capability-dot",
        "capability_dot",
        "provider",
        "provider-record",
        "provider_record",
        "skill",
        "platform-skill",
        "platform_skill",
        "action",
        "capability-action",
        "capability_action",
        "legacy-action",
        "legacy_action",
        "workflow",
        "legacy-workflow",
        "legacy_workflow",
        "dot-group",
        "dot_group",
        "legacy-dot-group",
        "legacy_dot_group",
    }
)

_WORKFLOW_SYNTHESIS_TYPES = frozenset(
    {
        "capability_workflow_synthesis",
        "workflow_synthesis",
        "candidate_workflow_synthesis",
    }
)

_ALLOWED_WORKFLOW_WRAPPERS = frozenset(
    {
        "candidate_workflows",
        "candidate_workflow",
        "workflows",
        "workflow_records",
        "records",
        "candidates",
    }
)

# This list is intentionally conservative.  A provider-specific Action is
# permitted only when human evidence explicitly says the provider itself is
# the intrinsic human outcome; ordinary workflow implementation details may
# never become a surface name.
_COMMON_PROVIDER_WORDS = frozenset(
    {
        "airtable",
        "anthropic",
        "apple",
        "asana",
        "aws",
        "azure",
        "canva",
        "chatgpt",
        "codex",
        "discord",
        "dropbox",
        "figma",
        "github",
        "gitlab",
        "google",
        "jira",
        "linear",
        "microsoft",
        "mongodb",
        "mysql",
        "notion",
        "openai",
        "postgres",
        "postgresql",
        "playwright",
        "selenium",
        "slack",
        "spotify",
        "supabase",
        "telegram",
        "todoist",
        "trello",
        "twilio",
        "whatsapp",
    }
)
_PROVIDER_MARKERS = frozenset(
    {
        "provider",
        "vendor",
        "platform",
        "skill",
        "source",
        "implementation",
        "connector",
        "integration",
    }
)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ActionInductionInputError("Induction evidence must be portable JSON") from error


def _digest(value: Any, *, prefix: str, length: int = 32) -> str:
    return f"{prefix}{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()[:length]}"


def _text(value: Any) -> str:
    return _SPACE_RE.sub(" ", value.strip()) if isinstance(value, str) else ""


def _key(value: Any) -> str:
    return _text(value).casefold().replace("-", "_").replace(" ", "_")


def _normalised_phrase(value: Any) -> str:
    text = _text(value).casefold()
    text = re.sub(r"[.!?]+$", "", text).strip()
    return text


def _slug(value: Any, *, prefix: str = "intent") -> str:
    text = _text(value).casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        text = _digest(value, prefix=f"{prefix}-", length=20).split("-", 1)[-1]
    text = text[:110].strip("-")
    if not text:
        text = f"{prefix}-candidate"
    if not re.match(r"^[a-z0-9]", text):
        text = f"{prefix}-{text}"
    return text[:128]


def _nonblank(value: Any, label: str) -> str:
    text = _text(value)
    if not text:
        raise ActionInductionInputError(f"{label} must be a non-blank string")
    return text


def _action_verb(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    if _ACTION_VERB_RE.fullmatch(text) is None:
        raise ActionInductionInputError(f"{label} must be one lowercase English one-word verb")
    return text


def _naming_provenance(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ActionInductionInputError(f"{label} must be an object")
    provenance = _safe_copy(dict(value))
    if set(provenance) != {"component_id", "version", "evidence_ids"}:
        raise ActionInductionInputError(
            f"{label} must contain component_id, version, and evidence_ids"
        )
    if provenance.get("component_id") != "naming-system":
        raise ActionInductionInputError(f"{label} must identify the maintained naming-system")
    version = _nonblank(provenance.get("version"), f"{label}.version")
    if (
        re.fullmatch(
            r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?",
            version,
        )
        is None
    ):
        raise ActionInductionInputError(f"{label}.version must be a semantic version")
    evidence_ids = _evidence_ids(provenance.get("evidence_ids"))
    if not evidence_ids:
        raise ActionInductionInputError(f"{label}.evidence_ids must not be empty")
    return {
        "component_id": "naming-system",
        "version": version,
        "evidence_ids": evidence_ids,
    }


def _evidence_ids(value: Any, *, fallback: Any = None) -> list[str]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = list(value)
    else:
        values = [value]
    result: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("evidence_id", item.get("id", item.get("reference")))
        text = _text(item)
        if text:
            result.add(text)
    if not result and fallback is not None:
        result.add(_digest(fallback, prefix="intent-evidence-", length=20))
    return sorted(result, key=lambda item: (item.casefold(), item))


def _safe_copy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except (TypeError, ValueError) as error:
        raise ActionInductionInputError("Induction input must be copyable JSON data") from error


def _order_independent(value: Any) -> Any:
    """Canonicalise human evidence where list order carries no meaning."""

    if isinstance(value, Mapping):
        return {
            key: _order_independent(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        children = [_order_independent(child) for child in value]
        return sorted(children, key=_canonical)
    return _safe_copy(value)


def _reject_forbidden_keys(
    value: Any,
    *,
    path: str = "$",
    allow_workflow_targets: bool = False,
) -> None:
    """Reject lower-layer identity and old-surface keys in evidence.

    Workflow records are validated by the canonical Workflow contract and are
    not passed through this generic traversal because their ``dot_refs`` and
    intrinsically provider-specific outcome fields are part of that contract.
    Human and naming evidence have no such exception.
    """

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _key(raw_key)
            if allow_workflow_targets and key in _WORKFLOW_TARGET_KEYS:
                _reject_forbidden_keys(child, path=f"{path}.{raw_key}", allow_workflow_targets=True)
                continue
            if key in _FORBIDDEN_KEYS:
                raise ActionInductionInputError(
                    f"Source/Dot/provider/Skill/old Action identity is not allowed: {raw_key}"
                )
            # Provider-independent and intrinsic-provider flags are human
            # evidence, not provider selection.  All other provider_* keys
            # remain forbidden.
            if key.startswith("provider_") and key not in {
                "provider_independent",
                "provider_intrinsic",
                "provider_intrinsic_outcome",
                "intrinsically_provider_specific",
            }:
                raise ActionInductionInputError(
                    f"Provider selection/implementation is not allowed: {raw_key}"
                )
            _reject_forbidden_keys(
                child,
                path=f"{path}.{raw_key}",
                allow_workflow_targets=allow_workflow_targets,
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(
                child,
                path=f"{path}[{index}]",
                allow_workflow_targets=allow_workflow_targets,
            )


def _record_state(record: Mapping[str, Any]) -> str | None:
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        state = lifecycle.get("status", lifecycle.get("state"))
        if isinstance(state, str):
            return state
    state = record.get("status", record.get("state"))
    return state if isinstance(state, str) else None


def _workflow_identity(value: Mapping[str, Any]) -> tuple[str, str]:
    workflow_id = value.get("workflow_id")
    version = value.get("version")
    if not isinstance(workflow_id, str) or not _ID_RE.fullmatch(workflow_id):
        raise ActionInductionInputError("Candidate Workflow workflow_id is invalid")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ActionInductionInputError("Candidate Workflow version is invalid")
    return workflow_id, version


def _identity_text(identity: tuple[str, str]) -> str:
    return f"{identity[0]}@{identity[1]}"


def _flatten_workflow_records(value: Any) -> list[Mapping[str, Any]]:
    """Expand only candidate-workflow containers, rejecting all other records."""

    if value is None:
        raise ActionInductionInputError("Candidate Workflows are required")
    if isinstance(value, Mapping):
        record_type = _key(value.get("record_type"))
        if record_type:
            if record_type in _FORBIDDEN_RECORD_TYPES:
                raise ActionInductionInputError(
                    "Action induction accepts Candidate Workflows only; "
                    f"record type {value.get('record_type')!r} is forbidden"
                )
            if record_type not in {"capability_workflow", "candidate_workflow"}:
                if record_type in _WORKFLOW_SYNTHESIS_TYPES:
                    return _flatten_workflow_records(
                        value.get("candidate_workflows", value.get("candidates", []))
                    )
                raise ActionInductionInputError(
                    f"Unsupported Action induction record type: {value.get('record_type')!r}"
                )
            return [value]

        found: list[Mapping[str, Any]] = []
        for key, child in value.items():
            normal = _key(key)
            if normal in _ALLOWED_WORKFLOW_WRAPPERS:
                found.extend(_flatten_workflow_records(child))
                continue
            if normal in {
                "rejected",
                "conflicts",
                "diagnostics",
                "evidence",
                "method",
                "method_version",
            }:
                continue
            # An id-indexed collection is accepted only when every child is a
            # canonical Workflow record.  It cannot silently turn arbitrary
            # evidence or old records into a Workflow.
            if isinstance(child, Mapping) and (
                child.get("record_type") is not None or child.get("workflow_id") is not None
            ):
                found.extend(_flatten_workflow_records(child))
                continue
            raise ActionInductionInputError(
                f"Unsupported Candidate Workflow container field: {key!r}"
            )
        return found
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[Mapping[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise ActionInductionInputError(f"candidate_workflows[{index}] must be an object")
            result.extend(_flatten_workflow_records(item))
        return result
    raise ActionInductionInputError("Candidate Workflows must be an object or ordered list")


def _validate_candidate_workflows(value: Any) -> list[dict[str, Any]]:
    records = _flatten_workflow_records(value)
    if not records:
        raise ActionInductionInputError("At least one Candidate Workflow is required")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in records:
        try:
            validated = validate_workflow(raw)
        except (WorkflowValidationError, ValueError) as error:
            raise ActionInductionInputError(
                f"Action induction requires canonical Candidate Workflows: {error}"
            ) from error
        if _record_state(validated) != "candidate":
            raise ActionInductionInputError(
                f"Workflow {_identity_text(_workflow_identity(validated))} is not a Candidate Workflow"
            )
        identity = _workflow_identity(validated)
        if identity in result and _canonical(result[identity]) != _canonical(validated):
            raise ActionInductionInputError(
                f"Duplicate Candidate Workflow identity with different content: {_identity_text(identity)}"
            )
        result[identity] = _safe_copy(validated)
    return [
        result[key]
        for key in sorted(result, key=lambda item: (item[0].casefold(), item[0], item[1]))
    ]


def _workflow_map(records: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {_workflow_identity(item): dict(item) for item in records}


def _coerce_target(
    value: Any, workflows: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[tuple[str, str]]:
    """Resolve human-evidence Workflow targets without accepting old records."""

    if isinstance(value, Mapping):
        workflow_id = value.get("workflow_id")
        version = value.get("version", value.get("workflow_version"))
        if workflow_id is None:
            for key in ("ref", "workflow_ref"):
                if key in value:
                    return _coerce_target(value[key], workflows)
        if workflow_id is None:
            return []
        workflow_id = _text(workflow_id)
        if version is None:
            matches = [identity for identity in workflows if identity[0] == workflow_id]
            if len(matches) == 1:
                return matches
            raise ActionInductionInputError(
                f"Human intent evidence target {workflow_id!r} requires an exact Workflow version"
            )
        identity = (workflow_id, _text(version))
        if identity not in workflows:
            raise ActionInductionInputError(
                f"Human intent evidence names a missing Candidate Workflow: {_identity_text(identity)}"
            )
        return [identity]
    if isinstance(value, str):
        text = _text(value)
        if "@" in text:
            workflow_id, version = text.rsplit("@", 1)
            identity = (workflow_id, version)
            if identity not in workflows:
                raise ActionInductionInputError(
                    f"Human intent evidence names a missing Candidate Workflow: {_identity_text(identity)}"
                )
            return [identity]
        matches = [identity for identity in workflows if identity[0] == text]
        if len(matches) == 1:
            return matches
        raise ActionInductionInputError(
            f"Human intent evidence target {text!r} requires an exact Workflow version"
        )
    return []


def _targets_from_mapping(
    value: Mapping[str, Any], workflows: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    if "workflow_id" in value:
        targets.extend(_coerce_target(value, workflows))
    for key in ("workflow_ids", "workflow_refs", "workflow_ref"):
        if key not in value:
            continue
        raw = value[key]
        if isinstance(raw, (str, Mapping)):
            raw_values = [raw]
        elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
            raw_values = list(raw)
        else:
            raise ActionInductionInputError(f"human_intent_evidence.{key} must be a list or ref")
        for item in raw_values:
            targets.extend(_coerce_target(item, workflows))
    return sorted(set(targets), key=lambda item: (item[0].casefold(), item[0], item[1]))


def _intent_fields(value: Mapping[str, Any]) -> bool:
    keys = {_key(item) for item in value}
    return bool(
        keys
        & {
            "stable_id",
            "stable_intent",
            "stable_human_intent",
            "intent_key",
            "intent_id",
            "familiar",
            "familiar_name",
            "human_intent",
            "statement",
            "intent",
            "meaning",
            "human_language",
            "mental_model",
        }
    )


def _extract_intent_text(value: Mapping[str, Any], *, label: str) -> tuple[str, str, str, bool]:
    nested = value.get("human_intent")
    source: Mapping[str, Any] = nested if isinstance(nested, Mapping) else value
    statement_raw = source.get(
        "statement",
        source.get(
            "description", source.get("meaning", source.get("human_language", source.get("intent")))
        ),
    )
    familiar_raw = source.get(
        "familiar",
        source.get("familiar_name", source.get("label", source.get("name", statement_raw))),
    )
    stable_raw = value.get(
        "stable_id",
        value.get(
            "stable_intent",
            value.get(
                "intent_key", value.get("intent_id", source.get("stable_id", source.get("stable")))
            ),
        ),
    )
    statement = _nonblank(statement_raw, f"{label}.statement")
    familiar = _nonblank(familiar_raw, f"{label}.familiar")
    explicit_stable = bool(stable_raw and not isinstance(stable_raw, bool))
    if isinstance(stable_raw, bool):
        if stable_raw is False:
            raise ActionInductionInputError(f"{label} must describe a stable human intent")
        stable_raw = None
    stable = _slug(stable_raw or familiar, prefix="intent")
    return statement, familiar, stable, explicit_stable


def _distinct_evidence(value: Mapping[str, Any]) -> Any:
    for key in (
        "distinct_intent_evidence",
        "intent_distinction",
        "distinct_intent",
        "distinct",
        "materially_distinct",
        "different_intent",
        "boundary_evidence",
    ):
        if key not in value:
            continue
        candidate = value[key]
        if isinstance(candidate, bool):
            return {"distinct": candidate, "independent": candidate}
        if candidate:
            return _safe_copy(candidate)
    return None


def _compression_evidence(value: Mapping[str, Any]) -> Any:
    for key in (
        "compression_evidence",
        "compression_decision",
        "meaning_preservation_evidence",
        "ambiguity_evidence",
        "preserve_meaning",
        "meaning_preserved",
        "same_intent",
        "equivalent_intent",
        "merge_evidence",
        "merge_with",
        "equivalent_to",
    ):
        if key in value:
            candidate = value[key]
            if candidate is True:
                return {"same_intent": True, "meaning_preserved": True}
            if candidate:
                return _safe_copy(candidate)
    return None


def _provider_intrinsic(value: Mapping[str, Any]) -> bool:
    return any(
        value.get(key) is True
        for key in (
            "intrinsically_provider_specific",
            "provider_intrinsic",
            "provider_intrinsic_outcome",
            "provider_specific_intent",
        )
    )


@dataclass
class _IntentEvidence:
    identity: tuple[str, str]
    statement: str
    familiar: str
    stable_key: str
    explicit_stable: bool
    evidence_ids: list[str]
    raw: dict[str, Any]
    distinct: Any = None
    compression: Any = None
    provider_intrinsic: bool = False


def _normalise_intent_entry(
    value: Mapping[str, Any],
    targets: Sequence[tuple[str, str]],
    *,
    index: int,
) -> list[_IntentEvidence]:
    fields = _safe_copy(dict(value))
    for key in _WORKFLOW_TARGET_KEYS:
        fields.pop(key, None)
    statement, familiar, stable, explicit_stable = _extract_intent_text(
        value, label=f"human_intent_evidence[{index}]"
    )
    evidence_ids = _evidence_ids(
        value.get("evidence_ids", value.get("evidence_refs", value.get("evidence"))),
        fallback=fields,
    )
    return [
        _IntentEvidence(
            identity=identity,
            statement=statement,
            familiar=familiar,
            stable_key=stable,
            explicit_stable=explicit_stable,
            evidence_ids=evidence_ids,
            raw=fields,
            distinct=_distinct_evidence(value),
            compression=_compression_evidence(value),
            provider_intrinsic=_provider_intrinsic(value),
        )
        for identity in targets
    ]


def _walk_intent_evidence(
    value: Any,
    workflows: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    parent_target: tuple[str, str] | None = None,
    index: list[int] | None = None,
) -> list[_IntentEvidence]:
    """Read flexible structured evidence while keeping target resolution exact."""

    counter = index if index is not None else [0]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[_IntentEvidence] = []
        for item in value:
            result.extend(
                _walk_intent_evidence(item, workflows, parent_target=parent_target, index=counter)
            )
        return result
    if not isinstance(value, Mapping):
        raise ActionInductionInputError("human_intent_evidence must be structured objects or lists")

    _reject_forbidden_keys(value, allow_workflow_targets=True)
    counter[0] += 1
    targets = _targets_from_mapping(value, workflows)
    if not targets and parent_target is not None:
        targets = [parent_target]

    # Collections commonly wrap the actual intent entries.
    for key in ("entries", "intents", "evidence", "items", "records", "human_intents"):
        child = value.get(key)
        if child is None or key in _WORKFLOW_TARGET_KEYS:
            continue
        if isinstance(child, (Mapping, Sequence)) and not isinstance(
            child, (str, bytes, bytearray)
        ):
            remainder = {
                k: v for k, v in value.items() if k != key and _key(k) not in _WORKFLOW_TARGET_KEYS
            }
            if not _intent_fields(remainder) and not targets:
                return _walk_intent_evidence(
                    child, workflows, parent_target=parent_target, index=counter
                )

    if _intent_fields(value):
        if not targets:
            # A single global human mental model is valid and applies to each
            # supplied Workflow.  It is still evidence, not a Workflow name.
            targets = list(workflows)
        return _normalise_intent_entry(value, targets, index=counter[0] - 1)

    result: list[_IntentEvidence] = []
    # Id-keyed evidence maps are accepted only when the key resolves to an
    # exact Candidate Workflow.  Other keys are wrappers, not implicit names.
    for raw_key, child in value.items():
        key = _key(raw_key)
        if key in _WORKFLOW_TARGET_KEYS or key in {
            "entries",
            "intents",
            "evidence",
            "items",
            "records",
            "human_intents",
        }:
            continue
        targets_for_key = _coerce_target(raw_key, workflows)
        if targets_for_key:
            result.extend(
                _walk_intent_evidence(
                    child,
                    workflows,
                    parent_target=targets_for_key[0],
                    index=counter,
                )
            )
        elif isinstance(child, (Mapping, Sequence)) and not isinstance(
            child, (str, bytes, bytearray)
        ):
            result.extend(
                _walk_intent_evidence(child, workflows, parent_target=parent_target, index=counter)
            )
        else:
            raise ActionInductionInputError(
                f"Unsupported human_intent_evidence field or target: {raw_key!r}"
            )
    return result


def _parse_intent_evidence(
    value: Any,
    workflows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[_IntentEvidence]:
    if value is None:
        raise ActionInductionInputError("Structured human_intent_evidence is required")
    entries = _walk_intent_evidence(value, workflows)
    if not entries:
        raise ActionInductionInputError(
            "human_intent_evidence did not describe a stable human intent"
        )
    # Exact duplicate evidence is harmless and order-independent.
    dedup: dict[tuple[tuple[str, str], str, str, str, str], _IntentEvidence] = {}
    for item in entries:
        key = (
            item.identity,
            item.stable_key,
            _normalised_phrase(item.statement),
            _normalised_phrase(item.familiar),
            _canonical(item.raw),
        )
        dedup[key] = item
    return sorted(
        dedup.values(),
        key=lambda item: (
            item.identity[0].casefold(),
            item.identity[0],
            item.identity[1],
            item.stable_key,
            item.statement.casefold(),
            item.familiar.casefold(),
        ),
    )


def _entry_merge_allowed(item: _IntentEvidence) -> bool:
    if item.compression is None:
        return item.explicit_stable
    if isinstance(item.compression, Mapping):
        flags = [
            item.compression.get(key)
            for key in (
                "same_intent",
                "equivalent",
                "meaning_preserved",
                "preserve_meaning",
                "ambiguity_resolved",
                "merge",
            )
            if key in item.compression
        ]
        return any(flag is True for flag in flags) or bool(
            item.compression.get("reason", item.compression.get("rationale"))
        )
    return bool(item.compression)


def _entry_distinct(item: _IntentEvidence) -> bool:
    if item.distinct is None:
        return False
    if isinstance(item.distinct, bool):
        return item.distinct
    if isinstance(item.distinct, Mapping):
        flags = [
            item.distinct.get(key)
            for key in ("distinct", "independent", "different_intent", "intent_difference")
            if key in item.distinct
        ]
        return not flags or any(flag is True for flag in flags)
    return True


def _merge_evidence_for_group(items: Sequence[_IntentEvidence]) -> tuple[bool, list[str]]:
    evidence: set[str] = set()
    allowed = True
    for item in items:
        if item.compression is not None:
            evidence.update(_evidence_ids(item.compression, fallback=item.compression))
        if item.explicit_stable:
            evidence.add(f"stable-intent:{item.stable_key}")
        allowed = allowed and _entry_merge_allowed(item)
    if len(items) < 2:
        allowed = True
    if not evidence:
        evidence.add("meaning-preservation:stable-human-intent")
    return allowed, sorted(evidence, key=lambda item: (item.casefold(), item))


def _compression_links(entries: Sequence[_IntentEvidence]) -> dict[str, set[str]]:
    """Return explicit same-intent links between stable keys."""

    links: dict[str, set[str]] = defaultdict(set)
    for item in entries:
        candidate = item.compression
        if not isinstance(candidate, Mapping):
            continue
        values: list[Any] = []
        for key in ("same_as", "equivalent_to", "merge_with", "intent_keys", "stable_intents"):
            raw = candidate.get(key)
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                values.extend(raw)
            elif raw is not None:
                values.append(raw)
        if candidate.get("same_intent") is True or candidate.get("equivalent") is True:
            for value in values:
                target = _slug(value, prefix="intent")
                links[item.stable_key].add(target)
                links[target].add(item.stable_key)
    return links


def _union_find(values: Sequence[str], links: Mapping[str, set[str]]) -> dict[str, str]:
    parent = {value: value for value in values}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        if left not in parent or right not in parent:
            return
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left, rights in links.items():
        for right in rights:
            union(left, right)
    return {value: find(value) for value in values}


def _workflow_port_items(
    workflows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    evidence: Sequence[_IntentEvidence],
) -> list[Any]:
    for item in evidence:
        raw = item.raw.get(field)
        if raw is not None:
            return _safe_copy(
                raw if isinstance(raw, Sequence) and not isinstance(raw, str) else [raw]
            )
    values: dict[str, Any] = {}
    for workflow in workflows:
        raw = workflow.get(field, [])
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            continue
        for port in raw:
            if isinstance(port, Mapping):
                port_id = port.get("id", port.get("name"))
                if isinstance(port_id, str):
                    values.setdefault(port_id, _safe_copy(port))
            elif isinstance(port, str):
                values.setdefault(port, port)
    if values:
        return [values[key] for key in sorted(values, key=lambda item: (item.casefold(), item))]
    return []


def _contract_items(
    value: Any, *, label: str, fallback: Sequence[Any], default_id: str
) -> list[Any]:
    raw = value if value is not None else fallback
    values = (
        list(raw)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray))
        else [raw]
    )
    values = [item for item in values if item is not None]
    if not values:
        values = [default_id]
    result: list[Any] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            child = _safe_copy(dict(item))
            child.pop("workflow_id", None)
            child.pop("version", None)
            item_id = child.get(
                "id", child.get("name", child.get("input_id", child.get("output_id")))
            )
            if not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id):
                item_id = _slug(item_id or default_id, prefix=default_id)
            child["id"] = item_id
            child.setdefault("type", "value")
            child.setdefault("required", True)
            key = item_id
            if key in seen:
                continue
            seen.add(key)
            result.append(child)
        else:
            item_id = item if isinstance(item, str) else _slug(item, prefix=default_id)
            if not _ID_RE.fullmatch(item_id):
                item_id = _slug(item_id, prefix=default_id)
            if item_id in seen:
                continue
            seen.add(item_id)
            result.append(item_id)
    if not result:
        raise ActionInductionValidationError(f"{label} must have at least one contract item")
    return result


def _name_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _provider_name_tokens(
    name: str,
    workflows: Sequence[Mapping[str, Any]],
) -> set[str]:
    tokens = _name_tokens(name)
    found = tokens & (_COMMON_PROVIDER_WORDS | _PROVIDER_MARKERS)
    for workflow in workflows:
        provider = workflow.get("provider_specific")
        if isinstance(provider, Mapping):
            provider_id = provider.get("provider_id")
            if isinstance(provider_id, str):
                found.update(tokens & _name_tokens(provider_id))
    return found


def _naming_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    _reject_forbidden_keys(value, allow_workflow_targets=True)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise ActionInductionInputError(
                    "Naming System proposals must be structured objects"
                )
            result.extend(_naming_records(item))
        return result
    if not isinstance(value, Mapping):
        raise ActionInductionInputError("Naming System proposals must be an object or list")

    # A top-level {proposals, rationale, alternatives} record is expanded into
    # one record per proposal while retaining the shared rationale.
    if "proposals" in value:
        proposals = value["proposals"]
        shared_rationale = value.get("rationale", value.get("naming_rationale"))
        has_alternatives = "alternatives" in value or "naming_alternatives" in value
        shared_alternatives = value.get("alternatives", value.get("naming_alternatives"))
        shared_metadata = {
            key: _safe_copy(child)
            for key, child in value.items()
            if key
            not in {
                "proposals",
                "rationale",
                "alternatives",
                "naming_rationale",
                "naming_alternatives",
            }
        }
        if isinstance(proposals, Mapping):
            result: list[dict[str, Any]] = []
            for key, proposal in proposals.items():
                child = proposal if isinstance(proposal, Mapping) else {"proposal": proposal}
                child = {**shared_metadata, "intent_key": key, **dict(child)}
                rationale = (
                    shared_rationale.get(key)
                    if isinstance(shared_rationale, Mapping)
                    else shared_rationale
                )
                alternatives = (
                    shared_alternatives.get(key)
                    if isinstance(shared_alternatives, Mapping)
                    else shared_alternatives
                )
                child.setdefault("rationale", rationale)
                if has_alternatives:
                    child.setdefault("alternatives", alternatives)
                result.extend(_naming_records(child))
            return result
        if (
            isinstance(proposals, Sequence)
            and not isinstance(proposals, (str, bytes, bytearray))
            and all(isinstance(item, Mapping) for item in proposals)
        ):
            result = []
            for proposal in proposals:
                child = {**shared_metadata, **dict(proposal)}
                if shared_rationale is not None:
                    child.setdefault("rationale", shared_rationale)
                if has_alternatives:
                    child.setdefault("alternatives", shared_alternatives)
                result.extend(_naming_records(child))
            return result
        values = (
            [proposals]
            if isinstance(proposals, str)
            else list(proposals)
            if isinstance(proposals, Sequence)
            else [proposals]
        )
        return [
            {
                "proposal": proposal,
                "rationale": shared_rationale,
                **({"alternatives": shared_alternatives} if has_alternatives else {}),
                **shared_metadata,
            }
            for proposal in values
        ]

    # Direct keyed proposal map: {review: ["Review", ...], teach: ...}.
    if not any(
        key in value
        for key in (
            "name",
            "proposal",
            "proposed_name",
            "human_name",
            "label",
            "intent_key",
            "stable_intent",
            "workflow_id",
            "workflow_ref",
            "rationale",
            "alternatives",
        )
    ):
        result = []
        for key, proposal in value.items():
            child = proposal if isinstance(proposal, Mapping) else {"proposal": proposal}
            child = {"intent_key": key, **dict(child)}
            result.extend(_naming_records(child))
        return result
    return [_safe_copy(dict(value))]


def _naming_for_group(
    value: Any,
    *,
    stable_keys: Sequence[str],
    familiar: str,
    workflows: Sequence[Mapping[str, Any]],
    intent_entries: Sequence[_IntentEvidence],
) -> tuple[str, str, list[Any], dict[str, Any]]:
    records = _naming_records(value)
    stable_set = set(stable_keys)
    records_by_stable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        keys = {
            _slug(record[key], prefix="intent")
            for key in ("intent_key", "stable_intent", "stable_id", "intent_id")
            if record.get(key) is not None
        }
        for key in keys & stable_set:
            records_by_stable[key].append(record)
    missing = sorted(stable_set - set(records_by_stable))
    if missing:
        raise ActionInductionInputError(
            "Naming System proposals must target every exact stable intent: " + ", ".join(missing)
        )

    def record_proposals(record: Mapping[str, Any]) -> list[str]:
        values: list[Any] = []
        for key in (
            "proposal",
            "proposals",
            "proposed_name",
            "name",
            "human_name",
            "label",
            "familiar",
        ):
            if key in record:
                candidate = record[key]
                values.extend(
                    [candidate]
                    if isinstance(candidate, str)
                    else list(candidate)
                    if isinstance(candidate, Sequence)
                    and not isinstance(candidate, (str, bytes, bytearray))
                    else []
                )
        return sorted(
            {_text(candidate) for candidate in values if _text(candidate)},
            key=lambda item: (item.casefold(), item),
        )

    proposals_by_stable: dict[str, list[str]] = {}
    selected_records: list[dict[str, Any]] = []
    rationale_values: list[str] = []
    alternatives_values: list[Any] = []
    naming_provenance_values: list[dict[str, Any]] = []
    for stable in sorted(stable_set):
        target_records = records_by_stable[stable]
        target_proposals: set[str] = set()
        target_rationale: list[str] = []
        target_alternatives: list[Any] = []
        for record in target_records:
            if record.get("language") != "en" or record.get("part_of_speech") != "verb":
                raise ActionInductionInputError(
                    f"Naming System must classify intent {stable} as an English verb"
                )
            provenance = _naming_provenance(
                record.get("provenance"),
                label=f"Naming System provenance for intent {stable}",
            )
            naming_provenance_values.append(provenance)
            target_values = [
                _action_verb(candidate, f"Naming System proposal for intent {stable}")
                for candidate in record_proposals(record)
            ]
            if target_values:
                target_proposals.update(target_values)
                selected_records.append(record)
            rationale_value = _text(record.get("rationale"))
            if rationale_value:
                target_rationale.append(rationale_value)
            if "alternatives" not in record:
                raise ActionInductionInputError(
                    f"Naming System alternatives must be an explicit list for intent {stable}"
                )
            alternatives = record["alternatives"]
            if isinstance(alternatives, (str, bytes, bytearray)) or not isinstance(
                alternatives, Sequence
            ):
                raise ActionInductionInputError(
                    f"Naming System alternatives must be an explicit list for intent {stable}"
                )
            target_alternatives.extend(
                _action_verb(
                    alternative,
                    f"Naming System alternative for intent {stable}",
                )
                for alternative in alternatives
            )
        if not target_proposals:
            raise ActionInductionInputError(
                f"Naming System proposals must include a nonblank proposal for intent {stable}"
            )
        if not target_rationale:
            raise ActionInductionInputError(
                f"Naming System rationale must target exact intent {stable}"
            )
        proposals_by_stable[stable] = sorted(
            target_proposals,
            key=lambda item: (item.casefold(), item),
        )
        rationale_values.append(
            sorted(target_rationale, key=lambda item: (item.casefold(), item))[0]
        )
        alternatives_values.extend(target_alternatives)

    familiar = _action_verb(familiar, "Human familiar Action name")
    proposal = familiar
    rationale = rationale_values[0]
    alternatives = sorted(alternatives_values, key=_canonical)
    selected: dict[str, Any] = {}
    for record in selected_records:
        selected.update(record)
    proposal = _action_verb(proposal, "Naming System proposal")
    rationale = _nonblank(rationale, "Naming System rationale")
    alternatives = [
        _action_verb(item, "Naming System alternative")
        for item in alternatives
        if item not in (None, "")
    ]
    alternatives = sorted(set(alternatives))
    if proposal in alternatives:
        raise ActionInductionInputError(
            "Naming System alternatives cannot repeat the selected proposal"
        )

    provenance_identities = {
        (item["component_id"], item["version"]) for item in naming_provenance_values
    }
    if len(provenance_identities) != 1:
        raise ActionInductionInputError(
            "Compressed Action naming evidence must use one maintained Naming System version"
        )
    provenance_component, provenance_version = next(iter(provenance_identities))
    provenance = {
        "component_id": provenance_component,
        "version": provenance_version,
        "evidence_ids": sorted(
            {
                evidence_id
                for item in naming_provenance_values
                for evidence_id in item["evidence_ids"]
            }
        ),
    }

    all_proposals = {
        _normalised_phrase(candidate)
        for values in proposals_by_stable.values()
        for candidate in values
    }
    if _normalised_phrase(proposal) not in all_proposals:
        raise ActionInductionInputError(
            "Human familiar name must be one of the exact Naming System proposals"
        )
    if _normalised_phrase(familiar) not in all_proposals:
        raise ActionInductionInputError(
            "Human familiar name must be one of the exact Naming System proposals"
        )

    workflow_names = {_normalised_phrase(item.get("human_name")) for item in workflows}
    if _normalised_phrase(proposal) in workflow_names and not (
        selected.get("supports_workflow_name") is True
        or selected.get("workflow_name_supported") is True
        or selected.get("name_evidence")
        or selected
    ):
        raise ActionInductionInputError(
            "Naming System must independently support a name copied from a Workflow"
        )

    intrinsic = any(item.provider_intrinsic for item in intent_entries) or any(
        selected.get(key) is True
        for key in (
            "intrinsically_provider_specific",
            "provider_intrinsic",
            "provider_specific_intent",
        )
    )
    provider_tokens = _provider_name_tokens(proposal, workflows)
    if provider_tokens and not intrinsic:
        raise ActionInductionInputError(
            "Provider-specific naming requires explicit intrinsic human-intent evidence"
        )
    if (
        provider_tokens
        and intrinsic
        and not selected.get("intrinsic_evidence")
        and not any(item.provider_intrinsic for item in intent_entries)
    ):
        raise ActionInductionInputError(
            "Provider-specific naming requires intrinsic outcome evidence"
        )

    return (
        proposal,
        rationale,
        alternatives,
        {
            "proposal": proposal,
            "rationale": rationale,
            "alternatives": alternatives,
            "language": "en",
            "part_of_speech": "verb",
            "provenance": provenance,
            "provider_independent": not bool(provider_tokens),
            **selected,
        },
    )


def _stable_group_key(item: _IntentEvidence, *, distinct_index: int | None = None) -> str:
    if distinct_index is None:
        return item.stable_key
    return f"{item.stable_key}--distinct-{_digest({'identity': item.identity, 'index': distinct_index}, prefix='', length=12)}"


@dataclass
class _ActionGroup:
    group_key: str
    stable_keys: set[str] = field(default_factory=set)
    entries: list[_IntentEvidence] = field(default_factory=list)
    identities: set[tuple[str, str]] = field(default_factory=set)
    compression_evidence: list[str] = field(default_factory=list)


def _cluster_intents(
    entries: Sequence[_IntentEvidence],
    workflows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[_ActionGroup]:
    by_identity: dict[tuple[str, str], list[_IntentEvidence]] = defaultdict(list)
    for entry in entries:
        by_identity[entry.identity].append(entry)
    missing = set(workflows) - set(by_identity)
    if missing:
        missing_text = ", ".join(_identity_text(item) for item in sorted(missing))
        raise ActionInductionInputError(
            f"Missing human intent evidence for Candidate Workflow(s): {missing_text}"
        )

    # First, reject multiple materially different assignments unless explicit
    # distinct-intent evidence makes the shared Workflow boundary truthful.
    for identity, values in by_identity.items():
        if len(values) < 2:
            continue
        stable_values = {item.stable_key for item in values}
        if len(stable_values) > 1 and not all(_entry_distinct(item) for item in values):
            raise ActionInductionInputError(
                f"Workflow {_identity_text(identity)} has multiple intents without explicit distinct-intent evidence"
            )
        if (
            len(stable_values) == 1
            and any(_entry_distinct(item) for item in values)
            and not all(_entry_distinct(item) for item in values)
        ):
            raise ActionInductionInputError(
                f"Workflow {_identity_text(identity)} has incomplete distinct-intent evidence"
            )

    stable_values = sorted({item.stable_key for item in entries})
    links = _compression_links(entries)
    roots = _union_find(stable_values, links)
    grouped: dict[str, _ActionGroup] = {}
    # A shared Workflow with explicit distinct evidence gets one group per
    # assignment.  Otherwise exact stable human intent is compressed.
    for stable in stable_values:
        relevant = [item for item in entries if item.stable_key == stable]
        root = roots.get(stable, stable)
        distinct_items = [item for item in relevant if _entry_distinct(item)]
        if distinct_items:
            for index, item in enumerate(
                sorted(distinct_items, key=lambda x: (_canonical(x.raw), x.identity))
            ):
                key = _stable_group_key(item, distinct_index=index)
                group = grouped.setdefault(key, _ActionGroup(key))
                group.stable_keys.add(stable)
                group.entries.append(item)
                group.identities.add(item.identity)
            for item in relevant:
                if not _entry_distinct(item):
                    # A non-distinct duplicate supports the first stable group,
                    # but cannot silently join a shared distinct assignment.
                    key = _stable_group_key(item)
                    group = grouped.setdefault(key, _ActionGroup(key))
                    group.stable_keys.add(stable)
                    group.entries.append(item)
                    group.identities.add(item.identity)
            continue
        key = root
        group = grouped.setdefault(key, _ActionGroup(key))
        group.stable_keys.add(stable)
        group.entries.extend(relevant)
        group.identities.update(item.identity for item in relevant)

    # If explicit compression links union distinct stable keys, add all linked
    # entries to the root group and remove their old groups.
    for root in sorted(set(roots.values())):
        keys = {stable for stable, candidate in roots.items() if candidate == root}
        if len(keys) < 2:
            continue
        merged = _ActionGroup(root, stable_keys=set(keys))
        for key in keys:
            old = grouped.pop(key, None)
            if old:
                merged.entries.extend(old.entries)
                merged.identities.update(old.identities)
        merged.entries.sort(
            key=lambda item: (item.identity, item.statement.casefold(), item.familiar.casefold())
        )
        allowed, evidence = _merge_evidence_for_group(merged.entries)
        if not allowed:
            raise ActionInductionInputError(
                f"Compression of intents {sorted(keys)} lacks ambiguity/meaning-preservation evidence"
            )
        merged.compression_evidence = evidence
        grouped[root] = merged

    result = list(grouped.values())
    for group in result:
        if not group.compression_evidence:
            allowed, evidence = _merge_evidence_for_group(group.entries)
            if len(group.entries) > 1 and not allowed:
                raise ActionInductionInputError(
                    f"Compression of intent {sorted(group.stable_keys)} lacks ambiguity/meaning-preservation evidence"
                )
            group.compression_evidence = evidence
        group.entries.sort(
            key=lambda item: (item.identity, item.statement.casefold(), item.familiar.casefold())
        )
    return sorted(result, key=lambda item: (item.group_key.casefold(), item.group_key))


def _action_digest(base_digest: str, group: _ActionGroup) -> str:
    return _digest(
        {"input": base_digest, "group": group.group_key, "workflows": sorted(group.identities)},
        prefix="action-induction-",
    )


def _build_candidate_actions(
    groups: Sequence[_ActionGroup],
    workflows: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    human_intent_evidence: Any,
    naming_system: Any,
    base_digest: str,
) -> list[dict[str, Any]]:
    provisional: list[tuple[_ActionGroup, dict[str, Any], str, str]] = []
    used_ids: set[str] = set()
    for group in groups:
        group_workflows = [workflows[identity] for identity in sorted(group.identities)]
        entries = group.entries
        familiar_counts: dict[str, int] = defaultdict(int)
        for item in entries:
            familiar_counts[item.familiar] += 1
        familiar = sorted(
            familiar_counts,
            key=lambda item: (-familiar_counts[item], item.casefold(), item),
        )[0]
        statement = sorted(
            {item.statement for item in entries},
            key=lambda item: (item.casefold(), item),
        )[0]
        name, rationale, alternatives, naming_record_value = _naming_for_group(
            naming_system,
            stable_keys=sorted(group.stable_keys),
            familiar=familiar,
            workflows=group_workflows,
            intent_entries=entries,
        )
        action_id = _action_verb(name, "induced Action name")
        if action_id in used_ids:
            raise ActionInductionInputError(
                f"Unrelated human intents compete for Action verb {action_id!r}; "
                "Naming System must resolve the ambiguity without a suffix"
            )
        used_ids.add(action_id)
        workflow_clusters = [
            {
                "cluster_id": _slug(
                    _digest(
                        {"group": group.group_key, "workflow": identity},
                        prefix="cluster-",
                        length=20,
                    ),
                    prefix="cluster",
                ),
                "workflow_id": identity[0],
                "version": identity[1],
            }
            for identity in sorted(group.identities)
        ]
        intent_analysis = {
            "stable_intents": sorted(group.stable_keys),
            "statement": statement,
            "familiar": familiar,
            "evidence_ids": sorted(
                {evidence_id for item in entries for evidence_id in item.evidence_ids},
                key=lambda item: (item.casefold(), item),
            ),
            "workflow_evidence": [
                {
                    "sequence": sequence,
                    "evidence_ids": sorted(
                        {
                            evidence_id
                            for item in entries
                            if item.identity == identity
                            for evidence_id in item.evidence_ids
                        },
                        key=lambda item: (item.casefold(), item),
                    ),
                }
                for sequence, identity in enumerate(sorted(group.identities), start=1)
            ],
        }
        compression_decision = {
            "decision": "merge-same-intent"
            if len(group.identities) > 1
            else "preserve-distinct-intent",
            "stable_intents": sorted(group.stable_keys),
            "meaning_preservation_evidence": group.compression_evidence,
            "ambiguity_preserved": True,
            "no_count_target": True,
        }
        anti_seed = {
            "attested": True,
            "independent": True,
            "no_inherited_action": True,
            "no_legacy_action": True,
            "no_preserve_old_logic": True,
            "basis": "Candidate Workflows plus human intent and Naming System evidence only.",
            "evidence_ids": [
                f"anti-seed-{_digest({'group': group.group_key, 'input': base_digest}, prefix='', length=20)}"
            ],
        }
        action_input_digest = _action_digest(base_digest, group)
        induction = {
            "new_workflow_clusters": workflow_clusters,
            "human_intent_analysis": intent_analysis,
            "compression_decision": compression_decision,
            "naming_system": {
                "rationale": rationale,
                "alternatives": alternatives,
                "proposal": name,
                "language": naming_record_value["language"],
                "part_of_speech": naming_record_value["part_of_speech"],
                "provenance": naming_record_value["provenance"],
            },
            "anti_seed_attestation": anti_seed,
            "input_digest": action_input_digest,
        }
        # A Workflow shared across Action groups is valid only with explicit
        # distinct-intent evidence.  The graph validator checks this exact
        # nested field after every candidate is built.
        provisional.append((group, induction, name, statement))

    membership: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, (group, _induction, _name, _statement) in enumerate(provisional):
        for identity in group.identities:
            membership[identity].append(index)
    for identity, owners in membership.items():
        if len(owners) < 2:
            continue
        for owner in owners:
            induction = provisional[owner][1]
            evidence_ids = [
                f"distinct-intent-{_digest({'workflow': identity, 'group': provisional[owner][0].group_key}, prefix='', length=20)}"
            ]
            induction["distinct_intent_evidence"] = {
                "distinct": True,
                "independent": True,
                "intent_difference": True,
                "reason": "The shared Workflow is reused for a materially different human outcome.",
                "evidence_ids": evidence_ids,
            }

    # Same one-word verb across materially distinct groups is an unresolved
    # routing ambiguity.  The pass above rejects it; never manufacture a
    # hyphenated identifier or silently retain two user-facing meanings.
    by_name: dict[str, list[int]] = defaultdict(list)
    for index, (_group, _induction, name, _statement) in enumerate(provisional):
        by_name[_normalised_phrase(name)].append(index)
    for name, owners in by_name.items():
        if len(owners) < 2:
            continue
        raise ActionInductionInputError(
            f"Unrelated human intents compete for Action verb {name!r}; "
            "Naming System must choose distinct one-word verbs"
        )

    actions: list[dict[str, Any]] = []
    for group, induction, name, statement in provisional:
        entries = group.entries
        group_workflows = [workflows[identity] for identity in sorted(group.identities)]
        # Human evidence may explicitly provide interface contracts.  When it
        # does not, the already-canonical Workflow boundary is the only safe
        # fallback; Dot/provider internals are never copied.
        input_value = next(
            (item.raw.get("inputs") for item in entries if item.raw.get("inputs") is not None), None
        )
        output_value = next(
            (item.raw.get("outputs") for item in entries if item.raw.get("outputs") is not None),
            None,
        )
        inputs = _contract_items(
            input_value,
            label="Action inputs",
            fallback=_workflow_port_items(group_workflows, "inputs", evidence=entries),
            default_id="request",
        )
        outputs = _contract_items(
            output_value,
            label="Action outputs",
            fallback=_workflow_port_items(group_workflows, "outputs", evidence=entries),
            default_id="result",
        )
        match = next(
            (
                item.raw.get("match_contract")
                for item in entries
                if item.raw.get("match_contract") is not None
            ),
            None,
        )
        match_contract = (
            _safe_copy(match) if isinstance(match, Mapping) and match else {"intent": statement}
        )
        success = next(
            (
                item.raw.get("success_family", item.raw.get("success"))
                for item in entries
                if item.raw.get("success_family", item.raw.get("success")) is not None
            ),
            None,
        )
        if success is None:
            success = {"human_intent": statement, "checks": ["return the declared human outcome"]}
        recovery = next(
            (item.raw.get("recovery") for item in entries if item.raw.get("recovery") is not None),
            None,
        )
        if not isinstance(recovery, Mapping) or not recovery:
            recovery = {
                "strategy": "Keep this Candidate Action inactive and restore the prior bounded surface if verification fails.",
                "evidence_ids": [
                    f"action-recovery-{_digest(group.group_key, prefix='', length=20)}"
                ],
            }
        elif "evidence_ids" not in recovery:
            recovery = {
                **dict(recovery),
                "evidence_ids": [
                    f"action-recovery-{_digest(group.group_key, prefix='', length=20)}"
                ],
            }
        human_intent = {
            "statement": statement,
            "familiar": name,
            "stable": True,
            "stable_id": sorted(group.stable_keys)[0],
        }
        action_id = _action_verb(name, "induced Action name")
        try:
            action = build_action(
                action_id=action_id,
                version="1.0.0",
                human_name=name,
                human_intent=human_intent,
                match_contract=match_contract,
                inputs=inputs,
                outputs=outputs,
                workflow_refs=[
                    {
                        "sequence": index,
                        "workflow_id": identity[0],
                        "version": identity[1],
                        "lifecycle": "candidate",
                    }
                    for index, identity in enumerate(sorted(group.identities), start=1)
                ],
                success_family=success,
                recovery=recovery,
                induction_evidence=induction,
                status="candidate",
                workflow_records=list(workflows.values()),
            )
        except (ActionValidationError, ValueError) as error:
            raise ActionInductionValidationError(
                f"Induced Candidate Action for intent {sorted(group.stable_keys)} failed canonical validation: {error}"
            ) from error
        actions.append(action)

    # The final graph check enforces shared Workflow evidence and duplicate
    # familiar-name boundaries.  It is intentionally called after all
    # evidence is attached, not used as a substitute for induction.
    try:
        return validate_action_graph(actions, workflow_records=list(workflows.values()))
    except ActionValidationError as error:
        raise ActionInductionValidationError(
            f"Candidate Action graph is invalid: {error}"
        ) from error


def _legacy_records(value: Any) -> list[Mapping[str, Any]]:
    """Read a legacy fixture only for excluded comparison/audit generation."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("actions", "legacy_actions", "records", "candidates"):
            if key in value:
                return _legacy_records(value[key])
        if value.get("action_id") is not None:
            return [value]
        return [child for child in value.values() if isinstance(child, Mapping)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    raise ActionInductionInputError("legacy_fixture must be an object or list when supplied")


def _legacy_removal_audit(value: Any, actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records = _legacy_records(value)
    if not records:
        return []
    audit: list[dict[str, Any]] = []
    normalised_actions = [
        (
            action,
            _normalised_phrase(action.get("human_name")),
            _normalised_phrase(
                action.get("human_intent", {}).get("familiar")
                if isinstance(action.get("human_intent"), Mapping)
                else ""
            ),
        )
        for action in actions
    ]
    for index, old in enumerate(records):
        old_id = _text(old.get("action_id", old.get("id", "")))
        old_name = _normalised_phrase(old.get("human_name", old.get("name", "")))
        match = next(
            (
                action
                for action, action_name, action_familiar in normalised_actions
                if (old_id and action.get("action_id") == old_id)
                or (old_name and old_name in {action_name, action_familiar})
            ),
            None,
        )
        legacy_digest = _digest(
            {"index": index, "identity": old_id or old_name or "unidentified-legacy-record"},
            prefix="legacy-fixture-",
            length=20,
        )
        if match is None:
            audit.append(
                {
                    "legacy_reference": old_id or legacy_digest,
                    "legacy_reference_digest": legacy_digest,
                    "status": "removed",
                    "evidence": {
                        "excluded_from_induction": True,
                        "no_candidate_carry_forward": True,
                    },
                }
            )
        else:
            audit.append(
                {
                    "legacy_reference": old_id or legacy_digest,
                    "legacy_reference_digest": legacy_digest,
                    "status": "independently_reinduced",
                    "new_action_id": match["action_id"],
                    "evidence": {
                        "excluded_from_induction": True,
                        "new_workflow_cluster_to_intent_to_naming": True,
                        "induction_evidence_digest": match["induction_evidence"]["input_digest"],
                    },
                }
            )
    return audit


def induce_candidate_actions(
    candidate_workflows: Any = None,
    human_intent_evidence: Any = None,
    naming_system: Any = None,
    *,
    naming_proposals: Any = None,
    naming_evidence: Any = None,
    legacy_fixture: Any = None,
    legacy_actions: Any = None,
    **options: Any,
) -> dict[str, Any]:
    """Induce and compress inactive canonical Candidate Actions.

    ``candidate_workflows`` may be a canonical Workflow, a list, or a
    synthesis-style ``candidate_workflows``/``candidates`` wrapper.  All
    other input records are rejected.  ``legacy_fixture`` and
    ``legacy_actions`` are mutually exclusive audit-only comparisons and are
    never included in the induction digest.
    """

    if candidate_workflows is None:
        candidate_workflows = options.pop("workflows", options.pop("workflow_records", None))
    if human_intent_evidence is None:
        human_intent_evidence = options.pop("human_intents", options.pop("intent_evidence", None))
    if naming_system is None:
        naming_system = naming_proposals if naming_proposals is not None else naming_evidence
    elif naming_proposals is not None or naming_evidence is not None:
        raise ActionInductionInputError("Use one Naming System proposal input")
    if options:
        separate_proposals = options.pop("naming_system_proposals", options.pop("proposals", None))
        separate_rationale = options.pop(
            "naming_system_rationale", options.pop("naming_rationale", None)
        )
        separate_alternatives = options.pop(
            "naming_system_alternatives", options.pop("naming_alternatives", None)
        )
        if naming_system is None and any(
            value is not None
            for value in (separate_proposals, separate_rationale, separate_alternatives)
        ):
            naming_system = {
                "proposals": separate_proposals,
                "rationale": separate_rationale,
                "alternatives": separate_alternatives or [],
            }
    if options:
        unsupported = ", ".join(sorted(options))
        raise ActionInductionInputError(f"Unsupported Action induction option(s): {unsupported}")
    if legacy_fixture is not None and legacy_actions is not None:
        raise ActionInductionInputError("legacy_fixture and legacy_actions are aliases; supply one")
    legacy_value = legacy_fixture if legacy_fixture is not None else legacy_actions

    workflows = _validate_candidate_workflows(candidate_workflows)
    workflow_map = _workflow_map(workflows)
    # Evidence is intentionally canonicalised before digesting.  Legacy data
    # is absent from this value by construction.
    _reject_forbidden_keys(human_intent_evidence, allow_workflow_targets=True)
    if naming_system is None:
        raise ActionInductionInputError(
            "Naming System proposals/rationale/alternatives are required"
        )
    _reject_forbidden_keys(naming_system)
    entries = _parse_intent_evidence(human_intent_evidence, workflow_map)
    # Naming evidence is part of the input digest even if a proposal is later
    # derived from familiar human language.
    digest_input = {
        "candidate_workflows": workflows,
        "human_intent_evidence": _order_independent(human_intent_evidence),
        "naming_system": _order_independent(naming_system),
    }
    base_digest = _digest(digest_input, prefix="action-induction-input-")
    groups = _cluster_intents(entries, workflow_map)
    actions = _build_candidate_actions(
        groups,
        workflow_map,
        human_intent_evidence=human_intent_evidence,
        naming_system=naming_system,
        base_digest=base_digest,
    )
    audit = _legacy_removal_audit(legacy_value, actions)
    result = {
        "record_type": INDUCTION_RECORD_TYPE,
        "record_version": INDUCTION_RECORD_VERSION,
        "method": INDUCTION_METHOD,
        "method_version": INDUCTION_METHOD_VERSION,
        "candidate_actions": copy.deepcopy(actions),
        "candidates": copy.deepcopy(actions),
        "actions": copy.deepcopy(actions),
        "input_digest": base_digest,
        "anti_seed_attestation": {
            "attested": True,
            "independent": True,
            "no_inherited_action": True,
            "no_legacy_action": True,
            "basis": "Candidate Workflows plus human intent and Naming System evidence only.",
            "evidence_ids": [
                f"anti-seed-{base_digest.removeprefix('action-induction-input-')[:20]}"
            ],
        },
        "compression": [
            copy.deepcopy(action["induction_evidence"]["compression_decision"])
            for action in actions
        ],
        "legacy_removal_audit": audit,
        "removal_audit": copy.deepcopy(audit),
    }
    return result


def induce_candidate_action(
    candidate_workflows: Any,
    human_intent_evidence: Any,
    naming_system: Any,
    **options: Any,
) -> dict[str, Any]:
    """Convenience helper requiring exactly one induced Candidate Action."""

    result = induce_candidate_actions(
        candidate_workflows,
        human_intent_evidence,
        naming_system,
        **options,
    )
    candidates = result["candidate_actions"]
    if len(candidates) != 1:
        raise ActionInductionValidationError(
            f"Expected one Candidate Action, induced {len(candidates)} distinct human intents"
        )
    return copy.deepcopy(candidates[0])


def compress_candidate_actions(
    candidate_workflows: Any,
    human_intent_evidence: Any,
    naming_system: Any,
    **options: Any,
) -> dict[str, Any]:
    """Run the deliberate second-stage surface compression.

    Compression is intentionally coupled to induction so it cannot accept an
    old Action graph as its seed.  The returned ``compression`` records make
    every merge/split decision inspectable while retaining all meaningful
    human intents.
    """

    return induce_candidate_actions(
        candidate_workflows,
        human_intent_evidence,
        naming_system,
        **options,
    )


def classify_incremental_action_fit(
    candidate_workflow: Mapping[str, Any],
    existing_actions: Any,
    human_intent_evidence: Any,
) -> dict[str, Any]:
    """Classify one new Workflow against the stable active/candidate Action surface.

    This is an evolution-only comparison, never a Genesis seed.  Exact stable
    human-intent evidence may attach a related Workflow to one existing Action.
    A new intent becomes a Candidate Action signal.  Competing meanings for the
    same one-word verb remain explicitly ambiguous and never merge or acquire a
    suffix automatically.
    """

    workflows = _validate_candidate_workflows([candidate_workflow])
    workflow = workflows[0]
    workflow_identity = _workflow_identity(workflow)
    workflow_map = {workflow_identity: workflow}
    entries = _parse_intent_evidence(human_intent_evidence, workflow_map)
    evidence_ids = sorted({evidence_id for entry in entries for evidence_id in entry.evidence_ids})
    if not evidence_ids:
        raise ActionInductionInputError(
            "incremental Action fit requires direct human-intent evidence"
        )

    if isinstance(existing_actions, Mapping):
        if existing_actions.get("action_id") is not None:
            raw_actions = [existing_actions]
        else:
            for key in ("actions", "candidates", "records"):
                if key in existing_actions:
                    raw_actions = existing_actions[key]
                    break
            else:
                raw_actions = list(existing_actions.values())
    else:
        raw_actions = existing_actions
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, (str, bytes, bytearray)):
        raise ActionInductionInputError("existing_actions must be a list or registry")
    actions = [validate_action(item) for item in raw_actions]
    if not actions:
        raise ActionInductionInputError("existing_actions must not be empty")

    stable_keys = {entry.stable_key for entry in entries}
    familiar_verbs = {
        _action_verb(entry.familiar, "incremental human familiar Action name") for entry in entries
    }
    base = {
        "record_type": ACTION_FIT_RECORD_TYPE,
        "record_version": ACTION_FIT_RECORD_VERSION,
        "workflow_ref": {
            "workflow_id": workflow_identity[0],
            "version": workflow_identity[1],
        },
        "stable_intents": sorted(stable_keys),
        "familiar_verbs": sorted(familiar_verbs),
        "evidence_ids": evidence_ids,
        "persistent": False,
        "mutation": False,
    }
    if len(stable_keys) != 1 or len(familiar_verbs) != 1:
        return {
            **base,
            "classification": "ambiguous",
            "reason": "The new Workflow has competing human-intent evidence.",
            "action_refs": [],
        }

    stable_key = next(iter(stable_keys))
    familiar_verb = next(iter(familiar_verbs))
    exact_matches = [
        action
        for action in actions
        if isinstance(action.get("human_intent"), Mapping)
        and action["human_intent"].get("stable_id") == stable_key
    ]
    name_competitors = [action for action in actions if action["action_id"] == familiar_verb]
    refs = [
        {"action_id": action["action_id"], "version": action["version"]}
        for action in sorted(
            [*exact_matches, *name_competitors],
            key=lambda item: (item["action_id"], item["version"]),
        )
    ]
    # Dicts are unhashable; preserve deterministic uniqueness by canonical ref.
    refs = [json.loads(value) for value in sorted({_canonical(ref) for ref in refs})]
    if len(exact_matches) == 1 and not _entry_distinct(entries[0]):
        action = exact_matches[0]
        if action["action_id"] != familiar_verb:
            return {
                **base,
                "classification": "ambiguous",
                "reason": (
                    "Stable intent fits an existing Action but the proposed verb "
                    "would rename its familiar surface."
                ),
                "action_refs": refs,
            }
        if not all(_entry_merge_allowed(entry) for entry in entries):
            return {
                **base,
                "classification": "ambiguous",
                "reason": "Related-Workflow fit lacks meaning-preservation evidence.",
                "action_refs": refs,
            }
        already_member = workflow_identity in {
            (ref["workflow_id"], ref["version"]) for ref in action["workflow_refs"]
        }
        return {
            **base,
            "classification": "fit-existing",
            "reason": "Exact stable human intent supports the same one-word Action verb.",
            "action_ref": {
                "action_id": action["action_id"],
                "version": action["version"],
            },
            "already_member": already_member,
        }
    if exact_matches or name_competitors:
        return {
            **base,
            "classification": "ambiguous",
            "reason": (
                "Unrelated or competing human intents cannot silently share an Action verb."
            ),
            "action_refs": refs,
        }
    return {
        **base,
        "classification": "candidate-new-action",
        "reason": "No existing Action owns the evidenced stable human intent.",
        "action_refs": [],
    }


# Public aliases follow the naming conventions used by the other genesis
# boundaries and make callers independent of British/American spelling.
induce_actions = induce_candidate_actions
induct_actions = induce_candidate_actions
induct_candidate_actions = induce_candidate_actions
synthesize_candidate_actions = induce_candidate_actions
synthesise_candidate_actions = induce_candidate_actions
synthesize_actions = induce_candidate_actions
synthesise_actions = induce_candidate_actions
compile_candidate_actions = induce_candidate_actions
build_candidate_actions = induce_candidate_actions
build_actions = induce_candidate_actions
induce_surface_actions = induce_candidate_actions
compress_action_surface = compress_candidate_actions
compress_actions = compress_candidate_actions
classify_workflow_action_fit = classify_incremental_action_fit


def validate_induced_action(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one inactive Candidate Action at this boundary."""

    try:
        result = validate_action(value)
    except (ActionValidationError, ValueError) as error:
        raise ActionInductionValidationError(str(error)) from error
    if (
        _record_state(result) != "candidate"
        or result.get("activation", {}).get("status") != "inactive"
    ):
        raise ActionInductionValidationError("Induced Action must remain an inactive Candidate")
    if "platform_projections" in result:
        raise ActionInductionValidationError(
            "Action induction cannot install a platform projection"
        )
    return result


validate_candidate_action = validate_induced_action


def validate_candidate_actions(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [validate_induced_action(value) for value in values]


validate_capability_action_induction = validate_induced_action


__all__ = [
    "ACTION_FIT_RECORD_TYPE",
    "ACTION_FIT_RECORD_VERSION",
    "ActionInductionError",
    "ActionInductionInputError",
    "ActionInductionValidationError",
    "ActionInductionInputValidationError",
    "CapabilityActionInductionError",
    "CapabilityActionInductionValidationError",
    "INDUCTION_METHOD",
    "INDUCTION_METHOD_VERSION",
    "INDUCTION_RECORD_TYPE",
    "INDUCTION_RECORD_VERSION",
    "build_candidate_actions",
    "build_actions",
    "classify_incremental_action_fit",
    "classify_workflow_action_fit",
    "compile_candidate_actions",
    "compress_action_surface",
    "compress_actions",
    "compress_candidate_actions",
    "induct_candidate_actions",
    "induct_actions",
    "induce_actions",
    "induce_candidate_action",
    "induce_candidate_actions",
    "induce_surface_actions",
    "synthesise_candidate_actions",
    "synthesise_actions",
    "synthesize_actions",
    "synthesize_candidate_actions",
    "validate_candidate_action",
    "validate_candidate_actions",
    "validate_capability_action_induction",
    "validate_induced_action",
]
