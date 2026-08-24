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
    library = blueprint["element_library"]
    expected = {
        library["core"]["philosophy"]["element_id"],
        library["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in library["genres"]
            for element in genre["elements"]
        ),
    }
    assert {item["element_id"] for item in audit["mappings"]} == expected
    assert [item["flow_id"] for item in audit["flow_mappings"]] == [
        item["flow_id"] for item in blueprint["flows"]["entries"]
    ]


def test_gap_audit_separates_protocol_source_from_staged_and_live_execution() -> None:
    audit = load_blueprint_implementation_map()
    by_id = {item["element_id"]: item for item in audit["mappings"]}
    by_flow = {item["flow_id"]: item for item in audit["flow_mappings"]}
    assert by_flow["map-implementations-to-blueprint"]["implementation_assessment"] == (
        "verified-staged"
    )
    assert by_id["steal"]["implementation_assessment"] == "verified-staged"
    assert by_id["work-signature"]["implementation_assessment"] == "implemented"
    assert by_id["reality-check"]["implementation_assessment"] == "verified-staged"
    assert by_id["fatigue"]["implementation_assessment"] == "verified-staged"
    assert by_id["curiosity"]["implementation_assessment"] == "verified-staged"
    assert by_id["system-review"]["implementation_assessment"] == "verified-staged"


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
