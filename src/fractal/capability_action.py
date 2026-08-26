"""Canonical System-owned Action contracts.

An Action is the user-facing result of induction.  It is deliberately one
layer above reusable Workflows: an Action may point to Workflows, but it may
not make a Source, Dot, Implementation, provider, or platform Skill part of
its canonical identity.  Platform projections are derived records and do not
carry Action authority.

This module contains pure validation and constructors only.  It does not run
induction, read or write Workplace state, activate a version, or publish a
platform projection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

ACTION_RECORD_TYPE = "capability-action"
ACTION_RECORD_VERSION = 1
PROJECTION_RECORD_TYPE = "capability-action-projection"
PROJECTION_RECORD_VERSION = 1
SCHEMA_URI = "https://fractal.local/schemas/capability-action.schema.json"

_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_ACTION_VERB_PATTERN = re.compile(r"[a-z]+\Z")
_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?\Z"
)

_LIFECYCLE_STATES = frozenset({"candidate", "active", "retired"})
_VERIFICATION_STATES = frozenset(
    {"unverified", "in-progress", "verified-staged", "verified", "failed"}
)
_REVIEW_STATES = frozenset({"pending", "in-progress", "completed", "rejected"})
_DECISION_STATES = frozenset({"pending", "approved", "rejected", "deferred"})
_ACTIVATION_STATES = frozenset({"inactive", "ready", "active", "revoked"})

# Public state sets mirror the Dot and Workflow contracts.
LIFECYCLE_STATES = _LIFECYCLE_STATES
VERIFICATION_STATES = _VERIFICATION_STATES
SYSTEM_REVIEW_STATES = _REVIEW_STATES
DECISION_STATES = _DECISION_STATES
ACTIVATION_STATES = _ACTIVATION_STATES

_MATERIAL_FIELDS = (
    "action_id",
    "human_name",
    "human_intent",
    "match_contract",
    "inputs",
    "outputs",
    "workflow_refs",
    "success_family",
)

# These keys are authority-bearing identities from lower layers.  They are
# rejected as keys, not as words in evidence prose: an anti-seed attestation
# may truthfully say that it found no inherited Action.
_FORBIDDEN_KEYS = frozenset(
    {
        "source",
        "source_id",
        "source_ref",
        "source_refs",
        "sources",
        "dot",
        "dot_id",
        "dot_ref",
        "dot_refs",
        "dots",
        "implementation",
        "implementation_id",
        "implementation_ref",
        "implementation_refs",
        "implementations",
        "provider",
        "provider_id",
        "provider_ref",
        "provider_refs",
        "provider_selection",
        "provider_implementation",
        "provider_specific",
        "provider_semantics",
        "platform_skill",
        "platform_skill_id",
        "platform_skill_ref",
        "skill",
        "skill_id",
        "skill_ref",
        "skill_name",
        "platform",
        "platform_id",
        "workflow",
        "workflow_id",
        "workflow_ref",
        "action_ref",
        "action_refs",
        "inherited_action",
        "legacy_action",
        "preserve_old",
        "preserve_old_action",
        "preserve_old_logic",
        "old_action",
    }
)


class ActionError(ValueError):
    """Base error for an invalid Action contract."""


class ActionValidationError(ActionError):
    """Raised when an Action or Action graph is invalid."""


class ActionVersionError(ActionValidationError):
    """Raised when immutable Action version lineage is invalid."""


# Compatibility names mirror the neighbouring capability contracts.
CapabilityActionError = ActionError
CapabilityActionValidationError = ActionValidationError


@lru_cache(maxsize=2)
def _validator(filename: str) -> Draft202012Validator:
    schema = json.loads(files("fractal.schemas").joinpath(filename).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_validate(
    value: Mapping[str, Any], filename: str = "capability-action.schema.json"
) -> None:
    try:
        _validator(filename).validate(value)
    except ValidationError as error:
        path = ".".join(str(part) for part in error.absolute_path)
        location = f" at {path}" if path else ""
        if "anti_seed_attestation" in error.message:
            raise ActionValidationError(
                "induction_evidence requires anti-seed attestation"
            ) from error
        raise ActionValidationError(f"Invalid {filename}{location}: {error.message}") from error


def _as_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ActionValidationError(f"{label} must be an object")
    return dict(value)


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionValidationError(f"{label} must be a non-empty string")
    return value


def _id(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    if _ID_PATTERN.fullmatch(text) is None:
        raise ActionValidationError(f"{label} is not a stable identifier: {text!r}")
    return text


def _action_verb(value: Any, label: str) -> str:
    """Validate the exact user-facing Action naming surface.

    Semantic English-verb judgement is supplied by the maintained Naming
    System evidence below; this boundary enforces the mechanically decidable
    one-word lowercase syntax and keeps invocation slashes out of identity.
    """

    text = _nonblank(value, label)
    if _ACTION_VERB_PATTERN.fullmatch(text) is None:
        raise ActionValidationError(f"{label} must be one lowercase English one-word verb")
    return text


def _version(value: Any, label: str = "version") -> str:
    text = _nonblank(value, label)
    if _VERSION_PATTERN.fullmatch(text) is None:
        raise ActionValidationError(f"{label} is not a supported version: {text!r}")
    return text


def _evidence_ids(value: Any, label: str, *, required: bool = False) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ActionValidationError(f"{label} must be a list of evidence ids")
    result = [_nonblank(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ActionValidationError(f"{label} contains duplicate evidence ids")
    return result


def _normal_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _reject_forbidden_keys(value: Any, *, path: str = "$") -> None:
    """Reject lower-layer identities and inheritance controls.

    ``platform_projections`` is the sole place where a platform Skill name is
    meaningful.  Even there, it remains a derived display field and lower
    Source/Dot/Implementation/provider references are still rejected.
    """

    if isinstance(value, Mapping):
        in_projection = "platform_projections" in path or "projections" in path
        in_workflow_context = any(
            marker in path
            for marker in (
                ".workflow_refs",
                "new_workflow_clusters",
                "workflow_clusters",
                "new_workflows",
                "workflow_ref",
            )
        )
        for raw_key, child in value.items():
            key = _normal_key(raw_key)
            if (
                key in {"action_id", "action_version"}
                and path != "$"
                and not (in_projection and key == "action_id")
                and not ("activation_evidence" in path and key == "action_version")
            ):
                # A projection's derived_from block is checked separately and
                # may name the Action it projects.
                raise ActionValidationError(
                    f"Nested Action identity is not canonical Action authority: {raw_key}"
                )
            if key in _FORBIDDEN_KEYS and not (
                in_workflow_context
                and key in {"workflow_id", "workflow_ref"}
                or in_projection
                and key
                in {
                    "platform_skill",
                    "platform_skill_id",
                    "platform_skill_ref",
                    "skill",
                    "skill_id",
                    "skill_name",
                    "platform",
                    "platform_id",
                }
            ):
                raise ActionValidationError(
                    "Raw Source/Dot/Implementation/provider/Skill or inherited Action "
                    f"reference is not allowed: {raw_key}"
                )
            _reject_forbidden_keys(child, path=f"{path}.{raw_key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, path=f"{path}[{index}]")


def _normalise_contract_items(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ActionValidationError(f"{label} must be an ordered list")
    if not value:
        raise ActionValidationError(f"{label} must not be empty")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if isinstance(raw, str):
            item: dict[str, Any] = {"id": _id(raw, f"{label}[{index}]")}
            item["type"] = "value"
            item["required"] = True
        elif isinstance(raw, Mapping):
            item = copy.deepcopy(dict(raw))
            if "id" not in item:
                for alias in ("input_id", "output_id", "name"):
                    if alias in item:
                        item["id"] = item.pop(alias)
                        break
            item.setdefault("type", "value")
            item.setdefault("required", True)
            _id(item.get("id"), f"{label}[{index}].id")
            if not isinstance(item["required"], bool):
                raise ActionValidationError(f"{label}[{index}].required must be boolean")
        else:
            raise ActionValidationError(f"{label}[{index}] must be a string or object")
        item_id = _id(item.get("id"), f"{label}[{index}].id")
        if item_id in seen:
            raise ActionValidationError(f"Duplicate {label} id: {item_id}")
        seen.add(item_id)
        result.append(item)
    return result


def _normalise_human_intent(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        text = _nonblank(value, "human_intent")
        return {"statement": text, "familiar": text, "stable": True}
    intent = _as_mapping(value, "human_intent")
    statement = intent.get(
        "statement",
        intent.get(
            "intent",
            intent.get(
                "description",
                intent.get("text", intent.get("familiar", intent.get("familiar_name"))),
            ),
        ),
    )
    familiar = intent.get(
        "familiar",
        intent.get("familiar_name", intent.get("label", statement)),
    )
    _nonblank(statement, "human_intent.statement")
    _nonblank(familiar, "human_intent.familiar")
    stable = intent.get("stable", intent.get("stable_intent", True))
    stable_identifier: str | None = None
    if isinstance(stable, str):
        stable_identifier = _nonblank(stable, "human_intent.stable")
        stable = True
    if not isinstance(stable, bool):
        raise ActionValidationError("human_intent.stable must be boolean or a stable identifier")
    if not stable:
        raise ActionValidationError("human_intent must describe a stable human intent")
    result = copy.deepcopy(intent)
    result["statement"] = statement
    result["familiar"] = familiar
    result["stable"] = True
    if stable_identifier is not None:
        result["stable_id"] = stable_identifier
    return result


def _normalise_workflow_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ActionValidationError("workflow_refs must be an ordered list")
    if not value:
        raise ActionValidationError("An Action requires at least one Workflow reference")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for sequence, raw in enumerate(value, start=1):
        if isinstance(raw, str):
            raise ActionValidationError(
                "Workflow references require workflow_id and version; raw ids are ambiguous"
            )
        if not isinstance(raw, Mapping):
            raise ActionValidationError(f"workflow_refs[{sequence}] must be an object")
        item = copy.deepcopy(dict(raw))
        if "ref" in item:
            ref = item.pop("ref")
            if not isinstance(ref, Mapping):
                raise ActionValidationError("workflow_refs.ref must be an object")
            merged = dict(ref)
            merged.update(item)
            item = merged
        if "lifecycle" not in item:
            item["lifecycle"] = item.pop("required_state", item.pop("state", "candidate"))
        item.setdefault("sequence", sequence)
        if item.get("sequence") != sequence:
            raise ActionValidationError("Workflow references must have contiguous sequence values")
        item["workflow_id"] = _id(item.get("workflow_id"), "workflow_id")
        item["version"] = _version(item.get("version"), "Workflow version")
        if item.get("lifecycle") not in {"candidate", "active"}:
            raise ActionValidationError("Workflow reference lifecycle must be active or candidate")
        if item["workflow_id"] in seen_ids:
            raise ActionValidationError(
                f"Duplicate Workflow reference in one Action: {item['workflow_id']}"
            )
        seen_ids.add(item["workflow_id"])
        result.append(item)
    return result


def _normalise_action_aliases(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    if "human_intent" not in result:
        for alias in (
            "familiar_human_intent",
            "familiar_intent",
            "stable_human_intent",
            "human_intent_contract",
        ):
            if alias in result:
                result["human_intent"] = result.pop(alias)
                break
    if "workflow_refs" not in result and "workflow_references" in result:
        result["workflow_refs"] = result.pop("workflow_references")
    if "induction_evidence" not in result:
        for alias in ("induction", "induction_record"):
            if alias in result:
                result["induction_evidence"] = result.pop(alias)
                break
    if "platform_projections" not in result and "projections" in result:
        result["platform_projections"] = result.pop("projections")
    induction = result.get("induction_evidence")
    if isinstance(induction, Mapping):
        induction = copy.deepcopy(dict(induction))
        if "new_workflow_clusters" not in induction:
            for alias in (
                "workflow_clusters",
                "new_workflows",
                "new_workflow_cluster_refs",
            ):
                if alias in induction:
                    induction["new_workflow_clusters"] = induction[alias]
                    break
        if "new_workflow_clusters" not in induction:
            cluster_ids = induction.get(
                "new_workflow_cluster_ids", induction.get("workflow_cluster_ids")
            )
            versions = induction.get("new_workflow_versions", induction.get("workflow_versions"))
            if (
                isinstance(cluster_ids, Sequence)
                and not isinstance(cluster_ids, (str, bytes, bytearray))
                and isinstance(versions, Sequence)
                and not isinstance(versions, (str, bytes, bytearray))
            ):
                if len(cluster_ids) != len(versions):
                    raise ActionValidationError("Workflow cluster ids and versions must align")
                induction["new_workflow_clusters"] = [
                    {
                        "cluster_id": cluster_id,
                        "workflow_id": cluster_id,
                        "version": version,
                    }
                    for cluster_id, version in zip(cluster_ids, versions, strict=True)
                ]
        if "anti_seed_attestation" not in induction and "anti_seed" in induction:
            induction["anti_seed_attestation"] = induction["anti_seed"]
        if "input_digest" not in induction and "input_digest_sha256" in induction:
            induction["input_digest"] = induction["input_digest_sha256"]
        if "naming_system" not in induction and (
            "naming_rationale" in induction or "naming_system_rationale" in induction
        ):
            induction["naming_system"] = {
                "rationale": induction.get(
                    "naming_rationale", induction.get("naming_system_rationale")
                ),
                "alternatives": induction.get(
                    "naming_alternatives", induction.get("naming_system_alternatives")
                ),
            }
        result["induction_evidence"] = induction
    result["human_intent"] = _normalise_human_intent(result.get("human_intent"))
    result["inputs"] = _normalise_contract_items(result.get("inputs"), "inputs")
    result["outputs"] = _normalise_contract_items(result.get("outputs"), "outputs")
    result["workflow_refs"] = _normalise_workflow_refs(result.get("workflow_refs"))
    return result


def _workflow_identity(ref: Mapping[str, Any]) -> tuple[str, str]:
    return ref["workflow_id"], ref["version"]


def _record_state(record: Mapping[str, Any]) -> str | None:
    lifecycle = record.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        status = lifecycle.get("status", lifecycle.get("state"))
        if isinstance(status, str):
            return status
    state = record.get("status", record.get("state"))
    return state if isinstance(state, str) else None


def _records(values: Any, *, kind: str) -> list[Mapping[str, Any]]:
    if values is None:
        return []
    identity = "workflow_id" if kind == "workflow" else "action_id"
    if isinstance(values, Mapping):
        if values.get(identity) is not None:
            return [values]
        result: list[Mapping[str, Any]] = []
        for key, item in values.items():
            if not isinstance(item, Mapping):
                continue
            if item.get(identity) is None and isinstance(key, str):
                result.append({identity: key, **dict(item)})
            else:
                result.append(item)
        return result
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        return [item for item in values if isinstance(item, Mapping)]
    raise ActionValidationError(f"{kind} records must be an object, list, or id-indexed mapping")


def _lookup_workflow(workflows: Any, workflow_id: str, version: str) -> Mapping[str, Any] | None:
    for workflow in _records(workflows, kind="workflow"):
        if workflow.get("workflow_id") == workflow_id and workflow.get("version") == version:
            return workflow
    return None


def _validate_workflow_registry(
    refs: Sequence[Mapping[str, Any]], workflows: Any, *, action_status: str
) -> None:
    for ref in refs:
        observed = _lookup_workflow(workflows, ref["workflow_id"], ref["version"])
        if observed is None:
            raise ActionValidationError(
                f"Action references missing Workflow {ref['workflow_id']}@{ref['version']}"
            )
        state = _record_state(observed)
        if state not in {"candidate", "active"}:
            raise ActionValidationError(f"Workflow is not executable: {ref['workflow_id']}")
        if state != ref["lifecycle"]:
            raise ActionValidationError(
                f"Workflow lifecycle does not match reference: {ref['workflow_id']}"
            )
        if action_status == "active" and state != "active":
            raise ActionValidationError("An active Action may reference only active Workflows")


def _validate_lifecycle(value: Any) -> tuple[dict[str, Any], str]:
    lifecycle = _as_mapping(value, "lifecycle")
    status = lifecycle.get("status", lifecycle.get("state"))
    if (
        lifecycle.get("status") is not None
        and lifecycle.get("state") is not None
        and lifecycle["status"] != lifecycle["state"]
    ):
        raise ActionValidationError("Action lifecycle status and state disagree")
    if status not in _LIFECYCLE_STATES:
        raise ActionValidationError(f"Invalid Action lifecycle status: {status!r}")
    dimensions = {
        "candidate": lifecycle.get("candidate"),
        "active": lifecycle.get("active"),
        "active_surface": lifecycle.get("active_surface"),
    }
    present = [item is not None for item in dimensions.values()]
    if any(present):
        if not all(present) or any(not isinstance(item, bool) for item in dimensions.values()):
            raise ActionValidationError(
                "Action lifecycle candidate/active/active_surface dimensions are incomplete"
            )
        expected = {
            "candidate": status == "candidate",
            "active": status == "active",
            "active_surface": status == "active",
        }
        if dimensions != expected:
            raise ActionValidationError(
                "Action lifecycle candidate/active/active_surface dimensions do not match status"
            )
    if lifecycle.get("transition_evidence") is not None:
        _evidence_ids(lifecycle["transition_evidence"], "lifecycle.transition_evidence")
    return lifecycle, status


def _validate_dimension(value: Any, label: str, statuses: frozenset[str]) -> dict[str, Any]:
    dimension = _as_mapping(value, label)
    status = dimension.get("status")
    if status not in statuses:
        raise ActionValidationError(f"{label}.status is invalid")
    _evidence_ids(dimension.get("evidence_ids"), f"{label}.evidence_ids")
    if status == "approved":
        _nonblank(dimension.get("decision_id"), f"{label}.decision_id")
    if status == "completed":
        review_id = dimension.get("review_id")
        if review_id is not None:
            _id(review_id, f"{label}.review_id")
    return dimension


def _authorised(value: Mapping[str, Any], label: str) -> bool:
    values = [value[key] for key in ("authorised", "authorized") if key in value]
    if not values:
        return False
    if any(not isinstance(item, bool) for item in values):
        raise ActionValidationError(f"{label} authorisation flags must be boolean")
    if len(values) == 2 and values[0] != values[1]:
        raise ActionValidationError(f"{label} authorisation spellings disagree")
    return values[0]


def _validate_activation(
    value: Any, *, action_version: str, lifecycle_status: str
) -> dict[str, Any]:
    activation = _as_mapping(value, "activation")
    status = activation.get("status")
    if status not in _ACTIVATION_STATES:
        raise ActionValidationError("activation.status is invalid")
    authorised = _authorised(activation, "activation")
    evidence = activation.get("activation_evidence")
    if evidence is not None:
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            raise ActionValidationError("activation.activation_evidence must be a list")
        seen: set[str] = set()
        for index, raw in enumerate(evidence):
            item = _as_mapping(raw, f"activation.activation_evidence[{index}]")
            evidence_id = _nonblank(
                item.get("evidence_id"), f"activation.activation_evidence[{index}].evidence_id"
            )
            if evidence_id in seen:
                raise ActionValidationError("activation evidence ids must be unique")
            seen.add(evidence_id)
            exact = item.get("action_version", item.get("version"))
            if _version(exact, "activation evidence action version") != action_version:
                raise ActionValidationError(
                    "activation evidence must name the exact Action version"
                )
            if item.get("authorised", item.get("authorized")) is not True:
                raise ActionValidationError("activation evidence must be explicitly authorised")
    if status == "active":
        if lifecycle_status != "active":
            raise ActionValidationError("candidate Action cannot claim active activation")
        if not authorised:
            raise ActionValidationError("active activation requires explicit authorisation")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            raise ActionValidationError("active activation requires versioned activation evidence")
        if not evidence:
            raise ActionValidationError("active activation requires versioned activation evidence")
        activated_version = activation.get("activated_version")
        if (
            activated_version is not None
            and _version(activated_version, "activation.activated_version") != action_version
        ):
            raise ActionValidationError("activation.activated_version must equal Action version")
    return {**activation, "_authorised": authorised}


def _validate_recovery(value: Any, label: str = "recovery") -> dict[str, Any]:
    recovery = _as_mapping(value, label)
    _nonblank(recovery.get("strategy"), f"{label}.strategy")
    _evidence_ids(recovery.get("evidence_ids"), f"{label}.evidence_ids", required=True)
    return recovery


def _normalise_clusters(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = evidence.get("new_workflow_clusters")
    if raw is None:
        raw = evidence.get(
            "workflow_clusters",
            evidence.get("new_workflows", evidence.get("new_workflow_cluster_refs")),
        )
    if raw is None:
        ids = evidence.get("new_workflow_cluster_ids", evidence.get("workflow_cluster_ids"))
        versions = evidence.get("new_workflow_versions", evidence.get("workflow_versions"))
        if isinstance(ids, Sequence) and not isinstance(ids, (str, bytes, bytearray)):
            if not isinstance(versions, Sequence) or isinstance(versions, (str, bytes, bytearray)):
                raise ActionValidationError(
                    "induction_evidence.new_workflow_versions is required with cluster ids"
                )
            if len(ids) != len(versions):
                raise ActionValidationError("Workflow cluster ids and versions must align")
            raw = [
                {"cluster_id": cluster_id, "workflow_id": cluster_id, "version": version}
                for cluster_id, version in zip(ids, versions, strict=True)
            ]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or not raw:
        raise ActionValidationError(
            "induction_evidence requires exact new Workflow cluster ids and versions"
        )
    result: list[dict[str, Any]] = []
    cluster_ids: set[str] = set()
    workflow_ids: set[tuple[str, str]] = set()
    for index, raw_item in enumerate(raw):
        item = _as_mapping(raw_item, f"induction_evidence.new_workflow_clusters[{index}]")
        cluster_id = item.get("cluster_id", item.get("id"))
        cluster_id = _id(cluster_id, "Workflow cluster id")
        nested = item.get("workflow_ref", item.get("workflow"))
        candidates: list[tuple[Any, Any]] = []
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
            candidates.extend(
                (entry.get("workflow_id"), entry.get("version"))
                for entry in nested
                if isinstance(entry, Mapping)
            )
        elif isinstance(nested, Mapping):
            candidates.append((nested.get("workflow_id"), nested.get("version")))
        workflow_ids_value = item.get("workflow_ids")
        versions_value = item.get("versions", item.get("workflow_versions"))
        if isinstance(workflow_ids_value, Sequence) and not isinstance(
            workflow_ids_value, (str, bytes, bytearray)
        ):
            if (
                not isinstance(versions_value, Sequence)
                or isinstance(versions_value, (str, bytes, bytearray))
                or len(workflow_ids_value) != len(versions_value)
            ):
                raise ActionValidationError("Workflow cluster ids and versions must align")
            candidates.extend(zip(workflow_ids_value, versions_value, strict=True))
        if not candidates:
            candidates.append((item.get("workflow_id"), item.get("version")))
        for workflow_id, version in candidates:
            workflow_id = _id(workflow_id, "induction Workflow id")
            version = _version(version, "induction Workflow version")
            identity = (workflow_id, version)
            if identity in workflow_ids:
                raise ActionValidationError(
                    f"Duplicate induced Workflow identity: {workflow_id}@{version}"
                )
            workflow_ids.add(identity)
            result.append(
                {
                    **item,
                    "cluster_id": cluster_id,
                    "workflow_id": workflow_id,
                    "version": version,
                }
            )
        cluster_ids.add(cluster_id)
    return result


def _validate_naming_system_evidence(value: Any, *, action_verb: str) -> dict[str, Any]:
    naming = _as_mapping(value, "induction_evidence.naming_system")
    proposal = _action_verb(naming.get("proposal"), "naming_system.proposal")
    if proposal != action_verb:
        raise ActionValidationError(
            "naming_system.proposal must equal the canonical Action name and id"
        )
    _nonblank(naming.get("rationale"), "naming_system.rationale")
    if naming.get("language") != "en" or naming.get("part_of_speech") != "verb":
        raise ActionValidationError("Naming System must classify the Action as an English verb")
    alternatives = naming.get("alternatives")
    if not isinstance(alternatives, Sequence) or isinstance(alternatives, (str, bytes, bytearray)):
        raise ActionValidationError("naming_system.alternatives must be a list")
    checked_alternatives = [
        _action_verb(item, f"naming_system.alternatives[{index}]")
        for index, item in enumerate(alternatives)
    ]
    if len(checked_alternatives) != len(set(checked_alternatives)):
        raise ActionValidationError("naming_system.alternatives must be unique")
    if proposal in checked_alternatives:
        raise ActionValidationError(
            "naming_system.alternatives cannot repeat the selected proposal"
        )
    provenance = _as_mapping(naming.get("provenance"), "naming_system.provenance")
    if set(provenance) != {"component_id", "version", "evidence_ids"}:
        raise ActionValidationError(
            "naming_system.provenance must contain component_id, version, and evidence_ids"
        )
    if provenance.get("component_id") != "naming-system":
        raise ActionValidationError(
            "naming_system.provenance must identify the maintained naming-system"
        )
    _version(provenance.get("version"), "naming_system.provenance.version")
    _evidence_ids(
        provenance.get("evidence_ids"),
        "naming_system.provenance.evidence_ids",
        required=True,
    )
    return {
        **naming,
        "proposal": proposal,
        "alternatives": checked_alternatives,
        "language": "en",
        "part_of_speech": "verb",
        "provenance": provenance,
    }


def _validate_induction_evidence(
    value: Any,
    refs: Sequence[Mapping[str, Any]],
    *,
    action_verb: str,
) -> dict[str, Any]:
    evidence = _as_mapping(value, "induction_evidence")
    clusters = _normalise_clusters(evidence)
    expected = {_workflow_identity(item) for item in refs}
    observed = {(item["workflow_id"], item["version"]) for item in clusters}
    if observed != expected:
        raise ActionValidationError(
            "induction_evidence must name exactly the new Workflow cluster ids and versions"
        )

    intent_analysis = evidence.get("human_intent_analysis")
    if isinstance(intent_analysis, Mapping) or (
        isinstance(intent_analysis, Sequence)
        and not isinstance(intent_analysis, (str, bytes, bytearray))
    ):
        if not intent_analysis:
            raise ActionValidationError("human_intent_analysis must not be empty")
    else:
        _nonblank(intent_analysis, "human_intent_analysis")

    compression = evidence.get("compression_decision")
    if isinstance(compression, Mapping):
        if not compression:
            raise ActionValidationError("compression_decision must not be empty")
    else:
        _nonblank(compression, "compression_decision")

    naming = _validate_naming_system_evidence(
        evidence.get("naming_system"), action_verb=action_verb
    )
    evidence["naming_system"] = naming

    anti_seed = evidence.get("anti_seed_attestation", evidence.get("anti_seed"))
    if anti_seed is None:
        raise ActionValidationError("induction_evidence requires anti-seed attestation")
    if isinstance(anti_seed, Mapping):
        if not anti_seed:
            raise ActionValidationError("anti_seed_attestation must not be empty")
        flags = [
            anti_seed[key]
            for key in (
                "attested",
                "passed",
                "clean",
                "independent",
                "no_inherited_action",
                "no_legacy_action",
                "no_preserve_old",
                "no_inherited",
                "no_legacy",
                "no_preserve_old_logic",
            )
            if key in anti_seed
        ]
        if flags and any(item is not True for item in flags):
            raise ActionValidationError(
                "anti_seed_attestation must affirm an independent induction"
            )
        if (
            not flags
            and anti_seed.get("status") not in {"passed", "clean", "attested"}
            and not any(
                isinstance(anti_seed.get(key), str) and anti_seed[key].strip()
                for key in ("statement", "attestation", "reason")
            )
        ):
            raise ActionValidationError("anti_seed_attestation requires an explicit attestation")
        _evidence_ids(anti_seed.get("evidence_ids"), "anti_seed_attestation.evidence_ids")
    else:
        _nonblank(anti_seed, "anti_seed_attestation")
    evidence["anti_seed_attestation"] = anti_seed

    digest = evidence.get("input_digest", evidence.get("input_digest_sha256"))
    if isinstance(digest, Mapping):
        _nonblank(digest.get("value", digest.get("digest")), "induction_evidence.input_digest")
    else:
        _nonblank(digest, "induction_evidence.input_digest")
    evidence["input_digest"] = digest
    return {**evidence, "new_workflow_clusters": clusters}


def _projection_ref(value: Any, *, action_id: str, version: str) -> dict[str, Any]:
    ref = copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}
    if "derived_from" not in ref:
        source_action = ref.pop("source_action", ref.pop("action", None))
        source_version = ref.pop("source_version", ref.pop("version", None))
        if source_action is not None or source_version is not None:
            ref["derived_from"] = {
                "action_id": source_action or action_id,
                "version": source_version or version,
            }
    if "derived_from" not in ref:
        ref["derived_from"] = {"action_id": action_id, "version": version}
    return ref


def validate_platform_projection(
    projection: Mapping[str, Any], *, action_id: str | None = None, version: str | None = None
) -> dict[str, Any]:
    """Validate one derived platform projection; it can never be canonical."""

    value = _as_mapping(projection, "platform projection")
    _reject_forbidden_keys(value, path="$.platform_projections")
    value.setdefault("record_type", PROJECTION_RECORD_TYPE)
    value.setdefault("record_version", PROJECTION_RECORD_VERSION)
    platform = value.get("platform", value.get("platform_id"))
    _nonblank(platform, "platform projection.platform")
    derived = _as_mapping(value.get("derived_from"), "platform projection.derived_from")
    derived_action = _id(derived.get("action_id"), "platform projection.derived_from.action_id")
    derived_version = _version(derived.get("version"), "platform projection.derived_from.version")
    if action_id is not None and derived_action != action_id:
        raise ActionValidationError("platform projection is derived from a different Action")
    if version is not None and derived_version != version:
        raise ActionValidationError(
            "platform projection is derived from a different Action version"
        )
    if value.get("canonical", False) is not False:
        raise ActionValidationError("platform projections are derived and cannot be canonical")
    authority = value.get("authority", value.get("authority_type", "derived"))
    if authority not in {"derived", "projection", "platform"}:
        raise ActionValidationError("platform projection cannot claim canonical authority")
    value["platform"] = platform
    value["derived_from"] = {"action_id": derived_action, "version": derived_version}
    value["canonical"] = False
    value["authority"] = authority
    return copy.deepcopy(value)


def _validate_projections(value: Any, *, action_id: str, version: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ActionValidationError("platform_projections must be a list")
    result = [
        validate_platform_projection(item, action_id=action_id, version=version) for item in value
    ]
    projection_ids = [item.get("projection_id") for item in result if item.get("projection_id")]
    if len(projection_ids) != len(set(projection_ids)):
        raise ActionValidationError("platform projection ids must be unique")
    return result


def _validate_reinduction_evidence(value: Any, label: str) -> None:
    evidence = value
    if evidence is None:
        raise ActionValidationError(
            f"{label} requires machine-readable changed-output or removal evidence"
        )
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)):
        if not evidence:
            raise ActionValidationError(f"{label} must not be empty")
        for item in evidence:
            _validate_reinduction_evidence(item, label)
        return
    if not isinstance(evidence, Mapping):
        _nonblank(evidence, label)
        return
    if not evidence:
        raise ActionValidationError(f"{label} must not be empty")
    flags = [
        evidence[key]
        for key in (
            "independent",
            "output_changed",
            "changed_output",
            "removal_changed_output",
            "removed_old_output",
            "changed_or_removed",
        )
        if key in evidence
    ]
    if any(item is False for item in flags):
        raise ActionValidationError(f"{label} must attest changed or removed output")
    kind = str(evidence.get("change_type", evidence.get("kind", ""))).lower()
    changed = any(item is True for item in flags) or kind in {
        "changed-output",
        "removed-output",
        "output-change",
        "removal",
        "split",
        "merge",
    }
    if not changed:
        raise ActionValidationError(f"{label} must attest changed or removed output")
    if "independent" in evidence and evidence["independent"] is not True:
        raise ActionValidationError(f"{label} must be independent")
    if "evidence_ids" in evidence:
        _evidence_ids(evidence["evidence_ids"], f"{label}.evidence_ids", required=True)
    else:
        _nonblank(evidence.get("evidence", evidence.get("reason")), f"{label}.reason")


def _validate_distinct_intent_evidence(value: Any, label: str) -> None:
    """Validate evidence that a shared Workflow serves a different intent."""

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ActionValidationError(f"{label} must not be empty")
        for index, item in enumerate(value):
            _validate_distinct_intent_evidence(item, f"{label}[{index}]")
        return
    if not isinstance(value, Mapping):
        _nonblank(value, label)
        return
    if not value:
        raise ActionValidationError(f"{label} must not be empty")
    for key in ("independent", "distinct", "different_intent", "intent_difference"):
        if key in value and value[key] is not True:
            raise ActionValidationError(f"{label}.{key} must be true")
    if "evidence_ids" in value:
        _evidence_ids(value["evidence_ids"], f"{label}.evidence_ids", required=True)
    else:
        _nonblank(
            value.get("reason", value.get("statement", value.get("evidence"))),
            f"{label}.reason",
        )


def _action_name_reuse_evidence(action: Mapping[str, Any]) -> Any:
    direct = action.get("name_reuse_evidence", action.get("same_name_evidence"))
    if direct is not None:
        return direct
    induction = action.get("induction_evidence")
    if isinstance(induction, Mapping):
        return induction.get(
            "name_reuse_evidence",
            induction.get("same_name_evidence", induction.get("reinduction_evidence")),
        )
    return None


def _validate_name_reuse_fields(action: Mapping[str, Any]) -> None:
    evidence = _action_name_reuse_evidence(action)
    if evidence is not None:
        _validate_reinduction_evidence(evidence, "name_reuse_evidence")


def validate_action(
    action: Mapping[str, Any],
    *,
    workflow_records: Any = None,
    workflows: Any = None,
    require_active: bool | None = None,
) -> dict[str, Any]:
    """Validate one candidate, active, or retired canonical Action.

    ``workflow_records`` is optional for candidate records.  If supplied, it
    is an authoritative exact registry.  Active records always require active
    lifecycle references; callers should supply the registry when they need
    proof that the referenced Workflow records themselves are active.
    """

    value = _as_mapping(action, "Action")
    _reject_forbidden_keys(value)
    value = _normalise_action_aliases(value)
    action_id = _action_verb(value.get("action_id"), "action_id")
    human_name = _action_verb(value.get("human_name"), "human_name")
    if human_name != action_id:
        raise ActionValidationError(
            "canonical Action human_name and action_id must be the same lowercase verb"
        )
    human_intent = _normalise_human_intent(value.get("human_intent"))
    if human_intent["familiar"] != action_id:
        raise ActionValidationError("human_intent.familiar must equal the canonical Action verb")
    value["action_id"] = action_id
    value["human_name"] = human_name
    value["human_intent"] = human_intent
    _schema_validate(value)
    if (
        value["record_type"] != ACTION_RECORD_TYPE
        or value["record_version"] != ACTION_RECORD_VERSION
    ):
        raise ActionValidationError("Action record type or version is not canonical")
    action_version = _version(value["version"])
    if not isinstance(value["match_contract"], Mapping) or not value["match_contract"]:
        raise ActionValidationError("match_contract must be a non-empty object")
    lifecycle, lifecycle_status = _validate_lifecycle(value["lifecycle"])
    refs = _normalise_workflow_refs(value["workflow_refs"])
    registry = workflow_records if workflow_records is not None else workflows
    if registry is not None:
        _validate_workflow_registry(refs, registry, action_status=lifecycle_status)
    if lifecycle_status == "active" and any(ref["lifecycle"] != "active" for ref in refs):
        raise ActionValidationError("An active Action may reference only active Workflows")

    success_family = value["success_family"]
    if isinstance(success_family, Mapping) or (
        isinstance(success_family, Sequence)
        and not isinstance(success_family, (str, bytes, bytearray))
    ):
        if not success_family:
            raise ActionValidationError("success_family must not be empty")
    else:
        _nonblank(success_family, "success_family")
    _validate_recovery(value["recovery"])
    induction = _validate_induction_evidence(
        value["induction_evidence"], refs, action_verb=action_id
    )
    verification = _validate_dimension(value["verification"], "verification", _VERIFICATION_STATES)
    system_review = _validate_dimension(value["system_review"], "system_review", _REVIEW_STATES)
    decision = _validate_dimension(value["human_decision"], "human_decision", _DECISION_STATES)
    activation = _validate_activation(
        value["activation"], action_version=action_version, lifecycle_status=lifecycle_status
    )
    if lifecycle_status == "candidate" and activation["status"] == "active":
        raise ActionValidationError("candidate Action cannot claim active activation")
    if lifecycle_status != "active" and activation["status"] == "active":
        raise ActionValidationError("active activation requires an active Action lifecycle")
    if lifecycle_status == "active":
        if not _evidence_ids(
            lifecycle.get("transition_evidence"),
            "lifecycle.transition_evidence",
            required=True,
        ):
            raise ActionValidationError("active Action requires lifecycle transition evidence")
        if verification["status"] != "verified":
            raise ActionValidationError("active Action requires verified Action evidence")
        _evidence_ids(verification.get("evidence_ids"), "verification.evidence_ids", required=True)
        if system_review["status"] != "completed":
            raise ActionValidationError("active Action requires completed System Review")
        _evidence_ids(
            system_review.get("evidence_ids"),
            "system_review.evidence_ids",
            required=True,
        )
        if decision["status"] != "approved":
            raise ActionValidationError("active Action requires an approved human decision")
        _nonblank(decision.get("decision_id"), "human_decision.decision_id")
        _evidence_ids(decision.get("evidence_ids"), "human_decision.evidence_ids", required=True)
        if activation["status"] != "active" or activation["_authorised"] is not True:
            raise ActionValidationError(
                "active Action requires authorised active activation evidence"
            )

    projections = _validate_projections(
        value.get("platform_projections"), action_id=action_id, version=action_version
    )
    _validate_name_reuse_fields(value)
    result = copy.deepcopy(value)
    result["human_intent"] = human_intent
    result["inputs"] = _normalise_contract_items(value["inputs"], "inputs")
    result["outputs"] = _normalise_contract_items(value["outputs"], "outputs")
    result["workflow_refs"] = refs
    result["induction_evidence"] = induction
    if "platform_projections" in value:
        result["platform_projections"] = projections
    return result


def _normalise_lineage(value: Any, *, action_id: str, version: str) -> dict[str, Any]:
    lineage = _as_mapping(value, "lineage")
    predecessor_id = lineage.get("predecessor_action_id", lineage.get("predecessor_id"))
    predecessor_version = lineage.get("predecessor_version")
    _id(predecessor_id, "lineage.predecessor_action_id")
    _version(predecessor_version, "lineage.predecessor_version")
    change_type = lineage.get("change_type", lineage.get("kind"))
    _nonblank(change_type, "lineage.change_type")
    _nonblank(lineage.get("reason"), "lineage.reason")
    _evidence_ids(lineage.get("evidence_ids"), "lineage.evidence_ids", required=True)
    _evidence_ids(
        lineage.get("recovery_evidence_ids", lineage.get("recovery_evidence")),
        "lineage.recovery_evidence_ids",
        required=True,
    )
    return {
        **lineage,
        "predecessor_action_id": predecessor_id,
        "predecessor_version": predecessor_version,
    }


def _version_order(version: str) -> tuple[int, int, int, int, str]:
    _version(version)
    core, _, suffix = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return major, minor, patch, 0 if suffix else 1, suffix


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_material_fingerprint(action: Mapping[str, Any]) -> str:
    """Hash only executable/user-facing Action semantics, not evidence/state."""

    validated = validate_action(action)
    material = {field: validated.get(field) for field in _MATERIAL_FIELDS}
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def action_material_change(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return action_material_fingerprint(before) != action_material_fingerprint(after)


def next_action_version(action_or_version: Mapping[str, Any] | str) -> str:
    version = (
        action_or_version
        if isinstance(action_or_version, str)
        else action_or_version.get("version")
    )
    _version(version)
    major, minor, patch = (int(part) for part in version.split("-", 1)[0].split("."))
    return f"{major}.{minor}.{patch + 1}"


def validate_action_version_update(
    previous: Mapping[str, Any], updated: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate immutable Action lineage and candidate-first material changes."""

    old = validate_action(previous)
    new = validate_action(updated)
    renamed = old["action_id"] != new["action_id"]
    old_version = old["version"]
    new_version = new["version"]
    changed = action_material_change(old, new)
    if old_version == new_version:
        if changed:
            raise ActionVersionError("material Action changes require a new version")
        return new
    if _version_order(new_version) <= _version_order(old_version):
        raise ActionVersionError("Action version update must be strictly newer")
    if new["lifecycle"].get("status", new["lifecycle"].get("state")) != "candidate":
        raise ActionVersionError("a material Action version update must create a candidate first")
    lineage = _normalise_lineage(
        new.get("lineage"), action_id=old["action_id"], version=new_version
    )
    if lineage["predecessor_action_id"] != old["action_id"]:
        raise ActionVersionError("lineage predecessor_action_id does not match the previous Action")
    if lineage["predecessor_version"] != old_version:
        raise ActionVersionError("lineage predecessor_version does not match the previous Action")
    change_type = str(lineage.get("change_type", "")).casefold()
    if renamed and change_type not in {"rename", "merge", "split"}:
        raise ActionVersionError(
            "an Action id/name change requires rename, merge, or split lineage"
        )
    if not renamed and change_type == "rename":
        raise ActionVersionError("rename lineage requires a changed Action verb")
    if not new["recovery"].get("evidence_ids"):
        raise ActionVersionError("material Action update requires recovery evidence")
    if old["human_name"] == new["human_name"]:
        _validate_reinduction_evidence(_action_name_reuse_evidence(new), "name_reuse_evidence")
    return new


