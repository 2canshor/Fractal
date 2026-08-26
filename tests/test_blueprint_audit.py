from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fractal.apple_alignment import (
    AppleAlignmentError,
    load_apple_principles_registry,
    validate_responsibility_alignment,
)
from fractal.blueprint import load_blueprint
from fractal.blueprint_audit import (
    RESPONSIBILITY_IDS,
    discover_responsibility_artifacts,
    load_blueprint_implementation_map,
    render_blueprint_implementation_gap,
    validate_blueprint_implementation_map,
    validate_responsibility_artifact_coverage,
)
from fractal.methods import load_agentic_element_map

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SYSTEM_VERSION = "0.1.0-alpha.8-r1-b5b8a90"
HISTORICAL_BASELINE_STATE_ROLE = "historical-implementation-baseline-not-live-status"

EXPECTED_ELEMENT_ASSESSMENTS = {
    "continuous-improvement": "partial",
    "system-review": "verified-staged",
    "fatigue": "verified-staged",
    "curiosity": "verified-staged",
    "greed": "verified-staged",
    "project-review": "verified-staged",
    "component-governance": "implemented",
    "deterministic-over-probabilistic": "implemented",
    "quantity-over-quality": "verified-staged",
    "subtraction-first": "verified-staged",
    "global-outcome-over-local-optimisation": "verified-staged",
    "work-signature": "implemented",
    "naming-system": "implemented",
    "capability-check": "implemented",
    "hooks": "verified-staged",
    "reality-check": "verified-staged",
    "experiment": "implemented",
    "human-control": "implemented",
    "donor-quarantine": "verified-staged",
    "donor-registry": "verified-staged",
    "environment-adapters": "verified-staged",
    "cause-research": "verified-staged",
    "two-sided-review": "verified-staged",
    "steal": "verified-staged",
}

EXPECTED_FLOW_ASSESSMENTS = {
    "find-problems": "verified-staged",
    "find-local-patterns": "verified-staged",
    "find-global-patterns": "verified-staged",
    "find-global-pattern-reasons": "verified-staged",
    "find-global-pattern-solutions": "verified-staged",
    "map-implementations-to-blueprint": "verified-staged",
    "debate-global-pattern-solutions": "verified-staged",
    "present-decisions-one-by-one": "verified-staged",
}


def test_gap_audit_covers_every_classified_blueprint_element() -> None:
    blueprint = load_blueprint()
    audit = load_blueprint_implementation_map()
    library = blueprint["element_library"]
    expected = {
        library["core"]["philosophy"]["element_id"],
        library["core"]["protagonist"]["element_id"],
        *(
            element["element_id"]
            for genre in library["genres"]
            for element in genre["elements"]
        ),
    }
    assert {item["element_id"] for item in audit["mappings"]} == expected
    assert len(audit["mappings"]) == 24
    assert {
        item["element_id"]: item["implementation_assessment"] for item in audit["mappings"]
    } == EXPECTED_ELEMENT_ASSESSMENTS
    assert len(audit["flow_mappings"]) == 8
    assert {
        item["flow_id"]: item["implementation_assessment"] for item in audit["flow_mappings"]
    } == EXPECTED_FLOW_ASSESSMENTS
    assert [item["flow_id"] for item in audit["flow_mappings"]] == [
        item["flow_id"] for item in blueprint["flows"]["entries"]
    ]


