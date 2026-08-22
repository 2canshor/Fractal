from __future__ import annotations

from fractal.components import REQUIRED_CONTRACT_FIELDS, load_node_implementation_map


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