def revise_action(
    action: Mapping[str, Any], changes: Mapping[str, Any], *, version: str | None = None
) -> dict[str, Any]:
    """Return a detached candidate Action with a material-change lineage."""

    current = validate_action(action)
    if not isinstance(changes, Mapping):
        raise ActionError("Action changes must be an object")
    if {"record_type", "record_version"}.intersection(changes):
        raise ActionError("Action record type cannot be changed")
    updated = copy.deepcopy(current)
    for key, value in changes.items():
        updated[key] = copy.deepcopy(value)
    if "action_id" in changes or "human_name" in changes:
        if updated.get("action_id") != updated.get("human_name"):
            raise ActionError(
                "Action rename must change action_id and human_name to the same lowercase verb"
            )
        intent = updated.get("human_intent")
        if isinstance(intent, Mapping):
            intent = copy.deepcopy(dict(intent))
            intent["familiar"] = updated["action_id"]
            updated["human_intent"] = intent
    material = action_material_change(current, updated)
    if material:
        requested = version or updated.get("version")
        if requested is None or requested == current["version"]:
            requested = next_action_version(current["version"])
        _version(requested)
        updated["version"] = requested
        lifecycle = updated.setdefault("lifecycle", {})
        lifecycle.update(
            {
                "status": "candidate",
                "state": "candidate",
                "candidate": True,
                "active": False,
                "active_surface": False,
                "material_change": True,
                "supersedes": f"{current['action_id']}@{current['version']}",
            }
        )
        updated["verification"] = {"status": "unverified"}
        updated["system_review"] = {"status": "pending"}
        updated["human_decision"] = {"status": "pending"}
        updated["activation"] = {"status": "inactive"}
        renamed = current["action_id"] != updated["action_id"]
        updated["lineage"] = {
            "predecessor_action_id": current["action_id"],
            "predecessor_version": current["version"],
            "change_type": "rename" if renamed else "material",
            "reason": (
                "Action verb changed through a candidate-first rename."
                if renamed
                else "Material Action contract change requires a candidate version."
            ),
            "evidence_ids": [f"action-lineage-{current['action_id']}-{requested}"],
            "recovery_evidence_ids": [f"action-recovery-{current['action_id']}-{requested}"],
        }
    validate_action(updated)
    return updated