def test_gap_audit_is_a_historical_baseline_not_live_status() -> None:
    audit = load_blueprint_implementation_map()
    assert audit["baseline_system_version"] == BASELINE_SYSTEM_VERSION
    assert audit["state_role"] == HISTORICAL_BASELINE_STATE_ROLE
    assert "active_system_version" not in audit

    rendered = render_blueprint_implementation_gap(audit)
    assert "Active System Version Compared" not in rendered
    assert f"Baseline System Version Compared: `{BASELINE_SYSTEM_VERSION}`" in rendered
    assert f"State Role: `{HISTORICAL_BASELINE_STATE_ROLE}`" in rendered
    assert "dynamic `fractal status`" in rendered
    assert "live System Version pointer" in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_system_version", ""),
        ("state_role", ""),
        ("state_role", "current-live-status"),
    ],
)
def test_gap_audit_rejects_missing_or_live_state_metadata(field: str, value: str) -> None:
    audit = copy.deepcopy(load_blueprint_implementation_map())
    audit[field] = value
    with pytest.raises(ValueError):
        validate_blueprint_implementation_map(
            audit,
            blueprint=load_blueprint(),
            agentic_map=load_agentic_element_map(),
        )


def test_gap_audit_rejects_legacy_active_system_version_field() -> None:
    audit = copy.deepcopy(load_blueprint_implementation_map())
    audit["active_system_version"] = BASELINE_SYSTEM_VERSION
    with pytest.raises(ValueError, match="must not claim the current active"):
        validate_blueprint_implementation_map(
            audit,
            blueprint=load_blueprint(),
            agentic_map=load_agentic_element_map(),
        )


def test_gap_audit_separates_protocol_source_from_staged_and_live_execution() -> None:
    audit = load_blueprint_implementation_map()
    by_id = {item["element_id"]: item for item in audit["mappings"]}
    by_flow = {item["flow_id"]: item for item in audit["flow_mappings"]}
    assert by_flow["map-implementations-to-blueprint"]["implementation_assessment"] == (
        "verified-staged"
    )
    assert by_id["steal"]["implementation_assessment"] == "verified-staged"
    assert by_id["work-signature"]["implementation_assessment"] == "implemented"
    assert by_id["reality-check"]["implementation_assessment"] == "verified-staged"
    assert by_id["fatigue"]["implementation_assessment"] == "verified-staged"
    assert by_id["curiosity"]["implementation_assessment"] == "verified-staged"
    assert by_id["system-review"]["implementation_assessment"] == "verified-staged"


def test_gap_audit_rejects_unknown_retained_evidence() -> None:
    audit = copy.deepcopy(load_blueprint_implementation_map())
    audit["mappings"][0]["current_node_ids"] = ["not-a-current-node"]
    with pytest.raises(ValueError, match="Unknown retained implementation evidence"):
        validate_blueprint_implementation_map(
            audit,
            blueprint=load_blueprint(),
            agentic_map=load_agentic_element_map(),
        )


def test_every_persistent_responsibility_has_one_blueprint_and_improvement_path() -> None:
    audit = load_blueprint_implementation_map()
    mappings = audit["responsibility_mappings"]
    assert [item["responsibility_id"] for item in mappings] == list(RESPONSIBILITY_IDS)
    assert len(mappings) == 20
    assert all(item["primary_element_id"] for item in mappings)
    assert all(item["flow_ids"] for item in mappings)
    assert all(
        item["continuous_improvement_path"]
        == ["project-review", "system-review", "continuous-improvement"]
        for item in mappings
    )
    assert all(
        item["apple_alignment"]["responsibility_id"] == item["responsibility_id"]
        for item in mappings
    )
    assert all(item["proof_layers"]["active_live"] == "pending" for item in mappings)
    assert all(
        item["candidate_only"]
        is (item["responsibility_id"] in {"RESP-16", "RESP-17", "RESP-18"})
        for item in mappings
    )


def test_every_current_implementation_artifact_has_exactly_one_primary_owner() -> None:
    audit = load_blueprint_implementation_map()
    observed = discover_responsibility_artifacts(ROOT)
    owners = validate_responsibility_artifact_coverage(audit, observed_paths=observed)
    assert set(owners) == observed
    assert len(owners) == len(observed) == 142


