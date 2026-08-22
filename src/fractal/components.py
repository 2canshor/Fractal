"""Validation and lookup for the versioned Node Implementation Map."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

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


def load_node_implementation_map() -> dict[str, Any]:
    """Load and validate the packaged Initial Build mapping."""
    path = files("fractal.data").joinpath("node-implementation-map.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    component_ids: set[str] = set()
    for node in value["nodes"]:
        missing = REQUIRED_CONTRACT_FIELDS.difference(node)
        if missing:
            raise ValueError(f"Incomplete component contract: {sorted(missing)}")
        if node["component_id"] in component_ids:
            raise ValueError(f"Duplicate component id: {node['component_id']}")
        component_ids.add(node["component_id"])
        if node["implementation_status"] != "implemented":
            raise ValueError(f"Inactive node in implemented list: {node['component_id']}")
    if component_ids.intersection(value["planned_nodes"]):
        raise ValueError("A Node cannot be both implemented and planned")
    return value