def validate_action_graph(
    actions: Any,
    *,
    workflow_records: Any = None,
    workflows: Any = None,
) -> list[dict[str, Any]]:
    """Validate Action membership and cross-Action induction boundaries.

    A Workflow can support more than one Action only where the Actions have
    different human intents and each carries explicit machine-readable
    distinct-intent evidence for that shared Workflow.  Likewise, a familiar
    name can recur only with independent changed-output/removal evidence.
    """

    records = _records(actions, kind="action")
    if not records:
        raise ActionValidationError("Action graph must contain at least one Action")
    registry = workflow_records if workflow_records is not None else workflows
    validated = [validate_action(item, workflow_records=registry) for item in records]
    by_action_id: dict[str, dict[str, Any]] = {}
    for item in validated:
        if item["action_id"] in by_action_id:
            raise ActionValidationError(f"Duplicate Action id: {item['action_id']}")
        by_action_id[item["action_id"]] = item

    memberships: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in validated:
        for ref in item["workflow_refs"]:
            memberships.setdefault(_workflow_identity(ref), []).append(item)
    for identity, owners in memberships.items():
        if len(owners) < 2:
            continue
        intents = {
            _canonical_json(
                {
                    "statement": owner["human_intent"].get("statement"),
                    "familiar": owner["human_intent"].get("familiar"),
                }
            )
            for owner in owners
        }
        if len(intents) != len(owners):
            raise ActionValidationError(
                "A shared Workflow requires truthful distinct human intents"
            )
        workflow_id, version = identity
        for owner in owners:
            induction = owner.get("induction_evidence", {})
            raw_evidence = induction.get(
                "distinct_intent_evidence",
                induction.get("intent_distinction", induction.get("shared_workflow_evidence")),
            )
            if raw_evidence is None:
                raw_evidence = owner.get("distinct_intent_evidence")
            evidence = raw_evidence
            if isinstance(raw_evidence, Mapping):
                target_id = raw_evidence.get("workflow_id")
                target_version = raw_evidence.get("version", raw_evidence.get("workflow_version"))
                if (target_id is not None and target_id != workflow_id) or (
                    target_version is not None and target_version != version
                ):
                    evidence = None
            elif isinstance(raw_evidence, Sequence) and not isinstance(
                raw_evidence, (str, bytes, bytearray)
            ):
                matched = [
                    item
                    for item in raw_evidence
                    if isinstance(item, Mapping)
                    and item.get("workflow_id", workflow_id) == workflow_id
                    and item.get("version", item.get("workflow_version", version)) == version
                ]
                evidence = matched[0] if matched else None
            if evidence is None:
                raise ActionValidationError(
                    "A Workflow used by multiple Actions requires explicit distinct-intent evidence"
                )
            _validate_distinct_intent_evidence(evidence, "distinct_intent_evidence")

    names: dict[str, list[dict[str, Any]]] = {}
    for item in validated:
        names.setdefault(item["human_name"], []).append(item)
    for name, owners in names.items():
        if len(owners) < 2:
            continue
        # Different versions of the same Action are normal; cross-Action name
        # reuse is the case that requires independent re-induction evidence.
        distinct_actions = {item["action_id"] for item in owners}
        if len(distinct_actions) < 2:
            continue
        for owner in owners:
            _validate_reinduction_evidence(
                _action_name_reuse_evidence(owner),
                f"same human name {name!r} name_reuse_evidence",
            )
        digests = {
            _canonical_json(owner["induction_evidence"].get("input_digest")) for owner in owners
        }
        if len(digests) != len(owners):
            raise ActionValidationError(
                "same human name across Actions requires independent induction input digests"
            )
    return validated


