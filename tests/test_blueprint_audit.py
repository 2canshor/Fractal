from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fractal.blueprint import load_blueprint
from fractal.blueprint_audit import (
    load_blueprint_implementation_map,
    render_blueprint_implementation_gap,
    validate_blueprint_implementation_map,
)
from fractal.methods import load_agentic_element_map

ROOT = Path(__file__).resolve().parents[1]


def test_gap_audit_covers_every_classified_blueprint_element() -> None:
    blueprint = load_blueprint()
    audit = load_blueprint_implementation_map()
    expected = {
        blueprint["core"]["philosophy"]["element_id"],
        blueprint["core"]["protagonist"]["element_id"],
        *(element["element_id"] for genre in blueprint["genres"] for element in genre["elements"]),
    }
    assert {item["element_id"] for item in audit["mappings"]} == expected


def test_gap_audit_records_mapping_and_steal_as_implemented_sources() -> None:
    audit = load_blueprint_implementation_map()
    by_id = {item["element_id"]: item for item in audit["mappings"]}
    assert by_id["map-implementations-to-blueprint"]["implementation_assessment"] == (
        "implemented"
    )
    assert by_id["steal"]["implementation_assessment"] == "implemented"
    assert by_id["work-signature"]["implementation_assessment"] == "implemented"
    assert by_id["reality-check"]["implementation_assessment"] == "partial"


def test_gap_audit_rejects_unknown_retained_evidence() -> None:
    audit = copy.deepcopy(load_blueprint_implementation_map())
    audit["mappings"][0]["current_node_ids"] = ["not-a-current-node"]
    with pytest.raises(ValueError, match="Unknown retained implementation evidence"):
        validate_blueprint_implementation_map(
            audit,
            blueprint=load_blueprint(),
            agentic_map=load_agentic_element_map(),
        )


def test_rendered_gap_document_matches_validated_mapping() -> None:
    assert (ROOT / "docs" / "blueprint-implementation-gap.md").read_text(
        encoding="utf-8"
    ) == render_blueprint_implementation_gap()
