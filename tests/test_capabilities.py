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
from fractal.surface_symbols import load_surface_symbol_manifest

ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "capabilities" / "skills"


def test_registry_separates_availability_activation_and_execution() -> None:
    registry = load_capability_registry()
    assert len(registry["capabilities"]) == 11
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
        ("run a milestone review for this active project", "project-review"),
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


@pytest.mark.parametrize(
    "skill_id",
    [
        "assess",
        "automate",
        "complete",
        "create",
        "edit",
        "match",
        "publish",
        "research",
        "review",
        "version",
    ],
)
def test_user_entries_have_portable_source_and_explicit_ui_examples(
    skill_id: str,
) -> None:
    result = validate_skill_source(SKILLS / skill_id)

    assert result["valid"] is True
    assert set(result["symbol_assets"]["assets"]) == {"small", "large"}


def test_user_surface_symbol_manifest_has_unique_assets_and_exact_match_symbol() -> None:
    manifest = load_surface_symbol_manifest()
    symbols = {item["entry_id"]: item for item in manifest["symbols"]}
    asset_hashes = [
        item["assets"][size]["sha256"]
        for item in manifest["symbols"]
        for size in ("small", "large")
    ]

    assert symbols["match"]["name"] == "slider.horizontal.2.square"
    assert symbols["match"]["container_shape"] == "square"
    assert symbols["match"]["palette"] == "command-outline"
    assert symbols["match"]["foreground_color"] == "#BF5AF2"
    assert manifest["summary"] == {
        "entry_count": 10,
        "action_count": 6,
        "command_count": 4,
    }
    assert manifest["verification_contract"] == {
        "required_sizes_px": [16, 20, 24, 32],
        "required_appearances": ["light", "dark"],
        "codex_discovery_order": ["plugin/installed", "skills/list:forceReload"],
        "live_ui_required_after_install": True,
    }
    assert symbols["match"]["selection"]["search_terms"] == [
        "match",
        "align",
        "adjust",
        "slider",
        "perspective",
        "reality",
    ]
    assert "equal.square.fill" in symbols["match"]["selection"][
        "alternatives_considered"
    ]
    assert len(asset_hashes) == len(set(asset_hashes)) == 20


def test_naming_system_owns_the_required_user_surface_symbol_method() -> None:
    naming = (SKILLS / "naming-system" / "SKILL.md").read_text(encoding="utf-8")
    capability = (SKILLS / "capability-development" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    reference = (
        SKILLS / "naming-system" / "references" / "user-surface-symbols.md"
    ).read_text(encoding="utf-8")
    renderer = (SKILLS / "render_sf_symbol_assets.swift").read_text(encoding="utf-8")

    assert "Blueprint-required `Select User-Surface Symbol` sub-step" in naming
    assert "invoke Naming System's Blueprint-required" in capability
    for required in (
        "name_availability.plist",
        "symbol_search.plist",
        "alternatives_considered",
        "--contact-sheet-dir",
        "plugin/installed",
        "skills/list",
        "It is not a separate Blueprint element",
    ):
        assert required in reference
    assert "manifestSymbols.count == 10" not in renderer
    assert '"entry_count": manifestSymbols.count' in renderer


def test_user_skill_rejects_an_icon_path_outside_its_manifest(tmp_path: Path) -> None:
    source = tmp_path / "match"
    shutil.copytree(SKILLS / "match", source)
    metadata = source / "agents" / "openai.yaml"
    metadata.write_text(
        metadata.read_text(encoding="utf-8").replace(
            'icon_small: "./assets/match-small.png"',
            'icon_small: "../match-small.png"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(CapabilityError, match="icon path drifted"):
        validate_skill_source(source)


def test_user_skill_rejects_an_icon_checksum_change(tmp_path: Path) -> None:
    source = tmp_path / "match"
    shutil.copytree(SKILLS / "match", source)
    asset = source / "assets" / "match-small.png"
    asset.write_bytes(asset.read_bytes() + b"drift")

    with pytest.raises(CapabilityError, match="checksum drifted"):
        validate_skill_source(source)


@pytest.mark.parametrize(
    "skill_id", ["automate", "create", "edit", "publish", "research", "review"]
)
def test_actions_distinguish_route_match_and_dependency_states(skill_id: str) -> None:
    source = (SKILLS / skill_id / "SKILL.md").read_text(encoding="utf-8")
    for state in ("`exact`", "`partial`", "`missing`", "`unavailable`"):
        assert state in source
    assert "never claim the dependency worked" in source


def test_version_job_requires_activation_before_completion() -> None:
    source = (SKILLS / "version" / "SKILL.md").read_text(encoding="utf-8")
    assert "activate that exact manifest" in source
    assert "fresh session" in source
    assert "This action never activates" not in source
    assert "activation is always excluded" not in source


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
