"""Validate and render the New Blueprint implementation-gap audit."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

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
    return value


def load_blueprint_implementation_map() -> dict[str, Any]:
    """Load and validate the packaged implementation-gap mapping."""
    path = files("fractal.data").joinpath("blueprint-implementation-map.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    return validate_blueprint_implementation_map(
        value,
        blueprint=load_blueprint(),
        agentic_map=load_agentic_element_map(),
    )


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
        f"- Active System Version Compared: `{audit['active_system_version']}`",
        f"- Architecture Only: `{counts['architecture-only']}`",
        f"- Partial: `{counts['partial']}`",
        f"- Implemented: `{counts['implemented']}`",
        f"- Verified Staged: `{counts['verified-staged']}`",
        f"- Verified Live: `{counts['verified-live']}`",
        "",
        f"> {audit['claim_boundary']}",
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
    return "\n".join(lines) + "\n"
