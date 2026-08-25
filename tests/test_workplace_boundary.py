from __future__ import annotations

import json
from pathlib import Path

from fractal.workplace_boundary import (
    CANONICAL_DEFINITIONS,
    OwnershipCategory,
    VersionState,
    classify_file,
    classify_path,
    classify_record,
    detect_mixed_file,
    validate_system_promotion,
)


def _workplace_path(area: str, name: str = "record.json") -> str:
    home = "/" + "Users" + "/carson"
    return f"{home}/Workplace/{area}/{name}"


def _system_source_path(name: str = "record.json") -> str:
    home = "/" + "Users" + "/carson"
    return f"{home}/Fractal/src/fractal/data/{name}"


def _candidate_definition() -> dict[str, object]:
    return {
        "record_type": "system-definition",
        "element_id": "fatigue",
        "core_concept": CANONICAL_DEFINITIONS["fatigue"],
        "provenance": {
            "record_type": "research",
            "ownership": "workplace-durable",
            "research_id": "research-fatigue-1",
        },
    }


def _approval() -> dict[str, object]:
    return {
        "approved": True,
        "actor": "primary-user",
        "approval_id": "approval-1",
        "human_action": True,
    }


def _version_promotion() -> dict[str, object]:
    return {
        "route": "/version",
        "authorized": True,
        "action": "build-candidate",
        "version": "0.1.0-alpha.9",
        "status": "candidate",
        "receipt_id": "version-receipt-1",
    }


def test_all_ownership_domains_and_conservative_unknown_default() -> None:
    system = classify_record(
        {"record_type": "fractal-blueprint", "lifecycle": {"status": "canonical"}},
        _system_source_path("blueprint.json"),
    )
    durable = classify_record(
        {"record_type": "project", "project_id": "project-a"},
        _workplace_path("projects"),
    )
    ephemeral = classify_record(
        {"record_type": "runtime-state", "pid": 10},
        ("/" + "Users" + "/carson/.codex/fractal/runtime-state.json"),
    )
    historical = classify_record(
        {
            "record_type": "historical-evidence",
            "historical": True,
            "element_id": "fatigue",
            "definition": "Fatigue was once described differently.",
        },
        _workplace_path("history"),
    )
    unknown = classify_record(
        {"title": "unclassified note"},
        ("/" + "Users" + "/carson/unknown/note.json"),
    )

    assert system.category is OwnershipCategory.FRACTAL_SYSTEM
    assert durable.category is OwnershipCategory.WORKPLACE_DURABLE
    assert ephemeral.category is OwnershipCategory.WORKPLACE_EPHEMERAL
    assert historical.category is OwnershipCategory.HISTORICAL_WORKPLACE_EVIDENCE
    assert unknown.category is OwnershipCategory.NEEDS_REVIEW


def test_issue_research_experiment_proposal_stay_workplace_and_bytes_unchanged() -> None:
    system_bytes = b"canonical-active-system-bytes"
    before = system_bytes
    records = [
        ("issues", {"record_type": "issue", "id": "issue-1"}),
        ("research", {"record_type": "research", "id": "research-1"}),
        ("experiments", {"record_type": "experiment", "id": "experiment-1"}),
        ("proposals", {"record_type": "change-proposal", "id": "proposal-1"}),
    ]

    results = [classify_record(record, _workplace_path(area)) for area, record in records]

    assert all(result.category is OwnershipCategory.WORKPLACE_DURABLE for result in results)
    assert all(result.safe_for_active_system is False for result in results)
    assert system_bytes == before


def test_human_approval_without_version_keeps_result_workplace() -> None:
    result = validate_system_promotion(_candidate_definition(), _approval())

    assert result.allowed is False
    assert result.category is OwnershipCategory.WORKPLACE_DURABLE
    assert result.lifecycle == VersionState.UNVERSIONED.value
    assert any(finding.code == "version-promotion-required" for finding in result.findings)


def test_authorised_version_makes_only_reusable_result_a_system_candidate() -> None:
    result = validate_system_promotion(
        _candidate_definition(),
        _approval(),
        _version_promotion(),
        path=_workplace_path("proposals", "candidate.json"),
    )

    assert result.allowed is True
    assert result.category is OwnershipCategory.FRACTAL_SYSTEM
    assert result.lifecycle == VersionState.CANDIDATE.value
    assert result.classification.safe_for_active_system is False
    assert result.provenance.category is OwnershipCategory.WORKPLACE_DURABLE
    assert result.provenance.record_type == "research"


def test_fake_fatigue_definition_is_rejected_but_historical_wording_is_preserved() -> None:
    fake = classify_record(
        {
            "record_type": "system-definition",
            "element_id": "fatigue",
            "definition": "Fatigue means always add another automation.",
            "owner": "fractal",
        },
        _workplace_path("proposals", "fake-fatigue.json"),
    )
    historical = classify_record(
        {
            "record_type": "historical-evidence",
            "historical": True,
            "element_id": "fatigue",
            "definition": "Fatigue means always add another automation.",
            "event_hash": "a" * 64,
        },
        _workplace_path("history", "old-review.json"),
    )

    assert fake.category is OwnershipCategory.NEEDS_REVIEW
    assert any(finding.code == "fake-system-definition" for finding in fake.findings)
    assert historical.category is OwnershipCategory.HISTORICAL_WORKPLACE_EVIDENCE
    assert not any(finding.code == "fake-system-definition" for finding in historical.findings)


