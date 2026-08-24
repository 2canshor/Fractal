from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from fractal.steal import (
    load_steal_dry_runs,
    select_donors_for_need,
    stage_local_source_snapshot,
    validate_steal_run,
)


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
    run["target"]["current_assessment"] = "verified-staged"
    with pytest.raises(ValueError, match="target disagree"):
        validate_steal_run(run)


def test_donor_selection_comes_from_current_element_need_not_a_fixed_list() -> None:
    greed = select_donors_for_need("greed")
    cause = select_donors_for_need("cause-research")
    assert {(item["donor_id"], item["capability_id"]) for item in greed} == {
        ("mlflow", "metric-threshold-baseline-comparison")
    }
    assert {(item["donor_id"], item["capability_id"]) for item in cause} == {
        ("gpt-researcher", "planned-provenance-first-retrieval"),
        ("hermes-dojo", "dojo-weakness-analysis"),
    }
    assert all(item["architecture_authority"] is False for item in [*greed, *cause])


def test_selected_source_snapshot_is_local_immutable_and_not_a_runtime_dependency(
    tmp_path: Path,
) -> None:
    licence = b"Test licence evidence"
    arguments = {
        "snapshot_root": tmp_path / "snapshots",
        "donor_id": "source-project",
        "capability_id": "bounded-method",
        "source_url": "https://github.com/example/source-project",
        "commit": "a" * 40,
        "acquired_at": "2026-08-24",
        "licence_spdx": "MIT",
        "licence_text": licence,
        "expected_licence_sha256": hashlib.sha256(licence).hexdigest(),
        "source_files": {"method/source.py": b"def source(): return True\n"},
        "fractal_local_name": "Bounded Method",
        "implementation_modules": ["fractal.steal"],
        "target_element_id": "steal",
    }
    first = stage_local_source_snapshot(**arguments)
    second = stage_local_source_snapshot(**arguments)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert first["runtime_dependency_on_upstream"] is False
    assert first["runtime_dependency_on_snapshot"] is False
    assert first["active"] is False
    assert (Path(first["path"]) / "method" / "source.py").is_file()

    changed = {**arguments, "source_files": {"method/source.py": b"changed\n"}}
    with pytest.raises(ValueError, match="Immutable donor source snapshot"):
        stage_local_source_snapshot(**changed)


def test_source_snapshot_rejects_licence_mismatch_and_path_escape(tmp_path: Path) -> None:
    base = {
        "snapshot_root": tmp_path / "snapshots",
        "donor_id": "source-project",
        "capability_id": "bounded-method",
        "source_url": "https://github.com/example/source-project",
        "commit": "b" * 40,
        "acquired_at": "2026-08-24",
        "licence_spdx": "MIT",
        "licence_text": b"licence",
        "expected_licence_sha256": "0" * 64,
        "source_files": {"source.py": b"source"},
        "fractal_local_name": "Bounded Method",
        "implementation_modules": ["fractal.steal"],
        "target_element_id": "steal",
    }
    with pytest.raises(ValueError, match="licence digest"):
        stage_local_source_snapshot(**base)

    safe = {**base, "expected_licence_sha256": hashlib.sha256(b"licence").hexdigest()}
    with pytest.raises(ValueError, match="Unsafe donor source path"):
        stage_local_source_snapshot(**{**safe, "source_files": {"../escape": b"source"}})