def build_action(
    *,
    action_id: str,
    version: str,
    human_name: str,
    human_intent: Any = None,
    familiar_human_intent: Any = None,
    match_contract: Mapping[str, Any],
    inputs: Sequence[Any],
    outputs: Sequence[Any],
    workflow_refs: Sequence[Mapping[str, Any]],
    success_family: Any,
    recovery: Mapping[str, Any],
    induction_evidence: Mapping[str, Any],
    status: str = "candidate",
    verification: Mapping[str, Any] | None = None,
    system_review: Mapping[str, Any] | None = None,
    human_decision: Mapping[str, Any] | None = None,
    activation: Mapping[str, Any] | None = None,
    lifecycle: Mapping[str, Any] | None = None,
    platform_projections: Sequence[Mapping[str, Any]] | None = None,
    lineage: Mapping[str, Any] | None = None,
    workflow_records: Any = None,
) -> dict[str, Any]:
    """Construct and validate a canonical candidate or active Action."""

    if human_intent is None:
        human_intent = familiar_human_intent
    if human_intent is None:
        raise ActionError("human_intent is required")
    if status not in _LIFECYCLE_STATES:
        raise ActionError(f"Invalid Action status: {status!r}")
    lifecycle_value = (
        copy.deepcopy(dict(lifecycle))
        if lifecycle is not None
        else {
            "status": status,
            "state": status,
            "candidate": status == "candidate",
            "active": status == "active",
            "active_surface": status == "active",
        }
    )
    record: dict[str, Any] = {
        "record_type": ACTION_RECORD_TYPE,
        "record_version": ACTION_RECORD_VERSION,
        "action_id": _action_verb(action_id, "action_id"),
        "version": _version(version),
        "human_name": _action_verb(human_name, "human_name"),
        "human_intent": _normalise_human_intent(human_intent),
        "match_contract": copy.deepcopy(dict(match_contract)),
        "inputs": _normalise_contract_items(inputs, "inputs"),
        "outputs": _normalise_contract_items(outputs, "outputs"),
        "workflow_refs": _normalise_workflow_refs(workflow_refs),
        "success_family": copy.deepcopy(success_family),
        "lifecycle": lifecycle_value,
        "verification": copy.deepcopy(dict(verification or {"status": "unverified"})),
        "system_review": copy.deepcopy(dict(system_review or {"status": "pending"})),
        "human_decision": copy.deepcopy(dict(human_decision or {"status": "pending"})),
        "activation": copy.deepcopy(dict(activation or {"status": "inactive"})),
        "recovery": copy.deepcopy(dict(recovery)),
        "induction_evidence": copy.deepcopy(dict(induction_evidence)),
    }
    if platform_projections is not None:
        record["platform_projections"] = copy.deepcopy(list(platform_projections))
    if lineage is not None:
        record["lineage"] = copy.deepcopy(dict(lineage))
    return validate_action(record, workflow_records=workflow_records)


