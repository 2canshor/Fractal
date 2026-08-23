from __future__ import annotations

import copy
import hashlib
from importlib.resources import files

import pytest

from fractal.components import (
    REQUIRED_CONTRACT_FIELDS,
    load_node_implementation_map,
    validate_node_implementation_map,
)
from fractal.methods import load_method_registry


def test_implemented_nodes_have_full_operational_contracts() -> None:
    mapping = load_node_implementation_map()
    assert mapping["status"] == "initial-build-mapping"
    assert mapping["change_policy"]["allowed_proposals"] == [
        "add",
        "remove",
        "replace",
        "merge",
        "split",
    ]
    assert len(mapping["nodes"]) == 9
    assert "system-review" not in mapping["planned_nodes"]
    for node in mapping["nodes"]:
        assert REQUIRED_CONTRACT_FIELDS.issubset(node)
        assert node["implementation_status"] == "implemented"
        assert node["trigger"]
        assert node["non_trigger"]
        assert node["preconditions"]
        assert node["stop_condition"]
        assert node["fallback"]
        assert all(node["evaluation"].values())


def test_planned_nodes_are_not_claimed_as_implemented() -> None:
    mapping = load_node_implementation_map()
    implemented = {node["component_id"] for node in mapping["nodes"]}
    assert implemented.isdisjoint(mapping["planned_nodes"])


def validate_mutation(value: dict) -> dict:
    method_path = files("fractal.data").joinpath("method-registry.json")
    return validate_node_implementation_map(
        value,
        method_registry=load_method_registry(),
        method_registry_sha256=hashlib.sha256(method_path.read_bytes()).hexdigest(),
    )


def test_lineage_gate_reports_deleted_field_path_and_hierarchy_inversion() -> None:
    mapping = load_node_implementation_map()
    missing = copy.deepcopy(mapping)
    del missing["nodes"][-1]["original_requirement"]
    with pytest.raises(ValueError, match=r"nodes\[8\]\.original_requirement"):
        validate_mutation(missing)

    inverted = copy.deepcopy(mapping)
    inverted["lineage_contract"]["original_system_review_backbone"][0]["name"] = (
        "reality-check"
    )
    with pytest.raises(ValueError, match="Hierarchy inversion"):
        validate_mutation(inverted)


def test_lineage_shadow_accepts_rename_and_approved_later_capability() -> None:
    mapping = copy.deepcopy(load_node_implementation_map())
    mapping["nodes"][0]["human_name"] = "Direction"
    later = copy.deepcopy(mapping["nodes"][-1])
    later["component_id"] = "approved-later-capability"
    later["decision_status"] = "approved-technical-decision"
    later["lineage_class"] = "separately-approved-later-capability"
    later["primary_user_decision_id"] = "decision-q9-primary-user"
    mapping["nodes"].append(later)
    assert validate_mutation(mapping)["nodes"][-1]["component_id"] == (
        "approved-later-capability"
    )
