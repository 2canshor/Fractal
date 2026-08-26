"""Validate and render the New Blueprint implementation-gap audit."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from fractal.apple_alignment import (
    load_apple_principles_registry,
    validate_responsibility_alignment,
)
from fractal.blueprint import load_blueprint
from fractal.methods import load_agentic_element_map

ASSESSMENTS = {
    "architecture-only",
    "partial",
    "implemented",
    "verified-staged",
    "verified-live",
}
ALIGNMENTS = {
    "aligned-core-concept",
    "missing-implementation",
    "needs-blueprint-contract",
    "needs-blueprint-projection",
    "needs-donor-specialisation",
    "needs-live-verification",
    "needs-reclassification",
    "needs-role-redesign",
    "needs-step-extraction",
    "needs-workflow-redesign",
    "aligned-infrastructure-source",
    "staged-arrow-needs-active-hook-proof",
    "staged-curiosity-route-needs-acquisition-runner",
    "staged-donor-route-needs-live-research-adapter",
    "staged-execution-receipts-not-active",
    "staged-local-donor-methods-needs-refresh-route",
    "staged-local-learning-not-active",
    "staged-orchestration-connection",
    "staged-orchestrator-needs-live-investigator",
}
HISTORICAL_BASELINE_STATE_ROLE = "historical-implementation-baseline-not-live-status"
RESPONSIBILITY_IDS = tuple(f"RESP-{index:02d}" for index in range(20))
RESPONSIBILITY_ARTIFACT_PREFIXES = ("src/fractal/", "tests/")
PROOF_LAYERS = ("contract", "synthetic", "staged", "active_live")
PROOF_STATES = {"verified", "pending", "not-applicable"}


def _blueprint_element_ids(blueprint: dict[str, Any]) -> set[str]:
    return {
        blueprint["element_library"]["core"]["philosophy"]["element_id"],
        blueprint["element_library"]["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in blueprint["element_library"]["genres"]
            for element in genre["elements"]
        ),
    }


def discover_responsibility_artifacts(repository_root: str | Path) -> set[str]:
    """Return the exact maintained implementation/test artifact surface.

    Package ``__init__.py`` files are deliberately excluded because they do not
    own behavior. Data and schema JSON are included because they are executable
    contracts or persistent registries, not documentation.
    """

    root = Path(repository_root)
    result = {
        path.relative_to(root).as_posix()
        for path in (root / "src" / "fractal").glob("*.py")
        if path.name != "__init__.py"
    }
    result.update(
        path.relative_to(root).as_posix()
        for directory in (
            root / "src" / "fractal" / "data",
            root / "src" / "fractal" / "schemas",
        )
        for path in directory.glob("*.json")
    )
    result.update(
        path.relative_to(root).as_posix()
        for path in (root / "tests").glob("test_*.py")
    )
    return result


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "possibly empty " if allow_empty else ""
        raise ValueError(f"{label} must be a {qualifier}list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return value


def validate_responsibility_artifact_coverage(
    value: dict[str, Any],
    *,
    observed_paths: set[str] | None = None,
) -> dict[str, str]:
    """Fail closed unless each maintained artifact has exactly one primary owner."""

    responsibility_mappings = value.get("responsibility_mappings")
    if not isinstance(responsibility_mappings, list):
        raise ValueError("Responsibility mappings are missing")
    owners: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for mapping in responsibility_mappings:
        responsibility_id = mapping.get("responsibility_id", "unknown")
        for field in ("source_paths", "schema_paths", "data_paths", "test_paths"):
            paths = _string_list(
                mapping.get(field),
                f"{responsibility_id}.{field}",
                allow_empty=True,
            )
            for path in paths:
                if not path.startswith(RESPONSIBILITY_ARTIFACT_PREFIXES):
                    raise ValueError(
                        "Responsibility artifact path is outside the maintained surface: "
                        f"{path}"
                    )
                valid_kind = {
                    "source_paths": (
                        path.startswith("src/fractal/")
                        and "/data/" not in path
                        and "/schemas/" not in path
                        and path.endswith(".py")
                        and not path.endswith("/__init__.py")
                    ),
                    "schema_paths": (
                        path.startswith("src/fractal/schemas/")
                        and path.endswith(".schema.json")
                    ),
                    "data_paths": (
                        path.startswith("src/fractal/data/") and path.endswith(".json")
                    ),
                    "test_paths": (
                        path.startswith("tests/test_") and path.endswith(".py")
                    ),
                }[field]
                if not valid_kind:
                    raise ValueError(
                        f"Responsibility artifact is classified under the wrong path kind: {path}"
                    )
                if path in owners:
                    duplicates.setdefault(path, [owners[path]]).append(responsibility_id)
                else:
                    owners[path] = responsibility_id
    if duplicates:
        raise ValueError(f"Responsibility artifacts have multiple primary owners: {duplicates}")
    if observed_paths is not None:
        missing = sorted(observed_paths.difference(owners))
        extra = sorted(set(owners).difference(observed_paths))
        if missing or extra:
            raise ValueError(
                f"Responsibility artifact coverage mismatch: missing={missing}, extra={extra}"
            )
    return owners


def _validate_responsibility_mappings(
    value: dict[str, Any],
    *,
    blueprint: dict[str, Any],
) -> None:
    mappings = value.get("responsibility_mappings")
    if not isinstance(mappings, list):
        raise ValueError("Responsibility mappings are missing")
    if [mapping.get("responsibility_id") for mapping in mappings] != list(
        RESPONSIBILITY_IDS
    ):
        raise ValueError("Responsibility mappings must contain RESP-00 through RESP-19 in order")

    element_ids = _blueprint_element_ids(blueprint)
    flow_ids = [flow["flow_id"] for flow in blueprint["flows"]["entries"]]
    registry = load_apple_principles_registry()
    for mapping in mappings:
        responsibility_id = mapping["responsibility_id"]
        if not str(mapping.get("human_name", "")).strip():
            raise ValueError(f"Responsibility human name is missing: {responsibility_id}")
        primary = mapping.get("primary_element_id")
        if primary not in element_ids:
            raise ValueError(f"Unknown primary Element for {responsibility_id}: {primary}")
        supporting = _string_list(
            mapping.get("supporting_element_ids"),
            f"{responsibility_id}.supporting_element_ids",
            allow_empty=True,
        )
        if primary in supporting or not set(supporting).issubset(element_ids):
            raise ValueError(f"Invalid supporting Elements for {responsibility_id}")
        used_flows = _string_list(mapping.get("flow_ids"), f"{responsibility_id}.flow_ids")
        if used_flows != [flow_id for flow_id in flow_ids if flow_id in used_flows]:
            raise ValueError(
                f"Flow references are unknown or out of Blueprint order: {responsibility_id}"
            )
        path = _string_list(
            mapping.get("continuous_improvement_path"),
            f"{responsibility_id}.continuous_improvement_path",
        )
        if path != ["project-review", "system-review", "continuous-improvement"]:
            raise ValueError(
                "Responsibility must end at System Review then Continuous Improvement: "
                f"{responsibility_id}"
            )
        _string_list(mapping.get("user_surfaces"), f"{responsibility_id}.user_surfaces")
        _string_list(
            mapping.get("persistent_state"),
            f"{responsibility_id}.persistent_state",
            allow_empty=True,
        )
        for field in ("authority", "privacy", "recovery"):
            boundary = mapping.get(field)
            if not isinstance(boundary, dict) or not str(boundary.get("rule", "")).strip():
                raise ValueError(f"{responsibility_id}.{field} boundary is missing")
            _string_list(
                boundary.get("evidence_paths"),
                f"{responsibility_id}.{field}.evidence_paths",
            )
        proof = mapping.get("proof_layers")
        if not isinstance(proof, dict) or tuple(proof) != PROOF_LAYERS:
            raise ValueError(f"Proof layers are incomplete or reordered: {responsibility_id}")
        if any(state not in PROOF_STATES for state in proof.values()):
            raise ValueError(f"Proof layer state is invalid: {responsibility_id}")
        if not str(mapping.get("claim_boundary", "")).strip():
            raise ValueError(f"Claim boundary is missing: {responsibility_id}")
        alignment = mapping.get("apple_alignment")
        if (
            not isinstance(alignment, dict)
            or alignment.get("responsibility_id") != responsibility_id
        ):
            raise ValueError(f"Apple alignment identity mismatch: {responsibility_id}")
        validate_responsibility_alignment(
            alignment,
            registry,
            require_human_qualitative_acceptance=False,
        )
    validate_responsibility_artifact_coverage(value)


def validate_blueprint_implementation_map(
    value: dict[str, Any],
    *,
    blueprint: dict[str, Any],
    agentic_map: dict[str, Any],
) -> dict[str, Any]:
    """Require complete target coverage and traceable retained source evidence."""
    if value.get("record_type") != "blueprint-implementation-map":
        raise ValueError("Blueprint implementation map record type is invalid")
    if value.get("blueprint_version") != blueprint["blueprint_version"]:
        raise ValueError("Blueprint implementation map version mismatch")
    if "active_system_version" in value:
        raise ValueError(
            "Blueprint implementation map must not claim the current active System Version"
        )
    if not isinstance(value.get("baseline_system_version"), str) or not value[
        "baseline_system_version"
    ].strip():
        raise ValueError("Blueprint implementation baseline System Version is missing")
    if value.get("state_role") != HISTORICAL_BASELINE_STATE_ROLE:
        raise ValueError(
            "Blueprint implementation map state role must identify a historical baseline, "
            "not current live status"
        )
    mappings = value.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("Blueprint implementation mappings are missing")
    mapping_ids = [item.get("element_id") for item in mappings]
    if len(mapping_ids) != len(set(mapping_ids)):
        raise ValueError("Blueprint implementation mappings must be unique")
    target_ids = _blueprint_element_ids(blueprint)
    if set(mapping_ids) != target_ids:
        missing = sorted(target_ids.difference(mapping_ids))
        extra = sorted(set(mapping_ids).difference(target_ids))
        raise ValueError(
            f"Blueprint implementation coverage mismatch: missing={missing}, extra={extra}"
        )
    current_nodes = {item["node_id"] for item in agentic_map["mappings"]}
    flow_mappings = value.get("flow_mappings")
    if not isinstance(flow_mappings, list):
        raise ValueError("Blueprint Flow implementation mappings are missing")
    expected_flow_ids = [item["flow_id"] for item in blueprint["flows"]["entries"]]
    if [item.get("flow_id") for item in flow_mappings] != expected_flow_ids:
        raise ValueError("Blueprint Flow implementation coverage is incomplete or out of order")
    for mapping in flow_mappings:
        if mapping.get("implementation_assessment") not in ASSESSMENTS:
            raise ValueError(f"Invalid Flow assessment: {mapping['flow_id']}")
        if mapping.get("target_alignment") not in ALIGNMENTS:
            raise ValueError(f"Invalid Flow alignment: {mapping['flow_id']}")
        if not str(mapping.get("gap", "")).strip():
            raise ValueError(f"Flow gap summary is missing: {mapping['flow_id']}")
        unknown = sorted(set(mapping.get("current_node_ids", [])).difference(current_nodes))
        if unknown:
            raise ValueError(
                f"Unknown retained Flow evidence for {mapping['flow_id']}: {unknown}"
            )
    for mapping in mappings:
        if mapping.get("implementation_assessment") not in ASSESSMENTS:
            raise ValueError(f"Invalid implementation assessment: {mapping['element_id']}")
        if mapping.get("target_alignment") not in ALIGNMENTS:
            raise ValueError(f"Invalid target alignment: {mapping['element_id']}")
        if not str(mapping.get("gap", "")).strip():
            raise ValueError(f"Implementation gap summary is missing: {mapping['element_id']}")
        unknown = sorted(set(mapping.get("current_node_ids", [])).difference(current_nodes))
        if unknown:
            raise ValueError(
                f"Unknown retained implementation evidence for {mapping['element_id']}: {unknown}"
            )
        if mapping["implementation_assessment"] == "architecture-only" and mapping.get(
            "current_node_ids"
        ):
            raise ValueError(
                f"Architecture-only mapping cannot claim a current Node: {mapping['element_id']}"
            )
    _validate_responsibility_mappings(value, blueprint=blueprint)
    return value


def load_blueprint_implementation_map() -> dict[str, Any]:
    """Load and validate the packaged implementation-gap mapping."""
    path = files("fractal.data").joinpath("blueprint-implementation-map.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    validated = validate_blueprint_implementation_map(
        value,
        blueprint=load_blueprint(),
        agentic_map=load_agentic_element_map(),
    )
    repository_root = Path(__file__).resolve().parents[2]
    if (repository_root / "pyproject.toml").is_file() and (
        repository_root / "tests"
    ).is_dir():
        validate_responsibility_artifact_coverage(
            validated,
            observed_paths=discover_responsibility_artifacts(repository_root),
        )
    return validated


def render_blueprint_implementation_gap(value: dict[str, Any] | None = None) -> str:
    """Render the target-to-current gap without promoting retained evidence."""
    blueprint = load_blueprint()
    agentic_map = load_agentic_element_map()
    audit = (
        validate_blueprint_implementation_map(
            value,
            blueprint=blueprint,
            agentic_map=agentic_map,
        )
        if value is not None
        else load_blueprint_implementation_map()
    )
    current = {item["node_id"]: item["status"] for item in agentic_map["mappings"]}
    counts = {
        assessment: sum(
            item["implementation_assessment"] == assessment for item in audit["mappings"]
        )
        for assessment in (
            "architecture-only",
            "partial",
            "implemented",
            "verified-staged",
            "verified-live",
        )
    }
    lines = [
        "# Blueprint Implementation Gap",
        "",
        f"- Blueprint Version: `{audit['blueprint_version']}`",
        f"- Baseline System Version Compared: `{audit['baseline_system_version']}`",
        f"- State Role: `{audit['state_role']}`",
        f"- Architecture Only: `{counts['architecture-only']}`",
        f"- Partial: `{counts['partial']}`",
        f"- Implemented: `{counts['implemented']}`",
        f"- Verified Staged: `{counts['verified-staged']}`",
        f"- Verified Live: `{counts['verified-live']}`",
        "",
        f"> {audit['claim_boundary']}",
        "> Current live status comes from dynamic `fractal status` and the live System Version "
        "pointer; this baseline does not duplicate that state.",
        "",
        "| Blueprint Element | Assessment | Target Alignment | Retained Evidence | Gap |",
        "|---|---|---|---|---|",
    ]
    for item in audit["mappings"]:
        evidence = []
        for node_id in item["current_node_ids"]:
            status = current[node_id]
            evidence.append(
                f"`{node_id}`: {status['source']} / {status['projection']} / {status['execution']}"
            )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item['element_id']}`",
                    f"`{item['implementation_assessment']}`",
                    f"`{item['target_alignment']}`",
                    "<br>".join(evidence) if evidence else "None",
                    item["gap"],
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Persistent Responsibility Coverage",
            "",
            (
                "| Responsibility | Primary Element | Supporting Elements | Flows | "
                "Proof | Apple alignment | Claim boundary |"
            ),
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in audit["responsibility_mappings"]:
        proof = ", ".join(
            f"{layer}={item['proof_layers'][layer]}" for layer in PROOF_LAYERS
        )
        supporting = ", ".join(
            f"`{value}`" for value in item["supporting_element_ids"]
        )
        flows = ", ".join(f"`{value}`" for value in item["flow_ids"])
        alignment = item["apple_alignment"]
        lines.append(
            f"| `{item['responsibility_id']}` {item['human_name']} | "
            f"`{item['primary_element_id']}` | {supporting or 'None'} | "
            f"{flows} | {proof} | API v{alignment['record_version']}, manifest-bound, "
            "human Delight pending | "
            f"{item['claim_boundary']} |"
        )
    return "\n".join(lines) + "\n"
