"""Validation and lookup for the versioned Node Implementation Map."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from typing import Any

from fractal.methods import METHOD_REGISTRY_SECTIONS, load_method_registry

REQUIRED_CONTRACT_FIELDS = {
    "component_id",
    "human_name",
    "class",
    "implementation_status",
    "decision_status",
    "purpose",
    "trigger",
    "non_trigger",
    "preconditions",
    "stop_condition",
    "fallback",
    "inputs",
    "outputs",
    "authority",
    "executor",
    "checks",
    "evidence",
    "human_view",
    "evaluation",
    "version",
    "change_failure_path",
}


def validate_node_implementation_map(
    value: dict[str, Any],
    *,
    method_registry: dict[str, Any],
    method_registry_sha256: str,
) -> dict[str, Any]:
    """Validate structural lineage without guessing semantic equivalence."""
    lineage = value.get("lineage_contract")
    if not isinstance(lineage, dict):
        raise ValueError("Missing required JSON path: lineage_contract")
    required_lineage_keys = [
        "canonical_baseline_id",
        "canonical_baseline_sha256",
        "core_identity",
        "required_fields_for_major_nodes",
        "allowed_lineage_classes",
        "original_system_review_backbone",
    ]
    for key in required_lineage_keys:
        if key not in lineage:
            raise ValueError(f"Missing required JSON path: lineage_contract.{key}")
    if set(lineage) != set(required_lineage_keys):
        raise ValueError("Architecture lineage contract fields are incomplete or unexpected")
    if method_registry_sha256 != lineage["canonical_baseline_sha256"]:
        raise ValueError("Architecture lineage canonical baseline hash mismatch")
    if lineage["canonical_baseline_id"] != (f"method-registry-{method_registry['system_version']}"):
        raise ValueError("Architecture lineage canonical baseline id mismatch")
    if not str(lineage["core_identity"]).strip():
        raise ValueError("Missing required JSON path: lineage_contract.core_identity")
    blueprint_flows = sorted(method_registry["flows"], key=lambda item: item["sequence"])
    backbone = lineage["original_system_review_backbone"]
    if not isinstance(backbone, list) or len(backbone) != len(blueprint_flows):
        raise ValueError("Hierarchy inversion at lineage_contract.original_system_review_backbone")
    expected_backbone = [(item["sequence"], item["id"]) for item in blueprint_flows]
    observed_backbone: list[tuple[Any, Any]] = []
    for index, item in enumerate(backbone):
        for key in ("flow", "name", "original_question", "implemented_by"):
            if key not in item:
                raise ValueError(
                    "Missing required JSON path: "
                    f"lineage_contract.original_system_review_backbone[{index}].{key}"
                )
        if not item["implemented_by"]:
            raise ValueError(
                "Missing required JSON path: "
                f"lineage_contract.original_system_review_backbone[{index}].implemented_by"
            )
        observed_backbone.append((item["flow"], item["name"]))
    if observed_backbone != expected_backbone:
        raise ValueError("Hierarchy inversion at lineage_contract.original_system_review_backbone")
    canonical_references = {
        item["id"] for section in METHOD_REGISTRY_SECTIONS for item in method_registry[section]
    }
    canonical_references.update(
        reference
        for section in METHOD_REGISTRY_SECTIONS
        for item in method_registry[section]
        for reference in item["operational_mapping"]
    )
    canonical_references.update(
        reference for item in backbone for reference in item["implemented_by"]
    )
    required_major_fields = set(lineage["required_fields_for_major_nodes"])
    allowed_lineage_classes = set(lineage["allowed_lineage_classes"])
    component_ids: set[str] = set()
    for index, node in enumerate(value["nodes"]):
        missing = REQUIRED_CONTRACT_FIELDS.difference(node)
        if missing:
            raise ValueError(f"Missing required JSON path: nodes[{index}].{sorted(missing)[0]}")
        if node["component_id"] in component_ids:
            raise ValueError(f"Duplicate component id: {node['component_id']}")
        component_ids.add(node["component_id"])
        if node["implementation_status"] != "implemented":
            raise ValueError(f"Inactive node in implemented list: {node['component_id']}")
        if node["decision_status"] == "architecture-baseline" or "lineage_class" in node:
            missing_lineage = required_major_fields.difference(node)
            if missing_lineage:
                raise ValueError(
                    f"Missing required JSON path: nodes[{index}].{sorted(missing_lineage)[0]}"
                )
            if node["lineage_class"] not in allowed_lineage_classes:
                raise ValueError(f"Invalid lineage class: {node['component_id']}")
            orphaned = sorted(set(node["implemented_by"]).difference(canonical_references))
            if orphaned:
                raise ValueError(
                    f"Orphan architecture lineage references for {node['component_id']}: {orphaned}"
                )
            if (
                node["lineage_class"] == "separately-approved-later-capability"
                and re.fullmatch(
                    r"decision-[a-z0-9-]+", str(node.get("primary_user_decision_id", ""))
                )
                is None
            ):
                raise ValueError(
                    "A separately approved later capability requires a primary-user decision id"
                )
    if component_ids.intersection(value["planned_nodes"]):
        raise ValueError("A Node cannot be both implemented and planned")
    return value


def load_node_implementation_map() -> dict[str, Any]:
    """Load and validate the packaged Initial Build mapping."""
    path = files("fractal.data").joinpath("node-implementation-map.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    method_path = files("fractal.data").joinpath("method-registry.json")
    return validate_node_implementation_map(
        value,
        method_registry=load_method_registry(),
        method_registry_sha256=hashlib.sha256(method_path.read_bytes()).hexdigest(),
    )
