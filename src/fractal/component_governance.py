"""Universal Fractal component registration, projection, and drift checks."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
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
