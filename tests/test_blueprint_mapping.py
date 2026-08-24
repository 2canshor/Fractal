from __future__ import annotations

import copy

import pytest

from fractal.blueprint_mapping import (
    load_donor_candidate_mappings,
    validate_candidate_mapping,
)


def mapping() -> dict:
    return copy.deepcopy(load_donor_candidate_mappings()["mappings"][0])


def test_dojo_signal_candidate_is_mapped_to_existing_find_problems() -> None:
    value = mapping()
    assert value["target"] == {
        "existing_element_id": "find-problems",
        "new_element_id": None,
        "genre_id": "steps",
        "element_type": "deuteragonist",
        "marker": "$",
    }
    assert value["change_action"] == "reconfigure"
    assert value["donor_ids"] == ["hermes-dojo"]
    assert value["authority"]["activation"] is False


def test_existing_target_cannot_be_misclassified_or_treated_as_addition() -> None:
    value = mapping()
    value["target"]["genre_id"] = "methods"
    value["target"]["element_type"] = "prop"
    value["target"]["marker"] = "¢"
    with pytest.raises(ValueError, match="misclassifies"):
        validate_candidate_mapping(value)

    value = mapping()
    value["change_action"] = "add"
    with pytest.raises(ValueError, match="cannot use the add action"):
        validate_candidate_mapping(value)


def test_candidate_cannot_inherit_mutation_or_activation_authority() -> None:
    value = mapping()
    value["authority"]["persistent_mutation"] = True
    with pytest.raises(ValueError, match="persistent_mutation"):
        validate_candidate_mapping(value)

    value = mapping()
    value["authority"]["activation"] = True
    with pytest.raises(ValueError, match="activation"):
        validate_candidate_mapping(value)


def test_candidate_must_assess_every_principle() -> None:
    value = mapping()
    value["principle_effects"].pop()
    with pytest.raises(ValueError, match="every current Principle"):
        validate_candidate_mapping(value)


def test_additive_candidate_must_follow_genre_priority() -> None:
    value = mapping()
    value["change_action"] = "add"
    value["target"] = {
        "existing_element_id": None,
        "new_element_id": "new-method",
        "genre_id": "methods",
        "element_type": "prop",
        "marker": "¢",
    }
    value["addition_priority_review"] = [
        {"element_type": "prop", "result": "selected", "reason": "Reusable Method"}
    ]
    with pytest.raises(ValueError, match=r"\^ > ¢ > % > \$"):
        validate_candidate_mapping(value)

    value["addition_priority_review"] = [
        {
            "element_type": "extra",
            "result": "insufficient",
            "reason": "The capability is a reusable method rather than Infrastructure.",
        },
        {"element_type": "prop", "result": "selected", "reason": "Reusable Method"},
    ]
    assert validate_candidate_mapping(value) is value
