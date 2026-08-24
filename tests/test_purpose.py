from __future__ import annotations

from pathlib import Path

from fractal.purpose import (
    build_continuous_improvement_purpose_receipt,
    render_continuous_improvement_purpose_audit,
)

ROOT = Path(__file__).resolve().parents[1]


def test_every_library_element_has_one_path_to_continuous_improvement() -> None:
    receipt = build_continuous_improvement_purpose_receipt()
    assert receipt["aligned"] is True
    assert receipt["flow_owner"] == "system-review"
    assert receipt["purpose"] == "continuous-improvement"
    assert len(receipt["element_paths"]) == 22
    assert all(path["flows"] for path in receipt["element_paths"].values())
    assert receipt["unrelated_change_groups"] == []


def test_naming_symbol_scope_remains_supporting_infrastructure() -> None:
    receipt = build_continuous_improvement_purpose_receipt()
    assert receipt["naming_and_symbol_path"] == [
        "naming-system",
        "map-implementations-to-blueprint",
        "system-review",
        "continuous-improvement",
    ]


def test_rendered_purpose_audit_matches_current_source() -> None:
    assert (ROOT / "docs" / "continuous-improvement-purpose-audit.md").read_text(
        encoding="utf-8"
    ) == render_continuous_improvement_purpose_audit()