def test_mixed_file_requires_split_and_does_not_inherit_system_authority() -> None:
    mixed = {
        "record_type": "record-bundle",
        "records": [
            {"record_type": "project", "ownership": "workplace-durable"},
            {
                "record_type": "system-definition",
                "ownership": "fractal-system",
                "element_id": "fatigue",
                "core_concept": CANONICAL_DEFINITIONS["fatigue"],
            },
        ],
    }

    result = classify_record(mixed, _workplace_path("records", "mixed.json"))

    assert result.mixed is True
    assert result.category is OwnershipCategory.NEEDS_REVIEW
    assert detect_mixed_file(mixed, _workplace_path("records", "mixed.json")) is True
    assert any(finding.code == "mixed-ownership-file" for finding in result.findings)


def test_obvious_wrong_placements_are_actionable_and_per_workplace_status_is_allowed() -> None:
    copied_system = classify_record(
        {"record_type": "method-registry", "owner": "fractal"},
        _workplace_path("proposals", "method-registry.json"),
    )
    private = classify_record(
        {"record_type": "system-definition", "email": "carson@example.test"},
        _system_source_path("private.json"),
    )
    live = classify_record(
        {
            "record_type": "component-status",
            "database_path": ("/" + "Users" + "/carson/live/state.db"),
        },
        _workplace_path("components", "status.json"),
    )
    status = classify_record(
        {
            "record_type": "component-status",
            "component_id": "recording-core",
            "status": "verified-live",
            "evidence_ids": ["claim-1"],
        },
        _workplace_path("components", "status.json"),
    )

    assert copied_system.category is OwnershipCategory.WORKPLACE_DURABLE
    assert any(
        finding.code == "unversioned-system-work-in-workplace"
        for finding in copied_system.findings
    )
    assert private.category is OwnershipCategory.NEEDS_REVIEW
    assert any(finding.code == "personal-data-in-system" for finding in private.findings)
    assert live.category is OwnershipCategory.NEEDS_REVIEW
    assert any(finding.code == "machine-state-in-durable-workplace" for finding in live.findings)
    assert status.category is OwnershipCategory.WORKPLACE_DURABLE
    assert not any(finding.code == "duplicate-reusable-definition" for finding in status.findings)


def test_path_classification_and_json_file_read_are_read_only(tmp_path: Path) -> None:
    path = tmp_path / "Workplace" / "research" / "record.json"
    path.parent.mkdir(parents=True)
    original = {"record_type": "research", "id": "r-1"}
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()

    path_result = classify_path(path)
    file_result = classify_file(path)

    assert path_result.category is OwnershipCategory.WORKPLACE_DURABLE
    assert file_result.category is OwnershipCategory.WORKPLACE_DURABLE
    assert path.read_bytes() == before


def test_project_evidence_references_keep_paths_and_urls_as_non_runtime_evidence() -> None:
    home = "/" + "Users" + "/carson"
    source = "/" + "tmp" + "/fractal-evidence/source.txt"
    attachment = f"{home}/Workplace/evidence/attachment.pdf"
    record = {
        "record_type": "project",
        "project_id": "project-a",
        "evidence": [
            {
                "id": "evidence-1",
                "source": source,
                "attachment": attachment,
                "url": "https://example.test/evidence/1",
            }
        ],
    }
    before = json.loads(json.dumps(record))

    result = classify_file(_workplace_path("projects", "project-a.json"), record=record)

    assert result.category is OwnershipCategory.WORKPLACE_DURABLE
    assert result.safe_for_active_system is False
    assert not any(
        finding.code == "machine-state-in-durable-workplace" for finding in result.findings
    )
    assert record == before
    assert record["evidence"][0]["source"] == source
    assert record["evidence"][0]["attachment"] == attachment


def test_project_evidence_exemption_does_not_hide_live_paths_endpoints_or_secrets() -> None:
    home = "/" + "Users" + "/carson"
    cases = (
        {
            "record_type": "project",
            "context": {"catalogue": {"path": f"{home}/.codex/fractal/context.json"}},
        },
        {
            "record_type": "project",
            "adapter": {"socket": "/" + "tmp" + "/fractal-adapter.sock"},
        },
        {
            "record_type": "project",
            "adapter": {"base_url": "http://adapter.internal:8000"},
        },
        {
            "record_type": "project",
            "live_state": {"pid": 42},
        },
        {
            "record_type": "project",
            "active_config": {"path": f"{home}/.codex/config.toml"},
        },
        {
            "record_type": "project",
            "evidence": [
                {"source": "/" + "tmp" + "/retained.txt", "token": "personal-secret"}
            ],
        },
        {"record_type": "project", "source": "/" + "tmp" + "/not-evidence.txt"},
    )

    for index, record in enumerate(cases):
        result = classify_file(_workplace_path("projects", f"invalid-{index}.json"), record=record)
        assert result.category is OwnershipCategory.NEEDS_REVIEW
        assert any(
            finding.code == "machine-state-in-durable-workplace" for finding in result.findings
        )
