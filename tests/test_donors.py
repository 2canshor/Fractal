from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fractal.donors import (
    load_donor_inventory,
    load_local_donor_adaptations,
    render_donor_inventory,
    validate_donor_inventory,
)

ROOT = Path(__file__).resolve().parents[1]


def test_hermes_and_bounded_donors_are_exactly_recorded() -> None:
    inventory = load_donor_inventory()
    by_id = {item["donor_id"]: item for item in inventory["donors"]}
    assert by_id["hermes-agent"]["commit"] == "fcbd1076a93841fa88855acce810e342a5b78101"
    assert by_id["hermes-agent"]["version"] == "v0.20.5 / v2026.8.19"
    assert by_id["hermes-agent"]["architecture_authority"] is False
    assert by_id["hermes-dojo"]["licence"]["status"] == "verified-file"
    assert by_id["hermes-agent-self-evolution"]["licence"]["status"] == ("declared-no-licence-file")
    assert by_id["super-hermes"]["commit"] == "ffe2d10042041dcc23325f013e8b7e607e069952"
    assert by_id["hermes-workspace"]["commit"] == (
        "c631425d8baa933f8c61d8447040f4ec8b5f571c"
    )
    assert by_id["distilly"]["commit"] == "04c72cc26c04e12c673405b94c8a42400287d403"
    assert by_id["temporal-python-sdk"]["commit"] == (
        "84b519e0ff407b049da88ac7d1711f110494ff4d"
    )
    assert by_id["in-toto"]["licence"]["spdx"] == "Apache-2.0"
    assert by_id["mlflow"]["commit"] == "9a1c0d9a9827acd23c7a215f0999e4b0f97e9870"
    assert by_id["mlflow"]["capabilities"][0]["blueprint_target"] == "greed"
    assert by_id["optuna"]["capabilities"][0]["disposition"] == "research-only"
    assert by_id["promptfoo"]["capabilities"][0]["disposition"] == "research-only"
    assert by_id["storm"]["commit"] == "e80d9bbea7362141a479940dabb751c1f244e4b6"
    assert by_id["gpt-researcher"]["version"] == "v3.6.1"
    assert all(item["context_cost"]["always_loaded_tokens_delta"] == 0 for item in by_id.values())


def test_only_verified_licence_source_can_be_a_code_adaptation_candidate() -> None:
    inventory = copy.deepcopy(load_donor_inventory())
    evolution = next(
        item for item in inventory["donors"] if item["donor_id"] == "hermes-agent-self-evolution"
    )
    evolution["capabilities"][0]["disposition"] = "staged-adaptation-candidate"
    with pytest.raises(ValueError, match="requires verified licence text"):
        validate_donor_inventory(inventory)


def test_every_staged_donor_capability_has_a_named_offline_local_adaptation() -> None:
    inventory = load_donor_inventory()
    adaptations = load_local_donor_adaptations(inventory)
    staged_ids = {
        capability["capability_id"]
        for donor in inventory["donors"]
        for capability in donor["capabilities"]
        if capability["disposition"] == "staged-adaptation-candidate"
    }
    assert {item["capability_id"] for item in adaptations["adaptations"]} == staged_ids
    assert adaptations["runtime_dependency_on_upstream"] is False
    assert adaptations["donor_set_fixed"] is False
    assert all(item["implementation_modules"] for item in adaptations["adaptations"])


def test_donor_cannot_receive_architecture_authority() -> None:
    inventory = copy.deepcopy(load_donor_inventory())
    inventory["donors"][0]["architecture_authority"] = True
    with pytest.raises(ValueError, match="architecture authority"):
        validate_donor_inventory(inventory)


def test_rendered_donor_inventory_matches_validated_source() -> None:
    assert (ROOT / "docs" / "donor-inventory.md").read_text(
        encoding="utf-8"
    ) == render_donor_inventory()