# Naming aliases keep the public boundary discoverable in the same way as the
# Source, Dot, and Workflow modules.
validate_capability_action = validate_action
validate_action_version = validate_action_version_update
validate_version_update = validate_action_version_update
build_capability_action = build_action
new_action_version = next_action_version
validate_capability_action_graph = validate_action_graph
validate_action_registry = validate_action_graph
validate_actions = validate_action_graph
revise_capability_action = revise_action


__all__ = [
    "ACTION_RECORD_TYPE",
    "ACTION_RECORD_VERSION",
    "ACTIVATION_STATES",
    "ActionError",
    "ActionValidationError",
    "ActionVersionError",
    "CapabilityActionError",
    "CapabilityActionValidationError",
    "DECISION_STATES",
    "LIFECYCLE_STATES",
    "PROJECTION_RECORD_TYPE",
    "PROJECTION_RECORD_VERSION",
    "SCHEMA_URI",
    "SYSTEM_REVIEW_STATES",
    "VERIFICATION_STATES",
    "action_material_change",
    "action_material_fingerprint",
    "build_action",
    "build_capability_action",
    "new_action_version",
    "next_action_version",
    "revise_action",
    "revise_capability_action",
    "validate_action",
    "validate_action_graph",
    "validate_action_registry",
    "validate_action_version",
    "validate_action_version_update",
    "validate_capability_action",
    "validate_capability_action_graph",
    "validate_actions",
    "validate_platform_projection",
    "validate_version_update",
]
