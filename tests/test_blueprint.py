from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fractal.blueprint import load_blueprint, render_blueprint, validate_blueprint

ROOT = Path(__file__).resolve().parents[1]

ACTIVE_WORKFLOW_SOURCES = [
    "README.md",
    "ARCHITECTURE.md",
    "capabilities/skills/learn/SKILL.md",
    "capabilities/skills/learn/agents/openai.yaml",
    "capabilities/skills/align/SKILL.md",
    "capabilities/skills/project-review/SKILL.md",
    "capabilities/skills/project-review/agents/openai.yaml",
    "capabilities/skills/system-review/SKILL.md",
    "docs/agentic-element-map.md",
    "docs/architecture-lineage.md",
    "docs/continuous-improvement-methods.md",
    "docs/product-introduction.md",
    "docs/project-lifecycle.md",
    "docs/system-review-and-versioning.md",
    "src/fractal/data/agentic-element-map.json",
    "src/fractal/data/method-registry.json",
    "src/fractal/data/node-implementation-map.json",
    "src/fractal/system_review.py",
]


def test_blueprint_is_canonical_and_pointer_activated() -> None:
    blueprint = load_blueprint()
    assert blueprint["lifecycle"]["status"] == "canonical"
    assert blueprint["lifecycle"]["active_state_source"] == "system-version-pointer"
    assert blueprint["element_library"]["core"]["protagonist"]["element_id"] == (
        "system-review"
    )
    assert blueprint["blueprint_version"] == "0.1.0"


def test_three_sections_are_separate_and_reality_check_is_infrastructure() -> None:
    blueprint = load_blueprint()
    assert [
        blueprint["element_library"]["section_id"],
        blueprint["flows"]["section_id"],
        blueprint["blueprint_change_rules"]["section_id"],
    ] == ["element-library", "flows", "blueprint-change-rules"]
    genres = {
        item["genre_id"]: item for item in blueprint["element_library"]["genres"]
    }
    assert set(genres) == {"values", "principles", "infrastructure", "methods"}
    flows = blueprint["flows"]["entries"]
    assert [item["sequence"] for item in flows] == list(range(1, 9))
    assert [item["flow_id"] for item in flows][-5:] == [
        "find-global-pattern-reasons",
        "find-global-pattern-solutions",
        "map-implementations-to-blueprint",
        "debate-global-pattern-solutions",
        "present-decisions-one-by-one",
    ]
    assert "flows" not in genres
    assert not {
        item["flow_id"] for item in flows
    }.intersection(
        element["element_id"]
        for genre in genres.values()
        for element in genre["elements"]
    )
    infrastructure = {item["element_id"] for item in genres["infrastructure"]["elements"]}
    assert "reality-check" in infrastructure


def test_signature_function_is_a_cross_genre_tag_not_a_genre() -> None:
    blueprint = load_blueprint()
    library = blueprint["element_library"]
    assert library["tag_definitions"][0]["tag_id"] == "signature-function"
    assert library["tag_definitions"][0]["cross_genre"] is True
    genres = {item["genre_id"]: item for item in library["genres"]}
    assert "signature-functions" not in genres
    assert "functions" not in genres
    tagged = {
        element["element_id"]
        for genre in library["genres"]
        for element in genre["elements"]
        if "signature-function" in element.get("tags", [])
    }
    assert tagged == {
        "curiosity",
        "fatigue",
        "greed",
        "project-review",
    }


def test_eight_flows_are_primary_user_confirmed_use_rules() -> None:
    blueprint = load_blueprint()
    assert blueprint["flows"]["decision_status"] == "primary-user-confirmed"
    assert len(blueprint["flows"]["entries"]) == 8
    rules = blueprint["blueprint_change_rules"]["modification_rules"]
    assert rules["flows_are_elements"] is False
    assert rules["flows_are_genres"] is False
    assert rules["flow_references_require_registered_elements"] is True


def test_perspective_name_and_experiment_classification_are_confirmed() -> None:
    blueprint = load_blueprint()
    by_id = {
        element["element_id"]: {**element, "genre_id": genre["genre_id"]}
        for genre in blueprint["element_library"]["genres"]
        for element in genre["elements"]
    }
    assert by_id["project-review"]["human_name"] == "Perspective"
    assert by_id["project-review"]["historical_names"] == ["Project Review"]
    assert by_id["project-review"]["naming_status"] == "primary-user-confirmed"
    assert by_id["project-review"]["genre_id"] == "methods"
    assert by_id["project-review"]["marker"] == "$"
    assert by_id["experiment"]["genre_id"] == "infrastructure"
    assert by_id["experiment"]["marker"] == "^"
    assert by_id["component-governance"]["genre_id"] == "infrastructure"
    assert by_id["component-governance"]["marker"] == "^"
    assert "signature-function" not in by_id["component-governance"].get("tags", [])
    assert blueprint["blueprint_change_rules"]["candidate_queue"] == []
    assert blueprint["open_questions"] == []


