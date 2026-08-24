from __future__ import annotations

from pathlib import Path

from fractal.blueprint import load_blueprint
from fractal.flagship import (
    load_flagship_implementation_matrix,
    render_flagship_implementation_matrix,
)

ROOT = Path(__file__).resolve().parents[1]


def test_every_blueprint_element_has_one_current_flagship_decision() -> None:
    blueprint = load_blueprint()
    matrix = load_flagship_implementation_matrix()
    expected = {
        blueprint["element_library"]["core"]["philosophy"]["element_id"],
        blueprint["element_library"]["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in blueprint["element_library"]["genres"]
            for element in genre["elements"]
        ),
    }
    assert {entry["element_id"] for entry in matrix["entries"]} == expected
    assert len(matrix["entries"]) == 24


def test_donor_set_is_need_led_and_every_implementation_has_recovery() -> None:
    matrix = load_flagship_implementation_matrix()
    by_id = {entry["element_id"]: entry for entry in matrix["entries"]}
    assert by_id["greed"]["adopted_source_ids"] == ["mlflow"]
    assert by_id["cause-research"]["adopted_source_ids"] == [
        "storm",
        "gpt-researcher",
        "hermes-dojo",
    ]
    assert by_id["two-sided-review"]["adopted_source_ids"] == ["fractal-native"]
    assert by_id["two-sided-review"]["evaluated_source_ids"] == [
        "evey-hermes-plugins",
        "autogen",
    ]
    assert all(entry["recovery"] for entry in matrix["entries"])


def test_rendered_flagship_matrix_matches_current_source() -> None:
    assert (ROOT / "docs" / "flagship-implementation-matrix.md").read_text(
        encoding="utf-8"
    ) == render_flagship_implementation_matrix()
