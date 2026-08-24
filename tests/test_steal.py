from __future__ import annotations

import copy

import pytest

from fractal.steal import load_steal_dry_runs, validate_steal_run


def steal_run() -> dict:
    return copy.deepcopy(load_steal_dry_runs()["runs"][0])


def test_complete_steal_dry_run_keeps_later_versions_out_of_scope() -> None:
    run = steal_run()
    assert [item["effort_share"] for item in run["research_routes"]] == [60, 20, 20]
    assert run["disposition"] == "staged-adaptation-candidate"
    assert run["automatic_replace"] is False
    assert run["next_version_eligible"] is True
    assert all(value is False for value in run["authority"].values())


def test_steal_rejects_incomplete_research_and_comparison() -> None:
    run = steal_run()
    run["research_routes"].pop()
    with pytest.raises(ValueError, match="60/20/20"):
        validate_steal_run(run)

    run = steal_run()
    run["comparison"].pop()
    with pytest.raises(ValueError, match="dimensions"):
        validate_steal_run(run)


def test_steal_rejects_donor_authority_and_automatic_replacement() -> None:
    run = steal_run()
    run["authority"]["persistent_mutation"] = True
    with pytest.raises(ValueError, match="persistent_mutation"):
        validate_steal_run(run)

    run = steal_run()
    run["automatic_replace"] = True
    with pytest.raises(ValueError, match="automatically"):
        validate_steal_run(run)


def test_steal_requires_verified_licence_for_staged_adaptation() -> None:
    run = steal_run()
    run["donor_candidate"]["donor_id"] = "hermes-agent-self-evolution"
    run["donor_candidate"]["capability_id"] = "evolution-dataset-builder"
    run["donor_candidate"]["licence_status"] = "declared-no-licence-file"
    with pytest.raises(ValueError, match="verified licence"):
        validate_steal_run(run)


def test_steal_requires_blueprint_mapping_to_match_the_target() -> None:
    run = steal_run()
    run["target"]["blueprint_element_id"] = "fatigue"
    run["target"]["current_assessment"] = "partial"
    with pytest.raises(ValueError, match="target disagree"):
        validate_steal_run(run)
