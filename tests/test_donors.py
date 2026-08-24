from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fractal.donors import (
    load_donor_inventory,
    render_donor_inventory,
    validate_donor_inventory,
)

ROOT = Path(__file__).resolve().parents[1]


def test_hermes_and_bounded_donors_are_exactly_recorded() -> None:
    inventory = load_donor_inventory()
    by_id = {item["donor_id"]: item for item in inventory["donors"]}
    assert by_id["hermes-agent"]["commit"] == "91e867631e9d2eb9fbd69edd4459475d38070979"
    assert by_id["hermes-agent"]["architecture_authority"] is False
    assert by_id["hermes-dojo"]["licence"]["status"] == "verified-file"
    assert by_id["hermes-agent-self-evolution"]["licence"]["status"] == ("declared-no-licence-file")
    assert by_id["super-hermes"]["repository_status"] == "no-primary-source-finding"


def test_only_verified_licence_source_can_be_a_code_adaptation_candidate() -> None:
    inventory = copy.deepcopy(load_donor_inventory())
    evolution = next(
        item for item in inventory["donors"] if item["donor_id"] == "hermes-agent-self-evolution"
    )
    evolution["capabilities"][0]["disposition"] = "staged-adaptation-candidate"
    with pytest.raises(ValueError, match="requires verified licence text"):
        validate_donor_inventory(inventory)


def test_donor_cannot_receive_architecture_authority() -> None:
    inventory = copy.deepcopy(load_donor_inventory())
    inventory["donors"][0]["architecture_authority"] = True
    with pytest.raises(ValueError, match="architecture authority"):
        validate_donor_inventory(inventory)


def test_rendered_donor_inventory_matches_validated_source() -> None:
    assert (ROOT / "docs" / "donor-inventory.md").read_text(
        encoding="utf-8"
    ) == render_donor_inventory()