def test_genre_and_role_are_independent_axes() -> None:
    blueprint = load_blueprint()
    methods = next(
        genre
        for genre in blueprint["element_library"]["genres"]
        if genre["genre_id"] == "methods"
    )
    by_id = {item["element_id"]: item for item in methods["elements"]}
    assert methods["allowed_markers"] == ["$", "¢"]
    assert by_id["project-review"]["marker"] == "$"
    assert by_id["two-sided-review"]["marker"] == "¢"


def test_hermes_is_governed_as_a_donor_and_steal_is_a_prop() -> None:
    blueprint = load_blueprint()
    donor_policy = blueprint["blueprint_change_rules"]["donor_policy"]
    assert donor_policy["hermes_is_a_donor"] is True
    assert donor_policy["donor_architecture_has_authority"] is False
    assert donor_policy["donor_set_fixed"] is False
    assert donor_policy["local_fork_required"] is True
    assert donor_policy["runtime_upstream_dependency_allowed"] is False
    assert donor_policy["donor_name_becomes_fractal_name"] is False
    methods = next(
        genre["elements"]
        for genre in blueprint["element_library"]["genres"]
        if genre["genre_id"] == "methods"
    )
    steal = next(item for item in methods if item["element_id"] == "steal")
    assert steal["human_name"] == "Adapt"
    assert steal["marker"] == "¢"
    assert steal["technical_id"] == "donor-implementation-reuse"
    assert "activation" in steal["does_not_own"]
    assert steal["activation_contract"] == {
        "activated_by": "curiosity",
        "route_kind": "implementation-evidence-acquisition",
        "serves_flow": "find-global-pattern-solutions",
        "hands_off_to_flow": "map-implementations-to-blueprint",
        "cannot_replace": ["project-review", "cause-research", "two-sided-review"],
    }


def test_blueprint_rejects_a_second_genre_for_one_element() -> None:
    blueprint = copy.deepcopy(load_blueprint())
    project_review = copy.deepcopy(
        next(
            item
            for genre in blueprint["element_library"]["genres"]
            for item in genre["elements"]
            if item["element_id"] == "project-review"
        )
    )
    next(
        genre
        for genre in blueprint["element_library"]["genres"]
        if genre["genre_id"] == "methods"
    )[
        "elements"
    ].append(project_review)
    with pytest.raises(ValueError, match="more than one Genre"):
        validate_blueprint(blueprint)


def test_blueprint_rejects_donor_authority_and_false_activation() -> None:
    blueprint = copy.deepcopy(load_blueprint())
    blueprint["blueprint_change_rules"]["donor_policy"][
        "donor_architecture_has_authority"
    ] = True
    with pytest.raises(ValueError, match="Donor architecture"):
        validate_blueprint(blueprint)

    blueprint = copy.deepcopy(load_blueprint())
    blueprint["lifecycle"]["active_state_source"] = "blueprint-file"
    with pytest.raises(ValueError, match="System Version pointer"):
        validate_blueprint(blueprint)


def test_flow_rejects_a_capability_absent_from_the_element_library() -> None:
    blueprint = copy.deepcopy(load_blueprint())
    blueprint["flows"]["entries"][0]["uses_elements"].append("imaginary-book")
    with pytest.raises(ValueError, match="absent from the Library"):
        validate_blueprint(blueprint)


def test_every_library_element_is_used_by_at_least_one_flow() -> None:
    blueprint = copy.deepcopy(load_blueprint())
    for flow in blueprint["flows"]["entries"]:
        flow["uses_elements"] = [
            item for item in flow["uses_elements"] if item != "environment-adapters"
        ]
    with pytest.raises(ValueError, match="unused by every Flow"):
        validate_blueprint(blueprint)


def test_rendered_blueprint_document_matches_the_validated_source() -> None:
    expected = render_blueprint()
    assert (ROOT / "docs" / "blueprint.md").read_text(encoding="utf-8") == expected


def test_active_workflow_sources_cannot_regress_to_the_old_architecture() -> None:
    forbidden = {
        "five-step",
        "Five Steps",
        "Secondary Mechanism",
        "Choose the System Response",
        "Step 5: Reality Check",
        "Project Review",
    }
    findings = []
    for relative in ACTIVE_WORKFLOW_SOURCES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for phrase in forbidden:
            if phrase in text:
                findings.append(f"{relative}: {phrase}")
    assert findings == []
