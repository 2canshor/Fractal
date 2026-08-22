from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fractal.capabilities import (
    CapabilityError,
    build_skill_package,
    get_capability_status,
    load_capability_registry,
    select_capability,
    validate_skill_source,
    verify_skill_package,
    verify_skill_projection,
)

ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "capabilities" / "skills"


def test_registry_separates_availability_activation_and_execution() -> None:
    registry = load_capability_registry()
    assert len(registry["capabilities"]) == 10
    for capability in registry["capabilities"]:
        assert set(capability["status"]) == {
            "availability",
            "activation_authority",
            "execution",
        }
        assert (ROOT / capability["source"] / "SKILL.md").is_file()
        assert validate_skill_source(ROOT / capability["source"])["valid"] is True
    unknown = get_capability_status("not-installed")
    assert unknown["availability"]["state"] == "unknown"
    assert unknown["execution"]["state"] == "unknown"


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("search web for current primary sources", "research"),
        ("fill form and submit it", "web-operations"),
        ("redesign this landing page", "interface-design"),
        ("review the UI accessibility", "interface-design"),
        ("make this sound less like AI", "writing-authenticity"),
        ("delegate this bounded implementation", "delegation-workflow"),
        ("rename this class name", "naming-system"),
        ("run a system review for the completed project", "system-review"),
        ("review this legacy old skill replacement", "legacy-material-review"),
        ("resolve this material unknown", "clarification"),
        ("create a Skill eval", "capability-development"),
    ],
)
def test_routing_eval_selects_one_rebuilt_capability(request_text: str, expected: str) -> None:
    assert select_capability(request_text) == expected


def test_design_sources_are_merged_instead_of_competing() -> None:
    ids = {item["capability_id"] for item in load_capability_registry()["capabilities"]}
    assert "interface-design" in ids
    assert ids.isdisjoint(
        {"design-taste", "frontend-design", "ui-ux-pro-max", "web-design-guidelines"}
    )


def test_package_and_projection_are_verifiably_derived_from_source(tmp_path: Path) -> None:
    source = SKILLS / "clarification"
    package = tmp_path / "clarification.skill"
    first = build_skill_package(source, package)
    first_bytes = package.read_bytes()
    second = build_skill_package(source, package)
    assert package.read_bytes() == first_bytes
    assert second["package_sha256"] == first["package_sha256"]
    assert verify_skill_package(source, package)["verified"] is True

    projection = tmp_path / "projections" / "clarification"
    shutil.copytree(source, projection)
    assert verify_skill_projection(source, projection)["verified"] is True
    (projection / "SKILL.md").write_text("tampered")
    with pytest.raises(CapabilityError, match="projection drift"):
        verify_skill_projection(source, projection)
