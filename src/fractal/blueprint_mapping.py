"""Validate concrete implementation Candidates against the New Blueprint."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from fractal.blueprint import ROLE_BY_MARKER, load_blueprint
from fractal.donors import load_donor_inventory

CHANGE_ACTIONS = {
    "add",
    "delete",
    "merge",
    "no-change",
    "reconfigure",
    "replace",
    "shorten",
    "simplify",
}
RELATIONSHIPS = {"contains", "implements", "informs", "serves", "validates"}
VISIBILITIES = {"background-only", "operator-facing", "product-facing"}
PRINCIPLE_DIRECTIONS = {"closer", "farther", "neutral", "unknown"}


def _blueprint_indexes() -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]
]:
    blueprint = load_blueprint()
    genres = {
        genre["genre_id"]: genre for genre in blueprint["element_library"]["genres"]
    }
    elements = {
        element["element_id"]: {
            **element,
            "genre_id": genre["genre_id"],
            "element_type": ROLE_BY_MARKER[element["marker"]],
        }
        for genre in blueprint["element_library"]["genres"]
        for element in genre["elements"]
    }
    protagonist = blueprint["element_library"]["core"]["protagonist"]
    elements[protagonist["element_id"]] = {
        **protagonist,
        "section_id": "core",
        "genre_id": None,
        "element_type": "protagonist",
    }
    flow_ids = {flow["flow_id"] for flow in blueprint["flows"]["entries"]}
    return genres, elements, flow_ids


def validate_candidate_mapping(value: dict[str, Any]) -> dict[str, Any]:
    """Reject misclassification, implied authority and dishonest addition priority."""
    if value.get("record_type") != "blueprint-candidate-mapping":
        raise ValueError("Candidate Blueprint Mapping record type is invalid")
    if value.get("status") != "mapped-staged-not-active":
        raise ValueError("Candidate Blueprint Mapping must remain staged and inactive")
    if value.get("change_action") not in CHANGE_ACTIONS:
        raise ValueError("Candidate Blueprint Mapping change action is invalid")
    if not str(value.get("global_pattern_id", "")).strip():
        raise ValueError("Candidate Blueprint Mapping requires a Global Pattern")
    if not str(value.get("implementation_summary", "")).strip():
        raise ValueError("Candidate Blueprint Mapping requires an implementation summary")
    genres, elements, flow_ids = _blueprint_indexes()
    target = value.get("target")
    if not isinstance(target, dict):
        raise ValueError("Candidate Blueprint Mapping requires a target")
    existing_id = target.get("existing_element_id")
    new_id = target.get("new_element_id")
    if bool(existing_id) == bool(new_id):
        raise ValueError("Candidate target requires exactly one existing or new element id")
    if existing_id:
        existing = elements.get(existing_id)
        if existing is None:
            raise ValueError("Candidate target references an unknown Blueprint element")
        expected_target = (
            existing.get("section_id", "element-library"),
            existing["genre_id"],
            existing["element_type"],
            existing["marker"],
        )
        observed_target = (
            target.get("section_id", "element-library"),
            target.get("genre_id"),
            target.get("element_type"),
            target.get("marker"),
        )
        if expected_target != observed_target:
            raise ValueError("Candidate target misclassifies an existing Blueprint element")
        if value["change_action"] == "add":
            raise ValueError("An existing Blueprint target cannot use the add action")
        if value.get("addition_priority_review"):
            raise ValueError("A non-additive Candidate cannot manufacture an addition review")
    else:
        if target.get("genre_id") not in genres:
            raise ValueError("A new Blueprint Element requires a valid target Genre")
        genre = genres[target["genre_id"]]
        if target.get("marker") not in genre["allowed_markers"] or target.get(
            "element_type"
        ) != ROLE_BY_MARKER.get(target.get("marker")):
            raise ValueError("Candidate target does not follow its Genre contract")
        if value["change_action"] != "add":
            raise ValueError("A new Blueprint element requires the add action")
        _validate_addition_priority(value, target["element_type"])

    if not str(value.get("core_concept_continuity", "")).strip():
        raise ValueError("Candidate Mapping requires core concept continuity")
    for relationship in value.get("relationships", []):
        if relationship.get("relationship") not in RELATIONSHIPS:
            raise ValueError("Candidate Mapping relationship is invalid")
        if relationship.get("element_id") not in elements:
            raise ValueError("Candidate Mapping relationship target is unknown")
    for relationship in value.get("flow_relationships", []):
        if relationship.get("relationship") not in RELATIONSHIPS:
            raise ValueError("Candidate Mapping Flow relationship is invalid")
        if relationship.get("flow_id") not in flow_ids:
            raise ValueError("Candidate Mapping Flow target is unknown")
    if value.get("visibility") not in VISIBILITIES:
        raise ValueError("Candidate Mapping visibility is invalid")
    context = value.get("context_effect")
    if not isinstance(context, dict) or not str(context.get("summary", "")).strip():
        raise ValueError("Candidate Mapping requires a context-effect summary")
    for field in ("always_loaded_tokens_delta", "on_demand_tokens_delta"):
        if not isinstance(context.get(field), int):
            raise ValueError("Candidate context deltas must be explicit integers")
    if not isinstance(context.get("duplicate_responsibility"), bool):
        raise ValueError("Candidate context effect must assess duplicate responsibility")
    principles = {item["element_id"] for item in genres["principles"]["elements"]}
    observed_principles = set()
    for effect in value.get("principle_effects", []):
        if effect.get("element_id") not in principles:
            raise ValueError("Candidate Mapping Principle effect target is invalid")
        if effect.get("direction") not in PRINCIPLE_DIRECTIONS:
            raise ValueError("Candidate Mapping Principle direction is invalid")
        if not str(effect.get("summary", "")).strip():
            raise ValueError("Candidate Mapping Principle effect requires a summary")
        observed_principles.add(effect["element_id"])
    if observed_principles != principles:
        raise ValueError("Candidate Mapping must assess every current Principle")
    donor_ids = {item["donor_id"] for item in load_donor_inventory()["donors"]}
    unknown_donors = sorted(set(value.get("donor_ids", [])).difference(donor_ids))
    if unknown_donors:
        raise ValueError(f"Candidate Mapping contains unknown donors: {unknown_donors}")
    unknown_evaluated_donors = sorted(
        set(value.get("evaluated_donor_ids", [])).difference(donor_ids)
    )
    if unknown_evaluated_donors:
        raise ValueError(
            f"Candidate Mapping contains unknown evaluated donors: {unknown_evaluated_donors}"
        )
    if not value.get("evidence_ids"):
        raise ValueError("Candidate Mapping requires evidence")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("scope") != "proposal-only":
        raise ValueError("Candidate Mapping authority must remain proposal-only")
    for forbidden in ("activation", "canonical_write", "persistent_mutation", "publication"):
        if authority.get(forbidden) is not False:
            raise ValueError(f"Candidate Mapping cannot receive {forbidden} authority")
    if not str(value.get("recovery", "")).strip():
        raise ValueError("Candidate Mapping requires a recovery path")
    return value


def _validate_addition_priority(value: dict[str, Any], selected_type: str) -> None:
    priority = ["extra", "prop", "principle", "deuteragonist"]
    selected_index = priority.index(selected_type)
    review = value.get("addition_priority_review")
    if not isinstance(review, list):
        raise ValueError("An additive Candidate requires an addition-priority review")
    expected = priority[: selected_index + 1]
    if [item.get("element_type") for item in review] != expected:
        raise ValueError("Addition-priority review must follow ^ > ¢ > % > $")
    for index, item in enumerate(review):
        expected_result = "selected" if index == selected_index else "insufficient"
        if item.get("result") != expected_result or not str(item.get("reason", "")).strip():
            raise ValueError("Addition-priority review requires an honest result and reason")


def load_donor_candidate_mappings() -> dict[str, Any]:
    """Load and validate every staged donor Candidate Mapping."""
    path = files("fractal.data").joinpath("donor-candidate-mappings.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("record_type") != "blueprint-candidate-mapping-set":
        raise ValueError("Candidate Mapping set record type is invalid")
    if value.get("blueprint_version") != load_blueprint()["blueprint_version"]:
        raise ValueError("Candidate Mapping set Blueprint version mismatch")
    mappings = value.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("Candidate Mapping set is missing mappings")
    ids = [mapping.get("candidate_id") for mapping in mappings]
    if len(ids) != len(set(ids)):
        raise ValueError("Candidate Mapping ids must be unique")
    for mapping in mappings:
        validate_candidate_mapping(mapping)
    return value