def test_new_unmapped_artifact_fails_closed() -> None:
    audit = load_blueprint_implementation_map()
    observed = discover_responsibility_artifacts(ROOT)
    observed.add("src/fractal/new_unmapped_runtime.py")
    with pytest.raises(ValueError, match="missing=.*new_unmapped_runtime"):
        validate_responsibility_artifact_coverage(audit, observed_paths=observed)


def test_duplicate_primary_owner_fails_closed() -> None:
    audit = copy.deepcopy(load_blueprint_implementation_map())
    duplicate = audit["responsibility_mappings"][0]["source_paths"][0]
    audit["responsibility_mappings"][1]["source_paths"].append(duplicate)
    with pytest.raises(ValueError, match="multiple primary owners"):
        validate_responsibility_artifact_coverage(audit)


def test_candidate_genesis_responsibilities_never_claim_active_live() -> None:
    audit = load_blueprint_implementation_map()
    by_id = {
        item["responsibility_id"]: item for item in audit["responsibility_mappings"]
    }
    for responsibility_id in ("RESP-16", "RESP-17", "RESP-18"):
        mapping = by_id[responsibility_id]
        assert mapping["candidate_only"] is True
        assert mapping["proof_layers"]["active_live"] == "pending"
        assert "Candidate-only Genesis responsibility" in mapping["claim_boundary"]


def test_responsibility_apple_alignment_is_grouped_and_manifest_bound() -> None:
    audit = load_blueprint_implementation_map()
    for mapping in audit["responsibility_mappings"]:
        alignment = mapping["apple_alignment"]
        assert alignment["record_version"] == 2
        assert "source_alignment" not in alignment
        applicability = alignment["source_applicability"]
        assert applicability["registry_manifest_sha256"]
        assert applicability["universal"]["selection"] == "all"
        assert applicability["conditional"]["not_triggered_selection"] == (
            "all-except-triggered"
        )
        assert applicability["not_current"]["selection"] == "all"


def test_packaged_responsibilities_are_staged_until_human_delight_acceptance() -> None:
    audit = load_blueprint_implementation_map()
    registry = load_apple_principles_registry()
    for mapping in audit["responsibility_mappings"]:
        delight = mapping["apple_alignment"]["principle_alignment"][-1]
        assert delight["principle_id"] == "delight"
        assert delight["human_qualitative_acceptance"] == "pending"
        assert delight["human_qualitative_evidence_ids"] == []
        assert delight["observable_proxy_evidence_ids"]
        assert (
            validate_responsibility_alignment(
                mapping["apple_alignment"],
                registry,
                require_human_qualitative_acceptance=False,
            )
            == mapping["apple_alignment"]
        )

    with pytest.raises(AppleAlignmentError, match="human qualitative"):
        validate_responsibility_alignment(
            audit["responsibility_mappings"][0]["apple_alignment"],
            registry,
        )


def test_cli_responsibility_triggers_actual_hig_surfaces_with_direct_tests() -> None:
    audit = load_blueprint_implementation_map()
    cli = next(
        item
        for item in audit["responsibility_mappings"]
        if item["responsibility_id"] == "RESP-19"
    )
    conditional = cli["apple_alignment"]["source_applicability"]["conditional"]
    expected = {
        "hig:alerts",
        "hig:content",
        "hig:inputs",
        "hig:labels",
        "hig:layout-and-organization",
        "hig:menus-and-actions",
        "hig:navigation-and-search",
        "hig:progress-indicators",
        "hig:settings",
        "hig:system-experiences",
        "hig:typography",
    }
    assert expected.issubset(conditional["triggered_source_ids"])
    for source_id in expected:
        evidence = conditional["triggered_evidence"][source_id]
        assert "artifact:tests/test_project_cli.py" in evidence
        assert "artifact:tests/test_workplace_cli.py" in evidence


def test_rendered_gap_document_matches_validated_mapping() -> None:
    assert (ROOT / "docs" / "blueprint-implementation-gap.md").read_text(
        encoding="utf-8"
    ) == render_blueprint_implementation_gap()
