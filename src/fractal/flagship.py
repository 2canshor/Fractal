"""Validate one current flagship implementation decision per Blueprint Element."""

from __future__ import annotations

import json
from importlib import util
from importlib.resources import files
from typing import Any

from fractal.blueprint import load_blueprint
from fractal.blueprint_audit import load_blueprint_implementation_map
from fractal.donors import load_donor_inventory

DECISIONS = {
    "adopted-local-adaptation",
    "retained-fractal-native-after-comparison",
    "purpose-owner-not-donor-replaceable",
}
SPECIAL_SOURCES = {"fractal-native"}


def _element_ids() -> set[str]:
    blueprint = load_blueprint()
    library = blueprint["element_library"]
    return {
        library["core"]["philosophy"]["element_id"],
        library["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in library["genres"]
            for element in genre["elements"]
        ),
    }


def validate_flagship_implementation_matrix(value: dict[str, Any]) -> dict[str, Any]:
    """Reject missing Elements, unnamed donor copies and unevidenced recovery."""
    if value.get("record_type") != "flagship-implementation-matrix":
        raise ValueError("Flagship implementation matrix identity is invalid")
    if value.get("blueprint_version") != load_blueprint()["blueprint_version"]:
        raise ValueError("Flagship implementation matrix Blueprint version mismatch")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Flagship implementation entries are missing")
    entry_ids = [entry.get("element_id") for entry in entries]
    expected_ids = _element_ids()
    if len(entry_ids) != len(set(entry_ids)) or set(entry_ids) != expected_ids:
        missing = sorted(expected_ids.difference(entry_ids))
        extra = sorted(set(entry_ids).difference(expected_ids))
        raise ValueError(f"Flagship Element coverage mismatch: missing={missing}, extra={extra}")
    inventory = load_donor_inventory()
    donor_ids = {donor["donor_id"] for donor in inventory["donors"]}
    donor_names = {
        donor["donor_id"]: donor["human_name"].lower() for donor in inventory["donors"]
    }
    valid_sources = donor_ids | SPECIAL_SOURCES
    for entry in entries:
        element_id = entry["element_id"]
        if entry.get("decision") not in DECISIONS:
            raise ValueError(f"Flagship decision is invalid: {element_id}")
        adopted = entry.get("adopted_source_ids")
        evaluated = entry.get("evaluated_source_ids")
        if (
            not isinstance(adopted, list)
            or not adopted
            or not isinstance(evaluated, list)
            or not set(adopted + evaluated).issubset(valid_sources)
        ):
            raise ValueError(f"Flagship source decision is incomplete: {element_id}")
        local_name = str(entry.get("local_name", "")).strip()
        if not local_name:
            raise ValueError(f"Flagship local name is missing: {element_id}")
        for source_id in adopted:
            if source_id in donor_names and donor_names[source_id] == local_name.lower():
                raise ValueError(f"Flagship implementation copied a donor name: {element_id}")
        modules = entry.get("implementation_modules")
        if (
            not isinstance(modules, list)
            or not modules
            or any(
                not isinstance(module, str) or util.find_spec(module) is None
                for module in modules
            )
        ):
            raise ValueError(f"Flagship local implementation is missing: {element_id}")
        if (
            not str(entry.get("comparison_summary", "")).strip()
            or not isinstance(entry.get("rejected_behaviour"), list)
            or not entry["rejected_behaviour"]
            or not isinstance(entry.get("context_cost_tokens"), int)
            or not str(entry.get("recovery", "")).strip()
        ):
            raise ValueError(f"Flagship decision evidence is incomplete: {element_id}")
    return value


def load_flagship_implementation_matrix() -> dict[str, Any]:
    path = files("fractal.data").joinpath("flagship-implementation-matrix.json")
    return validate_flagship_implementation_matrix(
        json.loads(path.read_text(encoding="utf-8"))
    )


def render_flagship_implementation_matrix() -> str:
    matrix = load_flagship_implementation_matrix()
    assessments = {
        item["element_id"]: item["implementation_assessment"]
        for item in load_blueprint_implementation_map()["mappings"]
    }
    lines = [
        "# Flagship Implementation Matrix",
        "",
        f"> {matrix['claim_boundary']}",
        "",
        "| Element | Fractal implementation | Decision | Current sources | Proof | "
        "Rejected | Recovery |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in matrix["entries"]:
        sources = ", ".join(f"`{item}`" for item in entry["adopted_source_ids"])
        rejected = "; ".join(entry["rejected_behaviour"])
        lines.append(
            f"| `{entry['element_id']}` | {entry['local_name']} | "
            f"`{entry['decision']}` | {sources} | `{assessments[entry['element_id']]}` | "
            f"{rejected} | {entry['recovery']} |"
        )
    return "\n".join(lines) + "\n"
