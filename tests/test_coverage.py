from __future__ import annotations

from pathlib import Path

from fractal.blueprint import load_blueprint
from fractal.coverage import build_blueprint_coverage_matrix, render_blueprint_coverage_matrix

ROOT = Path(__file__).resolve().parents[1]


def test_matrix_covers_every_core_item_library_element_and_flow() -> None:
    blueprint = load_blueprint()
    matrix = build_blueprint_coverage_matrix()
    expected_elements = {
        blueprint["element_library"]["core"]["philosophy"]["element_id"],
        blueprint["element_library"]["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in blueprint["element_library"]["genres"]
            for element in genre["elements"]
        ),
    }
    assert {row["element_id"] for row in matrix["element_rows"]} == expected_elements
    assert [row["flow_id"] for row in matrix["flow_rows"]] == [
        flow["flow_id"] for flow in blueprint["flows"]["entries"]
    ]


def test_matrix_contains_every_exact_flow_to_element_use_once() -> None:
    blueprint = load_blueprint()
    matrix = build_blueprint_coverage_matrix()
    expected = {
        (flow["flow_id"], element_id)
        for flow in blueprint["flows"]["entries"]
        for element_id in flow["uses_elements"]
    }
    observed = {(row["flow_id"], row["element_id"]) for row in matrix["flow_use_rows"]}
    assert observed == expected
    assert len(matrix["flow_use_rows"]) == len(expected)


def test_matrix_never_promotes_contract_proof_to_execution_proof() -> None:
    matrix = build_blueprint_coverage_matrix()
    assert all(row["contract_proof"] == "verified" for row in matrix["flow_use_rows"])
    assert all(row["synthetic_proof"] == "pending" for row in matrix["flow_use_rows"])
    assert all(row["staged_proof"] == "pending" for row in matrix["flow_use_rows"])
    assert all(row["active_live_proof"] == "pending" for row in matrix["flow_use_rows"])


def test_matrix_separates_staged_flow_travel_from_active_live_lifecycle() -> None:
    matrix = build_blueprint_coverage_matrix()
    assert len(matrix["flow_transition_rows"]) == 9
    assert all(row["staged_proof"] == "verified" for row in matrix["flow_transition_rows"])
    assert all(
        row["active_live_proof"] == "pending" for row in matrix["flow_transition_rows"]
    )
    lifecycle = {row["arrow_id"]: row for row in matrix["lifecycle_arrow_rows"]}
    assert lifecycle["work-completed->work-signature"]["active_live_proof"] == "verified"
    assert lifecycle["project-completion->system-review"]["staged_proof"] == "verified"
    assert lifecycle["project-completion->system-review"]["active_live_proof"] == "pending"
    assert lifecycle["human-approved-candidate->system-version"]["staged_proof"] == "pending"
    assert lifecycle["system-version->future-project-outcome"]["synthetic_proof"] == "pending"


def test_matrix_projects_the_same_twenty_persistent_responsibilities() -> None:
    matrix = build_blueprint_coverage_matrix()
    rows = matrix["responsibility_rows"]
    assert [row["responsibility_id"] for row in rows] == [
        f"RESP-{index:02d}" for index in range(20)
    ]
    assert sum(row["artifact_count"] for row in rows) == 142
    assert all(
        row["continuous_improvement_path"][-2:]
        == ["system-review", "continuous-improvement"]
        for row in rows
    )
    assert all(
        row["apple_alignment"]
        == "deterministic-validated-human-delight-pending"
        for row in rows
    )


def test_rendered_matrix_matches_the_current_source() -> None:
    assert (ROOT / "docs" / "blueprint-coverage-matrix.md").read_text(
        encoding="utf-8"
    ) == render_blueprint_coverage_matrix()
