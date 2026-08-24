"""Validation and lookup for Fractal's improvement hierarchy and element map."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from fractal.blueprint import load_blueprint

VALID_STATUSES = {
    "architecture-baseline",
    "approved-technical-decision",
    "proposed-technical-baseline",
    "intent-established-mechanism-deferred",
    "intent-established-mechanism-under-research",
    "intent-established-mechanism-partially-defined",
    "intent-established-methodology-partially-defined",
    "concept-under-clarification",
    "retired",
}

METHOD_REGISTRY_SECTIONS = (
    "core_philosophies",
    "protagonist_mechanisms",
    "methodologies",
    "secondary_mechanisms",
    "mechanisms",
)

AGENTIC_ELEMENTS = {
    "main-agent",
    "skill",
    "hook",
    "subagent",
    "mcp",
    "plugin",
    "deterministic-program",
}

SOURCE_STATUSES = {"implemented", "partially-implemented"}
PROJECTION_STATUSES = {"active", "staged", "not-applicable"}
EXECUTION_STATUSES = {
    "verified-live",
    "verified-staged",
    "verified-synthetic",
    "not-run",
}


def choose_execution_element(
    *,
    task_id: str,
    repeatable_rule_available: bool,
    exact_output_contract: bool,
    requires_interpretation: bool = False,
    requires_causal_reasoning: bool = False,
    requires_tradeoff: bool = False,
    requires_synthesis: bool = False,
) -> dict[str, Any]:
    """Apply Deterministic Over Probabilistic to one bounded unit of work."""
    if not task_id.strip():
        raise ValueError("Execution selection requires a task id")
    judgement_reasons = [
        name
        for name, required in (
            ("interpretation", requires_interpretation),
            ("causal-reasoning", requires_causal_reasoning),
            ("tradeoff", requires_tradeoff),
            ("synthesis", requires_synthesis),
        )
        if required
    ]
    deterministic_ready = repeatable_rule_available and exact_output_contract
    if deterministic_ready and not judgement_reasons:
        route = "deterministic-program"
    elif deterministic_ready:
        route = "deterministic-first-then-main-agent"
    elif judgement_reasons:
        route = "main-agent"
    else:
        raise ValueError("Execution selection lacks an exact rule or a judgement need")
    return {
        "record_type": "execution-element-selection",
        "task_id": task_id,
        "mechanism": "deterministic-over-probabilistic",
        "route": route,
        "deterministic_ready": deterministic_ready,
        "judgement_reasons": judgement_reasons,
    }


def load_method_registry() -> dict[str, Any]:
    """Load the hierarchy and reject misplaced or incomplete Nodes."""
    path = files("fractal.data").joinpath("method-registry.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if [item["id"] for item in value["core_philosophies"]] != ["continuous-improvement"]:
        raise ValueError("Continuous Improvement must be the only Core Philosophy")
    blueprint = load_blueprint()
    if value.get("blueprint_version") != blueprint["blueprint_version"]:
        raise ValueError("Method registry and Blueprint versions disagree")
    if [item["id"] for item in value["protagonist_mechanisms"]] != ["system-review"]:
        raise ValueError("System Review must be the sole Protagonist")
    if [item["id"] for item in value["secondary_mechanisms"]] != ["project-review"]:
        raise ValueError("Perspective compatibility identity is missing")
    perspective = value["secondary_mechanisms"][0]
    if (
        perspective["human_name"] != "Perspective"
        or perspective.get("blueprint_genre") != "methods"
        or perspective.get("blueprint_marker") != "$"
    ):
        raise ValueError("Perspective must be a Methods Deuteragonist")

    flows = value["flows"]
    blueprint_flows = blueprint["flows"]["entries"]
    if [item["id"] for item in flows] != [item["flow_id"] for item in blueprint_flows]:
        raise ValueError("Method registry Flows do not match the Blueprint")
    if [item["sequence"] for item in flows] != list(range(1, 9)):
        raise ValueError("The active System Review must preserve Blueprint Flows 1 to 8")
    if [item["human_name"] for item in flows] != [
        item["human_name"] for item in blueprint_flows
    ]:
        raise ValueError("Method registry Flow names do not match the Blueprint")
    for flow in flows:
        if flow.get("flow_kind") != "system-review-flow":
            raise ValueError(f"Flow is incorrectly classified as an Element: {flow['id']}")
        if any(key in flow for key in ("blueprint_genre", "blueprint_marker", "blueprint_tags")):
            raise ValueError(f"Flow cannot carry Element classification: {flow['id']}")
        if flow.get("decision_status") not in VALID_STATUSES or not flow.get(
            "operational_mapping"
        ):
            raise ValueError(f"Flow registry entry is incomplete: {flow['id']}")

    methodologies = value["methodologies"]
    three_values = [item for item in methodologies if item.get("methodology_kind") == "three-value"]
    if [item["id"] for item in three_values] != ["fatigue", "curiosity", "greed"]:
        raise ValueError("The three Values must be Fatigue, Curiosity, and Greed")
    if len(methodologies) != 3:
        raise ValueError("Element methodologies must contain exactly the three Values")
    for methodology in three_values:
        if (
            methodology["decision_status"] != "intent-established-methodology-partially-defined"
            or not methodology.get("open_questions")
            or not methodology.get("evidence_requirement")
        ):
            raise ValueError(
                f"Three-Value Methodology lacks its open design record: {methodology['id']}"
            )

    mechanism_ids = {item["id"] for item in value["mechanisms"]}
    required_mechanisms = {
        element["element_id"]
        for genre in blueprint["element_library"]["genres"]
        if genre["genre_id"] in {"principles", "infrastructure", "methods"}
        for element in genre["elements"]
        if element["element_id"] != "project-review"
    }
    if not required_mechanisms <= mechanism_ids:
        missing = sorted(required_mechanisms - mechanism_ids)
        raise ValueError(f"Required Mechanisms are missing: {missing}")

    all_items = [item for section in METHOD_REGISTRY_SECTIONS for item in value[section]]
    ids: set[str] = set()
    for item in all_items:
        if item["id"] in ids:
            raise ValueError(f"Duplicate Method id: {item['id']}")
        ids.add(item["id"])
        if item["decision_status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid Decision Status: {item['decision_status']}")
        if not item.get("operational_mapping"):
            raise ValueError(f"Missing operational mapping: {item['id']}")
        if item["id"] not in {
            "continuous-improvement",
            "system-review",
        } and (not item.get("blueprint_genre") or not item.get("blueprint_marker")):
            raise ValueError(f"Missing Blueprint classification: {item['id']}")
    return value


def load_agentic_element_map() -> dict[str, Any]:
    """Load the Node-to-element map and verify complete hierarchy coverage."""
    path = files("fractal.data").joinpath("agentic-element-map.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    registry = load_method_registry()
    expected_ids = {
        item["id"] for section in METHOD_REGISTRY_SECTIONS for item in registry[section]
    }
    expected_ids.update(item["id"] for item in registry["flows"])
    mappings = value["mappings"]
    mapped_ids = [item["node_id"] for item in mappings]
    if len(mapped_ids) != len(set(mapped_ids)):
        raise ValueError("Agentic element map contains duplicate Node ids")
    if set(mapped_ids) != expected_ids:
        missing = sorted(expected_ids - set(mapped_ids))
        extra = sorted(set(mapped_ids) - expected_ids)
        raise ValueError(f"Agentic element map coverage mismatch; missing={missing}, extra={extra}")

    for item in mappings:
        if item["primary_element"] not in AGENTIC_ELEMENTS:
            raise ValueError(f"Unknown primary element: {item['node_id']}")
        if not set(item["supporting_elements"]) <= AGENTIC_ELEMENTS:
            raise ValueError(f"Unknown supporting element: {item['node_id']}")
        for required in ("job", "selection_reason", "trigger", "output", "permissions"):
            if not item.get(required):
                raise ValueError(f"Missing {required}: {item['node_id']}")
        status = item["status"]
        if status["source"] not in SOURCE_STATUSES:
            raise ValueError(f"Unknown source status: {item['node_id']}")
        if status["projection"] not in PROJECTION_STATUSES:
            raise ValueError(f"Unknown projection status: {item['node_id']}")
        if status["execution"] not in EXECUTION_STATUSES:
            raise ValueError(f"Unknown execution status: {item['node_id']}")
        if status["execution"] == "verified-live" and status["projection"] != "active":
            raise ValueError(
                f"Live execution cannot be claimed for an inactive Node: {item['node_id']}"
            )
    return value
