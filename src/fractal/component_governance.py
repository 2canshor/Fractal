"""Universal Fractal component registration, projection, and drift checks."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fractal.review_contracts import ReviewContractError, validate_claim_receipt


class ComponentGovernanceError(RuntimeError):
    """Raised when component state is incomplete, ambiguous, or drifting."""


ACTIVE_DISPOSITIONS = {
    "fractal-owned-canonical",
    "approved-external-managed",
    "platform-managed-adapter",
}

APPLE_COMPONENT_PRINCIPLES = (
    "purpose",
    "agency",
    "responsibility",
    "familiarity",
    "flexibility",
    "simplicity",
    "craft",
    "delight",
)

COMPONENT_CONTINUOUS_IMPROVEMENT_ROUTE = (
    "component-governance",
    "capability-check",
    "environment-adapters",
    "system-review",
    "continuous-improvement",
)

_AUDIT_EXECUTION_STATES = {
    "available-unverified": "available-unverified",
    "verified-staged": "staged",
    "verified-live": "live",
    "unavailable": "unavailable",
}

_CREDENTIAL_VALUE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
        r"\bgh[opurs]_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}",
    )
)

_DESTRUCTIVE_RECOVERY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\brm\s+-[^\n]*r[^\n]*f\b",
        r"\bsudo\s+rm\b",
        r"\b(?:permanently\s+delete|delete\s+permanently)\b",
        r"\b(?:drop\s+(?:database|schema|table)|truncate\s+table)\b",
        r"\b(?:destroy|purge)\s+(?:all|the|this)\b",
        r"\bno\s+(?:restore|recovery)\b",
        r"\birrecoverable\b",
    )
)


def is_transient_component_path(path: Path) -> bool:
    """Return whether a path is generated local clutter rather than component source."""
    return "__pycache__" in path.parts or path.name == ".DS_Store" or path.suffix == ".pyc"


def tree_sha256(root: Path) -> str:
    """Hash one component tree deterministically without following symlinks."""
    root = Path(root)
    digest = hashlib.sha256()
    if root.is_file() and not root.is_symlink():
        digest.update(root.name.encode())
        digest.update(b"\0")
        digest.update(root.read_bytes())
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        if is_transient_component_path(path.relative_to(root)):
            continue
        if path.is_symlink():
            raise ComponentGovernanceError(f"Component source contains a symlink: {path}")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def load_component_registry(path: Path) -> dict[str, Any]:
    """Load the canonical registry and enforce governance invariants."""
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("record_version") == 2:
        registry = _migrate_component_registry_v2(registry)
    schema = json.loads(
        files("fractal.schemas")
        .joinpath("component-registry.schema.json")
        .read_text(encoding="utf-8")
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(registry),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path)
        raise ComponentGovernanceError(
            f"Invalid component registry at {location}: {errors[0].message}"
        )
    components = registry["components"]
    identifiers = [item["component_id"] for item in components]
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        raise ComponentGovernanceError("Component ids must be unique and sorted")
    for component in components:
        _validate_component_invariants(component)
    by_id = {item["component_id"]: item for item in components}
    for component in components:
        if not component["status"]["active"]:
            continue
        for dependency_id in component["dependencies"]:
            dependency = by_id.get(dependency_id)
            if dependency is None or not dependency["status"]["active"]:
                raise ComponentGovernanceError(
                    "Active component has an unavailable dependency: "
                    f"{component['component_id']} -> {dependency_id}"
                )
    return registry


def _migrate_component_registry_v2(registry: dict[str, Any]) -> dict[str, Any]:
    """Classify a v2 registry in memory without promoting any item to a user job."""
    migrated = json.loads(json.dumps(registry))
    migrated["record_version"] = 3
    for component in migrated.get("components", []):
        active = component.get("status", {}).get("active") is True
        if not active:
            audience = "technical-quarantine"
        elif component.get("disposition") == "platform-managed-adapter":
            audience = "platform-owned-external"
        elif component.get("kind") in {"tool", "mcp", "app", "plugin", "hook"}:
            audience = "tool-prerequisite"
        else:
            audience = "supporting-capability"
        mode = component.get("trigger", {}).get("mode")
        component["surface_audience"] = audience
        component["invocation"] = {
            "automatic_matching": active and mode == "automatic",
            "explicit_invocation": active and mode in {"automatic", "explicit", "platform"},
        }
        component["job_contract"] = None
        component["status"]["claim_receipt"] = None
    return migrated


def _validate_component_invariants(component: dict[str, Any]) -> None:
    naming = component["naming"]
    if (
        naming["registry_key_status"] != "passed"
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", component["component_id"]) is None
    ):
        raise ComponentGovernanceError(
            f"Fractal-controlled id did not pass Naming System: {component['component_id']}"
        )
    if component.get("external_identifier") is None:
        if (
            naming["external_identifier_status"] != "not-applicable"
            or naming["exemption_reason"] is not None
        ):
            raise ComponentGovernanceError(
                f"Internal component has an external-name exemption: {component['component_id']}"
            )
    elif (
        naming["external_identifier_status"] != "exempt-external" or not naming["exemption_reason"]
    ):
        raise ComponentGovernanceError(
            "External name exemption needs an owner or protocol reason: "
            f"{component['component_id']}"
        )
    active = component["disposition"] in ACTIVE_DISPOSITIONS
    if component["status"]["active"] != active:
        raise ComponentGovernanceError(
            f"Disposition and active state disagree: {component['component_id']}"
        )
    if active and not component["status"]["discoverable"]:
        raise ComponentGovernanceError(
            f"An active component must be discoverable: {component['component_id']}"
        )
    if component["disposition"] == "inactive-quarantined" and (
        component["projection"]["mode"] != "quarantine"
    ):
        raise ComponentGovernanceError(
            f"Quarantined component lacks a quarantine projection: {component['component_id']}"
        )
    if component["component_id"] in component["dependencies"]:
        raise ComponentGovernanceError(
            f"Component cannot depend on itself: {component['component_id']}"
        )
    audience = component["surface_audience"]
    contract = component["job_contract"]
    invocation = component["invocation"]
    if audience == "user-job":
        if component["kind"] != "skill" or not isinstance(contract, dict):
            raise ComponentGovernanceError(
                f"A user job requires one Skill job contract: {component['component_id']}"
            )
        if contract["action"] != component["component_id"]:
            raise ComponentGovernanceError(
                f"User job action and component id disagree: {component['component_id']}"
            )
        if not invocation["explicit_invocation"]:
            raise ComponentGovernanceError(
                f"A user job must remain commandable: {component['component_id']}"
            )
    elif contract is not None:
        raise ComponentGovernanceError(
            f"Only a user job may define a job contract: {component['component_id']}"
        )
    if audience == "technical-quarantine" and component["status"]["active"]:
        raise ComponentGovernanceError(
            f"Technical quarantine cannot be active: {component['component_id']}"
        )
    if not component["status"]["active"] and any(invocation.values()):
        raise ComponentGovernanceError(
            f"An inactive component cannot be invocable: {component['component_id']}"
        )
    execution = component["status"]["execution"]
    claim_receipt = component["status"]["claim_receipt"]
    if execution == "verified-live":
        if not isinstance(claim_receipt, dict):
            raise ComponentGovernanceError(
                f"Verified-live requires a Claim Gate receipt: {component['component_id']}"
            )
        try:
            validated_claim = validate_claim_receipt(claim_receipt)
        except ReviewContractError as error:
            raise ComponentGovernanceError(
                f"Invalid verified-live Claim Gate receipt: {component['component_id']}"
            ) from error
        if (
            validated_claim["subject_id"] != component["component_id"]
            or validated_claim["asserted_state"] != "verified-live"
            or validated_claim["scope"]["platform"] not in component["platforms"]
        ):
            raise ComponentGovernanceError(
                f"Verified-live Claim Gate scope mismatch: {component['component_id']}"
            )
    elif claim_receipt is not None:
        raise ComponentGovernanceError(
            "A non-live component cannot carry a live Claim Gate receipt: "
            f"{component['component_id']}"
        )


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(_non_empty_text(item) for item in value)
        and len(value) == len(set(value))
    )


def _credential_paths(value: Any, path: str = "$") -> list[str]:
    """Return only paths to likely credential values, never the values themselves."""
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key in sorted(value, key=str):
            paths.extend(_credential_paths(value[key], f"{path}.{key}"))
        return paths
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            paths.extend(_credential_paths(item, f"{path}[{index}]"))
        return paths
    if isinstance(value, str) and any(
        pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS
    ):
        return [path]
    return []


def _has_destructive_recovery_instruction(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    return any(pattern.search(value) for pattern in _DESTRUCTIVE_RECOVERY_PATTERNS)


def _component_apple_continuous_improvement_result(
    component: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    component_id = component.get("component_id")
    result_id = (
        component_id
        if _non_empty_text(component_id)
        else f"missing-component-id-at-index-{index}"
    )
    findings: list[str] = []

    if not _non_empty_text(component_id):
        findings.append("missing-component-id")
    human_name = component.get("human_name")
    if not _non_empty_text(human_name):
        findings.append("missing-human-name")

    audience = component.get("surface_audience")
    purpose_kind = "user-job" if audience == "user-job" else "supporting-purpose"
    trigger = component.get("trigger")
    trigger_description = trigger.get("description") if isinstance(trigger, Mapping) else None
    contract = component.get("job_contract")
    if purpose_kind == "user-job":
        purpose_description = contract.get("outcome") if isinstance(contract, Mapping) else None
        if not isinstance(contract, Mapping) or not _non_empty_text(purpose_description):
            findings.append("missing-user-job-purpose")
        elif contract.get("action") != component_id:
            findings.append("user-job-action-mismatch")
        purpose_basis = "job-contract"
    else:
        purpose_description = trigger_description
        if contract is not None:
            findings.append("supporting-purpose-has-user-job-contract")
        if not _non_empty_text(purpose_description):
            findings.append("missing-supporting-trigger-purpose")
        purpose_basis = "trigger"

    permissions = component.get("permissions")
    operations = permissions.get("operations") if isinstance(permissions, Mapping) else None
    execution = (
        component.get("status", {}).get("execution")
        if isinstance(component.get("status"), Mapping)
        else None
    )
    inactive_no_execution = (
        execution == "unavailable"
        and isinstance(permissions, Mapping)
        and permissions.get("profile") == "inactive-no-execution"
        and operations == []
    )
    if _string_list(operations):
        effective_operations = list(operations)
    elif inactive_no_execution:
        effective_operations = ["no-execution"]
    else:
        effective_operations = []
        findings.append("missing-or-ambiguous-operations")

    owner = component.get("owner")
    source = component.get("source")
    owner_complete = (
        isinstance(owner, Mapping)
        and _non_empty_text(owner.get("owner_id"))
        and isinstance(owner.get("source_controlled_by_owner"), bool)
    )
    source_complete = (
        isinstance(source, Mapping)
        and _non_empty_text(source.get("kind"))
        and _non_empty_text(source.get("locator"))
        and _non_empty_text(source.get("version"))
    )
    if not owner_complete:
        findings.append("missing-owner-provenance")
    source_hash_complete = False
    if not source_complete:
        findings.append("missing-source-provenance")
    elif source.get("content_sha256") is None:
        if source.get("kind") not in {"platform", "protocol"}:
            findings.append("missing-source-content-hash")
        else:
            source_hash_complete = True
    elif re.fullmatch(r"[0-9a-f]{64}", str(source.get("content_sha256"))) is None:
        findings.append("invalid-source-content-hash")
    else:
        source_hash_complete = True
    source_provenance_complete = source_complete and source_hash_complete

    permission_complete = (
        isinstance(permissions, Mapping)
        and _non_empty_text(permissions.get("profile"))
        and bool(effective_operations)
        and _non_empty_text(permissions.get("secret_boundary"))
    )
    if not permission_complete:
        findings.append("missing-permission-or-secret-boundary")
    credential_paths = _credential_paths(component)
    if credential_paths:
        findings.append("credential-content-present")

    status = component.get("status")
    execution_claim = _AUDIT_EXECUTION_STATES.get(execution)
    if execution_claim is None:
        findings.append("unknown-or-missing-execution-state")
    registered = _non_empty_text(component_id)
    active = status.get("active") if isinstance(status, Mapping) else None
    discoverable = status.get("discoverable") if isinstance(status, Mapping) else None
    claim_receipt = status.get("claim_receipt") if isinstance(status, Mapping) else None
    if not isinstance(active, bool) or not isinstance(discoverable, bool):
        findings.append("missing-registration-state")
    if execution in {"available-unverified", "verified-staged"} and (
        active is not True or claim_receipt is not None
    ):
        findings.append("misleading-non-live-execution-claim")
    if execution == "verified-live" and (
        active is not True or not isinstance(claim_receipt, Mapping)
    ):
        findings.append("missing-live-claim-receipt")
    if execution == "unavailable" and (
        active is not False or discoverable is not False or claim_receipt is not None
    ):
        findings.append("misleading-unavailable-execution-claim")
    for claim_key in ("success", "successful", "completed", "executed", "live"):
        if (
            isinstance(status, Mapping)
            and status.get(claim_key) is True
            and execution != "verified-live"
        ):
            findings.append("misleading-active-success-claim")
            break

    overlap = component.get("overlap")
    overlap_complete = (
        isinstance(overlap, Mapping)
        and _non_empty_text(overlap.get("decision"))
        and _string_list(overlap.get("with"), allow_empty=True)
        and component_id not in overlap.get("with", [])
    )
    if not overlap_complete:
        findings.append("missing-or-ambiguous-overlap-decision")

    platforms = component.get("platforms")
    projection = component.get("projection")
    platform_projection_complete = (
        _string_list(platforms)
        and isinstance(projection, Mapping)
        and _non_empty_text(projection.get("mode"))
        and _non_empty_text(projection.get("target"))
    )
    if not platform_projection_complete:
        findings.append("missing-platform-or-projection")

    recovery = component.get("recovery")
    removal = recovery.get("removal") if isinstance(recovery, Mapping) else None
    restore = recovery.get("restore") if isinstance(recovery, Mapping) else None
    recovery_complete = _non_empty_text(removal) and _non_empty_text(restore)
    if not recovery_complete:
        findings.append("missing-removal-or-restore-path")
    elif _has_destructive_recovery_instruction(removal) or _has_destructive_recovery_instruction(
        restore
    ):
        findings.append("destructive-or-irrecoverable-recovery")
    recovery_safe = (
        recovery_complete and "destructive-or-irrecoverable-recovery" not in findings
    )

    status_evidence = status.get("evidence_ids") if isinstance(status, Mapping) else None
    verification_evidence = component.get("verification_evidence")
    evidence_complete = (
        _string_list(status_evidence)
        and _string_list(verification_evidence)
        and set(status_evidence) == set(verification_evidence)
    )
    if not evidence_complete:
        findings.append("missing-or-inconsistent-evidence-ids")
    evidence_ids = sorted(set(status_evidence or []) | set(verification_evidence or []))

    purpose_complete = (
        _non_empty_text(human_name)
        and _non_empty_text(purpose_description)
        and bool(effective_operations)
    )
    execution_honest = not any(
        finding
        in {
            "unknown-or-missing-execution-state",
            "missing-registration-state",
            "misleading-non-live-execution-claim",
            "missing-live-claim-receipt",
            "misleading-unavailable-execution-claim",
            "misleading-active-success-claim",
        }
        for finding in findings
    )
    delight_proxy = (
        purpose_complete
        and permission_complete
        and execution_honest
        and evidence_complete
        and recovery_safe
    )
    human_acceptance = "pending" if purpose_kind == "user-job" else "not-applicable"
    principle_checks: dict[str, bool | str] = {
        "purpose": purpose_complete,
        "agency": permission_complete and recovery_safe and execution_honest,
        "responsibility": owner_complete
        and source_provenance_complete
        and permission_complete
        and not credential_paths,
        "familiarity": overlap_complete,
        "flexibility": platform_projection_complete,
        "simplicity": purpose_complete and purpose_kind in {"user-job", "supporting-purpose"},
        "craft": evidence_complete and recovery_safe and execution_honest,
        "delight": (
            "proxy-observed-human-pending"
            if human_acceptance == "pending" and delight_proxy
            else "proxy-failed-human-pending"
            if human_acceptance == "pending"
            else "not-directly-user-visible"
        ),
    }

    return {
        "component_id": result_id,
        "purpose": {
            "kind": purpose_kind,
            "basis": purpose_basis,
            "human_name_present": _non_empty_text(human_name),
            "description_present": _non_empty_text(purpose_description),
            "effective_operations": effective_operations,
        },
        "execution": {
            "registration_state": "registered" if registered else "missing",
            "execution_state": execution_claim or "unknown",
            "active": active if isinstance(active, bool) else None,
            "successful_execution_claimed": execution == "verified-live",
            "available_unverified_is_success": False,
        },
        "checks": {
            "owner_and_source_provenance": owner_complete and source_provenance_complete,
            "permissions_and_secret_boundary": permission_complete and not credential_paths,
            "honest_execution_state": execution_honest,
            "overlap_decision": overlap_complete,
            "platform_and_projection": platform_projection_complete,
            "safe_removal_and_restore": recovery_safe,
            "evidence_ids": evidence_complete,
        },
        "apple_principle_checks": principle_checks,
        "delight": {
            "observable_proxy": delight_proxy,
            "human_qualitative_acceptance": human_acceptance,
            "claimed_pass": False,
        },
        "continuous_improvement_route": list(COMPONENT_CONTINUOUS_IMPROVEMENT_ROUTE),
        "evidence_ids": evidence_ids,
        "credential_finding_paths": credential_paths,
        "findings": sorted(set(findings)),
        "deterministic_result": "pass" if not findings else "fail",
    }


def audit_component_apple_continuous_improvement_alignment(
    registry: Mapping[str, Any],
    *,
    registry_sha256: str,
) -> dict[str, Any]:
    """Audit every registered component against Apple-aligned governance proxies.

    This audit is deliberately narrower than human experience acceptance. It verifies
    deterministic component metadata, execution honesty, privacy boundaries, recovery,
    and the one existing path into System Review and Continuous Improvement. Directly
    user-visible Delight remains pending until a human evaluates the real experience.
    """
    registry_findings: list[str] = []
    if re.fullmatch(r"[0-9a-f]{64}", registry_sha256) is None:
        registry_findings.append("invalid-registry-sha256")
    components = registry.get("components")
    if not isinstance(components, list):
        components = []
        registry_findings.append("missing-components")
    identifiers = [
        component.get("component_id")
        for component in components
        if isinstance(component, Mapping)
    ]
    if len(identifiers) != len(components):
        registry_findings.append("non-object-component-record")
    valid_identifiers = [identifier for identifier in identifiers if _non_empty_text(identifier)]
    if len(valid_identifiers) != len(set(valid_identifiers)):
        registry_findings.append("duplicate-component-id")
    if valid_identifiers != sorted(valid_identifiers):
        registry_findings.append("component-ids-not-sorted")

    component_results = [
        _component_apple_continuous_improvement_result(component, index=index)
        for index, component in enumerate(components)
        if isinstance(component, Mapping)
    ]
    failed = [
        result["component_id"]
        for result in component_results
        if result["deterministic_result"] != "pass"
    ]
    delight_pending = [
        result["component_id"]
        for result in component_results
        if result["delight"]["human_qualitative_acceptance"] == "pending"
    ]
    technical_pass = (
        not registry_findings and not failed and len(component_results) == len(components)
    )
    if technical_pass and delight_pending:
        overall_status = "deterministic-pass-human-acceptance-pending"
    elif technical_pass:
        overall_status = "deterministic-pass"
    else:
        overall_status = "fail-closed"
    return {
        "record_type": "apple-continuous-improvement-component-audit",
        "record_version": 1,
        "snapshot_role": "current-candidate-registry-audit-evidence",
        "authority": "evidence-only; no activation, live-success, or lifecycle authority",
        "scope": (
            "Component metadata and governance proxies only; target-specific accessibility, "
            "inclusion, writing, interaction quality, and human Delight require separate "
            "acceptance."
        ),
        "registry": {
            "reference": "system/components/registry.json",
            "sha256_mode": "exact-file-bytes",
            "sha256": registry_sha256,
            "component_count": len(components),
            "record_version": registry.get("record_version"),
            "system_version": registry.get("system_version"),
            "candidate_status": registry.get("candidate_status"),
        },
        "apple_principles": list(APPLE_COMPONENT_PRINCIPLES),
        "continuous_improvement_route": list(COMPONENT_CONTINUOUS_IMPROVEMENT_ROUTE),
        "external_component_rule": (
            "available-unverified proves registration or availability only and never successful "
            "execution; external components do not require per-component verified-live proof here."
        ),
        "delight_rule": (
            "Observable metadata and execution proxies are recorded, but directly user-visible "
            "Delight is never marked passed without qualitative human acceptance."
        ),
        "summary": {
            "overall_status": overall_status,
            "deterministic_checks_passed": technical_pass,
            "component_count": len(component_results),
            "component_pass_count": len(component_results) - len(failed),
            "component_fail_count": len(failed),
            "human_qualitative_acceptance_pending_count": len(delight_pending),
            "release_readiness": (
                "blocked" if registry_findings or failed or delight_pending else "ready"
            ),
        },
        "registry_findings": sorted(set(registry_findings)),
        "failed_component_ids": sorted(failed),
        "human_qualitative_acceptance_pending_component_ids": sorted(delight_pending),
        "components": component_results,
    }


def audit_component_registry_apple_continuous_improvement(path: Path) -> dict[str, Any]:
    """Load and bind the Apple/Continuous Improvement audit to exact registry bytes."""
    registry_path = Path(path)
    raw = registry_path.read_bytes()
    registry = load_component_registry(registry_path)
    return audit_component_apple_continuous_improvement_alignment(
        registry,
        registry_sha256=hashlib.sha256(raw).hexdigest(),
    )


def write_component_apple_continuous_improvement_audit(
    registry_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Write portable, deterministic target-real component audit evidence."""
    audit = audit_component_registry_apple_continuous_improvement(registry_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return audit


def active_components(registry: dict[str, Any], platform: str) -> list[dict[str, Any]]:
    """Return the exact approved active set exposed on one platform."""
    return [
        component
        for component in registry["components"]
        if component["status"]["active"]
        and (platform in component["platforms"] or "shared" in component["platforms"])
    ]


def audit_component_drift(
    registry: dict[str, Any],
    observed: Iterable[dict[str, Any]],
    *,
    platform: str,
) -> dict[str, Any]:
    """Detect unmanaged live items, missing projections, and changed sources."""
    expected = {item["component_id"]: item for item in active_components(registry, platform)}
    registered_ids = {item["component_id"] for item in registry["components"]}
    actual = {
        item["component_id"]: item
        for item in observed
        if item.get("discoverable", True) or item.get("active", False)
    }
    unmanaged = sorted(set(actual).difference(registered_ids))
    missing = sorted(set(expected).difference(actual))
    changed = sorted(
        component_id
        for component_id in set(expected).intersection(actual)
        if expected[component_id]["projection"]["expected_sha256"] is not None
        and actual[component_id].get("content_sha256")
        != expected[component_id]["projection"]["expected_sha256"]
    )
    inactive_but_discoverable = sorted(
        component["component_id"]
        for component in registry["components"]
        if platform in component["platforms"]
        and not component["status"]["active"]
        and component["component_id"] in actual
    )
    return {
        "record_type": "component-drift-audit",
        "platform": platform,
        "clean": not unmanaged and not missing and not changed and not inactive_but_discoverable,
        "unmanaged": unmanaged,
        "registered_missing": missing,
        "hash_changed": changed,
        "inactive_but_discoverable": inactive_but_discoverable,
    }


def render_component_status(
    registry: dict[str, Any],
    *,
    platform: str | None = None,
    live_state: dict[str, Any] | None = None,
) -> str:
    """Render build identity separately from verified current runtime state."""
    components = registry["components"]
    if platform is not None:
        components = [
            item
            for item in components
            if platform in item["platforms"] or "shared" in item["platforms"]
        ]
    active_count = sum(item["status"]["active"] for item in components)
    quarantined_count = sum(item["disposition"] == "inactive-quarantined" for item in components)
    execution_counts = {
        state: sum(item["status"]["execution"] == state for item in components)
        for state in (
            "verified-live",
            "verified-staged",
            "available-unverified",
            "unknown",
            "unavailable",
        )
    }
    dependency_count = sum(len(item["dependencies"]) for item in components)
    user_job_count = sum(item["surface_audience"] == "user-job" for item in components)
    internal_count = sum(item["surface_audience"] != "user-job" for item in components)
    lines = [
        "# Fractal Component Status",
        "",
        f"- Adapter Build System Version: `{registry['system_version']}`",
        f"- Adapter Build State: `{registry['candidate_status']}`",
    ]
    if live_state is None:
        lines.extend(
            [
                "- Current Active System Version: `requires verified live runtime state`",
                "- Current Version State: `unknown until live verification`",
            ]
        )
    else:
        current_version = live_state["system_version"]
        lines.extend(
            [
                f"- Current Active System Version: `{current_version['version']}`",
                f"- Current Version State: `{current_version['status']}`",
            ]
        )
    lines.extend(
        [
            f"- Scope: `{platform or 'all-platforms'}`",
            f"- Registered Components: `{len(components)}`",
            f"- Active and Managed: `{active_count}`",
            f"- Inactive or Quarantined: `{quarantined_count}`",
            f"- Verified Live: `{execution_counts['verified-live']}`",
            f"- Verified Staged: `{execution_counts['verified-staged']}`",
            f"- Available, Not Yet Proven: `{execution_counts['available-unverified']}`",
            f"- Unknown: `{execution_counts['unknown']}`",
            f"- Unavailable: `{execution_counts['unavailable']}`",
            f"- Registered Dependency Links: `{dependency_count}`",
            f"- User Jobs: `{user_job_count}`",
            f"- Internal or Platform Components: `{internal_count}`",
            "",
            "`Registered` means Fractal knows and governs the component. `Verified Live` means "
            "there is evidence that it completed real work. Loading and callability are checked "
            "separately by `fractal codex inspect`; neither one proves a successful result.",
            "",
            "## Components",
            "",
            "| Component | Audience | Kind | Disposition | Platforms | Execution | Dependencies |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in components:
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                item["component_id"],
                item["surface_audience"],
                item["kind"],
                item["disposition"],
                ", ".join(item["platforms"]),
                item["status"]["execution"],
                ", ".join(item["dependencies"]) or "none",
            )
        )
    lines.extend(
        [
            "",
            "The slash-command menu is not this status surface. Use "
            "`fractal components show --registry <path>` or ask the agent to show "
            "Fractal component status.",
        ]
    )
    return "\n".join(lines) + "\n"
