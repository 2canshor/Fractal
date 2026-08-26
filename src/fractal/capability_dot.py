"""Governed reusable Capability Dot and Implementation contracts.

This module is deliberately a pure boundary.  It validates a Dot, its
executable Implementations, a bounded candidate execution receipt, and
material version lineage.  It does not read Workplace records, execute a
provider, persist a candidate, activate a version, or mutate a Dot.

The contract is intentionally bottom-up: a Dot describes one coherent
reusable responsibility, while an Implementation describes one way to execute
that responsibility.  User-facing Actions, Workflow composition, and Source
intake are separate contracts and are not authority fields here.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


class CapabilityDotError(ValueError):
    """Raised when a Dot or Implementation cannot pass its deterministic gates."""


# Short aliases make the contract convenient to consume without introducing a
# second exception hierarchy.
DotValidationError = CapabilityDotError
ImplementationValidationError = CapabilityDotError
ExecutionAuthorityError = CapabilityDotError
VersionUpdateError = CapabilityDotError

DOT_RECORD_TYPE = "capability-dot"
DOT_RECORD_VERSION = 1
SCHEMA_URI = "https://fractal.local/schemas/capability-dot.schema.json"
LIFECYCLE_STATES = frozenset({"candidate", "active", "retired"})
TRIAL_STATES = frozenset({"not-run", "pending", "running", "passed", "failed"})
VERIFICATION_STATES = frozenset(
    {"unverified", "in-progress", "verified-staged", "verified", "failed"}
)
SYSTEM_REVIEW_STATES = frozenset({"pending", "in-progress", "completed", "rejected"})
DECISION_STATES = frozenset({"pending", "approved", "rejected", "deferred"})
ACTIVATION_STATES = frozenset({"inactive", "ready", "active", "revoked"})

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_ONE_SENTENCE_END = re.compile(r"[.!?]$")
_WILDCARD_PATTERN = re.compile(r"[*?\[\]{}]|(?:^|[/])\.\.(?:[/]|$)")
_FORBIDDEN_KEYS = frozenset(
    {
        "method_ref",
        "action",
        "action_id",
        "workflow",
        "workflow_id",
        "source_id",
        "action_authority",
        "workflow_authority",
        "source_authority",
    }
)
_PERSISTENCE_TOKENS = frozenset(
    {
        "activate",
        "activation",
        "canonical-write",
        "commit",
        "mutate-project",
        "persist",
        "publish",
        "system-version",
        "write-canonical",
    }
)


def _schema() -> dict[str, Any]:
    """Load and check the packaged Dot schema once per validation call."""

    value = json.loads(
        files("fractal.schemas")
        .joinpath("capability-dot.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(value)
    return value


def _fail(message: str) -> None:
    raise CapabilityDotError(message)


def _as_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    return dict(value)


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-blank string")
    return value


def _validate_id(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    if _ID_PATTERN.fullmatch(text) is None:
        _fail(f"{label} is not a stable Fractal id: {text}")
    return text


def _validate_version(value: Any, label: str) -> str:
    text = _nonblank(value, label)
    if _VERSION_PATTERN.fullmatch(text) is None:
        _fail(f"{label} is not a supported version: {text}")
    return text


def _validate_evidence_ids(value: Any, label: str, *, required: bool = False) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list):
        _fail(f"{label} must be a list of evidence ids")
    if len(value) != len(set(value)):
        _fail(f"{label} contains duplicate evidence ids")
    result = [_nonblank(item, f"{label} item") for item in value]
    return result


def _parse_datetime(value: Any, label: str) -> datetime:
    text = _nonblank(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise CapabilityDotError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        _fail(f"{label} must include a timezone")
    return parsed


def _reject_forbidden_keys(value: Any, *, path: str = "$") -> None:
    """Reject names that would smuggle another contract into a Dot.

    ``source`` is allowed as ordinary provenance data; only authority-bearing
    Source/Action/Workflow names are blocked.  ``method_ref`` is never a Dot
    or Implementation field because a Dot points to a procedure or executable
    target, not to a provider method registry.
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text == "method_ref":
                _fail(f"method_ref is not allowed in a Dot contract ({path})")
            # A Source id may be retained as provenance evidence, but it does
            # not become Dot authority.  Direct Source identity fields remain
            # forbidden everywhere else.
            source_provenance_id = key_text == "source_id" and path.endswith(".provenance")
            if key_text in _FORBIDDEN_KEYS and not source_provenance_id:
                _fail(f"{key_text} cannot own Dot authority ({path})")
            if key_text.endswith("_authority") and key_text in {
                "action_authority",
                "workflow_authority",
                "source_authority",
            }:
                _fail(f"{key_text} cannot own Dot authority ({path})")
            _reject_forbidden_keys(child, path=f"{path}.{key_text}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, path=f"{path}[{index}]")


def _schema_validate(value: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(_schema())
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        _fail(f"Invalid capability Dot at {location}: {error.message}")


def _validate_one_sentence(value: Any) -> str:
    text = _nonblank(value, "responsibility")
    if "\n" in text or "\r" in text or _ONE_SENTENCE_END.search(text.strip()) is None:
        _fail("responsibility must be one complete sentence")
    # This catches a second sentence without trying to decide boundaries from
    # keywords.  Abbreviations and decimal values remain possible in the
    # first sentence; a second terminal mark followed by prose is not.
    if re.search(r"[.!?][ \t]+[A-Z]", text):
        _fail("responsibility must contain one sentence")
    return text


def _validate_ports(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        _fail(f"{label} must contain at least one item")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, str):
            name = _nonblank(item, f"{label}[{index}]")
        elif isinstance(item, Mapping):
            name = _nonblank(item.get("name"), f"{label}[{index}].name")
        else:
            _fail(f"{label}[{index}] must be a string or object")
        if name in seen:
            _fail(f"{label} contains duplicate item: {name}")
        seen.add(name)


def _validate_statements(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(f"{label} must contain at least one explicit statement")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _nonblank(item, f"{label}[{index}]")
        if text not in result:
            result.append(text)
    return result


def _validate_bounded_side_effects(value: Any, label: str) -> list[str]:
    """Validate an exact side-effect scope; an empty scope means read-only."""

    if not isinstance(value, list):
        _fail(f"{label} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _nonblank(item, f"{label}[{index}]")
        if text in result:
            _fail(f"{label} contains duplicate item: {text}")
        result.append(text)
    return result


def _validate_evidence_block(value: Any, label: str) -> dict[str, Any]:
    block = _as_mapping(value, label)
    _validate_evidence_ids(block.get("evidence_ids"), f"{label}.evidence_ids", required=True)
    return block


def _validate_recovery(value: Any, label: str) -> dict[str, Any]:
    block = _as_mapping(value, label)
    _nonblank(block.get("strategy"), f"{label}.strategy")
    _validate_evidence_ids(block.get("evidence_ids"), f"{label}.evidence_ids", required=True)
    return block


def _coherence_hooks(coherence: Mapping[str, Any]) -> list[dict[str, Any]]:
    hooks = coherence.get("boundary_evidence_hooks")
    if hooks is None:
        hooks = coherence.get("split_merge_evidence")
    if not isinstance(hooks, list) or not hooks:
        _fail("coherence requires explicit boundary_evidence_hooks")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(hooks):
        hook = _as_mapping(raw, f"coherence hook {index}")
        hook_id = _validate_id(hook.get("hook_id"), f"coherence hook {index}.hook_id")
        if hook_id in seen:
            _fail(f"coherence hooks contain duplicate id: {hook_id}")
        seen.add(hook_id)
        kind = hook.get("type", hook.get("kind", "boundary"))
        if kind not in {"split", "merge", "boundary", "not-applicable"}:
            _fail(f"coherence hook {hook_id} has an invalid type")
        _nonblank(hook.get("reason"), f"coherence hook {hook_id}.reason")
        _validate_evidence_ids(
            hook.get("evidence_ids"), f"coherence hook {hook_id}.evidence_ids", required=True
        )
        validated.append(hook)
    return validated


def _provider_id(provider: Any, label: str) -> str:
    if isinstance(provider, str):
        return _nonblank(provider, label)
    item = _as_mapping(provider, label)
    return _nonblank(item.get("provider_id"), f"{label}.provider_id")


def _reference_identity(value: Any, label: str) -> str:
    if isinstance(value, str):
        return _nonblank(value, label)
    reference = _as_mapping(value, label)
    for key in ("ref", "target", "entrypoint", "callable", "command", "path"):
        if key in reference:
            return _nonblank(reference[key], f"{label}.{key}")
    # A non-empty structured target is still deterministic when its canonical
    # JSON is stable, but requiring a recognisable locator keeps selection
    # ready and human-auditable.
    _fail(f"{label} requires a deterministic executable locator")


def _implementation_evidence(value: Any, label: str) -> list[str]:
    if isinstance(value, list):
        return _validate_evidence_ids(value, label, required=True)
    block = _as_mapping(value, label)
    return _validate_evidence_ids(block.get("evidence_ids"), f"{label}.evidence_ids", required=True)


def _validate_implementation(raw: Any, *, dot_version: str) -> dict[str, Any]:
    implementation = _as_mapping(raw, "implementation")
    has_procedure = "procedure_ref" in implementation
    has_executable = "executable_target" in implementation
    if has_procedure == has_executable:
        _fail("Implementation requires exactly one of procedure_ref or executable_target")
    _schema_validate_rootless(implementation, "Implementation")
    _validate_id(implementation.get("implementation_id"), "implementation_id")
    _validate_version(implementation.get("version"), "implementation.version")
    _provider_id(implementation.get("provider"), "implementation.provider")

    for field in ("dependencies", "capability_requirements"):
        values = implementation.get(field)
        if not isinstance(values, list):
            _fail(f"implementation.{field} must be a list")
        for index, item in enumerate(values):
            if isinstance(item, str):
                _nonblank(item, f"implementation.{field}[{index}]")
            elif isinstance(item, Mapping):
                if not item:
                    _fail(f"implementation.{field}[{index}] cannot be empty")
            else:
                _fail(f"implementation.{field}[{index}] must be a string or object")

    permissions = _as_mapping(implementation.get("permissions"), "implementation.permissions")
    if not permissions:
        _fail("implementation.permissions cannot be empty")
    for key, value in permissions.items():
        if str(key).lower() in _PERSISTENCE_TOKENS:
            _fail("Implementation permissions cannot include persistence authority")
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and _is_persistence_side_effect(item):
                    _fail("Implementation permissions cannot include persistence side effects")

    locator = _reference_identity(
        implementation.get("procedure_ref")
        if has_procedure
        else implementation.get("executable_target"),
        "implementation executable target",
    )

    provenance = _as_mapping(implementation.get("provenance"), "implementation.provenance")
    if not provenance:
        _fail("implementation.provenance cannot be empty")
    evidence = _implementation_evidence(implementation.get("evidence"), "implementation.evidence")

    compatibility = _as_mapping(
        implementation.get("compatibility"), "implementation.compatibility"
    )
    if not isinstance(compatibility.get("compatible"), bool):
        _fail("implementation.compatibility.compatible must be boolean")
    # A candidate may retain an implementation from an earlier Dot version as
    # a recorded migration input.  Selection and the active gate below still
    # treat it as incompatible until its compatibility evidence names this
    # exact Dot version.

    verification = _as_mapping(
        implementation.get("verification"), "implementation.verification"
    )
    verification_status = verification.get("status")
    if verification_status not in VERIFICATION_STATES:
        _fail("implementation.verification.status is invalid")
    verification_evidence = _validate_evidence_ids(
        verification.get("evidence_ids"),
        "implementation.verification.evidence_ids",
        required=verification_status in {"verified-staged", "verified"},
    )
    _validate_recovery(implementation.get("recovery"), "implementation.recovery")

    # ``locator`` and the evidence checks above are deliberately recomputed by
    # selection/active-gate callers.  Derived private fields must not leak into
    # the returned canonical record because they would make an otherwise
    # unchanged same-version Dot appear materially changed.
    del locator, evidence, verification_evidence
    return copy.deepcopy(implementation)


def _schema_validate_rootless(value: Mapping[str, Any], definition: str) -> None:
    schema = _schema()
    # References in a definition resolve against the schema document, so keep
    # the complete ``$defs`` table when validating a standalone subobject.
    definition_schema = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        **schema["$defs"][definition],
    }
    errors = sorted(
        Draft202012Validator(definition_schema).iter_errors(value),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        _fail(f"Invalid {definition} at {location}: {error.message}")


def _validate_dimension(
    value: Any,
    label: str,
    statuses: frozenset[str],
    *,
    evidence_required_for: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    dimension = _as_mapping(value, label)
    status = dimension.get("status")
    if status not in statuses:
        _fail(f"{label}.status is invalid")
    evidence = _validate_evidence_ids(
        dimension.get("evidence_ids"),
        f"{label}.evidence_ids",
        required=status in evidence_required_for,
    )
    if status == "approved":
        _nonblank(dimension.get("decision_id"), f"{label}.decision_id")
    return {**dimension, "_evidence_ids": evidence}


def _validate_provider_scope(dot: Mapping[str, Any]) -> None:
    direct_provider_fields = {
        field
        for field in ("provider", "provider_id", "provider_name", "vendor")
        if field in dot
    }
    if direct_provider_fields:
        _fail(
            "Provider identity belongs under an Implementation; a provider-specific Dot "
            "needs provider_specific intrinsic_provider_responsibility"
        )
    provider_specific = dot.get("provider_specific")
    if provider_specific is None:
        return
    scope = _as_mapping(provider_specific, "provider_specific")
    _nonblank(scope.get("provider_id"), "provider_specific.provider_id")
    reason = _as_mapping(
        scope.get("intrinsic_provider_responsibility"),
        "provider_specific.intrinsic_provider_responsibility",
    )
    reason_code = reason.get("reason_code", reason.get("reason"))
    _nonblank(reason_code, "intrinsic_provider_responsibility.reason_code")
    evidence_ids = _validate_evidence_ids(
        reason.get("evidence_ids"),
        "intrinsic_provider_responsibility.evidence_ids",
        required=True,
    )
    if not evidence_ids:
        _fail("intrinsic_provider_responsibility requires evidence")


def _activation_authorised(activation: Mapping[str, Any]) -> bool:
    values = [
        activation[key]
        for key in ("authorised", "authorized")
        if key in activation
    ]
    if not values:
        return False
    if any(not isinstance(value, bool) for value in values):
        _fail("activation authorisation flags must be boolean")
    if len(values) == 2 and values[0] != values[1]:
        _fail("activation authorisation spellings disagree")
    return values[0]


def _validate_activation(
    value: Any,
    *,
    dot_version: str,
    lifecycle_state: str,
) -> dict[str, Any]:
    activation = _as_mapping(value, "activation")
    status = activation.get("status")
    if status not in ACTIVATION_STATES:
        _fail("activation.status is invalid")
    authorised = _activation_authorised(activation)
    evidence = activation.get("activation_evidence")
    if evidence is not None:
        if not isinstance(evidence, list):
            _fail("activation.activation_evidence must be a list")
        seen: set[str] = set()
        for index, item in enumerate(evidence):
            record = _as_mapping(item, f"activation.activation_evidence[{index}]")
            evidence_id = _nonblank(
                record.get("evidence_id"),
                f"activation.activation_evidence[{index}].evidence_id",
            )
            if evidence_id in seen:
                _fail("activation evidence ids must be unique")
            seen.add(evidence_id)
            if _validate_version(
                record.get("dot_version"),
                f"activation.activation_evidence[{index}].dot_version",
            ) != dot_version:
                _fail("activation evidence must name the exact Dot version")
            record_authorised = record.get("authorised", record.get("authorized"))
            if record_authorised is not True:
                _fail("activation evidence must be explicitly authorised")
    if status == "active":
        if lifecycle_state != "active":
            _fail("active activation requires an active lifecycle")
        if not authorised:
            _fail("active activation requires explicit authorisation")
        if not isinstance(evidence, list) or not evidence:
            _fail("active activation requires versioned activation evidence")
        activated_version = activation.get("activated_version")
        if activated_version is not None and _validate_version(
            activated_version, "activation.activated_version"
        ) != dot_version:
            _fail("activation.activated_version must equal Dot version")
    if status == "active" and activation.get("evidence_ids") is not None:
        _validate_evidence_ids(activation["evidence_ids"], "activation.evidence_ids")
    return {**activation, "_authorised": authorised}


def _is_persistence_side_effect(value: str) -> bool:
    normalised = value.strip().lower().replace("_", "-").replace(" ", "-")
    return normalised in _PERSISTENCE_TOKENS or any(
        token in normalised for token in ("persist", "canonical-write", "activate", "publish")
    )


def _validate_active_gate(dot: Mapping[str, Any], implementations: list[dict[str, Any]]) -> None:
    lifecycle = _as_mapping(dot["lifecycle"], "lifecycle")
    if not _validate_evidence_ids(
        lifecycle.get("transition_evidence"), "lifecycle.transition_evidence", required=True
    ):
        _fail("active lifecycle requires transition evidence")
    if not _validate_evidence_ids(
        dot["evidence"].get("evidence_ids"), "evidence.evidence_ids", required=True
    ):
        _fail("active Dot requires evidence")

    trial = dot["trial"]
    if trial.get("status") != "passed":
        _fail("active Dot requires a passed trial")
    _validate_evidence_ids(trial.get("evidence_ids"), "trial.evidence_ids", required=True)

    verification = dot["verification"]
    if verification.get("status") != "verified":
        _fail("active Dot requires verified Dot-level evidence")
    _validate_evidence_ids(
        verification.get("evidence_ids"), "verification.evidence_ids", required=True
    )
    verified_ids = verification.get("verified_implementation_ids")
    if not isinstance(verified_ids, list) or not verified_ids:
        _fail("active Dot requires at least one verified Implementation")
    by_id = {item["implementation_id"]: item for item in implementations}
    unknown = set(verified_ids).difference(by_id)
    if unknown:
        _fail(f"verification names unknown Implementations: {sorted(unknown)}")
    compatible_ids = verification.get("compatible_implementation_ids")
    if compatible_ids is not None:
        if not isinstance(compatible_ids, list) or not set(compatible_ids):
            _fail("compatible_implementation_ids cannot be empty when present")
        if not set(compatible_ids) <= set(verified_ids):
            _fail("compatible Implementations must be verified Implementations")
    eligible = []
    for implementation_id in verified_ids:
        item = by_id[implementation_id]
        selection = _implementation_selection(item, dot["version"])
        if selection["verified"] and selection["compatible"]:
            eligible.append(implementation_id)
    if not eligible:
        _fail("active Dot requires a verified executable compatible Implementation")

    system_review = dot["system_review"]
    if system_review.get("status") != "completed":
        _fail("active Dot requires completed System Review")
    _validate_evidence_ids(
        system_review.get("evidence_ids"), "system_review.evidence_ids", required=True
    )
    decision = dot["human_decision"]
    if decision.get("status") != "approved":
        _fail("active Dot requires an approved human decision")
    _nonblank(decision.get("decision_id"), "human_decision.decision_id")
    _nonblank(decision.get("decided_by"), "human_decision.decided_by")
    _validate_evidence_ids(
        decision.get("evidence_ids"), "human_decision.evidence_ids", required=True
    )
    activation = _validate_activation(
        dot["activation"], dot_version=dot["version"], lifecycle_state="active"
    )
    if activation["status"] != "active" or activation["_authorised"] is not True:
        _fail("active Dot requires authorised active activation evidence")


def _implementation_selection(
    implementation: Mapping[str, Any], dot_version: str
) -> dict[str, Any]:
    """Return derived fields used only for deterministic selection/gates."""

    has_procedure = "procedure_ref" in implementation
    reference = implementation.get("procedure_ref") if has_procedure else implementation.get(
        "executable_target"
    )
    compatibility = _as_mapping(implementation.get("compatibility"), "implementation.compatibility")
    verification = _as_mapping(implementation.get("verification"), "implementation.verification")
    return {
        "provider_id": _provider_id(implementation.get("provider"), "implementation.provider"),
        "executable_locator": _reference_identity(reference, "implementation executable target"),
        "evidence_ids": _implementation_evidence(
            implementation.get("evidence"), "implementation.evidence"
        ),
        "verification_evidence_ids": _validate_evidence_ids(
            verification.get("evidence_ids"),
            "implementation.verification.evidence_ids",
            required=verification.get("status") == "verified",
        ),
        "recovery_evidence_ids": _validate_recovery(
            implementation.get("recovery"), "implementation.recovery"
        )["evidence_ids"],
        "compatible": compatibility.get("compatible") is True
        and _implementation_supports_version(compatibility, dot_version),
        "verified": verification.get("status") == "verified",
        "dot_version": dot_version,
    }


def _implementation_supports_version(
    compatibility: Mapping[str, Any], dot_version: str
) -> bool:
    target_versions: list[str] = []
    if compatibility.get("dot_version") is not None:
        target_versions.append(
            _validate_version(compatibility["dot_version"], "compatibility.dot_version")
        )
    if compatibility.get("dot_versions") is not None:
        values = compatibility["dot_versions"]
        if not isinstance(values, list):
            _fail("implementation.compatibility.dot_versions must be a list")
        target_versions.extend(
            _validate_version(item, "compatibility.dot_versions item") for item in values
        )
    return not target_versions or dot_version in target_versions


def validate_capability_dot(
    dot: Mapping[str, Any],
    *,
    require_active: bool | None = None,
) -> dict[str, Any]:
    """Validate one Dot and return an isolated, selection-ready copy.

    ``require_active`` is an optional caller gate.  ``None`` validates either
    candidate or active state; active state still has to pass every active gate.
    """

    value = _as_mapping(dot, "Dot")
    _reject_forbidden_keys(value)
    _validate_provider_scope(value)
    _schema_validate(value)
    if (
        value.get("record_type") != DOT_RECORD_TYPE
        or value.get("record_version") != DOT_RECORD_VERSION
    ):
        _fail("Dot record type or version is not canonical")
    _validate_id(value.get("dot_id"), "dot_id")
    dot_version = _validate_version(value.get("version"), "version")
    _nonblank(value.get("human_name"), "human_name")
    _validate_one_sentence(value.get("responsibility"))
    _validate_ports(value.get("inputs"), "inputs")
    _validate_ports(value.get("outputs"), "outputs")
    _validate_statements(value.get("preconditions"), "preconditions")
    side_effects = _validate_statements(value.get("side_effects"), "side_effects")
    for side_effect in side_effects:
        if side_effect.strip().lower() in {"*", "any", "unbounded"}:
            _fail("side_effects must be explicit and bounded")
    lifecycle = _as_mapping(value.get("lifecycle"), "lifecycle")
    lifecycle_state = lifecycle.get("status", lifecycle.get("state"))
    if (
        lifecycle.get("status") is not None
        and lifecycle.get("state") is not None
        and lifecycle["status"] != lifecycle["state"]
    ):
        _fail("lifecycle status and state disagree")
    if lifecycle_state not in LIFECYCLE_STATES:
        _fail("lifecycle.state is invalid")
    dimensions = {
        "candidate": lifecycle.get("candidate"),
        "active": lifecycle.get("active"),
        "active_surface": lifecycle.get("active_surface"),
    }
    present_dimensions = [value is not None for value in dimensions.values()]
    if any(present_dimensions):
        if not all(present_dimensions) or any(
            not isinstance(item, bool) for item in dimensions.values()
        ):
            _fail("lifecycle candidate/active/active_surface dimensions are incomplete")
        expected_dimensions = {
            "candidate": lifecycle_state == "candidate",
            "active": lifecycle_state == "active",
            "active_surface": lifecycle_state == "active",
        }
        if dimensions != expected_dimensions:
            _fail("lifecycle candidate/active/active_surface dimensions do not match state")
    if lifecycle.get("transition_evidence") is not None:
        _validate_evidence_ids(lifecycle["transition_evidence"], "lifecycle.transition_evidence")
    _validate_evidence_block(value.get("evidence"), "evidence")
    _validate_recovery(value.get("recovery"), "recovery")
    coherence = _as_mapping(value.get("coherence"), "coherence")
    if coherence.get("coherent_responsibility") is not True:
        _fail("coherence must affirm one coherent responsibility")
    _nonblank(coherence.get("reuse_rationale"), "coherence.reuse_rationale")
    _coherence_hooks(coherence)
    implementations: list[dict[str, Any]] = []
    seen_implementations: set[str] = set()
    for raw in value.get("implementations", []):
        implementation = _validate_implementation(raw, dot_version=dot_version)
        implementation_id = implementation["implementation_id"]
        if implementation_id in seen_implementations:
            _fail(f"Dot has duplicate implementation_id: {implementation_id}")
        seen_implementations.add(implementation_id)
        implementations.append(implementation)

    trial = _validate_dimension(value.get("trial"), "trial", TRIAL_STATES)
    verification = _validate_dimension(
        value.get("verification"), "verification", VERIFICATION_STATES
    )
    system_review = _validate_dimension(
        value.get("system_review"), "system_review", SYSTEM_REVIEW_STATES
    )
    human_decision = _validate_dimension(
        value.get("human_decision"), "human_decision", DECISION_STATES
    )
    activation = _validate_activation(
        value.get("activation"), dot_version=dot_version, lifecycle_state=lifecycle_state
    )
    if lifecycle_state == "candidate" and activation["status"] == "active":
        _fail("candidate Dot cannot claim active activation")
    if lifecycle_state == "active":
        _validate_active_gate(
            {
                **value,
                "trial": trial,
                "verification": verification,
                "system_review": system_review,
                "human_decision": human_decision,
                "activation": activation,
                "implementations": implementations,
            },
            implementations,
        )
    if require_active is True and lifecycle_state != "active":
        _fail("an active Dot is required")
    if require_active is False and lifecycle_state == "active":
        _fail("a candidate or retired Dot is required")

    result = copy.deepcopy(value)
    result["implementations"] = implementations
    return result


def validate_dot(dot: Mapping[str, Any], *, require_active: bool | None = None) -> dict[str, Any]:
    """Compatibility alias for :func:`validate_capability_dot`."""

    return validate_capability_dot(dot, require_active=require_active)


def validate_implementation(
    implementation: Mapping[str, Any], *, dot_version: str = "0.0.0"
) -> dict[str, Any]:
    """Validate one standalone Implementation contract."""

    _reject_forbidden_keys(implementation)
    _validate_version(dot_version, "dot_version")
    return _validate_implementation(implementation, dot_version=dot_version)


def _authority_schema_validate(authority: Mapping[str, Any]) -> None:
    schema = _schema()
    definition_schema = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        **schema["$defs"]["ExecutionAuthority"],
    }
    errors = sorted(
        Draft202012Validator(definition_schema).iter_errors(authority),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        _fail(f"Invalid candidate execution authority at {location}: {error.message}")


def validate_candidate_execution_authority(
    authority: Mapping[str, Any],
    *,
    project_id: str | None = None,
    project_revision: int | None = None,
    task_id: str | None = None,
    now: datetime | None = None,
    allowed_side_effects: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate exact, expiring execution scope without granting persistence.

    The optional expected values make the same receipt usable for a caller's
    exact Project/task check.  This function is pure: it neither consumes an
    authority nor writes candidate, Project, or System state.
    """

    value = _as_mapping(authority, "candidate execution authority")
    _reject_forbidden_keys(value)
    _authority_schema_validate(value)
    _nonblank(value.get("project_id"), "authority.project_id")
    if isinstance(value.get("project_revision"), bool) or not isinstance(
        value.get("project_revision"), int
    ):
        _fail("authority.project_revision must be a non-negative integer")
    if value["project_revision"] < 0:
        _fail("authority.project_revision must be a non-negative integer")
    _nonblank(value.get("task_id"), "authority.task_id")
    if value.get("persistence_state_change") is not False:
        _fail("candidate execution authority cannot change persistence state")
    effects = _validate_bounded_side_effects(
        value.get("allowed_side_effects"), "authority.allowed_side_effects"
    )
    for effect in effects:
        if _WILDCARD_PATTERN.search(effect) or effect.strip().lower() in {"*", "any", "unbounded"}:
            _fail("candidate allowed_side_effects must be deterministic and bounded")
        if _is_persistence_side_effect(effect):
            _fail("candidate execution authority cannot grant persistence side effects")
    if allowed_side_effects is not None:
        expected_effects = list(allowed_side_effects)
        if effects != expected_effects:
            _fail("candidate allowed_side_effects do not match the exact requested scope")
    expiry = _parse_datetime(value.get("expires_at"), "authority.expires_at")
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None:
        _fail("authority validation time must include a timezone")
    if observed_now >= expiry:
        _fail("candidate execution authority has expired")
    if project_id is not None and value["project_id"] != project_id:
        _fail("candidate execution authority names a different Project")
    if project_revision is not None and value["project_revision"] != project_revision:
        _fail("candidate execution authority names a different Project revision")
    if task_id is not None and value["task_id"] != task_id:
        _fail("candidate execution authority names a different task")
    return copy.deepcopy(value)


def validate_candidate_execution(
    dot: Mapping[str, Any],
    authority: Mapping[str, Any],
    *,
    project_id: str,
    project_revision: int,
    task_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a candidate Dot plus a bounded execution receipt.

    The return value is a detached execution plan.  In particular, it never
    changes the candidate's lifecycle, trial, verification, decision, or
    activation dimensions.
    """

    validated_dot = validate_capability_dot(dot, require_active=False)
    if validated_dot["lifecycle"]["state"] != "candidate":
        _fail("candidate execution requires a candidate Dot")
    validated_authority = validate_candidate_execution_authority(
        authority,
        project_id=project_id,
        project_revision=project_revision,
        task_id=task_id,
        now=now,
    )
    if validated_authority.get("dot_id") not in {None, validated_dot["dot_id"]}:
        _fail("candidate execution authority names a different Dot")
    if validated_authority.get("dot_version") not in {None, validated_dot["version"]}:
        _fail("candidate execution authority names a different Dot version")
    return {
        "dot_id": validated_dot["dot_id"],
        "dot_version": validated_dot["version"],
        "project_id": project_id,
        "project_revision": project_revision,
        "task_id": task_id,
        "allowed_side_effects": list(validated_authority["allowed_side_effects"]),
        "expires_at": validated_authority["expires_at"],
        "persistence_state_change": False,
        "implementation_ids": [
            item["implementation_id"] for item in validated_dot["implementations"]
        ],
    }


def _version_tuple(version: str) -> tuple[int, int, int, int, str]:
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        _fail(f"Invalid version: {version}")
    core, _, suffix = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    # A final release sorts after its pre-releases for the same numeric core.
    return major, minor, patch, 0 if suffix else 1, suffix


def validate_version_update(
    previous: Mapping[str, Any], updated: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate immutable Dot versioning and material-change lineage.

    A same-version update must be byte-for-byte equivalent after canonical
    JSON ordering.  Any changed version must be newer, candidate-only, and
    carry predecessor, reason, evidence, and recovery lineage.  No active Dot
    is mutated in place and no persistence operation is performed here.
    """

    old = validate_capability_dot(previous)
    new = validate_capability_dot(updated)
    if old["dot_id"] != new["dot_id"]:
        _fail("Dot version updates must retain dot_id")
    old_version = _validate_version(old["version"], "previous.version")
    new_version = _validate_version(new["version"], "updated.version")
    if old_version == new_version:
        if _canonical_json(old) != _canonical_json(new):
            _fail("material Dot changes require a new version")
        return new
    if _version_tuple(new_version) <= _version_tuple(old_version):
        _fail("Dot version update must be strictly newer")
    if new["lifecycle"]["state"] != "candidate":
        _fail("a material Dot version update must create a candidate first")
    lineage = _as_mapping(new.get("lineage"), "lineage")
    if lineage.get("predecessor_dot_id") != old["dot_id"]:
        _fail("lineage predecessor_dot_id does not match the previous Dot")
    if lineage.get("predecessor_version") != old_version:
        _fail("lineage predecessor_version does not match the previous Dot")
    if lineage.get("change_type") not in {"material", "split", "merge", "recovery"}:
        _fail("material Dot update requires a material lineage change_type")
    _nonblank(lineage.get("reason"), "lineage.reason")
    _validate_evidence_ids(lineage.get("evidence_ids"), "lineage.evidence_ids", required=True)
    _validate_evidence_ids(
        lineage.get("recovery_evidence_ids"),
        "lineage.recovery_evidence_ids",
        required=True,
    )
    recovery = _as_mapping(new["recovery"], "recovery")
    if not recovery.get("evidence_ids"):
        _fail("material Dot update requires recovery evidence")
    return new


def validate_dot_version_update(
    previous: Mapping[str, Any], updated: Mapping[str, Any]
) -> dict[str, Any]:
    """Compatibility alias for :func:`validate_version_update`."""

    return validate_version_update(previous, updated)


def select_compatible_implementation(
    dot: Mapping[str, Any],
    *,
    provider: str | None = None,
    required_capabilities: Sequence[str] = (),
    require_verified: bool = True,
) -> dict[str, Any]:
    """Select the first deterministic implementation ready for execution."""

    validated = validate_capability_dot(dot)
    required = {_nonblank(item, "required_capabilities item") for item in required_capabilities}
    choices = []
    for implementation in validated["implementations"]:
        selection = _implementation_selection(implementation, validated["version"])
        if require_verified and not selection["verified"]:
            continue
        if not selection["compatible"]:
            continue
        if provider is not None and selection["provider_id"] != provider:
            continue
        capabilities = implementation.get("capability_requirements", [])
        capability_ids = {
            item if isinstance(item, str) else str(item.get("capability_id", item.get("name", "")))
            for item in capabilities
        }
        if not required <= capability_ids:
            continue
        choices.append(implementation)
    if not choices:
        raise CapabilityDotError(
            "No verified compatible executable Implementation is selection-ready"
        )
    choices.sort(key=lambda item: (item["implementation_id"], _version_tuple(item["version"])))
    selected = copy.deepcopy(choices[0])
    selected.pop("_selection", None)
    return selected


def select_implementation(
    dot: Mapping[str, Any],
    *,
    provider: str | None = None,
    required_capabilities: Sequence[str] = (),
    require_verified: bool = True,
) -> dict[str, Any]:
    """Compatibility alias for :func:`select_compatible_implementation`."""

    return select_compatible_implementation(
        dot,
        provider=provider,
        required_capabilities=required_capabilities,
        require_verified=require_verified,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ACTIVATION_STATES",
    "CapabilityDotError",
    "DECISION_STATES",
    "DOT_RECORD_TYPE",
    "DOT_RECORD_VERSION",
    "DotValidationError",
    "ExecutionAuthorityError",
    "ImplementationValidationError",
    "LIFECYCLE_STATES",
    "SCHEMA_URI",
    "SYSTEM_REVIEW_STATES",
    "TRIAL_STATES",
    "VERIFICATION_STATES",
    "VersionUpdateError",
    "select_compatible_implementation",
    "select_implementation",
    "validate_candidate_execution",
    "validate_candidate_execution_authority",
    "validate_capability_dot",
    "validate_dot",
    "validate_dot_version_update",
    "validate_implementation",
    "validate_version_update",
]
